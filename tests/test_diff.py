"""diff.py: (current routine, desired three-section routine) → ChangeSet. Pure, no I/O."""

from __future__ import annotations

import pytest

from smartgym_mcp.diff import (
    DesiredExercise,
    DesiredRoutine,
    DiffError,
    diff_routine,
    moves_as_readd,
    view_of,
)
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog(
    [
        (194, "Push Up"),
        (20, "Plank"),
        (207, "Cable Chest Press"),
        (300, "Shoulder Circling"),
        (400, "Cross Arm Stretch"),
    ]
)
LAYOUT = {"warmup": [10], "main": [11, 12, 13], "cooldown": [14]}


def _sets(*pairs: tuple[float, float], base: int) -> list[TemplateSet]:
    return [
        TemplateSet(
            identifier=base + i,
            unique_hashid=base + i,
            index=i,
            reps=r,
            weight_kg=w,
            date_added=None,
        )
        for i, (r, w) in enumerate(pairs)
    ]


def _ex(
    ident: int,
    cat: int,
    name: str,
    section: str,
    idx: int,
    sets: list[TemplateSet],
    note: str | None = None,
) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident,
        unique_hashid=ident,
        catalog_id=cat,
        name=name,
        section=section,
        index=idx,
        rest_seconds=60,
        note=note,
        removed=False,
        template_sets=sets,
        logged_sets=[],
    )


def _routine() -> Routine:
    return Routine(
        identifier=1,
        unique_hashid=1,
        name="ZZ-R",
        days="2,4,6",
        goal=None,
        note="old",
        number=1,
        archived=False,
        removed=False,
        exercises=[
            _ex(10, 300, "Shoulder Circling", "warmup", 0, _sets((10, 0), base=100)),
            _ex(11, 207, "Cable Chest Press", "main", 1, _sets((12, 20), base=110)),
            _ex(
                12,
                207,
                "Cable Chest Press",
                "main",
                2,
                _sets((10, 40), (10, 40), (8, 42.5), base=200),
            ),
            _ex(13, 194, "Push Up", "main", 3, _sets((15, 0), base=300), note="Slow"),
            _ex(14, 400, "Cross Arm Stretch", "cooldown", 4, _sets((30, 0), base=400)),
        ],
    )


def _same(**over: DesiredExercise) -> dict[str, list[DesiredExercise]]:
    return {
        s: [over.get(f"e{i}", DesiredExercise(exercise_id=i)) for i in ids]
        for s, ids in LAYOUT.items()
    }


def _ids(*idents: int) -> list[DesiredExercise]:
    return [DesiredExercise(exercise_id=i) for i in idents]


def test_nothing_requested_is_empty_and_expected_equals_current() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(), CATALOG)
    assert cs.is_empty
    assert cs.expected == view_of(_routine())


def test_listing_every_section_unchanged_is_empty() -> None:
    assert diff_routine(_routine(), DesiredRoutine(**_same()), CATALOG).is_empty


def test_routine_fields_change_and_empty_string_clears() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(name="ZZ-R2", note="", days="2,4,6"), CATALOG)
    assert [(c.field, c.old, c.new) for c in cs.routine_changes] == [
        ("name", "ZZ-R", "ZZ-R2"),
        ("note", "old", None),
    ]
    assert (cs.expected.name, cs.expected.note, cs.expected.days) == ("ZZ-R2", None, "2,4,6")


def test_empty_name_rejected() -> None:
    with pytest.raises(DiffError, match="name must be non-empty"):
        diff_routine(_routine(), DesiredRoutine(name="  "), CATALOG)


def test_reorder_within_main() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(13, 11, 12)), CATALOG)
    assert cs.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert not (cs.added or cs.removed or cs.updated)


def test_omitted_from_given_section_is_removed_and_other_sections_kept() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(11, 13)), CATALOG)
    assert [(r.identifier, r.name) for r in cs.removed] == [(12, "Cable Chest Press")]
    assert cs.final_order is None
    assert not cs.updated


def test_move_main_to_warmup_listing_only_warmup() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 13)), CATALOG)
    (upd,) = cs.updated
    assert (upd.identifier, upd.new_section) == (13, "warmup")
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("section", "main", "warmup")]
    assert cs.removed == []
    assert cs.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert [e.section for e in cs.expected.exercises] == [
        "warmup",
        "warmup",
        "main",
        "main",
        "cooldown",
    ]


def test_move_keeping_relative_order_needs_no_order_request() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 11)), CATALOG)
    (upd,) = cs.updated
    assert (upd.identifier, upd.new_section) == (11, "warmup")
    assert cs.final_order is None


def test_add_new_exercise_at_end_of_cooldown() -> None:
    plank = DesiredExercise(exercise="plank", rest_seconds=30, sets=[SetSpec(reps=30)])
    cs = diff_routine(
        _routine(), DesiredRoutine(cooldown=[DesiredExercise(exercise_id=14), plank]), CATALOG
    )
    (added,) = cs.added
    assert (
        added.catalog_id,
        added.name,
        added.section,
        added.position,
        added.rest_seconds,
    ) == (20, "Plank", "cooldown", 5, 30)
    assert cs.final_order is None


def test_add_at_end_of_warmup_is_mid_routine_and_needs_order() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(
            warmup=[DesiredExercise(exercise_id=10), DesiredExercise(exercise="Plank")]
        ),
        CATALOG,
    )
    assert cs.added[0].position == 1
    assert cs.final_order == ["id:10", "new:0", "id:11", "id:12", "id:13", "id:14"]


def test_new_exercise_without_sets_gets_default_set_and_warning() -> None:
    wanted = _same()
    wanted["main"].append(DesiredExercise(exercise="Plank"))
    cs = diff_routine(_routine(), DesiredRoutine(**wanted), CATALOG)
    assert [(s.reps, s.weight_kg) for s in cs.added[0].sets] == [(10, 0.0)]
    assert any("default" in w for w in cs.warnings)


def test_duplicate_catalog_exercise_matched_by_identifier() -> None:
    working = DesiredExercise(exercise_id=12, rest_seconds=120)
    cs = diff_routine(_routine(), DesiredRoutine(**_same(e12=working)), CATALOG)
    (upd,) = cs.updated
    assert upd.identifier == 12
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("rest_seconds", "60", "120")]
    assert cs.expected.exercises[2].rest_seconds == 120
    assert cs.expected.exercises[2].sets == [(10.0, 40.0), (10.0, 40.0), (8.0, 42.5)]


def test_sets_update_add_and_remove_by_position() -> None:
    sets = [SetSpec(reps=10, weight_kg=40), SetSpec(reps=10, weight_kg=45)]
    cs = diff_routine(
        _routine(),
        DesiredRoutine(**_same(e12=DesiredExercise(exercise_id=12, sets=sets))),
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
        DesiredRoutine(**_same(e13=DesiredExercise(exercise_id=13, sets=more))),
        CATALOG,
    )
    (upd,) = cs.updated
    assert [(a.index, a.reps) for a in upd.added_sets] == [(1, 12)]
    assert upd.updated_sets == [] and upd.removed_set_ids == []


def test_set_index_gaps_are_renumbered_so_added_sets_never_collide() -> None:
    routine = _routine()
    routine.exercises[2].template_sets = [
        TemplateSet(
            identifier=200 + i,
            unique_hashid=200 + i,
            index=idx,
            reps=10,
            weight_kg=40,
            date_added=None,
        )
        for i, idx in enumerate((0, 2, 3))
    ]
    want = DesiredExercise(exercise_id=12, sets=[SetSpec(reps=10, weight_kg=40)] * 4)
    (upd,) = diff_routine(routine, DesiredRoutine(**_same(e12=want)), CATALOG).updated
    assert [(u.identifier, u.index) for u in upd.updated_sets] == [(201, 1), (202, 2)]
    assert [a.index for a in upd.added_sets] == [3]


def test_exercise_note_change_and_clear() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(
            **_same(
                e13=DesiredExercise(exercise_id=13, note=""),
                e10=DesiredExercise(exercise_id=10, note="Fast"),
            )
        ),
        CATALOG,
    )
    changes = {u.identifier: [(c.field, c.old, c.new) for c in u.changes] for u in cs.updated}
    assert changes == {10: [("note", None, "Fast")], 13: [("note", "Slow", None)]}


def test_empty_sets_list_rejected() -> None:
    with pytest.raises(DiffError, match="sets must not be empty"):
        diff_routine(
            _routine(),
            DesiredRoutine(**_same(e13=DesiredExercise(exercise_id=13, sets=[]))),
            CATALOG,
        )


def test_all_problems_reported_together() -> None:
    wanted = DesiredRoutine(
        warmup=_ids(11),
        main=[*_ids(999), DesiredExercise(exercise="Zercher squat"), *_ids(11)],
    )
    with pytest.raises(DiffError) as exc:
        diff_routine(_routine(), wanted, CATALOG)
    msg = str(exc.value)
    assert "999" in msg and "Zercher" in msg and "more than once" in msg


def test_empty_routine_rejected() -> None:
    with pytest.raises(DiffError, match="at least one exercise"):
        diff_routine(_routine(), DesiredRoutine(warmup=[], main=[], cooldown=[]), CATALOG)


def test_desired_exercise_needs_exactly_one_reference() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise()
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise(exercise_id=1, exercise="Plank")


def test_float_noise_from_server_is_not_a_change() -> None:
    routine = _routine()
    routine.exercises[1].template_sets = _sets((12.300000190734863, 20), base=110)
    want = DesiredExercise(exercise_id=11, sets=[SetSpec(reps=12.3, weight_kg=20)])
    assert diff_routine(routine, DesiredRoutine(**_same(e11=want)), CATALOG).is_empty


def test_section_order_wins_over_inconsistent_server_indexes() -> None:
    routine = _routine()
    routine.exercises[0].index = 9  # warm-up drill numbered after main (Return routines)
    assert [e.identifier for e in routine.active_exercises()] == [10, 11, 12, 13, 14]
    assert diff_routine(routine, DesiredRoutine(**_same()), CATALOG).is_empty


def test_append_after_remove_renumbers_so_idx_never_collides() -> None:
    plank = DesiredExercise(exercise="Plank")
    cs = diff_routine(
        _routine(),
        DesiredRoutine(main=_ids(11, 13), cooldown=[DesiredExercise(exercise_id=14), plank]),
        CATALOG,
    )
    assert [r.identifier for r in cs.removed] == [12]
    assert cs.added[0].position == 4  # exercise 14 still has idx 4 on the server
    assert cs.final_order == ["id:10", "id:11", "id:13", "id:14", "new:0"]


def test_append_to_routine_with_idx_gap_renumbers() -> None:
    routine = _routine()
    del routine.exercises[2]  # removed earlier without renumbering: idx 0, 1, 3, 4
    plank = DesiredExercise(exercise="Plank")
    cs = diff_routine(
        routine, DesiredRoutine(cooldown=[DesiredExercise(exercise_id=14), plank]), CATALOG
    )
    assert cs.final_order == ["id:10", "id:11", "id:13", "id:14", "new:0"]


def test_order_keeping_move_on_inconsistent_idx_needs_order_request() -> None:
    routine = _routine()
    routine.exercises[0].index = 9  # warm-up drill numbered after main (Return routines)
    cs = diff_routine(routine, DesiredRoutine(warmup=_ids(10, 11)), CATALOG)
    (upd,) = cs.updated
    assert (upd.identifier, upd.new_section) == (11, "warmup")
    assert cs.final_order == ["id:10", "id:11", "id:12", "id:13", "id:14"]


def test_moves_as_readd_turns_a_move_into_remove_plus_add() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 13)), CATALOG)
    fallback = moves_as_readd(cs, _routine())
    assert [r.identifier for r in fallback.removed] == [13]
    (added,) = fallback.added
    assert (added.catalog_id, added.section, added.position, added.note) == (
        194,
        "warmup",
        1,
        "Slow",
    )
    assert [(s.reps, s.weight_kg) for s in added.sets] == [(15, 0)]
    assert fallback.updated == []
    assert fallback.final_order == ["id:10", "new:0", "id:11", "id:12", "id:14"]
    assert any("re-add" in w for w in fallback.warnings)
    assert fallback.expected == cs.expected


def test_moves_as_readd_keeps_other_edits_of_the_moved_exercise() -> None:
    moved = DesiredExercise(exercise_id=13, rest_seconds=90, sets=[SetSpec(reps=20)])
    cs = diff_routine(_routine(), DesiredRoutine(cooldown=[*_ids(14), moved]), CATALOG)
    fallback = moves_as_readd(cs, _routine())
    (added,) = fallback.added
    assert (added.section, added.position, added.rest_seconds) == ("cooldown", 4, 90)
    assert [(s.reps, s.weight_kg) for s in added.sets] == [(20, 0)]
    assert fallback.updated == []


def test_moves_as_readd_is_identity_without_moves() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(13, 11, 12)), CATALOG)
    assert moves_as_readd(cs, _routine()) == cs
