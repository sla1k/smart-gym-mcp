"""api/store.py on a fake client — cache, resolution, fresh routine fetches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import FakeClient
from smartgym_mcp.api.store import AccountStore, AmbiguousRoutine, RoutineNotFound
from smartgym_mcp.model import ApiPayloadError

FIX = Path(__file__).parent / "fixtures" / "api"
ACCOUNT = json.loads((FIX / "history_all_synthetic.json").read_text(encoding="utf-8"))
SINGLE = json.loads((FIX / "routine_single_synthetic.json").read_text(encoding="utf-8"))
HISTORY = "history/all/1/"


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


def test_resolve_by_id_exact_name_and_substring() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    assert store.resolve("3000001").name == "ZZ-FB — Test"
    assert store.resolve("zz-fb — test").identifier == 3000001
    assert store.resolve("Old").identifier == 3000002  # archived routines resolve too


def test_resolve_excludes_removed_and_reports_ambiguity_with_ids() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    with pytest.raises(RoutineNotFound):
        store.resolve("ZZ-Gone")
    with pytest.raises(AmbiguousRoutine, match="3000001.*3000002"):
        store.resolve("ZZ-")


def test_routine_raw_is_fetched_fresh_each_time() -> None:
    client = FakeClient({"routine/single/3000001/": SINGLE})
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
