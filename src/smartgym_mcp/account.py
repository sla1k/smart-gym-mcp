"""Account reads over the SmartGym API and routine lookups in them (API-client spec §5, §11).

`history/all/<id>/` returns every routine, workout and equipment list in one answer (S4);
`exercise/allCustomExercises/` the account's custom exercises. AccountCache keeps both for
one tool call (`AppContext.use` clears it as each call starts) and drops them after every
write, so a write's verifying re-read sees the server's answer. Writes always work on a
fresh `routine/single/<id>/` (`fetch_routine`). The lookups are pure functions over the
parsed AccountData.
"""

from __future__ import annotations

from typing import Any

from .api.client import ApiCalls
from .catalog import CatalogExercise
from .model import AccountData, Routine, parse_custom_exercises, parse_history_all


class RoutineNotFound(ValueError):
    pass


class AccountCache:
    """Not thread-safe: the server runs tool calls one at a time (DESIGN invariant 7)."""

    def __init__(self, client: ApiCalls) -> None:
        self._client = client
        self._account: AccountData | None = None
        self._customs: list[CatalogExercise] | None = None

    def account(self) -> AccountData:
        if self._account is None:
            self._account = parse_history_all(
                self._client.get(f"history/all/{self._client.user_id}/")
            )
        return self._account

    def custom_exercises(self) -> list[CatalogExercise]:
        if self._customs is None:
            self._customs = parse_custom_exercises(
                self._client.get("exercise/allCustomExercises/")
            )
        return self._customs

    def invalidate(self) -> None:
        self._account = self._customs = None


def fetch_routine(client: ApiCalls, identifier: int) -> dict[str, Any]:
    """The routine's JSON from `routine/single/<id>/`, never cached."""
    body = client.get(f"routine/single/{identifier}/")
    routines = body.get("routines") or []
    if not routines:
        raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
    return dict(routines[0])


def find_routine(account: AccountData, ref: str | int) -> Routine:
    """The routine `ref` names: its id, its exact name, or a unique part of its name
    (case-insensitive; archived routines included, removed ones not)."""
    live = [r for r in account.routines if not r.removed]
    s = str(ref).strip()
    if s.isdigit():
        for r in live:
            if r.identifier == int(s):
                return r
        raise RoutineNotFound(f"No routine with id {s}. Use smartgym_list_routines.")
    exact = [r for r in live if r.name.lower() == s.lower()]
    matches = exact or [r for r in live if s.lower() in r.name.lower()]
    if not matches:
        raise RoutineNotFound(
            f"No routine matching {ref!r}. Use smartgym_list_routines to see names."
        )
    if len(matches) > 1:
        listed = ", ".join(
            f"{r.name} (id={r.identifier}{', archived' if r.archived else ''})"
            for r in matches
        )
        raise ValueError(
            f"Multiple routines match {ref!r}: {listed}. Pass the id to disambiguate."
        )
    return matches[0]


def routine_of_exercise(account: AccountData, exercise_id: int) -> Routine:
    """The live routine holding routine exercise `exercise_id`."""
    for r in account.routines:
        if not r.removed and any(
            e.identifier == exercise_id and not e.removed for e in r.exercises
        ):
            return r
    raise RoutineNotFound(
        f"No routine contains exercise {exercise_id}. "
        "Use smartgym_get_routine to see exercise ids."
    )
