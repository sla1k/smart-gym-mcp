"""matching.py: deterministic name → catalog id resolution (no DB)."""

from __future__ import annotations

import pytest

from smartgym_mcp.matching import ExerciseCatalog, UnresolvedExercise

CATALOG = ExerciseCatalog(
    [(1, "Dumbbell Bench Press"), (2, "Barbell Squat"), (194, "Push Up"), (20, "Plank")]
)


def test_exact_match_is_case_insensitive() -> None:
    res = CATALOG.resolve("push up")
    assert (res.catalog_id, res.resolved_name, res.fuzzy) == (194, "Push Up", False)


def test_numeric_ref_resolves_by_catalog_id() -> None:
    assert CATALOG.resolve("20").resolved_name == "Plank"
    with pytest.raises(UnresolvedExercise, match="999"):
        CATALOG.resolve("999")


def test_alias_expansion_gives_a_fuzzy_match() -> None:
    res = CATALOG.resolve("db bench press")
    assert (res.catalog_id, res.fuzzy) == (1, True)


def test_unresolvable_lists_candidates() -> None:
    with pytest.raises(UnresolvedExercise, match="Closest"):
        CATALOG.resolve("Zercher carry")


def test_a_name_shared_by_several_exercises_is_rejected_with_their_ids() -> None:
    catalog = ExerciseCatalog([(194, "Push Up"), (419950, "Push Up"), (20, "Plank")])
    with pytest.raises(UnresolvedExercise, match=r"id=194.*id=419950"):
        catalog.resolve("push up")
    with pytest.raises(UnresolvedExercise, match="several exercises"):
        catalog.resolve("pushup")  # fuzzy hit on the shared name
    assert catalog.resolve("419950").catalog_id == 419950


def test_extended_catalog_resolves_old_and_new_entries() -> None:
    from smartgym_mcp.catalog import CatalogExercise

    custom = CatalogExercise(
        id=397744,
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
    extended = CATALOG.extended([custom])
    assert extended.resolve("dead hang").catalog_id == 397744
    assert extended.resolve("Plank").catalog_id == 20
    with pytest.raises(UnresolvedExercise):
        CATALOG.resolve("dead hang")
