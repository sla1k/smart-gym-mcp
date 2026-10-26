"""api/reads.py — each read GETs its endpoint and returns the body as the client gave it."""

from __future__ import annotations

import json

from fakes import ACCOUNT, CUSTOM_EXERCISES, FIX, NO_CUSTOM_EXERCISES, FakeClient
from smartgym_mcp.api import reads

SINGLE_BODY = json.loads((FIX / "routine_single_synthetic.json").read_text(encoding="utf-8"))


def test_history_all_reads_the_users_history() -> None:
    client = FakeClient({"history/all/77/": ACCOUNT}, user_id="77")
    assert reads.history_all(client) == ACCOUNT
    assert client.get_calls == ["history/all/77/"]


def test_custom_exercises_reads_the_account_list() -> None:
    client = FakeClient({CUSTOM_EXERCISES: NO_CUSTOM_EXERCISES})
    assert reads.custom_exercises(client) == NO_CUSTOM_EXERCISES
    assert client.get_calls == [CUSTOM_EXERCISES]


def test_routine_single_reads_fresh_each_time() -> None:
    client = FakeClient({"routine/single/3000001/": SINGLE_BODY})
    assert reads.routine_single(client, 3000001) == SINGLE_BODY
    reads.routine_single(client, 3000001)
    assert client.get_calls == ["routine/single/3000001/"] * 2
