"""diff.py: (current routine, desired three-section routine) → ChangeSet. Pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fakes import TZ, by_id, exercise_catalog
from smartgym_mcp.diff import (
    ChangeSet,
    DesiredExercise,
    DesiredRoutine,
    DiffError,
    ExerciseView,
    RoutineView,
    diff_routine,
    new_routine_view,
    view_of,
)
from smartgym_mcp.inputs import ExerciseSpec, RoutineSpec, SetSpec, routine_entries
from smartgym_mcp.model import Routine, RoutineExercise, Section, TemplateSet
from smartgym_mcp.payloads import encode_change

CATALOG = exercise_catalog(
    (194, "Push Up"),
    (20, "Plank"),
    (207, "Cable Chest Press"),
    (300, "Shoulder Circling"),
    (400, "Cross Arm Stretch"),
)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
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
    section: Section,
    idx: int,
    sets: list[TemplateSet],
    note: str | None = None,
) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident,
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


def _sends_nothing(change: ChangeSet, current: Routine) -> bool:
    """The change encodes to no request (an add would need the catalog and fail here)."""
    return encode_change(change, current, timezone=TZ, now=NOW) == []


def test_nothing_requested_is_empty_and_expected_equals_current() -> None:
    change = diff_routine(_routine(), DesiredRoutine(), CATALOG)
    assert _sends_nothing(change, _routine())
    assert change.expected == view_of(_routine())


def test_listing_every_section_unchanged_is_empty() -> None:
    change = diff_routine(_routine(), DesiredRoutine.model_validate(_same()), CATALOG)
    assert _sends_nothing(change, _routine())


def test_routine_fields_change_and_empty_string_clears() -> None:
    change = diff_routine(
        _routine(), DesiredRoutine(name="ZZ-R2", note="", days=[2, 4, 6]), CATALOG
    )
    assert [(c.field, c.old, c.new) for c in change.routine_changes] == [
        ("name", "ZZ-R", "ZZ-R2"),
        ("note", "old", None),
    ]
    assert (change.expected.name, change.expected.note, change.expected.days) == (
        "ZZ-R2",
        None,
        "2,4,6",
    )


def test_empty_name_rejected() -> None:
    with pytest.raises(DiffError, match="name must be non-empty"):
        diff_routine(_routine(), DesiredRoutine(name="  "), CATALOG)


def test_reorder_within_main() -> None:
    change = diff_routine(_routine(), DesiredRoutine(main=by_id(13, 11, 12)), CATALOG)
    assert change.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert not (change.added or change.removed or change.updated)


def test_omitted_from_given_section_is_removed_and_other_sections_kept() -> None:
    change = diff_routine(_routine(), DesiredRoutine(main=by_id(11, 13)), CATALOG)
    assert [(r.identifier, r.name) for r in change.removed] == [(12, "Cable Chest Press")]
    assert change.final_order is None
    assert not change.updated


def test_move_main_to_warmup_listing_only_warmup() -> None:
    change = diff_routine(_routine(), DesiredRoutine(warmup=by_id(10, 13)), CATALOG)
    (upd,) = change.updated
    assert (upd.identifier, upd.new_section) == (13, "warmup")
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("section", "main", "warmup")]
    assert change.removed == []
    assert change.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert [e.section for e in change.expected.exercises] == [
        "warmup",
        "warmup",
        "main",
        "main",
        "cooldown",
    ]


def test_move_keeping_relative_order_needs_no_order_request() -> None:
    change = diff_routine(_routine(), DesiredRoutine(warmup=by_id(10, 11)), CATALOG)
    (upd,) = change.updated
    assert (upd.identifier, upd.new_section) == (11, "warmup")
    assert change.final_order is None


def test_add_new_exercise_at_end_of_cooldown() -> None:
    plank = DesiredExercise(exercise="plank", rest_seconds=30, sets=[SetSpec(reps=30)])
    change = diff_routine(
        _routine(), DesiredRoutine(cooldown=[DesiredExercise(exercise_id=14), plank]), CATALOG
    )
    (added,) = change.added
    assert (
        added.catalog_id,
        added.name,
        added.section,
        added.position,
        added.rest_seconds,
    ) == (20, "Plank", "cooldown", 5, 30)
    assert added.exercise.id == 20  # the catalog's own exercise, for the encoder
    assert "exercise" not in added.model_dump()
    assert change.final_order is None


def test_add_at_end_of_warmup_is_mid_routine_and_needs_order() -> None:
    change = diff_routine(
        _routine(),
        DesiredRoutine(
            warmup=[DesiredExercise(exercise_id=10), DesiredExercise(exercise="Plank")]
        ),
        CATALOG,
    )
    assert change.added[0].position == 1
    assert change.final_order == ["id:10", "new:0", "id:11", "id:12", "id:13", "id:14"]


def test_new_exercise_without_sets_gets_default_set_and_warning() -> None:
    wanted = _same()
    wanted["main"].append(DesiredExercise(exercise="Plank"))
    change = diff_routine(_routine(), DesiredRoutine.model_validate(wanted), CATALOG)
    assert [(s.reps, s.weight_kg) for s in change.added[0].sets] == [(10, 0.0)]
    assert any("default" in w for w in change.warnings)


def test_duplicate_catalog_exercise_matched_by_identifier() -> None:
    working = DesiredExercise(exercise_id=12, rest_seconds=120)
    change = diff_routine(
        _routine(), DesiredRoutine.model_validate(_same(e12=working)), CATALOG
    )
    (upd,) = change.updated
    assert upd.identifier == 12
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("rest_seconds", "60", "120")]
    assert change.expected.exercises[2].rest_seconds == 120
    assert change.expected.exercises[2].sets == [(10.0, 40.0), (10.0, 40.0), (8.0, 42.5)]


def test_sets_update_add_and_remove_by_position() -> None:
    sets = [SetSpec(reps=10, weight_kg=40), SetSpec(reps=10, weight_kg=45)]
    change = diff_routine(
        _routine(),
        DesiredRoutine.model_validate(_same(e12=DesiredExercise(exercise_id=12, sets=sets))),
        CATALOG,
    )
    (upd,) = change.updated
    assert [(u.identifier, u.index, u.reps, u.weight_kg) for u in upd.updated_sets] == [
        (201, 1, 10, 45)
    ]
    assert upd.removed_set_ids == [202]
    assert upd.added_sets == []

    more = [SetSpec(reps=15), SetSpec(reps=12)]
    change = diff_routine(
        _routine(),
        DesiredRoutine.model_validate(_same(e13=DesiredExercise(exercise_id=13, sets=more))),
        CATALOG,
    )
    (upd,) = change.updated
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
    (upd,) = diff_routine(
        routine, DesiredRoutine.model_validate(_same(e12=want)), CATALOG
    ).updated
    assert [(u.identifier, u.index) for u in upd.updated_sets] == [(201, 1), (202, 2)]
    assert [a.index for a in upd.added_sets] == [3]


def test_exercise_note_change_and_clear() -> None:
    change = diff_routine(
        _routine(),
        DesiredRoutine.model_validate(
            _same(
                e13=DesiredExercise(exercise_id=13, note=""),
                e10=DesiredExercise(exercise_id=10, note="Fast"),
            )
        ),
        CATALOG,
    )
    changes = {
        u.identifier: [(c.field, c.old, c.new) for c in u.changes] for u in change.updated
    }
    assert changes == {10: [("note", None, "Fast")], 13: [("note", "Slow", None)]}


def test_empty_sets_list_rejected() -> None:
    with pytest.raises(DiffError, match="sets must not be empty"):
        diff_routine(
            _routine(),
            DesiredRoutine.model_validate(_same(e13=DesiredExercise(exercise_id=13, sets=[]))),
            CATALOG,
        )


def test_all_problems_reported_together() -> None:
    wanted = DesiredRoutine(
        warmup=by_id(11),
        main=[*by_id(999), DesiredExercise(exercise="Zercher squat"), *by_id(11)],
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
    change = diff_routine(routine, DesiredRoutine.model_validate(_same(e11=want)), CATALOG)
    assert _sends_nothing(change, routine)


def test_section_order_wins_over_inconsistent_server_indexes() -> None:
    routine = _routine()
    routine.exercises[0].index = 9  # warm-up drill numbered after main (Return routines)
    assert [e.identifier for e in routine.active_exercises()] == [10, 11, 12, 13, 14]
    change = diff_routine(routine, DesiredRoutine.model_validate(_same()), CATALOG)
    assert _sends_nothing(change, routine)


def test_append_after_remove_renumbers_so_idx_never_collides() -> None:
    plank = DesiredExercise(exercise="Plank")
    change = diff_routine(
        _routine(),
        DesiredRoutine(main=by_id(11, 13), cooldown=[DesiredExercise(exercise_id=14), plank]),
        CATALOG,
    )
    assert [r.identifier for r in change.removed] == [12]
    assert change.added[0].position == 4  # exercise 14 still has idx 4 on the server
    assert change.final_order == ["id:10", "id:11", "id:13", "id:14", "new:0"]


def test_append_to_routine_with_idx_gap_renumbers() -> None:
    routine = _routine()
    del routine.exercises[2]  # removed earlier without renumbering: idx 0, 1, 3, 4
    plank = DesiredExercise(exercise="Plank")
    change = diff_routine(
        routine, DesiredRoutine(cooldown=[DesiredExercise(exercise_id=14), plank]), CATALOG
    )
    assert change.final_order == ["id:10", "id:11", "id:13", "id:14", "new:0"]


def test_order_keeping_move_on_inconsistent_idx_needs_order_request() -> None:
    routine = _routine()
    routine.exercises[0].index = 9  # warm-up drill numbered after main (Return routines)
    change = diff_routine(routine, DesiredRoutine(warmup=by_id(10, 11)), CATALOG)
    (upd,) = change.updated
    assert (upd.identifier, upd.new_section) == (11, "warmup")
    assert change.final_order == ["id:10", "id:11", "id:12", "id:13", "id:14"]


@pytest.mark.parametrize(
    ("days", "kept"),
    [
        ([1], [1]),
        ([2, 3], [2, 3]),
        ([1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7]),
        ([6, 2, 4, 2], [2, 4, 6]),  # sorted, repeats dropped
        ([], []),
        (None, None),
    ],
)
def test_days_takes_weekday_numbers_sorted_once_each(
    days: list[int] | None, kept: list[int] | None
) -> None:
    assert DesiredRoutine.model_validate({"days": days}).days == kept
    spec = RoutineSpec.model_validate(
        {"name": "ZZ", "goal": "G", "days": days, "exercises": [{"exercise": "Plank"}]}
    )
    assert spec.days == kept


@pytest.mark.parametrize("days", [[0], [8], ["2"], [2.5], "2,4,6", "Monday", ""])
def test_days_rejects_anything_but_weekday_numbers(days: object) -> None:
    with pytest.raises(ValidationError, match="days"):
        DesiredRoutine.model_validate({"days": days})
    with pytest.raises(ValidationError, match="days"):
        RoutineSpec.model_validate(
            {"name": "ZZ", "goal": "G", "days": days, "exercises": [{"exercise": "Plank"}]}
        )


@pytest.mark.parametrize("goal", ["", "  "])
def test_blank_goal_is_rejected_on_create_and_edit(goal: str) -> None:
    with pytest.raises(ValidationError, match="app can't save a routine without one"):
        RoutineSpec(name="ZZ", goal=goal, exercises=[ExerciseSpec(exercise="Plank")])
    with pytest.raises(ValidationError, match="app can't save a routine without one"):
        DesiredRoutine(goal=goal)


def test_goal_less_routine_gets_a_goal() -> None:
    change = diff_routine(_routine(), DesiredRoutine(goal="Strength"), CATALOG)
    assert [(c.field, c.old, c.new) for c in change.routine_changes] == [
        ("goal", None, "Strength")
    ]
    assert change.expected.goal == "Strength"


def test_new_routine_view_is_what_the_create_payload_sends() -> None:
    spec = RoutineSpec(
        name="ZZ-New",
        days=[],
        goal="Strength",
        note="",
        warmup=[
            ExerciseSpec(
                exercise="Plank", rest_seconds=30, note="slow", sets=[SetSpec(reps=45.0004)]
            )
        ],
        exercises=[ExerciseSpec(exercise="Push Up")],
    )
    resolutions = [CATALOG.resolve(ex.exercise) for _, ex in routine_entries(spec)]

    # Blank optional fields are left out of the payload; omitted sets become DEFAULT_SET.
    assert new_routine_view(spec, resolutions) == RoutineView(
        name="ZZ-New",
        days=None,
        goal="Strength",
        note=None,
        exercises=[
            ExerciseView(
                catalog_id=20,
                section="warmup",
                rest_seconds=30,
                note="slow",
                sets=[(45.0, 0.0)],
            ),
            ExerciseView(
                catalog_id=194, section="main", rest_seconds=0, note=None, sets=[(10.0, 0.0)]
            ),
        ],
    )
