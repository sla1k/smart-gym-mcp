"""payloads.py: routine/add/ JSON in the app's captured shape + archive forms."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.payloads import (
    add_routines_form,
    archive_form,
    encode_change,
    local_timezone_name,
    mint_unique_hashid,
    new_routine_payload,
    order_form,
    unarchive_form,
)

MADRID = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 5, 13, 30, 15, tzinfo=MADRID)

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
BAND = CatalogExercise(
    id=363,
    name="Resistance Band Pull Apart",
    type=0,
    category=5,
    sub_categories="101",
    two_sides=0,
    stretch=0,
    equipment_ids=("38",),
    images=("0363-1", "0363-2", "", "", "", ""),
)


def _counter() -> object:
    n = iter(range(1, 100))
    return lambda _now: 26100500000000 + next(n)


def test_new_routine_payload_matches_captured_shape() -> None:
    spec = RoutineSpec(
        name="ZZ-FB — Test",
        days="0001",
        note="note",
        exercises=[
            ExerciseSpec(
                exercise="Push Up",
                rest_seconds=60,
                sets=[SetSpec(reps=10), SetSpec(reps=8.5, weight_kg=12.5)],
            ),
            ExerciseSpec(exercise="band", note="per arm"),
        ],
    )
    p = new_routine_payload(spec, [PUSH_UP, BAND], number=21, now=NOW, mint=_counter())

    assert {
        k: p[k]
        for k in (
            "name",
            "days",
            "goal",
            "note",
            "number",
            "reference",
            "hasSynced",
            "migratedSets",
            "uniqueHashID",
            "dateCreated",
        )
    } == {
        "name": "ZZ-FB — Test",
        "days": "0001",
        "goal": None,
        "note": "note",
        "number": 21,
        "reference": 0,
        "hasSynced": 0,
        "migratedSets": 1,
        "uniqueHashID": 26100500000001,
        "dateCreated": "2026-10-04T22:00:00",  # local midnight, sent as UTC
    }
    assert "identifier" not in p

    push, band = p["exercises"]
    assert {
        k: push[k]
        for k in (
            "id",
            "genericID",
            "name",
            "idx",
            "index",
            "pause",
            "type",
            "category",
            "subCategories",
            "twoSides",
            "stretch",
            "isStretch",
            "isCustom",
            "requiresBands",
            "isSingleWeight",
            "listGroup",
            "mode",
            "totalSets",
            "totalRealSets",
            "firstImage",
            "sixthImage",
            "dateAdded",
        )
    } == {
        "id": 194,
        "genericID": 194,
        "name": "Push Up",
        "idx": 0,
        "index": 0,
        "pause": 60,
        "type": 1,
        "category": 11,
        "subCategories": "9,12",
        "twoSides": 0,
        "stretch": 0,
        "isStretch": False,
        "isCustom": 0,
        "requiresBands": False,
        "isSingleWeight": 0,
        "listGroup": 0,
        "mode": 0,
        "totalSets": 2,
        "totalRealSets": 2,
        "firstImage": "0194-1",
        "sixthImage": "",
        "dateAdded": "2026-10-05T11:30:15",
    }
    assert "note" not in push and "identifier" not in push and "routineID" not in push
    assert push["sets"] == [
        {
            "index": 0,
            "type": 0,
            "firstValue": 1,
            "secondValue": 10,
            "thirdValue": 0,
            "uniqueHashID": 26100500000003,
            "dateAdded": "2026-10-05T11:30:15",
        },
        {
            "index": 1,
            "type": 0,
            "firstValue": 1,
            "secondValue": 8.5,
            "thirdValue": 12.5,
            "uniqueHashID": 26100500000004,
            "dateAdded": "2026-10-05T11:30:15",
        },
    ]
    assert band["requiresBands"] is True
    assert band["note"] == "per arm"
    assert band["pause"] == 0
    assert [(s["secondValue"], s["thirdValue"]) for s in band["sets"]] == [(10, 0)]


def test_add_routines_form_is_utf8_json() -> None:
    form = add_routines_form([{"name": "FB-A — Return W1"}], timezone="Europe/Madrid")
    assert form["timezone"] == "Europe/Madrid"
    assert json.loads(form["routines"]) == [{"name": "FB-A — Return W1"}]
    assert "—" in form["routines"]  # not —-escaped


def test_archive_forms() -> None:
    assert archive_form(3681004) == {"routinesIDs": "3681004"}
    assert unarchive_form(3681004) == {"routineID": "3681004"}


def test_mint_unique_hashid_shape() -> None:
    h = mint_unique_hashid(datetime(2026, 10, 5, tzinfo=UTC))
    assert str(h).startswith("261005") and len(str(h)) == 14


FIX = Path(__file__).parent / "fixtures" / "api"
JSON_FIELDS = {"exercises", "updateExercises", "routines"}
TZ = "Europe/Madrid"
ABDOMINAL = CatalogExercise(
    id=22,
    name="Abdominal 4 points Drawing In",
    type=1,
    category=1,
    sub_categories="",
    two_sides=0,
    stretch=0,
    equipment_ids=(),
    images=("0022-1", "0022-2", "", "", "", ""),
)
BUNDLE = {22: ABDOMINAL}
CATALOG = ExerciseCatalog(
    [
        (194, "Push Up"),
        (20, "Plank"),
        (207, "Cable Chest Press"),
        (256, "Ab Machine"),
        (22, "Abdominal 4 points Drawing In"),
    ]
)


def _norm(form: dict[str, str]) -> dict[str, object]:
    return {k: json.loads(v) if k in JSON_FIELDS else v for k, v in form.items()}


def _fixture_form(name: str) -> dict[str, object]:
    form = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))["form"]
    return _norm(
        {k: v for k, v in form.items() if k not in ("authID", "appVersion", "requestDate")}
    )


def _ex(
    ident: int,
    cat: int,
    name: str,
    idx: int,
    *,
    rest: int = 15,
    sets: tuple[TemplateSet, ...] = (),
) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident,
        unique_hashid=ident,
        catalog_id=cat,
        name=name,
        section="main",
        index=idx,
        rest_seconds=rest,
        note=None,
        removed=False,
        template_sets=list(sets),
        logged_sets=[],
    )


def _routine(
    *exs: RoutineExercise,
    name: str = "ZZ-SPIKE2",
    days: str | None = "2,4,6",
    goal: str | None = "changed goal",
    note: str | None = None,
) -> Routine:
    return Routine(
        identifier=3681209,
        unique_hashid=26100512239894,
        name=name,
        days=days,
        goal=goal,
        note=note,
        number=17,
        archived=False,
        removed=False,
        exercises=list(exs),
    )


SPIKE = (
    _ex(34048391, 194, "Push Up", 0),
    _ex(34048392, 20, "Plank", 1),
    _ex(34048393, 207, "Cable Chest Press", 2),
)


def _ts(ident: int, hashid: int, idx: int, reps: float, kg: float, added: str) -> TemplateSet:
    return TemplateSet(
        identifier=ident,
        unique_hashid=hashid,
        index=idx,
        reps=reps,
        weight_kg=kg,
        date_added=added,
    )


AB3 = (
    _ts(149091969, 26100557160269, 0, 15, 36, "2026-10-05 15:16:23"),
    _ts(149091970, 26100549229340, 1, 15, 36, "2026-10-05 15:16:23"),
    _ts(149091971, 26100526419289, 2, 15, 36, "2026-10-05 15:16:23"),
)
AB_FOURTH = _ts(149092034, 26100568239109, 3, 10, 32, "2026-10-05 15:17:10")


def _encode(current: Routine, desired: DesiredRoutine, mint=None):  # type: ignore[no-untyped-def]
    cs = diff_routine(current, desired, CATALOG)
    enc = encode_change(cs, current, BUNDLE, timezone=TZ, now=NOW, mint=mint or _counter())
    return cs, enc


def _main(*items: DesiredExercise | int) -> DesiredRoutine:
    return DesiredRoutine(
        main=[
            i if isinstance(i, DesiredExercise) else DesiredExercise(exercise_id=i)
            for i in items
        ]
    )


def test_update_rest_matches_capture() -> None:
    rest = DesiredExercise(exercise_id=34048393, rest_seconds=30)
    _, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, rest))
    assert _norm(enc.structure or {}) == _fixture_form("update_rest")
    assert enc.exercise_edits is None


def test_reorder_goes_to_order_form_matching_capture() -> None:
    cs, enc = _encode(_routine(*SPIKE), _main(34048393, 34048391, 34048392))
    assert enc.structure is None and enc.exercise_edits is None
    assert order_form(cs, _routine(*SPIKE), [], timezone=TZ) == _fixture_form("update_reorder")


def test_remove_exercise_matches_capture() -> None:
    extra = _ex(34048426, 22, "Abdominal 4 points Drawing In", 3)
    cs, enc = _encode(_routine(*SPIKE, extra), _main(34048391, 34048392, 34048393))
    assert _norm(enc.structure or {}) == _fixture_form("update_remove_exercise")
    assert cs.final_order is None


@pytest.mark.parametrize(
    ("fixture", "before", "desired"),
    [
        (
            "update_rename",
            {"name": "ZZ-SPIKE", "days": None, "goal": "asd"},
            DesiredRoutine(name="ZZ-SPIKE2"),
        ),
        ("update_days", {"days": None, "goal": "asd"}, DesiredRoutine(days="2,4,6")),
        ("update_goal", {"goal": "asd"}, DesiredRoutine(goal="changed goal")),
        ("update_note", {}, DesiredRoutine(note="changed routine note")),
    ],
)
def test_routine_field_edits_match_capture(
    fixture: str, before: dict[str, str | None], desired: DesiredRoutine
) -> None:
    _, enc = _encode(_routine(*SPIKE, **before), desired)  # type: ignore[arg-type]
    assert _norm(enc.structure or {}) == _fixture_form(fixture)


def test_exercise_note_goes_to_update_exercise_matching_capture() -> None:
    note = DesiredExercise(exercise_id=34048393, note="cahnge note")
    _, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, note))
    assert enc.structure is None
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_exercise_note")


def test_add_set_matches_capture() -> None:
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=AB3))
    sets = [SetSpec(reps=15, weight_kg=36)] * 3 + [SetSpec(reps=10, weight_kg=32)]
    _, enc = _encode(
        current,
        _main(DesiredExercise(exercise_id=34048425, sets=sets)),
        mint=lambda _now: 26100568239109,
    )
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_add_set")


def test_change_set_matches_capture() -> None:
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=(*AB3, AB_FOURTH)))
    sets = [SetSpec(reps=15, weight_kg=36)] * 2 + [
        SetSpec(reps=5, weight_kg=6),
        SetSpec(reps=10, weight_kg=32),
    ]
    _, enc = _encode(current, _main(DesiredExercise(exercise_id=34048425, sets=sets)))
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_change_set")


def test_remove_set_matches_capture() -> None:
    third = _ts(149091971, 26100526419289, 2, 5, 6, "2026-10-05 15:16:23")
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=(*AB3[:2], third, AB_FOURTH)))
    sets = [SetSpec(reps=15, weight_kg=36)] * 2 + [SetSpec(reps=5, weight_kg=6)]
    _, enc = _encode(current, _main(DesiredExercise(exercise_id=34048425, sets=sets)))
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_remove_set")


def test_add_exercise_matches_capture_shape() -> None:
    new = DesiredExercise(
        exercise="Abdominal 4 points Drawing In", rest_seconds=0, sets=[SetSpec(reps=1)]
    )
    cs, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, 34048393, new))
    app = _fixture_form("update_add_exercise")
    ours = _norm(enc.structure or {})
    assert {k: v for k, v in ours.items() if k != "exercises"} == {
        k: v for k, v in app.items() if k != "exercises"
    }
    (app_ex,) = app["exercises"]  # type: ignore[misc]
    (our_ex,) = ours["exercises"]  # type: ignore[misc]
    assert set(app_ex) - set(our_ex) == {"identifier"}
    assert set(our_ex) - set(app_ex) <= {"subCategories", "mode"}  # both accepted in S6
    for key in (
        "id",
        "genericID",
        "name",
        "idx",
        "index",
        "pause",
        "listGroup",
        "routineID",
        "type",
        "category",
        "isCustom",
        "isSingleWeight",
        "requiresBands",
        "isStretch",
        "stretch",
        "twoSides",
        "firstImage",
        "secondImage",
    ):
        assert our_ex[key] == app_ex[key], key
    assert set(our_ex["sets"][0]) == set(app_ex["sets"][0])
    assert enc.added_hashids == [our_ex["uniqueHashID"]]
    assert cs.final_order is None


def test_mid_routine_add_produces_order_form_with_server_id() -> None:
    new = DesiredExercise(exercise="Abdominal 4 points Drawing In")
    cs, enc = _encode(_routine(*SPIKE), _main(34048391, new, 34048392, 34048393))
    assert _norm(enc.structure or {})["exercises"][0]["idx"] == 1  # type: ignore[index]
    form = order_form(cs, _routine(*SPIKE), [34049999], timezone=TZ)
    assert form is not None
    assert form["exercisesOrder"] == "34048391:0,34049999:1,34048392:2,34048393:3"


def test_cleared_routine_note_is_sent_as_empty_string() -> None:
    _, enc = _encode(_routine(*SPIKE, note="old"), DesiredRoutine(note=""))
    assert (enc.structure or {})["note"] == ""


def test_create_payload_sections_set_list_group_and_global_idx() -> None:
    spec = RoutineSpec(
        name="ZZ-Sections",
        warmup=[ExerciseSpec(exercise="band")],
        exercises=[ExerciseSpec(exercise="Push Up")],
        cooldown=[ExerciseSpec(exercise="band")],
    )
    p = new_routine_payload(spec, [BAND, PUSH_UP, BAND], number=1, now=NOW, mint=_counter())
    assert [(e["name"], e["listGroup"], e["idx"]) for e in p["exercises"]] == [
        ("Resistance Band Pull Apart", 1, 0),
        ("Push Up", 0, 1),
        ("Resistance Band Pull Apart", 2, 2),
    ]


def test_create_payload_rejects_misaligned_catalog_entries() -> None:
    spec = RoutineSpec(name="ZZ", exercises=[ExerciseSpec(exercise="Push Up")])
    with pytest.raises(ValueError, match="catalog entries"):
        new_routine_payload(spec, [PUSH_UP, BAND], number=1, now=NOW)


def test_exercise_spec_rejects_empty_sets() -> None:
    with pytest.raises(ValidationError):
        ExerciseSpec(exercise="Push Up", sets=[])


def test_local_timezone_name_is_iana_or_utc() -> None:
    name = local_timezone_name()
    assert name == "UTC" or "/" in name
