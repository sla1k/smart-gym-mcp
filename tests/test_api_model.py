"""model.py: SmartGym API JSON → typed routine model (strings coerced once)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartgym_mcp.model import (
    ApiPayloadError,
    parse_history_all,
    parse_routine,
    parse_routines_response,
)

FIXTURE = Path(__file__).parent / "fixtures" / "api" / "routine_single_synthetic.json"


def _raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_routine_fields_and_coerces_strings() -> None:
    (routine,) = parse_routines_response(_raw())
    assert routine.identifier == 3000001
    assert routine.unique_hashid == 26091100000001
    assert routine.name == "ZZ-FB — Test"
    assert routine.days == "0001"
    assert routine.goal is None  # "" normalizes to None
    assert routine.note == "Routine note"
    assert not routine.archived and not routine.removed


def test_active_exercises_sorted_by_index_and_skip_removed() -> None:
    (routine,) = parse_routines_response(_raw())
    active = routine.active_exercises()
    assert [e.name for e in active] == ["Cable Chest Press", "Resistance Band Pull Apart"]
    assert [e.catalog_id for e in active] == [207, 363]
    assert len(routine.exercises) == 3
    removed = next(e for e in routine.exercises if e.name == "Bridge")
    assert removed.removed


def test_template_sets_exclude_logged_and_removed_and_sort_by_index() -> None:
    (routine,) = parse_routines_response(_raw())
    band = next(e for e in routine.exercises if e.catalog_id == 363)
    assert [(s.identifier, s.reps, s.weight_kg) for s in band.template_sets] == [
        (50000001, 19.0, 0.0),
        (50000002, 15.0, 0.0),
    ]
    chest = next(e for e in routine.exercises if e.catalog_id == 207)
    assert chest.template_sets[0].weight_kg == 42.5
    assert chest.rest_seconds == 75
    assert chest.note is None  # "" normalizes to None


def test_non_success_code_raises() -> None:
    with pytest.raises(ApiPayloadError, match="ROUTINE_NOT_FOUND"):
        parse_routines_response({"code": "ROUTINE_NOT_FOUND"})


def test_missing_required_field_raises_payload_error() -> None:
    raw = _raw()["routines"][0]
    del raw["identifier"]
    with pytest.raises(ApiPayloadError, match="identifier"):
        parse_routine(raw)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [("pause", "10.0", 10), ("pause", "", 0), ("pause", None, 0), ("idx", "1.0", 1)],
)
def test_integral_strings_coerce(field: str, value: object, expected: int) -> None:
    raw = _raw()["routines"][0]
    raw["exercises"][1][field] = value
    ex = next(e for e in parse_routine(raw).exercises if e.catalog_id == 207)
    assert (ex.rest_seconds if field == "pause" else ex.index) == expected


@pytest.mark.parametrize(
    ("field", "value"), [("idx", ""), ("idx", "1.5"), ("pause", "abc"), ("secondValue", "")]
)
def test_bad_numbers_raise_payload_error_naming_field(field: str, value: str) -> None:
    raw = _raw()["routines"][0]
    target = raw["exercises"][1]
    if field == "secondValue":
        target = target["sets"][0]
    target[field] = value
    with pytest.raises(ApiPayloadError, match=field):
        parse_routine(raw)


SYNTH = Path(__file__).parent / "fixtures" / "api" / "history_all_synthetic.json"


def _account() -> dict:
    return json.loads(SYNTH.read_text(encoding="utf-8"))


def test_sections_map_from_list_group_and_order_active_exercises() -> None:
    data = parse_history_all(_account())
    routine = data.routines[0]
    assert [(e.section, e.name) for e in routine.active_exercises()] == [
        ("warmup", "Shoulder Circling"),
        ("main", "Cable Chest Press"),
        ("cooldown", "Cross Arm Stretch"),
    ]
    assert [e.name for e in routine.section("cooldown")] == ["Cross Arm Stretch"]


def test_unknown_list_group_is_rejected_naming_the_value() -> None:
    raw = _account()["routines"][0]
    raw["exercises"][0]["listGroup"] = "7"
    with pytest.raises(ApiPayloadError, match="listGroup.*7"):
        parse_routine(raw)


def test_logged_sets_are_separated_from_template_sets() -> None:
    chest = next(
        e for e in parse_history_all(_account()).routines[0].exercises if e.catalog_id == 207
    )
    assert [(s.identifier, s.reps, s.weight_kg) for s in chest.template_sets] == [
        (50000011, 10.0, 42.5)
    ]
    assert chest.template_sets[0].date_added == "2026-09-11 08:34:19"
    assert [s.identifier for s in chest.logged_sets] == [50000021, 50000022, 50000023]
    assert chest.logged_sets[0].logged_at == "2026-09-14 08:10:00"


def test_empty_string_dates_count_as_absent() -> None:
    data = parse_history_all(_account())
    circling = next(e for e in data.routines[0].exercises if e.catalog_id == 300)
    assert len(circling.template_sets) == 1 and circling.logged_sets == []
    gone = next(r for r in data.routines if r.name == "ZZ-Gone")
    assert not gone.archived and gone.removed
    assert next(r for r in data.routines if r.name == "ZZ-Old").archived


def test_workouts_parse_numbers_and_skip_removed_histories() -> None:
    data = parse_history_all(_account())
    assert [w.identifier for w in data.workouts] == [7000001, 7000002]
    first, second = data.workouts
    assert (first.routine_identifier, first.duration_s, first.calories) == (3000001, 3600, 350)
    assert (first.avg_hr, first.max_hr, first.end) == (120, 160, "2026-09-14 09:00:00")
    assert first.set_ids == [50000021, 50000022]
    assert (second.duration_s, second.calories, second.avg_hr, second.max_hr, second.end) == (
        2700,
        None,
        132,
        None,
        None,
    )


@pytest.mark.parametrize(
    "workout", [None, {}, {"startDate": ""}, {"startDate": None}, "oops", []]
)
def test_live_history_without_a_usable_workout_is_skipped(workout: object) -> None:
    raw = _account()
    raw["histories"].append(
        {"identifier": "7000009", "dateRemoved": None, "routine": None, "workout": workout}
    )
    data = parse_history_all(raw)
    assert [w.identifier for w in data.workouts] == [7000001, 7000002]


def test_history_without_a_workout_key_is_skipped() -> None:
    raw = _account()
    raw["histories"].append({"identifier": "7000009", "dateRemoved": None, "routine": None})
    assert [w.identifier for w in parse_history_all(raw).workouts] == [7000001, 7000002]


def test_workout_with_start_but_malformed_other_field_still_fails_closed() -> None:
    raw = _account()
    raw["histories"][0]["workout"]["duration"] = "abc"
    with pytest.raises(ApiPayloadError):
        parse_history_all(raw)


def test_equipment_lists_parse() -> None:
    (eq,) = parse_history_all(_account()).equipment_lists
    assert (eq.identifier, eq.selected, eq.equipment_ids) == (200109, True, [1, 2, 38])
    assert (eq.dumbbell_weights, eq.kettlebell_weights) == (None, "8,12")


def test_has_more_is_refused_rather_than_truncated() -> None:
    raw = _account()
    raw["hasMore"] = True
    with pytest.raises(ApiPayloadError, match="hasMore"):
        parse_history_all(raw)


def test_history_non_success_raises() -> None:
    with pytest.raises(ApiPayloadError, match="INVALID_TOKEN"):
        parse_history_all({"code": "INVALID_TOKEN"})
