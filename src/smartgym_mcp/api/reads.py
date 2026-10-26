"""The SmartGym read endpoints (API-client spec §5, §11): which path to GET, raw JSON back.

`history/all/<id>/` returns every routine, workout and equipment list in one answer (S4);
`exercise/allCustomExercises/` the account's custom exercises; `routine/single/<id>/` one
routine (its `routines` list is empty when the id names none). Parsing lives in model.py.
"""

from __future__ import annotations

from typing import Any

from .client import ApiCalls


def history_all(client: ApiCalls) -> dict[str, Any]:
    return client.get(f"history/all/{client.user_id}/")


def custom_exercises(client: ApiCalls) -> dict[str, Any]:
    return client.get("exercise/allCustomExercises/")


def routine_single(client: ApiCalls, identifier: int) -> dict[str, Any]:
    return client.get(f"routine/single/{identifier}/")
