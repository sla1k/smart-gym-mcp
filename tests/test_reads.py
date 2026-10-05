"""reads.py: AccountData → read-tool views. Pure; UTC + fixed 'today' for determinism."""

from __future__ import annotations

import json
from datetime import UTC, date
from pathlib import Path

import pytest

from smartgym_mcp.catalog import CatalogEquipment
from smartgym_mcp.model import parse_history_all
from smartgym_mcp.reads import equipment, list_routines, routine_detail, workout_history

DATA = parse_history_all(
    json.loads(
        (Path(__file__).parent / "fixtures" / "api" / "history_all_synthetic.json").read_text(
            encoding="utf-8"
        )
    )
)
TODAY = date(2026, 9, 30)


def test_list_routines_hides_archived_and_removed_and_counts_sections() -> None:
    (only,) = list_routines(DATA).routines
    assert (only.name, only.warmup, only.main, only.cooldown) == ("ZZ-FB — Test", 1, 1, 1)
    names = [r.name for r in list_routines(DATA, include_archived=True).routines]
    assert names == ["ZZ-FB — Test", "ZZ-Old"]


def test_routine_detail_groups_sections_and_sessions() -> None:
    detail = routine_detail(DATA.routines[0], DATA, history_depth=5, tz=UTC)
    assert [e.name for e in detail.warmup] == ["Shoulder Circling"]
    assert [e.name for e in detail.cooldown] == ["Cross Arm Stretch"]
    (chest,) = detail.main
    assert (chest.exercise_id, chest.rest_seconds, chest.note) == (40000012, 90, "Slow")
    assert [(s.set_no, s.reps, s.weight_kg) for s in chest.template_sets] == [(1, 10.0, 42.5)]
    assert [s.date for s in chest.sessions] == ["2026-09-21", "2026-09-14"]
    assert [(s.reps, s.weight_kg) for s in chest.sessions[1].sets] == [
        (10.0, 40.0),
        (8.0, 42.5),
    ]
    assert chest.top_set is not None and chest.top_set.weight_kg == 42.5
    assert chest.total_volume == 425.0


def test_routine_detail_history_depth_limits_sessions() -> None:
    detail = routine_detail(DATA.routines[0], DATA, history_depth=1, tz=UTC)
    assert [s.date for s in detail.main[0].sessions] == ["2026-09-21"]


def test_workout_history_newest_first_with_routine_names() -> None:
    result = workout_history(DATA, days=30, today=TODAY, tz=UTC)
    assert [s.identifier for s in result.sessions] == [7000002, 7000001]
    newest, older = result.sessions
    assert (newest.date, newest.routine, newest.duration_min, newest.calories) == (
        "2026-09-21 08:00",
        "ZZ-FB — Test",
        45,
        None,
    )
    assert (older.duration_min, older.calories, older.avg_hr, older.max_hr) == (
        60,
        350,
        120,
        160,
    )


def test_workout_history_date_range_routine_filter_and_paging() -> None:
    ranged = workout_history(DATA, date_from="2026-09-20", date_to="2026-09-30", tz=UTC)
    assert [s.identifier for s in ranged.sessions] == [7000002]
    other = DATA.routines[1]
    assert workout_history(DATA, days=30, routine=other, today=TODAY, tz=UTC).total == 0
    page = workout_history(DATA, days=30, limit=1, today=TODAY, tz=UTC)
    assert (page.total, page.count, page.has_more, page.next_offset) == (2, 1, True, 1)


def test_workout_history_rejects_unsupported_preset() -> None:
    with pytest.raises(ValueError, match="days must be one of"):
        workout_history(DATA, days=9, today=TODAY, tz=UTC)


def test_equipment_owned_flags_and_weights() -> None:
    catalog = [
        CatalogEquipment(id=1, name="Barbell", category=1),
        CatalogEquipment(id=2, name="Dumbbell", category=1),
        CatalogEquipment(id=5, name="Kettlebell", category=2),
        CatalogEquipment(id=38, name="Resistance Band", category=3),
    ]
    owned = equipment(DATA, catalog)
    assert [i.id for i in owned.equipment] == [1, 2, 38]
    everything = equipment(DATA, catalog, owned_only=False)
    assert [(i.id, i.owned) for i in everything.equipment] == [
        (1, True),
        (2, True),
        (5, False),
        (38, True),
    ]
    assert (owned.dumbbell_weights, owned.kettlebell_weights) == (None, "8,12")
