"""service.py on a fake client: dry run, snapshot, ordered sends, verification, failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClient
from smartgym_mcp.api.client import ApiError, WriteOutcomeUnknown
from smartgym_mcp.api.store import AccountStore, RoutineNotFound
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, DiffError
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.service import RoutineService, WriteVerifyError

HISTORY = "history/all/1/"
SINGLE = "routine/single/3000001/"
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
FIRST_HASH = 26100600000001


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


BUNDLE = {
    300: _cat(300, "Shoulder Circling"),
    207: _cat(207, "Cable Chest Press"),
    20: _cat(20, "Plank"),
}
CATALOG = ExerciseCatalog([(i, c.name) for i, c in BUNDLE.items()])


def _raw_ex(
    ident: int,
    cat: int,
    group: int,
    idx: int,
    sets: list[tuple[int, float, float]],
    *,
    pause: str = "60",
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(cat),
        "name": BUNDLE[cat].name,
        "identifier": str(ident),
        "uniqueHashID": str(ident),
        "idx": str(idx),
        "pause": pause,
        "note": note,
        "dateRemoved": None,
        "listGroup": str(group),
        "sets": [
            {
                "identifier": str(sid),
                "uniqueHashID": str(sid),
                "firstValue": "1",
                "secondValue": str(r),
                "thirdValue": str(w),
                "type": "0",
                "index": str(i),
                "dateAdded": "2026-10-05 10:00:00",
                "dateRemoved": None,
                "dateLogged": None,
            }
            for i, (sid, r, w) in enumerate(sets)
        ],
    }


def _raw_routine(
    *exs: dict[str, Any],
    ident: str = "3000001",
    name: str = "ZZ-Svc",
    hashid: str = "26100500000001",
    archived: str | None = None,
) -> dict[str, Any]:
    return {
        "identifier": ident,
        "uniqueHashID": hashid,
        "name": name,
        "days": "2,4,6",
        "goal": "",
        "note": None,
        "number": "5",
        "dateArchived": archived,
        "dateRemoved": None,
        "exercises": list(exs),
    }


def _account(*routines: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "SUCCESS",
        "hasMore": False,
        "routines": list(routines),
        "histories": [],
        "equipmentLists": [],
    }


WARM = _raw_ex(10, 300, 1, 0, [(100, 10, 0)])
CHEST = _raw_ex(11, 207, 0, 1, [(110, 10, 40)])
BEFORE = _raw_routine(WARM, CHEST)


def _single(*bodies: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"code": "SUCCESS", "routines": [b]} for b in bodies]


def _counter():  # type: ignore[no-untyped-def]
    n = iter(range(FIRST_HASH, FIRST_HASH + 1000))
    return lambda _now: next(n)


def _service(
    client: FakeClient, tmp_path: Path, *, move_in_place: bool = True
) -> RoutineService:
    return RoutineService(
        client,
        AccountStore(client),
        CATALOG,
        BUNDLE,
        backup_dir=tmp_path,
        timezone="Europe/Madrid",
        now=lambda: NOW,
        mint=_counter(),
        move_in_place=move_in_place,
    )


def _rest(seconds: int):  # type: ignore[no-untyped-def]
    return lambda _r: DesiredRoutine(
        main=[DesiredExercise(exercise_id=11, rest_seconds=seconds)]
    )


def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=True)
    assert result.dry_run and result.requests == ["routine/update/"]
    assert client.sent == [] and result.snapshot is None


def test_apply_snapshots_sends_and_verifies(tmp_path: Path) -> None:
    after = _raw_routine(WARM, {**CHEST, "pause": "90"})
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)
    assert [path for path, _ in client.sent] == ["routine/update/"]
    assert json.loads(client.sent[0][1]["updateExercises"]) == [
        {"pause": "90", "exerciseID": 11}
    ]
    assert result.snapshot is not None
    assert json.loads(Path(result.snapshot).read_text(encoding="utf-8")) == BEFORE


def test_server_ignoring_the_edit_is_a_verify_error(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, BEFORE)})
    with pytest.raises(WriteVerifyError, match="rest_seconds: expected 90, got 60"):
        _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)


def test_failure_after_first_request_keeps_class_and_reports_progress(tmp_path: Path) -> None:
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)},
        posts=[{"code": "SUCCESS"}, WriteOutcomeUnknown("timeout")],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=11, rest_seconds=90, note="Slow")]
    )
    with pytest.raises(WriteOutcomeUnknown) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    message = str(exc.value)
    assert "Already sent: routine/update/" in message and "Snapshot" in message
    assert "Outcome unknown: routine/updateExercise/" in message
    assert [path for path, _ in client.sent] == ["routine/update/", "routine/updateExercise/"]


@pytest.mark.parametrize(
    ("pause_after", "verdict"),
    [("90", "the change landed."), ("60", "the change did not land.")],
)
def test_unknown_outcome_rereads_and_reports_whether_it_landed(
    tmp_path: Path, pause_after: str, verdict: str
) -> None:
    after = _raw_routine(WARM, {**CHEST, "pause": pause_after})
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)},
        posts=[WriteOutcomeUnknown("timeout")],
    )
    with pytest.raises(WriteOutcomeUnknown) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)
    message = str(exc.value)
    assert "Already sent: nothing" in message
    assert "Outcome unknown: routine/update/" in message and verdict in message
    assert "Not sent" not in message


def test_failed_first_request_lists_the_requests_never_sent(tmp_path: Path) -> None:
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)},
        posts=[ApiError("SmartGym answered 'FAIL'.", code="FAIL")],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=11, rest_seconds=90, note="Slow")]
    )
    with pytest.raises(ApiError) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    message = str(exc.value)
    assert "Already sent: nothing. Failed: routine/update/." in message
    assert "Not sent: routine/updateExercise/." in message
    assert [path for path, _ in client.sent] == ["routine/update/"]


def test_failed_second_request_keeps_class_code_and_reports_progress(tmp_path: Path) -> None:
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)},
        posts=[{"code": "SUCCESS"}, ApiError("SmartGym answered 'FAIL'.", code="FAIL")],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=11, rest_seconds=90, note="Slow")]
    )
    with pytest.raises(ApiError) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    assert type(exc.value) is ApiError and exc.value.code == "FAIL"
    message = str(exc.value)
    assert "Already sent: routine/update/" in message
    assert "Failed: routine/updateExercise/" in message
    assert "Not sent" not in message
    snapshot = next(tmp_path.rglob("routine-3000001.json"))
    assert str(snapshot) in message


def test_unknown_outcome_reports_partial_landing(tmp_path: Path) -> None:
    after = _raw_routine(WARM, {**CHEST, "pause": "90"})
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)},
        posts=[{"code": "SUCCESS"}, WriteOutcomeUnknown("timeout")],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=11, rest_seconds=90, note="Slow")]
    )
    with pytest.raises(
        WriteOutcomeUnknown, match="landed only partially.*note: expected 'Slow'"
    ):
        _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)


def test_failing_verification_read_reports_sent_requests_and_snapshot(tmp_path: Path) -> None:
    gone = {"code": "SUCCESS", "routines": []}
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: [*_single(BEFORE), gone]})
    with pytest.raises(RoutineNotFound) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)
    message = str(exc.value)
    assert "All requests were sent (routine/update/)" in message
    assert str(next(tmp_path.rglob("routine-3000001.json"))) in message


def test_mid_routine_add_sends_order_in_the_same_update(tmp_path: Path) -> None:
    plank = _raw_ex(555, 20, 1, 1, [(600, 30, 0)], pause="0")
    after = _raw_routine(WARM, plank, {**CHEST, "idx": "2"})
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)},
        posts=[
            {"code": "SUCCESS", "exercises": [{"original_id": FIRST_HASH, "server_id": "555"}]}
        ],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        warmup=[
            DesiredExercise(exercise_id=10),
            DesiredExercise(exercise="Plank", sets=[SetSpec(reps=30)]),
        ]
    )
    result = _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    assert result.requests == ["routine/update/"]
    ((path, form),) = client.sent
    assert path == "routine/update/" and form["exercisesOrder"] == "10:0,11:2"
    (added,) = json.loads(form["exercises"])
    assert (added["idx"], added["listGroup"]) == (1, 1)


def test_pure_reorder_sends_order_in_the_structure_update(tmp_path: Path) -> None:
    row = _raw_ex(12, 20, 0, 2, [(120, 30, 0)])
    before = _raw_routine(WARM, CHEST, row)
    after = _raw_routine(WARM, {**CHEST, "idx": "2"}, {**row, "idx": "1"})
    client = FakeClient({HISTORY: _account(before), SINGLE: _single(before, after)})
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=12), DesiredExercise(exercise_id=11)]
    )
    result = _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    assert result.requests == ["routine/update/"]
    ((_, form),) = client.sent
    assert form["exercisesOrder"] == "10:0,12:1,11:2"


def test_section_move_is_sent_in_place(tmp_path: Path) -> None:
    after = _raw_routine(WARM, {**CHEST, "listGroup": "1"})
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)})
    build = lambda _r: DesiredRoutine(  # noqa: E731
        warmup=[DesiredExercise(exercise_id=10), DesiredExercise(exercise_id=11)], main=[]
    )
    result = _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    assert result.changes.removed == [] and result.changes.added == []
    ((_, form),) = client.sent
    assert json.loads(form["updateExercises"]) == [{"listGroup": "1", "exerciseID": 11}]
    assert "exercisesOrder" not in form


def test_nothing_to_change_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(60), dry_run=False)
    assert client.sent == [] and "Nothing to change" in result.notice


def test_moves_fall_back_to_readd_when_not_in_place(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    build = lambda _r: DesiredRoutine(  # noqa: E731
        warmup=[DesiredExercise(exercise_id=10), DesiredExercise(exercise_id=11)]
    )
    result = _service(client, tmp_path, move_in_place=False).edit(
        "ZZ-Svc", build, dry_run=True
    )
    assert [r.identifier for r in result.changes.removed] == [11]
    assert [(a.catalog_id, a.section) for a in result.changes.added] == [(207, "warmup")]


def test_create_rejects_existing_name(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE)})
    spec = RoutineSpec(name="zz-svc", exercises=[ExerciseSpec(exercise="Plank")])
    with pytest.raises(DiffError, match="'zz-svc' already exists — rename it."):
        _service(client, tmp_path).create([spec], dry_run=True)


ARCHIVED = _raw_routine(
    ident="3000002", name="ZZ-Old", hashid="2", archived="2026-09-30 10:00:00"
)


def test_create_may_reuse_an_archived_routines_name(tmp_path: Path) -> None:
    created = _raw_routine(
        _raw_ex(902, 20, 0, 0, [(903, 30, 0)], pause="0"),
        ident="3000009",
        name="ZZ-Old",
        hashid=str(FIRST_HASH),
    )
    created["days"] = ""
    client = FakeClient({HISTORY: [_account(BEFORE, ARCHIVED), _account(BEFORE, ARCHIVED)]})
    spec = RoutineSpec(
        name="zz-old", exercises=[ExerciseSpec(exercise="Plank", sets=[SetSpec(reps=30)])]
    )
    plan = _service(client, tmp_path).create([spec], dry_run=True)
    assert plan.dry_run and [p.name for p in plan.plan] == ["zz-old"]

    spec = RoutineSpec(
        name="ZZ-Old", exercises=[ExerciseSpec(exercise="Plank", sets=[SetSpec(reps=30)])]
    )
    client = FakeClient(
        {HISTORY: [_account(BEFORE, ARCHIVED), _account(BEFORE, ARCHIVED, created)]}
    )
    result = _service(client, tmp_path).create([spec], dry_run=False)
    assert [path for path, _ in client.sent] == ["routine/add/"]
    assert [(c.identifier, c.name) for c in result.created] == [(3000009, "ZZ-Old")]


def test_rename_onto_an_active_routines_name_is_rejected(tmp_path: Path) -> None:
    two = _raw_routine(ident="3000002", name="ZZ-Two", hashid="2")
    client = FakeClient({HISTORY: _account(BEFORE, two), SINGLE: _single(BEFORE)})
    with pytest.raises(DiffError, match="'zz-two' already exists — rename it."):
        _service(client, tmp_path).edit(
            "ZZ-Svc", lambda _r: DesiredRoutine(name="zz-two"), dry_run=True
        )
    assert client.sent == []


@pytest.mark.parametrize("new_name", ["ZZ-Old", "zz-svc"], ids=["archived", "own-name-case"])
def test_rename_onto_an_archived_or_own_name_is_allowed(tmp_path: Path, new_name: str) -> None:
    client = FakeClient({HISTORY: _account(BEFORE, ARCHIVED), SINGLE: _single(BEFORE)})
    result = _service(client, tmp_path).edit(
        "ZZ-Svc", lambda _r: DesiredRoutine(name=new_name), dry_run=True
    )
    assert [c.new for c in result.changes.routine_changes] == [new_name]


def test_create_sends_sections_and_verifies(tmp_path: Path) -> None:
    created = _raw_routine(
        _raw_ex(900, 300, 1, 0, [(901, 10, 0)], pause="0"),
        _raw_ex(902, 20, 0, 1, [(903, 30, 0)], pause="0"),
        ident="3000009",
        name="ZZ-New",
        hashid=str(FIRST_HASH),
    )
    created["days"] = ""
    client = FakeClient({HISTORY: [_account(BEFORE), _account(BEFORE, created)]})
    spec = RoutineSpec(
        name="ZZ-New",
        warmup=[ExerciseSpec(exercise="Shoulder Circling", sets=[SetSpec(reps=10)])],
        exercises=[ExerciseSpec(exercise="Plank", sets=[SetSpec(reps=30)])],
    )
    result = _service(client, tmp_path).create([spec], dry_run=False)
    ((path, form),) = client.sent
    (payload,) = json.loads(form["routines"])
    assert path == "routine/add/" and payload["number"] == 6
    assert [e["listGroup"] for e in payload["exercises"]] == [1, 0]
    assert [(c.identifier, c.name) for c in result.created] == [(3000009, "ZZ-New")]


def test_archive_and_skip_already_archived(tmp_path: Path) -> None:
    old = _raw_routine(
        ident="3000002", name="ZZ-Old", hashid="2", archived="2026-09-30 10:00:00"
    )
    archived_now = {**BEFORE, "dateArchived": "2026-10-06 10:00:00"}
    client = FakeClient({HISTORY: [_account(BEFORE, old), _account(archived_now, old)]})
    result = _service(client, tmp_path).set_archived(
        ["ZZ-Svc", "ZZ-Old"], archived=True, dry_run=False
    )
    assert client.sent == [("routine/archive/", {"routinesIDs": "3000001"})]
    assert [r.name for r in result.routines] == ["ZZ-Svc"]
    assert [r.name for r in result.skipped] == ["ZZ-Old"]


def test_archive_not_reflected_is_a_verify_error(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE)})
    with pytest.raises(WriteVerifyError, match="ZZ-Svc"):
        _service(client, tmp_path).set_archived(["ZZ-Svc"], archived=True, dry_run=False)


def test_create_with_unknown_outcome_rereads_and_reports(tmp_path: Path) -> None:
    created = _raw_routine(
        _raw_ex(902, 20, 0, 0, [(903, 30, 0)], pause="0"),
        ident="3000009",
        name="ZZ-New",
        hashid=str(FIRST_HASH),
    )
    client = FakeClient(
        {HISTORY: [_account(BEFORE), _account(BEFORE, created)]},
        posts=[WriteOutcomeUnknown("timeout")],
    )
    spec = RoutineSpec(name="ZZ-New", exercises=[ExerciseSpec(exercise="Plank")])
    with pytest.raises(WriteOutcomeUnknown) as exc:
        _service(client, tmp_path).create([spec], dry_run=False)
    message = str(exc.value)
    assert "Outcome unknown: routine/add/" in message
    assert "Re-read: created 'ZZ-New' (id 3000009)" in message


def test_failing_create_verification_read_keeps_class_and_reports_what_was_sent(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        {HISTORY: [_account(BEFORE), ApiError("SmartGym answered 'FAIL'.", code="FAIL")]}
    )
    spec = RoutineSpec(name="ZZ-New", exercises=[ExerciseSpec(exercise="Plank")])
    with pytest.raises(ApiError) as exc:
        _service(client, tmp_path).create([spec], dry_run=False)
    assert type(exc.value) is ApiError and exc.value.code == "FAIL"
    message = str(exc.value)
    assert "routine/add/ was sent and accepted, but re-reading to verify failed" in message
    assert "'ZZ-New'" in message
    assert [path for path, _ in client.sent] == ["routine/add/"]


@pytest.mark.parametrize("archived", [True, False], ids=["archive", "unarchive"])
def test_failing_archive_verification_read_keeps_class_and_reports_what_was_sent(
    tmp_path: Path, archived: bool
) -> None:
    routine = BEFORE if archived else ARCHIVED
    client = FakeClient({HISTORY: [_account(routine), WriteOutcomeUnknown("timeout")]})
    with pytest.raises(WriteOutcomeUnknown) as exc:
        _service(client, tmp_path).set_archived(
            [routine["name"]], archived=archived, dry_run=False
        )
    path = "routine/archive/" if archived else "routine/unarchive/"
    message = str(exc.value)
    assert f"{path} was sent and accepted, but re-reading to verify failed" in message
    assert f"Sent: {path} ({routine['name']})" in message


def test_archive_failure_reports_what_was_already_sent(tmp_path: Path) -> None:
    two = _raw_routine(ident="3000002", name="ZZ-Two", hashid="2")
    three = _raw_routine(ident="3000003", name="ZZ-Three", hashid="3")
    client = FakeClient(
        {HISTORY: _account(BEFORE, two, three)},
        posts=[{"code": "SUCCESS"}, ApiError("SmartGym answered 'FAIL'.", code="FAIL")],
    )
    with pytest.raises(ApiError) as exc:
        _service(client, tmp_path).set_archived(
            ["ZZ-Svc", "ZZ-Two", "ZZ-Three"], archived=True, dry_run=False
        )
    message = str(exc.value)
    assert "Already sent: routine/archive/ (ZZ-Svc)" in message
    assert "Failed: routine/archive/ (ZZ-Two)" in message
    assert "Not sent: routine/archive/ (ZZ-Three)." in message
    assert len(client.sent) == 2
