"""builders.py: single-field tool inputs → DesiredRoutine (thin wrappers over apply)."""

from __future__ import annotations

import pytest

from smartgym_mcp import builders
from smartgym_mcp.diff import DiffError, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog(
    [(300, "Shoulder Circling"), (207, "Cable Chest Press"), (20, "Plank")]
)


def _ex(ident: int, section: str, idx: int) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident,
        unique_hashid=ident,
        catalog_id=207,
        name=f"Ex {ident}",
        section=section,
        index=idx,
        rest_seconds=60,
        note=None,
        removed=False,
        template_sets=[],
        logged_sets=[],
    )


ROUTINE = Routine(
    identifier=1,
    unique_hashid=1,
    name="ZZ-B",
    days=None,
    goal=None,
    note=None,
    number=1,
    archived=False,
    removed=False,
    exercises=[
        _ex(10, "warmup", 0),
        _ex(11, "main", 1),
        _ex(12, "main", 2),
        _ex(14, "cooldown", 3),
    ],
)


def _ids(items: list | None) -> list:  # type: ignore[type-arg]
    return [d.exercise_id or d.exercise for d in items] if items is not None else None  # type: ignore[return-value]


def test_add_exercise_into_section_at_position() -> None:
    d = builders.add_exercise(
        ROUTINE,
        "Plank",
        section="warmup",
        position=0,
        rest_seconds=30,
        note=None,
        sets=[SetSpec(reps=30)],
    )
    assert _ids(d.warmup) == ["Plank", 10]
    assert d.main is None and d.cooldown is None
    assert d.warmup[0].rest_seconds == 30  # type: ignore[index]


def test_add_exercise_defaults_to_end() -> None:
    d = builders.add_exercise(
        ROUTINE,
        "Plank",
        section="main",
        position=None,
        rest_seconds=None,
        note=None,
        sets=None,
    )
    assert _ids(d.main) == [11, 12, "Plank"]


def test_move_between_sections_lists_both() -> None:
    d = builders.move_exercise(ROUTINE, 11, section="cooldown", position=0)
    assert (_ids(d.cooldown), _ids(d.main), d.warmup) == ([11, 14], [12], None)
    (upd,) = diff_routine(ROUTINE, d, CATALOG).updated
    assert (upd.identifier, upd.new_section) == (11, "cooldown")


def test_move_within_section_reorders() -> None:
    d = builders.move_exercise(ROUTINE, 12, section="main", position=0)
    assert (_ids(d.main), d.warmup, d.cooldown) == ([12, 11], None, None)


def test_remove_exercise_lists_its_section_without_it() -> None:
    d = builders.remove_exercise(ROUTINE, 12)
    assert (_ids(d.main), d.warmup, d.cooldown) == ([11], None, None)


def test_update_exercise_carries_only_given_fields() -> None:
    d = builders.update_exercise(ROUTINE, 11, rest_seconds=90, note=None, sets=None)
    first, second = d.main  # type: ignore[misc]
    assert (first.exercise_id, first.rest_seconds, second.rest_seconds) == (11, 90, None)


def test_update_exercise_needs_a_field() -> None:
    with pytest.raises(DiffError, match="at least one"):
        builders.update_exercise(ROUTINE, 11, rest_seconds=None, note=None, sets=None)


def test_unknown_exercise_is_rejected() -> None:
    with pytest.raises(DiffError, match="999"):
        builders.remove_exercise(ROUTINE, 999)


def test_reorder_must_keep_section_members() -> None:
    assert _ids(builders.reorder(ROUTINE, warmup=None, main=[12, 11], cooldown=None).main) == [
        12,
        11,
    ]
    with pytest.raises(DiffError, match="smartgym_move_exercise"):
        builders.reorder(ROUTINE, warmup=None, main=[12, 11, 14], cooldown=None)
    with pytest.raises(DiffError, match="at least one section"):
        builders.reorder(ROUTINE, warmup=None, main=None, cooldown=None)


def test_update_routine_needs_a_field() -> None:
    assert builders.update_routine(name=None, days="1,3", goal=None, note=None).days == "1,3"
    with pytest.raises(DiffError, match="at least one"):
        builders.update_routine(name=None, days=None, goal=None, note=None)
