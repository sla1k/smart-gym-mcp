"""lookups.py — routine lookups on parsed account data, no client."""

from __future__ import annotations

import copy

import pytest

from fakes import ACCOUNT
from smartgym_mcp.lookups import RoutineNotFound, find_routine, routine_of_exercise
from smartgym_mcp.model import parse_history_all

DATA = parse_history_all(copy.deepcopy(ACCOUNT))


def test_find_routine_by_id_exact_name_and_substring() -> None:
    assert find_routine(DATA, "3000001").name == "ZZ-FB — Test"
    assert find_routine(DATA, 3000001).name == "ZZ-FB — Test"
    assert find_routine(DATA, "zz-fb — test").identifier == 3000001
    assert find_routine(DATA, "Old").identifier == 3000002  # archived routines resolve too


def test_find_routine_excludes_removed_and_reports_ambiguity_with_ids() -> None:
    with pytest.raises(RoutineNotFound):
        find_routine(DATA, "ZZ-Gone")
    with pytest.raises(ValueError, match=r"Multiple routines.*3000001.*3000002"):
        find_routine(DATA, "ZZ-")


def test_routine_of_exercise() -> None:
    assert routine_of_exercise(DATA, 40000012).identifier == 3000001
    with pytest.raises(RoutineNotFound):
        routine_of_exercise(DATA, 40000013)  # removed exercise
