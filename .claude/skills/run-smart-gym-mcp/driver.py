"""Drive the smartgym-mcp stdio server the way an MCP client does — offline.

  uv run python .claude/skills/run-smart-gym-mcp/driver.py <command> [--real] [args]

Commands (each one starts a fresh server subprocess over stdio and stops it at the end):
  smoke                    canned flow: initialize, list tools/resources, health, list and
                           get routines, read a catalog, one dry-run edit, one applied edit
  tools                    tool names + annotations
  call TOOL [JSON]         call one tool, e.g. call smartgym_get_routine '{"routine": "ZZ-FB"}'
  read URI                 read a resource, e.g. read smartgym://catalog/equipment
  session                  read steps from stdin, one per line, against ONE server:
                             TOOL {json}   |   read URI   |   # comment
  serve                    the patched stdio server itself; the commands above spawn it,
                           and any MCP client can too (e.g. the Inspector CLI)

Default mode: the real server module, with `server.AppContext` patched so its services run
on an offline fake account — the synthetic fixture tests/fixtures/api/history_all_synthetic.json
plus a generated app bundle. Every POST is recorded, not sent: it is printed after the call
and appended to <sandbox>/posts.jsonl. The fake doesn't apply posts, so dry_run=false ends in
a "routine now differs from the plan" error after the post — that error means the write path
ran to its verifying re-read.

--real: the unpatched `smartgym-mcp` entry point with a credentials path that doesn't exist,
so nothing reaches the network or the account (what a client sees before setup).

Sandbox: $SMARTGYM_DRIVER_DIR or /tmp/smartgym-driver (bundle, backups/, posts.jsonl,
server.log). It is rebuilt on every run.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import plistlib
import shutil
import sys
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "api"
SANDBOX = Path(os.environ.get("SMARTGYM_DRIVER_DIR", "/tmp/smartgym-driver"))
USER_ID = "1"

# Extra bundle exercises, so add/apply can match names the fixture routine doesn't use.
EXTRA_EXERCISES = [("20", "Plank"), ("31", "Barbell Squat"), ("55", "Dumbbell Row")]


# ------------------------------------------------------------------- sandbox
def build_sandbox(sandbox: Path) -> None:
    if sandbox.exists():
        shutil.rmtree(sandbox)
    res = sandbox / "SmartGym.app" / "Contents" / "Resources"
    res.mkdir(parents=True)
    (sandbox / "backups").mkdir()
    account = json.loads((FIXTURES / "history_all_synthetic.json").read_text("utf-8"))
    seen: dict[str, str] = {}
    for routine in account["routines"]:
        for ex in routine["exercises"]:
            seen.setdefault(ex["id"], ex["name"])
    for ident, name in EXTRA_EXERCISES:
        seen.setdefault(ident, name)
    exercises = [
        {"id": i, "name": n, "type": "0", "category": "1", "equipments": "1"}
        for i, n in seen.items()
    ]
    (res / "Exercises.json").write_text(json.dumps({"exercises": exercises}), "utf-8")
    equipments = [
        {"identifier": "1", "category": "1", "name": {"en": "Barbell"}},
        {"identifier": "2", "category": "1", "name": {"en": "Dumbbell"}},
    ]
    (res / "Equipments.json").write_text(json.dumps({"equipments": equipments}), "utf-8")
    categories = [{"identifier": "1", "name": {"en": "Chest"}}]
    (res / "Categories.json").write_text(json.dumps({"categories": categories}), "utf-8")
    with (sandbox / "SmartGym.app" / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "8.0.3"}, f)
    (sandbox / "posts.jsonl").write_text("", "utf-8")


def server_env(sandbox: Path) -> dict[str, str]:
    return {
        **os.environ,
        "SMARTGYM_APP_BUNDLE": str(sandbox / "SmartGym.app"),
        "SMARTGYM_BACKUP_DIR": str(sandbox / "backups"),
        "SMARTGYM_CREDENTIALS": str(sandbox / "missing-credentials.json"),
    }


# ----------------------------------------------------------- server side (serve)
class OfflineClient:
    """Stands in for api.client.ApiClient: GETs from the fixture account, POSTs recorded."""

    def __init__(self, sandbox: Path) -> None:
        self.user_id = USER_ID
        self._posts = sandbox / "posts.jsonl"
        self._account = json.loads(
            (FIXTURES / "history_all_synthetic.json").read_text("utf-8")
        )
        custom = json.loads((FIXTURES / "all_custom_exercises.json").read_text("utf-8"))
        self._custom = custom["response"]

    def get(self, path: str) -> dict[str, Any]:
        if path == f"history/all/{USER_ID}/":
            return copy.deepcopy(self._account)
        if path == "exercise/allCustomExercises/":
            return copy.deepcopy(self._custom)
        if path.startswith("routine/single/"):
            ident = path.strip("/").rsplit("/", 1)[-1]
            found = [r for r in self._account["routines"] if r["identifier"] == ident]
            return {"code": "SUCCESS", "routines": copy.deepcopy(found)}
        from smartgym_mcp.api.client import ApiError

        raise ApiError(f"offline driver: no fixture for GET {path}")

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        with self._posts.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"path": path, "form": dict(form)}, ensure_ascii=False) + "\n")
        return {"code": "SUCCESS"}

    def close(self) -> None:
        pass


def serve(sandbox: Path) -> None:
    """Also runs standalone, e.g. under the Inspector CLI: builds the sandbox if missing and
    points the server's env at it, so it needs no -e flags."""
    if not (sandbox / "posts.jsonl").exists():
        build_sandbox(sandbox)
    os.environ.update(server_env(sandbox))
    from smartgym_mcp import catalog, runtime, server
    from smartgym_mcp.account import AccountCache
    from smartgym_mcp.config import Config
    from smartgym_mcp.matching import ExerciseCatalog
    from smartgym_mcp.service import RoutineService

    def factory(cfg: Config) -> runtime.Services:
        client = OfflineClient(sandbox)
        cache = AccountCache(client)
        service = RoutineService(
            client,
            cache,
            ExerciseCatalog(catalog.load_bundle_exercises(cfg)),
            backup_dir=cfg.backup_dir,
            timezone="Europe/Madrid",
        )
        equipment = catalog.load_bundle_equipment(cfg)
        return runtime.Services(client, cache, service, equipment)  # type: ignore[arg-type]

    # lifespan() looks AppContext up in server's globals on each start.
    server.AppContext = partial(runtime.AppContext, factory=factory)  # type: ignore[attr-defined,assignment]
    server.main()


# ------------------------------------------------------------------ client side
def _print_result(result: Any) -> None:
    flag = "ERROR " if result.is_error else ""
    if result.structured_content is not None:
        body = json.dumps(result.structured_content, ensure_ascii=False, indent=2)
    else:
        body = "\n".join(getattr(c, "text", str(c)) for c in result.content)
    print(f"{flag}{body}")


class Driver:
    def __init__(self, real: bool) -> None:
        self.real = real
        self._posts_seen = 0

    def params(self) -> Any:
        from mcp import StdioServerParameters

        if self.real:
            command = shutil.which("smartgym-mcp")
            if command is None:
                raise SystemExit("smartgym-mcp not on PATH: run the driver with `uv run`")
            return StdioServerParameters(command=command, env=server_env(SANDBOX))
        return StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).resolve()), "serve"],
            env=server_env(SANDBOX),
        )

    def new_posts(self) -> list[dict[str, Any]]:
        lines = (SANDBOX / "posts.jsonl").read_text("utf-8").splitlines()
        fresh, self._posts_seen = lines[self._posts_seen :], len(lines)
        return [json.loads(line) for line in fresh]

    async def step(self, client: Any, line: str) -> None:
        line = line.strip()
        if not line or line.startswith("#"):
            return
        head, _, rest = line.partition(" ")
        print(f"\n>>> {line}")
        if head == "read":
            res = await client.read_resource(rest.strip())
            text = res.contents[0].text
            print(text if len(text) < 2000 else text[:2000] + " …")
            return
        args = json.loads(rest) if rest.strip() else {}
        _print_result(await client.call_tool(head, args))
        for post in self.new_posts():
            form = {
                k: (v if len(v) < 300 else v[:300] + " …") for k, v in post["form"].items()
            }
            print(
                f"POST {post['path']} (recorded, not sent) {json.dumps(form, ensure_ascii=False)}"
            )

    async def run(self, lines: list[str], *, show_tools: bool = False) -> None:
        from mcp import Client
        from mcp.client.stdio import stdio_client

        build_sandbox(SANDBOX)
        log = (SANDBOX / "server.log").open("w")  # the server's stderr, kept off the output
        async with Client(stdio_client(self.params(), errlog=log)) as client:
            info = client.server_info
            assert info is not None
            print(
                f"connected: {info.name} {info.version} ({'real' if self.real else 'offline fake'})"
            )
            if show_tools:
                tools = (await client.list_tools()).tools
                for t in tools:
                    a = t.annotations
                    kind = (
                        "read"
                        if a and a.read_only_hint
                        else ("write idempotent" if a and a.idempotent_hint else "write")
                    )
                    print(f"  {t.name:32} {kind}")
                res = (await client.list_resources()).resources
                print(f"{len(tools)} tools; resources: {', '.join(str(r.uri) for r in res)}")
            for line in lines:
                await self.step(client, line)


SMOKE = [
    "smartgym_health",
    "smartgym_list_routines",
    'smartgym_get_routine {"routine": "zz-fb"}',
    "read smartgym://catalog/equipment",
    'smartgym_update_routine {"routine": "ZZ-FB", "name": "ZZ-FB renamed"}',
    'smartgym_update_routine {"routine": "ZZ-FB", "name": "ZZ-FB renamed", "dry_run": false}',
]


def main(argv: list[str]) -> None:
    real = "--real" in argv
    argv = [a for a in argv if a != "--real"]
    if not argv:
        raise SystemExit(__doc__)
    cmd, args = argv[0], argv[1:]
    driver = Driver(real)
    if cmd == "serve":
        serve(SANDBOX)
    elif cmd == "smoke":
        asyncio.run(driver.run(SMOKE, show_tools=True))
    elif cmd == "tools":
        asyncio.run(driver.run([], show_tools=True))
    elif cmd == "call" and args:
        asyncio.run(driver.run([" ".join([args[0], *args[1:2]])]))
    elif cmd == "read" and args:
        asyncio.run(driver.run([f"read {args[0]}"]))
    elif cmd == "session":
        asyncio.run(driver.run(sys.stdin.read().splitlines()))
    else:
        raise SystemExit(__doc__)
    if cmd != "serve":
        print(f"\nsandbox: {SANDBOX} (server.log, posts.jsonl, backups/)")


if __name__ == "__main__":
    main(sys.argv[1:])
