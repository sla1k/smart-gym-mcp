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

Sandbox: a fresh temp dir per run (bundle, backups/, posts.jsonl, server.log), printed at
the end; runs never share one. $SMARTGYM_DRIVER_DIR picks the dir instead, but only an empty
or missing dir or an earlier driver sandbox (it holds the .smartgym-driver marker): the
driver wipes and rebuilds it, so it refuses anything else.
"""

from __future__ import annotations

import asyncio
import json
import os
import plistlib
import shutil
import sys
import tempfile
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "api"
MARKER = ".smartgym-driver"

# Extra bundle exercises, so add/apply can match names the fixture routine doesn't use.
EXTRA_EXERCISES = [("20", "Plank"), ("31", "Barbell Squat"), ("55", "Dumbbell Row")]


# ------------------------------------------------------------------- sandbox
def make_sandbox() -> Path:
    """A new sandbox: $SMARTGYM_DRIVER_DIR (wiped first, if it is safe to) or a temp dir."""
    chosen = os.environ.get("SMARTGYM_DRIVER_DIR")
    if not chosen:
        sandbox = Path(tempfile.mkdtemp(prefix="smartgym-driver-"))
    else:
        sandbox = Path(chosen).expanduser().resolve()
        if sandbox.exists() and any(sandbox.iterdir()):
            if not (sandbox / MARKER).exists():
                raise SystemExit(
                    f"SMARTGYM_DRIVER_DIR={sandbox} is not empty and not a driver sandbox; "
                    "refusing to wipe it"
                )
            shutil.rmtree(sandbox)
    build_sandbox(sandbox)
    return sandbox


def build_sandbox(sandbox: Path) -> None:
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / MARKER).write_text("", "utf-8")
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
def offline_client(sandbox: Path) -> Any:
    """tests/fakes.FakeClient on the captured fixtures, with each POST also appended to
    <sandbox>/posts.jsonl so the driver can show it."""
    sys.path.insert(0, str(ROOT / "tests"))
    from fakes import CUSTOM_EXERCISES, HISTORY, FakeClient
    from smartgym_mcp.api.client import ApiError

    def fixture(name: str) -> dict[str, Any]:
        body: dict[str, Any] = json.loads((FIXTURES / name).read_text("utf-8"))
        return body

    class RecordingClient(FakeClient):
        def get(self, path: str) -> dict[str, Any]:
            if path not in self._gets:
                raise ApiError(f"offline driver: no fixture for GET {path}")
            return super().get(path)

        def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
            with (sandbox / "posts.jsonl").open("a", encoding="utf-8") as f:
                line = json.dumps({"path": path, "form": dict(form)}, ensure_ascii=False)
                f.write(line + "\n")
            return super().post(path, form)

    account = fixture("history_all_synthetic.json")
    # routine/single answers from the same account, as tests/test_server.py's _single() does:
    # the captured routine_single_synthetic.json is a different routine (other exercises).
    singles = {
        f"routine/single/{r['identifier']}/": {"code": "SUCCESS", "routines": [r]}
        for r in account["routines"]
    }
    return RecordingClient(
        {
            HISTORY: account,
            CUSTOM_EXERCISES: fixture("all_custom_exercises.json")["response"],
            **singles,
        }
    )


def serve() -> None:
    """Spawned by the client commands with the sandbox they built in SMARTGYM_DRIVER_DIR.
    Standalone (e.g. under the Inspector CLI) it makes its own and points the server's env
    at it, so it needs no -e flags."""
    spawned = os.environ.get("SMARTGYM_DRIVER_SPAWNED") == "1"
    sandbox = Path(os.environ["SMARTGYM_DRIVER_DIR"]) if spawned else make_sandbox()
    if not spawned:
        print(f"smartgym-driver sandbox: {sandbox}", file=sys.stderr)
    os.environ.update(server_env(sandbox))
    from smartgym_mcp import catalog, runtime, server
    from smartgym_mcp.account import AccountCache
    from smartgym_mcp.config import Config
    from smartgym_mcp.matching import ExerciseCatalog
    from smartgym_mcp.service import RoutineService

    def factory(cfg: Config) -> runtime.Services:
        client = offline_client(sandbox)
        cache = AccountCache(client)
        service = RoutineService(
            client,
            cache,
            ExerciseCatalog(catalog.load_bundle_exercises(cfg)),
            backup_dir=cfg.backup_dir,
            timezone="Europe/Madrid",
        )
        equipment = catalog.load_bundle_equipment(cfg)
        return runtime.Services(client, cache, service, equipment)

    # lifespan() looks AppContext up in server's globals on each start. If that ever changes,
    # the real services start instead, on the sandbox's missing credentials file: every
    # account tool then fails on credentials, and nothing reaches the account.
    if not hasattr(server, "AppContext"):
        raise SystemExit("server.AppContext is gone: update the driver's injection point")
    server.AppContext = partial(runtime.AppContext, factory=factory)
    sys.argv[1:] = []  # main() reads argv: `serve` would read as an unknown subcommand
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
        self.sandbox = make_sandbox()
        self._posts_seen = 0

    def params(self) -> Any:
        from mcp import StdioServerParameters

        if self.real:
            command = shutil.which("smartgym-mcp")
            if command is None:
                raise SystemExit("smartgym-mcp not on PATH: run the driver with `uv run`")
            return StdioServerParameters(command=command, env=server_env(self.sandbox))
        env = {
            **server_env(self.sandbox),
            "SMARTGYM_DRIVER_DIR": str(self.sandbox),
            "SMARTGYM_DRIVER_SPAWNED": "1",
        }
        return StdioServerParameters(
            command=sys.executable, args=[str(Path(__file__).resolve()), "serve"], env=env
        )

    def new_posts(self) -> list[dict[str, Any]]:
        lines = (self.sandbox / "posts.jsonl").read_text("utf-8").splitlines()
        fresh, self._posts_seen = lines[self._posts_seen :], len(lines)
        return [json.loads(line) for line in fresh]

    async def step(self, client: Any, line: str) -> None:
        """One step; a failing one (bad JSON, a resource error) is printed and the session
        goes on with the next."""
        line = line.strip()
        if not line or line.startswith("#"):
            return
        head, _, rest = line.partition(" ")
        print(f"\n>>> {line}")
        try:
            if head == "read":
                res = await client.read_resource(rest.strip())
                text = res.contents[0].text
                print(text if len(text) < 2000 else text[:2000] + " …")
                return
            args = json.loads(rest) if rest.strip() else {}
            _print_result(await client.call_tool(head, args))
        except Exception as exc:  # noqa: BLE001 - report the step, keep the session
            print(f"STEP FAILED {type(exc).__name__}: {exc}")
        for post in self.new_posts():
            form = {
                k: (v if len(v) < 300 else v[:300] + " …") for k, v in post["form"].items()
            }
            print(
                f"POST {post['path']} (recorded, not sent) {json.dumps(form, ensure_ascii=False)}"
            )

    async def run(self, lines: list[str], *, show_tools: bool = False) -> None:
        from mcp.client.stdio import stdio_client

        # the server's stderr, kept off the output
        with (self.sandbox / "server.log").open("w", encoding="utf-8") as log:
            await self._session(stdio_client(self.params(), errlog=log), lines, show_tools)

    async def _session(self, transport: Any, lines: list[str], show_tools: bool) -> None:
        from mcp import Client

        async with Client(transport) as client:
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
    if cmd == "serve":
        serve()
        return
    steps = {
        "smoke": SMOKE,
        "tools": [],
        "call": [" ".join(args)],  # TOOL JSON, even if the shell split the JSON
        "read": [f"read {' '.join(args)}"],
    }
    if cmd == "session":
        lines = sys.stdin.read().splitlines()
    elif cmd in steps and (args or cmd in ("smoke", "tools")):
        lines = steps[cmd]
    else:
        raise SystemExit(__doc__)
    driver = Driver(real)
    asyncio.run(driver.run(lines, show_tools=cmd in ("smoke", "tools")))
    print(f"\nsandbox: {driver.sandbox} (server.log, posts.jsonl, backups/)")


if __name__ == "__main__":
    main(sys.argv[1:])
