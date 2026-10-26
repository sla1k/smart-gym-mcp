"""cache.py — ReadCache keeps the parsed reads until invalidate(), on a fake client."""

from __future__ import annotations

import json

import pytest

from fakes import ACCOUNT, CUSTOM_EXERCISES, FIX, HISTORY, FakeClient
from smartgym_mcp.cache import ReadCache
from smartgym_mcp.model import ApiPayloadError


def test_account_is_cached_until_invalidate() -> None:
    client = FakeClient({HISTORY: ACCOUNT})
    cache = ReadCache(client)
    cache.account()
    cache.account()
    assert client.get_calls == [HISTORY]
    cache.invalidate()
    cache.account()
    assert client.get_calls == [HISTORY] * 2


def test_custom_exercises_are_cached_until_invalidate() -> None:
    customs = json.loads((FIX / "all_custom_exercises.json").read_text(encoding="utf-8"))
    client = FakeClient({CUSTOM_EXERCISES: customs["response"]})
    cache = ReadCache(client)
    assert [c.name for c in cache.custom_exercises()] == ["Dead Hang"]
    cache.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES]
    cache.invalidate()
    cache.custom_exercises()
    assert client.get_calls == [CUSTOM_EXERCISES] * 2


def test_partial_history_is_an_error() -> None:
    cache = ReadCache(FakeClient({HISTORY: {**ACCOUNT, "hasMore": True}}))
    with pytest.raises(ApiPayloadError):
        cache.account()
