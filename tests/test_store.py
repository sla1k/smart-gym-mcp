"""api/store.py on a fake client — cache, resolution, fresh routine fetches."""

from __future__ import annotations

import json

import pytest

from fakes import ACCOUNT, CUSTOM_EXERCISES, FIX, HISTORY, FakeClient
from smartgym_mcp.api.store import AccountStore, AmbiguousRoutine, RoutineNotFound
from smartgym_mcp.model import ApiPayloadError

SINGLE_BODY = json.loads((FIX / "routine_single_synthetic.json").read_text(encoding="utf-8"))


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _store(client: FakeClient, clock: Clock | None = None) -> AccountStore:
    return AccountStore(client, ttl_s=30, clock=clock or Clock())


def test_data_is_cached_until_ttl_or_invalidate() -> None:
    client, clock = FakeClient({HISTORY: ACCOUNT}), Clock()
    store = _store(client, clock)
    store.data()
    store.data()
    assert client.get_calls == [HISTORY]
    clock.t = 31
    store.data()
    store.invalidate()
    store.data()
    assert client.get_calls == [HISTORY] * 3


def test_custom_exercises_are_cached_until_ttl_or_invalidate() -> None:
    customs = json.loads((FIX / "all_custom_exercises.json").read_text(encoding="utf-8"))
    client, clock = FakeClient({CUSTOM_EXERCISES: customs["response"]}), Clock()
    store = _store(client, clock)
    assert [c.name for c in store.custom_exercises()] == ["Dead Hang"]
    store.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES]
    clock.t = 31
    store.custom_exercises()
    store.invalidate()
    store.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES] * 3


def test_resolve_by_id_exact_name_and_substring() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    assert store.resolve("3000001").name == "ZZ-FB — Test"
    assert store.resolve("zz-fb — test").identifier == 3000001
    assert store.resolve("Old").identifier == 3000002  # archived routines resolve too


def test_resolve_excludes_removed_and_reports_ambiguity_with_ids() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    with pytest.raises(RoutineNotFound):
        store.resolve("ZZ-Gone")
    with pytest.raises(AmbiguousRoutine, match=r"3000001.*3000002"):
        store.resolve("ZZ-")


def test_routine_raw_is_fetched_fresh_each_time() -> None:
    client = FakeClient({"routine/single/3000001/": SINGLE_BODY})
    store = _store(client)
    assert store.routine_raw(3000001)["identifier"] == "3000001"
    store.routine_raw(3000001)
    assert client.get_calls == ["routine/single/3000001/"] * 2


def test_routine_raw_missing_routine() -> None:
    store = _store(FakeClient({"routine/single/9/": {"code": "SUCCESS", "routines": []}}))
    with pytest.raises(RoutineNotFound, match="9"):
        store.routine_raw(9)


def test_routine_of_exercise() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    assert store.routine_of_exercise(40000012).identifier == 3000001
    with pytest.raises(RoutineNotFound):
        store.routine_of_exercise(40000013)  # removed exercise


def test_partial_history_is_an_error() -> None:
    store = _store(FakeClient({HISTORY: {**ACCOUNT, "hasMore": True}}))
    with pytest.raises(ApiPayloadError):
        store.data()
