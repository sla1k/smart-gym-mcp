"""diff.py: (current routine, desired routine) → ChangeSet. Pure, no I/O."""

from __future__ import annotations

import pytest

from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, DiffError, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog([(194, "Push Up"), (20, "Plank"), (207, "Cable Chest Press")])


def _sets(*pairs: tuple[float, float], base: int) -> list[TemplateSet]:
    return [
        TemplateSet(identifier=base + i, unique_hashid=base + i, index=i, reps=r, weight_kg=w)
        for i, (r, w) in enumerate(pairs)
    ]


def _routine() -> Routine:
    def ex(
        ident: int, cat: int, name: str, idx: int, sets: list[TemplateSet]
    ) -> RoutineExercise:
        return RoutineExercise(
            identifier=ident,
            unique_hashid=ident,
            catalog_id=cat,
            name=name,
            index=idx,
            rest_seconds=60,
            note=None,
            removed=False,
            template_sets=sets,
        )

    return Routine(
        identifier=1,
        unique_hashid=1,
        name="ZZ-R",
        days="0001",
        goal=None,
        note="old",
        archived=False,
        removed=False,
        exercises=[
            ex(11, 207, "Cable Chest Press", 0, _sets((12, 20), base=100)),  # warm-up
            ex(
                12, 207, "Cable Chest Press", 1, _sets((10, 40), (10, 40), (8, 42.5), base=200)
            ),
            ex(13, 194, "Push Up", 2, _sets((15, 0), base=300)),
        ],
    )


def _keep_all(**overrides: DesiredExercise) -> list[DesiredExercise]:
    return [overrides.get(str(i), DesiredExercise(exercise_id=i)) for i in (11, 12, 13)]


def test_nothing_requested_is_empty() -> None:
    assert diff_routine(_routine(), DesiredRoutine(), CATALOG).is_empty


def test_routine_fields_change_and_empty_string_clears() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(name="ZZ-R2", note="", days="0001"), CATALOG)
    assert [(c.field, c.old, c.new) for c in cs.routine_changes] == [
        ("name", "ZZ-R", "ZZ-R2"),
        ("note", "old", None),
    ]


def test_empty_name_rejected() -> None:
    with pytest.raises(DiffError, match="name must be non-empty"):
        diff_routine(_routine(), DesiredRoutine(name="  "), CATALOG)


def test_reorder_only() -> None:
    order = [DesiredExercise(exercise_id=i) for i in (13, 11, 12)]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=order), CATALOG)
    assert cs.final_order == ["id:13", "id:11", "id:12"]
    assert not (cs.added or cs.removed or cs.updated)


def test_omitted_exercise_is_removed_without_reorder() -> None:
    keep = [DesiredExercise(exercise_id=11), DesiredExercise(exercise_id=13)]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=keep), CATALOG)
    assert [(r.identifier, r.name) for r in cs.removed] == [(12, "Cable Chest Press")]
    assert cs.final_order is None


def test_add_new_exercise_by_name_at_position() -> None:
    wanted = _keep_all()
    wanted.insert(
        1, DesiredExercise(exercise="plank", rest_seconds=45, sets=[SetSpec(reps=30)])
    )
    cs = diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    (added,) = cs.added
    assert (added.catalog_id, added.name, added.position, added.rest_seconds) == (
        20,
        "Plank",
        1,
        45,
    )
    assert cs.final_order == ["id:11", "new:0", "id:12", "id:13"]


def test_new_exercise_without_sets_gets_default_set_and_warning() -> None:
    wanted = [*_keep_all(), DesiredExercise(exercise="Plank")]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    assert [(s.reps, s.weight_kg) for s in cs.added[0].sets] == [(10, 0.0)]
    assert any("default" in w for w in cs.warnings)


def test_duplicate_catalog_exercise_matched_by_identifier() -> None:
    working = DesiredExercise(exercise_id=12, rest_seconds=120)
    cs = diff_routine(
        _routine(), DesiredRoutine(exercises=_keep_all(**{"12": working})), CATALOG
    )
    (upd,) = cs.updated
    assert upd.identifier == 12
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("rest_seconds", "60", "120")]


def test_sets_update_add_and_remove_by_position() -> None:
    sets = [SetSpec(reps=10, weight_kg=40), SetSpec(reps=10, weight_kg=45)]
    cs = diff_routine(
        _routine(),
        DesiredRoutine(
            exercises=_keep_all(**{"12": DesiredExercise(exercise_id=12, sets=sets)})
        ),
        CATALOG,
    )
    (upd,) = cs.updated
    assert [(u.identifier, u.index, u.reps, u.weight_kg) for u in upd.updated_sets] == [
        (201, 1, 10, 45)
    ]
    assert upd.removed_set_ids == [202]
    assert upd.added_sets == []

    more = [SetSpec(reps=15), SetSpec(reps=12)]
    cs = diff_routine(
        _routine(),
        DesiredRoutine(
            exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, sets=more)})
        ),
        CATALOG,
    )
    (upd,) = cs.updated
    assert [(a.index, a.reps) for a in upd.added_sets] == [(1, 12)]
    assert upd.updated_sets == [] and upd.removed_set_ids == []


def test_exercise_note_change_and_clear() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(
            exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, note="Slow")})
        ),
        CATALOG,
    )
    assert [(c.field, c.old, c.new) for c in cs.updated[0].changes] == [("note", None, "Slow")]


def test_empty_sets_list_rejected() -> None:
    with pytest.raises(DiffError, match="sets must not be empty"):
        diff_routine(
            _routine(),
            DesiredRoutine(
                exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, sets=[])})
            ),
            CATALOG,
        )


def test_all_problems_reported_together() -> None:
    wanted = [
        DesiredExercise(exercise_id=999),
        DesiredExercise(exercise="Zercher squat"),
        DesiredExercise(exercise_id=11),
        DesiredExercise(exercise_id=11),
    ]
    with pytest.raises(DiffError) as exc:
        diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    msg = str(exc.value)
    assert "999" in msg and "Zercher" in msg and "more than once" in msg


def test_desired_exercise_needs_exactly_one_reference() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise()
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise(exercise_id=1, exercise="Plank")


def test_set_index_gaps_are_renumbered_so_added_sets_never_collide() -> None:
    routine = _routine()
    gappy = [
        TemplateSet(
            identifier=200 + i, unique_hashid=200 + i, index=idx, reps=10, weight_kg=40
        )
        for i, idx in enumerate((0, 2, 3))
    ]
    routine.exercises[1].template_sets = gappy
    sets = [SetSpec(reps=10, weight_kg=40)] * 4
    wanted = _keep_all(**{"12": DesiredExercise(exercise_id=12, sets=sets)})
    (upd,) = diff_routine(routine, DesiredRoutine(exercises=wanted), CATALOG).updated
    assert [(u.identifier, u.index) for u in upd.updated_sets] == [(201, 1), (202, 2)]
    assert [a.index for a in upd.added_sets] == [3]
