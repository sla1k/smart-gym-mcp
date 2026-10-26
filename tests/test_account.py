"""account.py — the per-call cache on a fake client, fresh routine fetches, pure lookups."""

from __future__ import annotations

import copy
import json

import pytest

from fakes import ACCOUNT, CUSTOM_EXERCISES, FIX, HISTORY, FakeClient
from smartgym_mcp.account import (
    AccountCache,
    RoutineNotFound,
    fetch_routine,
    find_routine,
    routine_of_exercise,
)
from smartgym_mcp.model import ApiPayloadError, parse_history_all

SINGLE_BODY = json.loads((FIX / "routine_single_synthetic.json").read_text(encoding="utf-8"))
DATA = parse_history_all(copy.deepcopy(ACCOUNT))


def test_account_is_cached_until_invalidate() -> None:
    client = FakeClient({HISTORY: ACCOUNT})
    cache = AccountCache(client)
    cache.account()
    cache.account()
    assert client.get_calls == [HISTORY]
    cache.invalidate()
    cache.account()
    assert client.get_calls == [HISTORY] * 2


def test_custom_exercises_are_cached_until_invalidate() -> None:
    customs = json.loads((FIX / "all_custom_exercises.json").read_text(encoding="utf-8"))
    client = FakeClient({CUSTOM_EXERCISES: customs["response"]})
    cache = AccountCache(client)
    assert [c.name for c in cache.custom_exercises()] == ["Dead Hang"]
    cache.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES]
    cache.invalidate()
    cache.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES] * 2


def test_partial_history_is_an_error() -> None:
    cache = AccountCache(FakeClient({HISTORY: {**ACCOUNT, "hasMore": True}}))
    with pytest.raises(ApiPayloadError):
        cache.account()


def test_fetch_routine_reads_fresh_each_time() -> None:
    client = FakeClient({"routine/single/3000001/": SINGLE_BODY})
    assert fetch_routine(client, 3000001)["identifier"] == "3000001"
    fetch_routine(client, 3000001)
    assert client.get_calls == ["routine/single/3000001/"] * 2


def test_fetch_routine_missing_routine() -> None:
    client = FakeClient({"routine/single/9/": {"code": "SUCCESS", "routines": []}})
    with pytest.raises(RoutineNotFound, match="9"):
        fetch_routine(client, 9)


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
