"""server.py: tool wiring with injected services — no credentials file, no network."""

from __future__ import annotations

import asyncio
import copy
import json
import plistlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fakes import FakeClient
from smartgym_mcp import server
from smartgym_mcp.api.auth import CredentialsError, FileCredentials
from smartgym_mcp.api.client import AuthError
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.config import Config, load_config
from smartgym_mcp.diff import DesiredExercise
from smartgym_mcp.matching import ExerciseCatalog
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
        catalog = ExerciseCatalog([(i, c.name) for i, c in BUNDLE.items()])
        service = RoutineService(
            client, store, catalog, BUNDLE, backup_dir=cfg.backup_dir, timezone="Europe/Madrid"
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
    writes = [t for t in tools if t.annotations and not t.annotations.readOnlyHint]
    assert len(writes) == 10
    for tool in writes:
        assert tool.inputSchema["properties"]["dry_run"]["default"] is True, tool.name


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
    with pytest.raises(CredentialsError, match="capture_credentials.py") as exc:
        server.smartgym_list_routines(_ctx(app))
    assert str(cfg.credentials_path) in str(exc.value)

    _write_credentials(
        cfg.credentials_path,
        {"authorization": SECRET, "authID": "42", "app_headers": APP},
        0o644,
    )
    with pytest.raises(CredentialsError, match="chmod 600"):
        server.smartgym_update_routine(_ctx(app), routine="ZZ", note="x")


def test_services_are_retried_after_a_failed_build(tmp_path: Path) -> None:
    attempts: list[int] = []
    good = _fake_app(tmp_path, FakeClient({HISTORY: ACCOUNT}))

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
    with pytest.raises(AuthError, match="rejected the credentials"):
        server.smartgym_list_routines(_ctx(app))
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


# ---------------------------------------------------------------------- health
def test_health_ok_counts_active_routines_and_warns_on_other_app_version(
    tmp_path: Path,
) -> None:
    app = _fake_app(tmp_path, FakeClient({HISTORY: ACCOUNT}))
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
    app = _fake_app(tmp_path, FakeClient({HISTORY: ACCOUNT}))
    result = server.smartgym_list_routines(_ctx(app))
    assert [r.name for r in result.routines] == ["ZZ-FB — Test"]


def test_get_routine_splits_sections(tmp_path: Path) -> None:
    app = _fake_app(tmp_path, FakeClient({HISTORY: ACCOUNT}))
    detail = server.smartgym_get_routine(_ctx(app), routine="zz-fb")
    assert [e.exercise_id for e in detail.warmup] == [40000010]
    assert [e.exercise_id for e in detail.main] == [40000012]
    assert [e.exercise_id for e in detail.cooldown] == [40000014]


@pytest.mark.parametrize(
    "arguments", [{"limit": 0}, {"limit": -1}, {"offset": -1}], ids=["zero", "neg", "offset"]
)
def test_workout_history_rejects_bad_paging(arguments: dict[str, int]) -> None:
    with pytest.raises(ToolError, match="limit|offset"):
        asyncio.run(server.mcp.call_tool("smartgym_get_workout_history", arguments))


# ---------------------------------------------------------------------- writes
def test_update_exercise_dry_run_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: ACCOUNT, SINGLE: _single()})
    result = server.smartgym_update_exercise(
        _ctx(_fake_app(tmp_path, client)), exercise_id=40000012, rest_seconds=120
    )
    assert result.dry_run and result.requests == ["routine/update/"]
    assert client.sent == []


def test_add_exercise_builds_on_the_fresh_routine(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: ACCOUNT, SINGLE: _single()})
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
        c, routines=[RoutineSpec(name="ZZ-New", exercises=[ExerciseSpec(exercise="Plank")])]
    ),
    "archive_routines": lambda c: server.smartgym_archive_routines(c, routines=["ZZ-FB"]),
    "unarchive_routine": lambda c: server.smartgym_unarchive_routine(c, routine="ZZ-Old"),
}


@pytest.mark.parametrize("call", DRY_RUN_CALLS.values(), ids=DRY_RUN_CALLS.keys())
def test_write_tools_dry_run_by_default_and_send_nothing(
    tmp_path: Path, call: WriteCall
) -> None:
    client = FakeClient({HISTORY: ACCOUNT, SINGLE: _single()})
    result = call(_ctx(_fake_app(tmp_path, client)))
    assert result.dry_run
    assert client.sent == []
    assert not (tmp_path / "backups").exists()


def test_archive_applies_when_dry_run_is_false(tmp_path: Path) -> None:
    archived = copy.deepcopy(ACCOUNT)
    archived["routines"][0]["dateArchived"] = "2026-10-06 10:00:00"
    client = FakeClient({HISTORY: [ACCOUNT, archived]})
    result = server.smartgym_archive_routines(
        _ctx(_fake_app(tmp_path, client)), routines=["ZZ-FB"], dry_run=False
    )
    assert not result.dry_run
    assert [r.identifier for r in result.routines] == [3000001]
    assert [path for path, _ in client.sent] == ["routine/archive/"]
