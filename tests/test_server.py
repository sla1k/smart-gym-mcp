"""server.py: tool wiring with injected services — no credentials file, no network."""

from __future__ import annotations

import asyncio
import copy
import json
import plistlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, override

import anyio
import anyio.to_thread
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import (
    ResourceError,
    ToolError,
    UnexpectedResourceError,
    UnexpectedToolError,
)

from fakes import FakeClient, account_client
from smartgym_mcp import server
from smartgym_mcp.api.auth import CredentialsError, FileCredentials
from smartgym_mcp.api.client import ApiClient, AuthError, WriteOutcomeUnknown
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.config import Config, load_config
from smartgym_mcp.diff import DesiredExercise
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.service import RoutineService

FIX = Path(__file__).parent / "fixtures" / "api"
ACCOUNT = json.loads((FIX / "history_all_synthetic.json").read_text(encoding="utf-8"))
HISTORY = "history/all/1/"
SINGLE = "routine/single/3000001/"
EXPECTED_TOOLS = {
    "smartgym_health", "smartgym_list_routines", "smartgym_get_routine",
    "smartgym_get_workout_history", "smartgym_get_equipment", "smartgym_create_program",
    "smartgym_update_routine", "smartgym_add_exercise", "smartgym_move_exercise",
    "smartgym_remove_exercise", "smartgym_reorder_routine", "smartgym_update_exercise",
    "smartgym_apply_routine", "smartgym_archive_routines", "smartgym_unarchive_routine",
}  # fmt: skip
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}
SECRET = "Bearer s3cr3t-token"


def _cat(ident: int, name: str) -> CatalogExercise:
    return CatalogExercise(
        id=ident,
        name=name,
        type=0,
        category=1,
        sub_categories="",
        two_sides=0,
        stretch=0,
        equipment_ids=(),
        images=(f"{ident}-1", "", "", "", "", ""),
    )


BUNDLE = {207: _cat(207, "Cable Chest Press"), 20: _cat(20, "Plank")}


def _ctx(app: server.AppContext) -> Any:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def _cfg(tmp_path: Path, **overrides: Any) -> Config:
    """Config that never points at the real app bundle, credentials or backups."""
    base = replace(
        load_config(),
        app_bundle=tmp_path / "SmartGym.app",
        backup_dir=tmp_path / "backups",
        credentials_path=tmp_path / "credentials.json",
    )
    return replace(base, **overrides)


def _fake_app(tmp_path: Path, client: FakeClient) -> server.AppContext:
    def factory(cfg: Config) -> server.Services:
        store = AccountStore(client)
        service = RoutineService(
            client, store, BUNDLE, backup_dir=cfg.backup_dir, timezone="Europe/Madrid"
        )
        return server.Services(client=client, store=store, service=service, equipment=[])  # type: ignore[arg-type]

    return server.AppContext(cfg=_cfg(tmp_path), factory=factory)


def _write_credentials(path: Path, data: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def _single() -> dict[str, Any]:
    return {"code": "SUCCESS", "routines": [copy.deepcopy(ACCOUNT["routines"][0])]}


# ---------------------------------------------------------------- registration
def test_all_tools_registered() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_write_tool_defaults_to_dry_run() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    writes = [t for t in tools if t.annotations and not t.annotations.read_only_hint]
    assert len(writes) == 10
    for tool in writes:
        assert tool.input_schema["properties"]["dry_run"]["default"] is True, tool.name


# ----------------------------------------------------------------- credentials
@pytest.mark.parametrize(
    ("content", "mode", "fix"),
    [
        (None, 0o600, "capture_credentials.py"),
        ({"authorization": SECRET, "authID": "42", "app_headers": APP}, 0o644, "chmod 600"),
        ({"authID": "42", "app_headers": APP}, 0o600, "capture_credentials.py"),
    ],
    ids=["missing", "world-readable", "incomplete"],
)
def test_health_reports_credentials_problem(
    tmp_path: Path, content: object, mode: int, fix: str
) -> None:
    cfg = _cfg(tmp_path)
    if content is not None:
        _write_credentials(cfg.credentials_path, content, mode)
    status = server.smartgym_health(_ctx(server.AppContext(cfg=cfg)))
    assert not status.ok
    assert status.problem is not None
    assert str(cfg.credentials_path) in status.problem
    assert fix in status.problem
    assert SECRET not in status.problem


def test_tools_answer_with_credentials_path_and_fix(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    app = server.AppContext(cfg=cfg)
    with pytest.raises(ToolError, match=r"capture_credentials\.py") as exc:
        server.smartgym_list_routines(_ctx(app))
    assert isinstance(exc.value.__cause__, CredentialsError)
    assert str(cfg.credentials_path) in str(exc.value)

    _write_credentials(
        cfg.credentials_path,
        {"authorization": SECRET, "authID": "42", "app_headers": APP},
        0o644,
    )
    with pytest.raises(ToolError, match="chmod 600"):
        server.smartgym_update_routine(_ctx(app), routine="ZZ", note="x")


def test_services_are_retried_after_a_failed_build(tmp_path: Path) -> None:
    attempts: list[int] = []
    good = _fake_app(tmp_path, account_client({HISTORY: ACCOUNT}))

    def flaky(cfg: Config) -> server.Services:
        attempts.append(1)
        if len(attempts) == 1:
            raise CredentialsError("not yet")
        return good.factory(cfg)

    app = server.AppContext(cfg=_cfg(tmp_path), factory=flaky)
    assert not server.smartgym_health(_ctx(app)).ok
    assert server.smartgym_health(_ctx(app)).ok
    server.smartgym_list_routines(_ctx(app))
    assert len(attempts) == 2


class _SessionClient(FakeClient):
    """Rejects every read when built from the expired session, like the server does."""

    def __init__(self, authorization: str) -> None:
        super().__init__({HISTORY: ACCOUNT})
        self.expired = authorization == "Bearer expired"

    def get(self, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.expired:
            raise AuthError("SmartGym rejected the credentials (HTTP 401).", code="401")
        return super().get(path, *args, **kwargs)


def test_recaptured_credentials_take_effect_after_an_auth_error(tmp_path: Path) -> None:
    built: list[_SessionClient] = []

    def factory(cfg: Config) -> server.Services:
        headers = FileCredentials(cfg.credentials_path).auth_headers()
        client = _SessionClient(headers["Authorization"])
        built.append(client)
        return _fake_app(tmp_path, client).factory(cfg)

    app = server.AppContext(cfg=_cfg(tmp_path), factory=factory)
    creds = {"authID": "1", "app_headers": APP}
    _write_credentials(app.cfg.credentials_path, {**creds, "authorization": "Bearer expired"})
    with pytest.raises(ToolError, match="rejected the credentials") as exc:
        server.smartgym_list_routines(_ctx(app))
    assert isinstance(exc.value.__cause__, AuthError)
    assert built[0].closed

    _write_credentials(app.cfg.credentials_path, {**creds, "authorization": "Bearer fresh"})
    result = server.smartgym_list_routines(_ctx(app))
    assert [r.name for r in result.routines] == ["ZZ-FB — Test"]
    assert len(built) == 2 and not built[1].closed
    assert server.smartgym_health(_ctx(app)).ok
    assert len(built) == 2


def test_health_drops_services_on_an_auth_error(tmp_path: Path) -> None:
    built: list[_SessionClient] = []

    def factory(cfg: Config) -> server.Services:
        client = _SessionClient("Bearer expired" if not built else "Bearer fresh")
        built.append(client)
        return _fake_app(tmp_path, client).factory(cfg)

    app = server.AppContext(cfg=_cfg(tmp_path), factory=factory)
    status = server.smartgym_health(_ctx(app))
    assert not status.ok and status.problem is not None and "rejected" in status.problem
    assert server.smartgym_health(_ctx(app)).ok
    assert len(built) == 2 and built[0].closed


# ---------------------------------------------------------------- tool errors
class _UnknownOutcomeClient(FakeClient):
    @override
    def post(self, path: str, form: Any) -> dict[str, Any]:
        raise WriteOutcomeUnknown(
            f"SmartGym did not answer {path} — the change may or may not have landed — "
            "re-fetch before retrying."
        )


def test_anticipated_errors_reach_the_model_with_their_message(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    app = _fake_app(tmp_path, client)
    with pytest.raises(ToolError, match="No routine matching 'nope'") as exc:
        asyncio.run(
            server.mcp.call_tool(
                "smartgym_get_routine", {"routine": "nope"}, context=_ctx(app)
            )
        )
    assert not isinstance(exc.value, UnexpectedToolError)

    app = _fake_app(tmp_path, _UnknownOutcomeClient({HISTORY: ACCOUNT, SINGLE: _single()}))
    arguments = {"exercise_id": 40000012, "rest_seconds": 120, "dry_run": False}
    with pytest.raises(ToolError) as exc:
        asyncio.run(
            server.mcp.call_tool("smartgym_update_exercise", arguments, context=_ctx(app))
        )
    assert "re-fetch before retrying" in str(exc.value)
    assert "Snapshot of the routine before this edit" in str(exc.value)


def test_missing_credentials_reach_an_mcp_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("SMARTGYM_APP_BUNDLE", str(cfg.app_bundle))
    monkeypatch.setenv("SMARTGYM_BACKUP_DIR", str(cfg.backup_dir))
    monkeypatch.setenv("SMARTGYM_CREDENTIALS", str(cfg.credentials_path))

    async def call() -> Any:
        async with Client(server.mcp) as client:
            return await client.call_tool("smartgym_list_routines", {})

    result = asyncio.run(call())
    assert result.is_error
    text = result.content[0].text
    assert "capture_credentials.py" in text and str(cfg.credentials_path) in text


def test_build_services_wires_bundle_and_credentials_offline(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    resources = cfg.app_bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    exercise = {"id": "207", "name": "Cable Chest Press", "type": "0", "category": "1"}
    (resources / "Exercises.json").write_text(json.dumps({"exercises": [exercise]}), "utf-8")
    equipment = {"identifier": "1", "category": "1", "name": {"en": "Barbell"}}
    (resources / "Equipments.json").write_text(
        json.dumps({"equipments": [equipment]}), "utf-8"
    )
    _write_credentials(
        cfg.credentials_path, {"authorization": SECRET, "authID": "42", "app_headers": APP}
    )
    services = server.build_services(cfg)
    try:
        assert services.client.user_id == "42"
        assert [e.name for e in services.equipment] == ["Barbell"]
    finally:
        services.client.close()


def test_build_services_closes_the_client_when_a_later_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Any] = []

    class _RecordingClient(ApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.closed = False
            built.append(self)

        def close(self) -> None:
            self.closed = True
            super().close()

    monkeypatch.setattr(server, "ApiClient", _RecordingClient)
    cfg = _cfg(tmp_path)  # no app bundle → loading Exercises.json fails
    _write_credentials(
        cfg.credentials_path, {"authorization": SECRET, "authID": "42", "app_headers": APP}
    )
    with pytest.raises(FileNotFoundError):
        server.build_services(cfg)
    assert len(built) == 1 and built[0].closed


# ---------------------------------------------------------------------- health
def test_health_ok_counts_active_routines_and_warns_on_other_app_version(
    tmp_path: Path,
) -> None:
    app = _fake_app(tmp_path, account_client({HISTORY: ACCOUNT}))
    contents = app.cfg.app_bundle / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "9.0.0"}, f)
    status = server.smartgym_health(_ctx(app))
    assert status.ok and status.problem is None
    assert status.routines == 1
    assert status.app_version == "9.0.0"
    assert status.warning is not None and "9.0.0" in status.warning


# ----------------------------------------------------------------------- reads
def test_list_routines_through_injected_services(tmp_path: Path) -> None:
    app = _fake_app(tmp_path, account_client({HISTORY: ACCOUNT}))
    result = server.smartgym_list_routines(_ctx(app))
    assert [r.name for r in result.routines] == ["ZZ-FB — Test"]


def test_get_routine_splits_sections(tmp_path: Path) -> None:
    app = _fake_app(tmp_path, account_client({HISTORY: ACCOUNT}))
    detail = server.smartgym_get_routine(_ctx(app), routine="zz-fb")
    assert [e.exercise_id for e in detail.warmup] == [40000010]
    assert [e.exercise_id for e in detail.main] == [40000012]
    assert [e.exercise_id for e in detail.cooldown] == [40000014]


@pytest.mark.parametrize(
    "arguments", [{"limit": 0}, {"limit": -1}, {"offset": -1}], ids=["zero", "neg", "offset"]
)
def test_workout_history_rejects_bad_paging(arguments: dict[str, int]) -> None:
    with pytest.raises(ToolError, match=r"limit|offset"):
        asyncio.run(server.mcp.call_tool("smartgym_get_workout_history", arguments))


# ---------------------------------------------------------------------- writes
@pytest.mark.parametrize(
    ("tool", "arguments", "pattern"),
    [
        (
            "smartgym_add_exercise",
            {"routine": "ZZ", "exercise": "Plank", "position": -1},
            "position",
        ),
        ("smartgym_move_exercise", {"exercise_id": 1, "section": "main", "position": -1}, "position"),
        ("smartgym_create_program", {"routines": []}, "routines"),
        ("smartgym_update_routine", {"routine": "ZZ", "days": "Monday"}, "days"),
        ("smartgym_apply_routine", {"routine": "ZZ", "days": "2,3,"}, "days"),
        (
            "smartgym_create_program",
            {"routines": [{"name": "ZZ", "exercises": [{"exercise": "Plank"}]}]},
            r"goal\s+Field required",
        ),
        ("smartgym_update_routine", {"routine": "ZZ", "goal": ""}, "goal must not be blank"),
        ("smartgym_apply_routine", {"routine": "ZZ", "goal": " "}, "goal must not be blank"),
    ],
    ids=["add-negative-position", "move-negative-position", "empty-program", "update-days",
         "apply-days", "create-no-goal", "update-blank-goal", "apply-blank-goal"],
)  # fmt: skip
def test_write_tools_reject_bad_arguments(
    tool: str, arguments: dict[str, Any], pattern: str
) -> None:
    with pytest.raises(ToolError, match=pattern):
        asyncio.run(server.mcp.call_tool(tool, arguments))


def test_days_format_is_documented_in_the_tool_schemas() -> None:
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in (
        "smartgym_update_routine",
        "smartgym_apply_routine",
        "smartgym_create_program",
    ):
        tool = tools[name]
        assert "1 = Sunday, 2 = Monday" in (tool.description or ""), name
    for name in ("smartgym_update_routine", "smartgym_apply_routine"):
        days = tools[name].input_schema["properties"]["days"]
        assert "1 = Sunday" in days["description"], name


def test_update_exercise_dry_run_sends_nothing(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    result = server.smartgym_update_exercise(
        _ctx(_fake_app(tmp_path, client)), exercise_id=40000012, rest_seconds=120
    )
    assert result.dry_run and result.requests == ["routine/update/"]
    assert client.sent == []


def test_add_exercise_builds_on_the_fresh_routine(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    result = server.smartgym_add_exercise(
        _ctx(_fake_app(tmp_path, client)),
        routine="ZZ-FB",
        exercise="plank",
        section="warmup",
        position=0,
    )
    assert result.dry_run
    assert [(a.section, a.position) for a in result.changes.added] == [("warmup", 0)]
    assert [e.section for e in result.changes.expected.exercises] == [
        "warmup",
        "warmup",
        "main",
        "cooldown",
    ]
    assert SINGLE in client.get_calls
    assert client.sent == []


WriteCall = Callable[[Any], Any]
DRY_RUN_CALLS: dict[str, WriteCall] = {
    "update_routine": lambda c: server.smartgym_update_routine(c, routine="ZZ-FB", note="n"),
    "move_exercise": lambda c: server.smartgym_move_exercise(
        c, exercise_id=40000012, section="cooldown"
    ),
    "remove_exercise": lambda c: server.smartgym_remove_exercise(c, exercise_id=40000012),
    "reorder_routine": lambda c: server.smartgym_reorder_routine(
        c, routine="3000001", main=[40000012]
    ),
    "update_exercise_sets": lambda c: server.smartgym_update_exercise(
        c, exercise_id=40000012, sets=[SetSpec(reps=8, weight_kg=20)]
    ),
    "apply_routine": lambda c: server.smartgym_apply_routine(
        c,
        routine="ZZ-FB",
        main=[DesiredExercise(exercise_id=40000012), DesiredExercise(exercise="Plank")],
    ),
    "create_program": lambda c: server.smartgym_create_program(
        c,
        routines=[
            RoutineSpec(name="ZZ-New", goal="G", exercises=[ExerciseSpec(exercise="Plank")])
        ],
    ),
    "archive_routines": lambda c: server.smartgym_archive_routines(c, routines=["ZZ-FB"]),
    "unarchive_routine": lambda c: server.smartgym_unarchive_routine(c, routine="ZZ-Old"),
}


@pytest.mark.parametrize("call", DRY_RUN_CALLS.values(), ids=DRY_RUN_CALLS.keys())
def test_write_tools_dry_run_by_default_and_send_nothing(
    tmp_path: Path, call: WriteCall
) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    result = call(_ctx(_fake_app(tmp_path, client)))
    assert result.dry_run
    assert client.sent == []
    assert not (tmp_path / "backups").exists()


def test_archive_applies_when_dry_run_is_false(tmp_path: Path) -> None:
    archived = copy.deepcopy(ACCOUNT)
    archived["routines"][0]["dateArchived"] = "2026-10-06 10:00:00"
    client = account_client({HISTORY: [ACCOUNT, archived]})
    result = server.smartgym_archive_routines(
        _ctx(_fake_app(tmp_path, client)), routines=["ZZ-FB"], dry_run=False
    )
    assert not result.dry_run
    assert [r.identifier for r in result.routines] == [3000001]
    assert [path for path, _ in client.sent] == ["routine/archive/"]


# ----------------------------------------------------------- one call at a time
READ_CALLS: dict[str, WriteCall] = {
    "list_routines": server.smartgym_list_routines,
    "get_routine": lambda c: server.smartgym_get_routine(c, routine="ZZ-FB"),
    "workout_history": server.smartgym_get_workout_history,
    "equipment": server.smartgym_get_equipment,
}


ALL_CALLS = {**READ_CALLS, **DRY_RUN_CALLS}


@pytest.mark.parametrize("call", ALL_CALLS.values(), ids=ALL_CALLS.keys())
def test_every_tool_waits_for_the_call_in_flight(tmp_path: Path, call: WriteCall) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    app = replace(_fake_app(tmp_path, client), call_wait_s=0)
    with app.use(), pytest.raises(ToolError, match="nothing was sent") as exc:
        call(_ctx(app))
    assert isinstance(exc.value.__cause__, server.ServerBusy)
    assert client.get_calls == [] and client.sent == []
    assert call(_ctx(app))  # free again once the other call is done


def test_health_reports_a_busy_server(tmp_path: Path) -> None:
    app = replace(_fake_app(tmp_path, account_client({HISTORY: ACCOUNT})), call_wait_s=0)
    with app.use():
        status = server.smartgym_health(_ctx(app))
    assert (
        not status.ok and status.problem is not None and "nothing was sent" in status.problem
    )


class _GatedClient(FakeClient):
    """Holds every post until `release` is set."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.posting = threading.Event()
        self.release = threading.Event()

    def post(self, path: str, form: Any) -> dict[str, Any]:
        self.posting.set()
        assert self.release.wait(5), "the test never released the post"
        return super().post(path, form)


def test_a_read_cannot_overlap_a_write_in_flight(tmp_path: Path) -> None:
    archived = copy.deepcopy(ACCOUNT)
    archived["routines"][0]["dateArchived"] = "2026-10-06 10:00:00"
    client = _GatedClient({HISTORY: [ACCOUNT, archived, archived]})
    app = replace(_fake_app(tmp_path, client), call_wait_s=0)
    with ThreadPoolExecutor(max_workers=1) as pool:
        write = pool.submit(
            server.smartgym_archive_routines, _ctx(app), routines=["ZZ-FB"], dry_run=False
        )
        assert client.posting.wait(5)
        reads_before = len(client.get_calls)
        with pytest.raises(ToolError, match="nothing was sent"):
            server.smartgym_list_routines(_ctx(app))
        assert len(client.get_calls) == reads_before
        client.release.set()
        assert not write.result(timeout=5).dry_run
    assert [path for path, _ in client.sent] == ["routine/archive/"]
    assert server.smartgym_list_routines(_ctx(app)).routines == []  # free again; FB archived


def test_close_waits_for_the_call_in_flight(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT})
    app = replace(_fake_app(tmp_path, client), call_wait_s=0)
    with app.use():
        app.close()
        assert not client.closed
    app.close()
    assert client.closed


def test_a_cancelled_call_stops_waiting_and_sends_nothing(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT, SINGLE: _single()})
    app = replace(_fake_app(tmp_path, client), call_wait_s=3)  # far past the cancellation

    def archive() -> Any:
        return server.smartgym_archive_routines(_ctx(app), routines=["ZZ-FB"], dry_run=False)

    async def cancel_while_waiting() -> None:
        with app.use(), anyio.move_on_after(0.5):  # another call in flight
            await anyio.to_thread.run_sync(archive)  # as MCP SDK 2 runs sync tools

    started = time.monotonic()
    asyncio.run(cancel_while_waiting())
    assert time.monotonic() - started < 2
    assert client.get_calls == [] and client.sent == []


def test_no_call_starts_after_close(tmp_path: Path) -> None:
    client = account_client({HISTORY: ACCOUNT})
    built: list[Config] = []
    app = _fake_app(tmp_path, client)
    factory = app.factory

    def recording(cfg: Config) -> server.Services:
        built.append(cfg)
        return factory(cfg)

    app.factory = recording
    app.close()
    with pytest.raises(ToolError, match="shutting down"):
        server.smartgym_list_routines(_ctx(app))
    status = server.smartgym_health(_ctx(app))
    assert not status.ok and status.problem is not None and "shutting down" in status.problem
    assert built == [] and client.get_calls == []


def test_input_errors_need_no_services_and_never_wait(tmp_path: Path) -> None:
    app = server.AppContext(cfg=_cfg(tmp_path))  # no credentials file, full wait
    calls: dict[str, WriteCall] = {
        "update_routine": lambda c: server.smartgym_update_routine(c, routine="ZZ-FB"),
        "apply_routine": lambda c: server.smartgym_apply_routine(
            c,
            routine="ZZ-FB",
            main=[{"exercise_id": 1, "exercise": "Plank"}],  # type: ignore[list-item]
        ),
    }
    for call in calls.values():
        with pytest.raises(ToolError, match=r"Pass at least one of|exactly one of"):
            call(_ctx(app))  # the input problem, not the missing credentials
    assert app._calls.acquire(timeout=1)  # another call in flight
    try:
        started = time.monotonic()
        for call in calls.values():
            with pytest.raises(ToolError, match=r"Pass at least one of|exactly one of"):
                call(_ctx(app))
        assert time.monotonic() - started < 5
    finally:
        app._calls.release()


def test_missing_catalog_resource_keeps_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTGYM_APP_BUNDLE", str(tmp_path / "nowhere.app"))
    with pytest.raises(ResourceError, match="SMARTGYM_APP_BUNDLE") as exc:
        asyncio.run(server.mcp.read_resource("smartgym://catalog/exercises"))
    assert not isinstance(exc.value, UnexpectedResourceError)


def test_undecodable_catalog_resource_keeps_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "SmartGym.app"
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "Exercises.json").write_bytes(b"\xff\xfe not utf-8")
    monkeypatch.setenv("SMARTGYM_APP_BUNDLE", str(bundle))
    with pytest.raises(ResourceError, match=r"Exercises\.json") as exc:
        asyncio.run(server.mcp.read_resource("smartgym://catalog/exercises"))
    assert not isinstance(exc.value, UnexpectedResourceError)
    assert "not valid UTF-8" in str(exc.value)
