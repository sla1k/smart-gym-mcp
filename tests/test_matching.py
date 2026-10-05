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
