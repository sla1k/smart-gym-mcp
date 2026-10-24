"""F8: the account's custom exercises, against the app's captured requests (2026-10-18).

Fixtures: `all_custom_exercises` (the account's custom exercises: Dead Hang),
`history_all_custom` (ZZ-F8 = Dead Hang + Push Up, ZZ-F8B = Push Up + Dead Hang),
`add_routine_custom` (app creates ZZ-F8), `update_add_custom_exercise`
(app adds Dead Hang to ZZ-F8B), `update_custom_change_set` / `update_custom_note` /
`update_custom_sets_rest` (app edits Dead Hang inside ZZ-F8).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import CUSTOM_EXERCISES, FakeClient
from smartgym_mcp.api.client import ApiError
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, DiffError, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import ApiPayloadError, parse_custom_exercises, parse_routine
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.payloads import Mint, encode_change, new_routine_payload
from smartgym_mcp.service import RoutineService

FIX = Path(__file__).parent / "fixtures" / "api"
NOW = datetime(2026, 10, 18, 13, 18, 16, tzinfo=UTC)
TZ = "Europe/Madrid"
HISTORY = "history/all/1/"
FIRST_HASH = 26101800000001
DEAD_HANG_ID = 397744
DEAD_HANG_NOTE = "Seconds. Stop on left-wrist pain. W2: 30 -> 35 s"

PUSH_UP = CatalogExercise(
    id=194,
    name="Push Up",
    type=1,
    category=11,
    sub_categories="9,12",
    two_sides=0,
    stretch=0,
    equipment_ids=(),
    images=("0194-1", "0194-2", "", "", "", ""),
)
BUNDLE = {PUSH_UP.id: PUSH_UP}


def _fixture(name: str) -> dict[str, Any]:
    return dict(json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")))


def _account() -> dict[str, Any]:
    return dict(_fixture("history_all_custom")["response"])


def _customs() -> dict[str, Any]:
    return dict(_fixture("all_custom_exercises")["response"])


def _raw_routine(name: str) -> dict[str, Any]:
    return next(r for r in _account()["routines"] if r["name"] == name)


def _dead_hang() -> CatalogExercise:
    (custom,) = parse_custom_exercises(_customs())
    return custom


def _counter() -> Iterator[int]:
    return iter(range(FIRST_HASH, FIRST_HASH + 1000))


def _mint() -> Mint:
    n = _counter()
    return lambda _now: next(n)


def _app_form(name: str) -> dict[str, Any]:
    form = _fixture(name)["form"]
    out: dict[str, Any] = {}
    for k, v in form.items():
        if k in ("authID", "appVersion", "requestDate"):
            continue
        out[k] = json.loads(v) if k in ("routines", "exercises", "updateExercises") else v
    return out


def _encoded(form: dict[str, str] | None) -> dict[str, Any]:
    assert form is not None
    return {
        k: json.loads(v) if k in ("exercises", "updateExercises") else v
        for k, v in form.items()
    }


# ------------------------------------------------------------------ parse + resolve


def test_all_custom_exercises_become_catalog_entries() -> None:
    assert _dead_hang() == CatalogExercise(
        id=DEAD_HANG_ID,
        name="Dead Hang",
        type=2,
        category=3,
        sub_categories="",
        two_sides=0,
        stretch=0,
        equipment_ids=(),
        images=("", "", "", "", "", ""),
        is_custom=True,
    )


def test_removed_custom_exercises_are_skipped() -> None:
    raw = _customs()
    gone = copy.deepcopy(raw["exercises"][0])
    gone.update(id="397800", name="Old Custom", dateRemoved="2026-10-01 10:00:00")
    raw["exercises"].append(gone)
    assert [c.name for c in parse_custom_exercises(raw)] == ["Dead Hang"]


def test_a_malformed_custom_exercise_is_skipped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = _customs()
    bad = copy.deepcopy(raw["exercises"][0])
    bad.update(id="397801", name="Broken Custom", type=None)
    raw["exercises"].insert(0, bad)
    assert [c.name for c in parse_custom_exercises(raw)] == ["Dead Hang"]
    assert "id=397801 name='Broken Custom'" in caplog.text


def test_custom_exercises_answer_must_be_success() -> None:
    with pytest.raises(ApiPayloadError, match="INVALID_TOKEN"):
        parse_custom_exercises({"code": "INVALID_TOKEN"})


def test_routine_exercise_of_a_custom_exercise_parses_with_its_custom_id() -> None:
    zz_f8 = parse_routine(_raw_routine("ZZ-F8"))
    dead_hang, push_up = zz_f8.active_exercises()
    assert (dead_hang.catalog_id, dead_hang.name) == (DEAD_HANG_ID, "Dead Hang")
    assert [(s.reps, s.weight_kg) for s in dead_hang.template_sets] == [(35, 0), (35, 0)]
    assert push_up.catalog_id == 194


@pytest.mark.parametrize("ref", ["Dead Hang", "dead hang", "397744"])
def test_custom_exercise_resolves_next_to_the_bundle(ref: str) -> None:
    catalog = ExerciseCatalog.from_exercises([PUSH_UP, _dead_hang()])
    res = catalog.resolve(ref)
    assert (res.catalog_id, res.resolved_name, res.fuzzy) == (DEAD_HANG_ID, "Dead Hang", False)
    assert catalog.resolve("Push Up").catalog_id == 194


# ------------------------------------------------------------------------ payloads


def test_create_payload_carries_the_custom_exercise_like_the_app() -> None:
    spec = RoutineSpec(
        name="ZZ-F8",
        goal="asd",
        exercises=[
            ExerciseSpec(
                exercise="Dead Hang",
                rest_seconds=90,
                note=DEAD_HANG_NOTE,
                sets=[SetSpec(reps=35), SetSpec(reps=35)],
            ),
            ExerciseSpec(exercise="Push Up", sets=[SetSpec(reps=10)]),
        ],
    )
    ours = new_routine_payload(spec, [_dead_hang(), PUSH_UP], number=31, now=NOW, mint=_mint())
    (app,) = _app_form("add_routine_custom")["routines"]
    app_ex, our_ex = app["exercises"][0], ours["exercises"][0]
    # The app also sends local placeholder ids, which a create may omit (S6); `mode` is
    # accepted (S6).
    assert set(app_ex) - set(our_ex) == {"identifier", "routineID"}
    assert set(our_ex) - set(app_ex) == {"mode"}
    # The app sends a local image name ("deadhang1"); the server keeps the custom
    # exercise's own (empty) firstImage either way (history_all_custom).
    skip = {"identifier", "routineID", "uniqueHashID", "dateAdded", "sets", "firstImage"}
    assert {k: v for k, v in our_ex.items() if k not in skip | {"mode"}} == {
        k: v for k, v in app_ex.items() if k not in skip
    }
    assert our_ex["isCustom"] == 1 and our_ex["firstImage"] == ""
    hashes = {"uniqueHashID", "dateAdded"}
    assert [{k: v for k, v in s.items() if k not in hashes} for s in our_ex["sets"]] == [
        {k: v for k, v in s.items() if k not in hashes} for s in app_ex["sets"]
    ]
    assert ours["exercises"][1]["isCustom"] == 0


def test_adding_a_custom_exercise_matches_the_app_update() -> None:
    raw = _raw_routine("ZZ-F8B")
    raw["exercises"] = [e for e in raw["exercises"] if e["name"] != "Dead Hang"]
    current = parse_routine(raw)
    (push_up,) = current.active_exercises()
    desired = DesiredRoutine(
        main=[
            DesiredExercise(exercise_id=push_up.identifier),
            DesiredExercise(
                exercise="Dead Hang",
                rest_seconds=90,
                note=DEAD_HANG_NOTE,
                sets=[SetSpec(reps=35), SetSpec(reps=35)],
            ),
        ]
    )
    exercises = {**BUNDLE, DEAD_HANG_ID: _dead_hang()}
    cs = diff_routine(current, desired, ExerciseCatalog.from_exercises(exercises.values()))
    enc = encode_change(cs, current, exercises, timezone=TZ, now=NOW, mint=_mint())
    ours = _encoded(enc.structure)
    app = _app_form("update_add_custom_exercise")
    assert {k: v for k, v in ours.items() if k != "exercises"} == {
        k: v for k, v in app.items() if k != "exercises"
    }
    ((our_ex,), (app_ex,)) = ours["exercises"], app["exercises"]
    assert set(app_ex) - set(our_ex) == {"identifier"}
    assert set(our_ex) - set(app_ex) == {"mode"}
    skip = {"identifier", "uniqueHashID", "dateAdded", "sets", "firstImage", "mode"}
    assert {k: v for k, v in our_ex.items() if k not in skip} == {
        k: v for k, v in app_ex.items() if k not in skip
    }
    assert enc.exercise_edits is None


# ------------------------------------------- editing a routine that holds a custom one


def _zz_f8_edit(**dead_hang: Any) -> tuple[Any, Any]:
    current = parse_routine(_raw_routine("ZZ-F8"))
    dh, pu = current.active_exercises()
    desired = DesiredRoutine(
        main=[
            DesiredExercise(exercise_id=dh.identifier, **dead_hang),
            DesiredExercise(exercise_id=pu.identifier),
        ]
    )
    exercises = {**BUNDLE, DEAD_HANG_ID: _dead_hang()}
    cs = diff_routine(current, desired, ExerciseCatalog.from_exercises(exercises.values()))
    return cs, encode_change(cs, current, exercises, timezone=TZ, now=NOW, mint=_mint())


def _app_dead_hang_entry(fixture: str) -> dict[str, Any]:
    (entry,) = _app_form(fixture)["updateExercises"]
    return dict(entry)


def test_listing_the_custom_exercise_unchanged_is_no_change() -> None:
    cs, enc = _zz_f8_edit()
    assert cs.is_empty
    assert enc.structure is None and enc.exercise_edits is None


def test_custom_exercise_set_change_matches_the_app() -> None:
    _, enc = _zz_f8_edit(sets=[SetSpec(reps=40), SetSpec(reps=40)])
    (ours,) = _encoded(enc.exercise_edits)["exercises"]
    # This editor sends set edits inside routine/update/'s updateExercises; ours go to
    # routine/updateExercise/ (S2/S3). The per-exercise entry is the same.
    assert ours == _app_dead_hang_entry("update_custom_change_set")
    assert enc.structure is None


def test_custom_exercise_note_matches_the_app() -> None:
    _, enc = _zz_f8_edit(note=f"{DEAD_HANG_NOTE}\n\nF8 note")
    (ours,) = _encoded(enc.exercise_edits)["exercises"]
    assert ours == _app_dead_hang_entry("update_custom_note")


def test_custom_exercise_rest_and_added_set_match_the_app() -> None:
    sets = [SetSpec(reps=40), SetSpec(reps=40), SetSpec(reps=30)]
    _, enc = _zz_f8_edit(rest_seconds=60, sets=sets)
    app = _app_dead_hang_entry("update_custom_sets_rest")
    (rest,) = _encoded(enc.structure)["updateExercises"]
    assert rest == {"pause": app["pause"], "exerciseID": app["exerciseID"]}
    (edit,) = _encoded(enc.exercise_edits)["exercises"]
    assert edit["updatedSets"] == app["updatedSets"]
    ((our_add,), (app_add,)) = edit["addedSets"], app["addedSets"]
    assert {k: v for k, v in our_add.items() if k != "uniqueHashID"} == {
        k: v for k, v in app_add.items() if k != "uniqueHashID"
    }


# ------------------------------------------------------------------------ service


def _service(
    client: FakeClient, tmp_path: Path, *, move_in_place: bool = True
) -> RoutineService:
    return RoutineService(
        client,
        AccountStore(client),
        BUNDLE,
        backup_dir=tmp_path,
        timezone=TZ,
        now=lambda: NOW,
        mint=_mint(),
        move_in_place=move_in_place,
    )


def test_create_resolves_and_sends_the_custom_exercise(tmp_path: Path) -> None:
    before = _account()
    before["routines"] = []
    created = _raw_routine("ZZ-F8")
    created["uniqueHashID"] = str(FIRST_HASH)
    after = _account()
    after["routines"] = [created]
    client = FakeClient({HISTORY: [before, after], CUSTOM_EXERCISES: _customs()})
    spec = RoutineSpec(
        name="ZZ-F8",
        goal="asd",
        exercises=[
            ExerciseSpec(
                exercise="dead hang",
                rest_seconds=90,
                note=DEAD_HANG_NOTE,
                sets=[SetSpec(reps=35), SetSpec(reps=35)],
            ),
            ExerciseSpec(exercise="Push Up", sets=[SetSpec(reps=10)]),
        ],
    )
    result = _service(client, tmp_path).create([spec], dry_run=False)
    ((path, form),) = client.sent
    (payload,) = json.loads(form["routines"])
    assert path == "routine/add/"
    assert [(e["id"], e["isCustom"]) for e in payload["exercises"]] == [
        (DEAD_HANG_ID, 1),
        (194, 0),
    ]
    assert [c.name for c in result.created] == ["ZZ-F8"]


def test_a_custom_exercise_in_no_routine_yet_resolves(tmp_path: Path) -> None:
    # history/all lists only custom exercises already used in a routine (live 2026-10-18);
    # allCustomExercises lists every one.
    customs = _customs()
    fresh = copy.deepcopy(customs["exercises"][0])
    fresh.update(id="419945", name="ZZ-F9 Live 2", type="0")
    customs["exercises"].append(fresh)
    account = _account()
    assert [c["name"] for c in account["customExercises"]] == ["Dead Hang"]
    account["routines"] = []
    client = FakeClient({HISTORY: account, CUSTOM_EXERCISES: customs})
    spec = RoutineSpec(
        name="ZZ-F9", goal="g", exercises=[ExerciseSpec(exercise="ZZ-F9 Live 2")]
    )
    result = _service(client, tmp_path).create([spec], dry_run=True)
    (res,) = result.plan[0].resolutions
    assert (res.catalog_id, res.fuzzy) == (419945, False)


def test_a_removed_custom_exercise_does_not_resolve(tmp_path: Path) -> None:
    account = _account()
    account["routines"] = []
    customs = _customs()
    customs["exercises"][0]["dateRemoved"] = "2026-10-18 13:00:00"
    client = FakeClient({HISTORY: account, CUSTOM_EXERCISES: customs})
    spec = RoutineSpec(name="ZZ-F8", goal="g", exercises=[ExerciseSpec(exercise="Dead Hang")])
    with pytest.raises(DiffError, match="Could not resolve exercise 'Dead Hang'"):
        _service(client, tmp_path).create([spec], dry_run=True)
    assert client.sent == []


def test_editing_a_routine_with_a_custom_exercise_leaves_it_untouched(tmp_path: Path) -> None:
    before = _raw_routine("ZZ-F8")
    after = copy.deepcopy(before)
    next(e for e in after["exercises"] if e["name"] == "Pushup")["pause"] = "45"
    single = "routine/single/3698443/"
    client = FakeClient(
        {
            HISTORY: _account(),
            # An edit that adds nothing never reads the custom exercises.
            CUSTOM_EXERCISES: ApiError("custom exercise list unavailable"),
            single: [{"code": "SUCCESS", "routines": [before]}, {"routines": [after]}],
        }
    )

    def rest_on_push_up(r):  # type: ignore[no-untyped-def]
        return DesiredRoutine(
            main=[
                DesiredExercise(
                    exercise_id=e.identifier, rest_seconds=45 if e.catalog_id == 194 else None
                )
                for e in r.active_exercises()
            ]
        )

    result = _service(client, tmp_path).edit("ZZ-F8", rest_on_push_up, dry_run=False)
    ((path, form),) = client.sent
    assert path == "routine/update/"
    assert json.loads(form["updateExercises"]) == [{"pause": "45", "exerciseID": 34215244}]
    assert "exercises" not in form and "removeExercises" not in form
    assert result.notice.startswith("Applied")
    assert CUSTOM_EXERCISES not in client.get_calls


def test_a_custom_exercise_named_like_a_built_in_one_is_ambiguous(tmp_path: Path) -> None:
    customs = _customs()
    customs["exercises"][0]["name"] = "Push Up"
    account = _account()
    account["routines"] = []
    client = FakeClient({HISTORY: account, CUSTOM_EXERCISES: customs})
    spec = RoutineSpec(name="ZZ-F8", goal="g", exercises=[ExerciseSpec(exercise="push up")])
    with pytest.raises(DiffError, match=r"several exercises .*id=194.*id=397744"):
        _service(client, tmp_path).create([spec], dry_run=True)
    by_id = RoutineSpec(name="ZZ-F8", goal="g", exercises=[ExerciseSpec(exercise="397744")])
    (res,) = _service(client, tmp_path).create([by_id], dry_run=True).plan[0].resolutions
    assert res.catalog_id == DEAD_HANG_ID


def test_re_adding_a_deleted_custom_exercise_is_rejected_not_crashed(tmp_path: Path) -> None:
    raw = _raw_routine("ZZ-F8")
    client = FakeClient(
        {
            HISTORY: _account(),
            CUSTOM_EXERCISES: {"code": "SUCCESS", "exercises": []},
            "routine/single/3698443/": {"code": "SUCCESS", "routines": [raw]},
        }
    )

    def dead_hang_to_warmup(r):  # type: ignore[no-untyped-def]
        dh, pu = r.active_exercises()
        return DesiredRoutine(
            warmup=[DesiredExercise(exercise_id=dh.identifier)],
            main=[DesiredExercise(exercise_id=pu.identifier)],
        )

    service = _service(client, tmp_path, move_in_place=False)
    with pytest.raises(DiffError, match=r"'Dead Hang' \(catalog id 397744\)"):
        service.edit("ZZ-F8", dead_hang_to_warmup, dry_run=True)
    assert client.sent == []


def test_custom_single_weight_flag_is_sent_from_its_record() -> None:
    raw = _customs()
    raw["exercises"][0]["isSingleWeight"] = "1"
    (custom,) = parse_custom_exercises(raw)
    spec = RoutineSpec(name="ZZ-F8", goal="g", exercises=[ExerciseSpec(exercise="Dead Hang")])
    payload = new_routine_payload(spec, [custom], number=1, now=NOW, mint=_mint())
    assert payload["exercises"][0]["isSingleWeight"] == 1
    assert _dead_hang().single_weight == 0  # as captured


def test_fallback_mode_reads_custom_exercises_only_for_a_real_re_add(tmp_path: Path) -> None:
    raw = _raw_routine("ZZ-F8")
    client = FakeClient(
        {
            HISTORY: _account(),
            CUSTOM_EXERCISES: ApiError("custom exercise list unavailable"),
            "routine/single/3698443/": {"code": "SUCCESS", "routines": [raw]},
        }
    )

    def rest_on_dead_hang(r):  # type: ignore[no-untyped-def]
        dh, pu = r.active_exercises()
        return DesiredRoutine(
            main=[
                DesiredExercise(exercise_id=dh.identifier, rest_seconds=60),
                DesiredExercise(exercise_id=pu.identifier),
            ]
        )

    service = _service(client, tmp_path, move_in_place=False)
    result = service.edit("ZZ-F8", rest_on_dead_hang, dry_run=True)
    assert result.requests == ["routine/update/"]
    assert CUSTOM_EXERCISES not in client.get_calls
