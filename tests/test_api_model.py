"""model.py: SmartGym API JSON → typed routine model (strings coerced once)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartgym_mcp.model import ApiPayloadError, parse_routine, parse_routines_response

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
