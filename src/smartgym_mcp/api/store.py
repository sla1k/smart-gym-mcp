"""Account reads over the SmartGym API (API-client spec §5, §11).

`history/all/<id>/` returns every routine, workout and equipment list in one
answer (S4); `exercise/allCustomExercises/` the account's custom exercises. Both
are cached briefly and dropped after every write. Writes always work on a fresh
`routine/single/<id>/` instead.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..catalog import CatalogExercise
from ..model import AccountData, Routine, parse_custom_exercises, parse_history_all
from .client import ApiCalls


class RoutineNotFound(ValueError):
    pass


class AmbiguousRoutine(ValueError):
    pass


@dataclass
class _Slot[T]:
    """One cached read, with its own lock: a slow read never waits on another one."""

    value: T | None = None
    at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class AccountStore:
    def __init__(
        self,
        client: ApiCalls,
        *,
        ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl_s
        self._clock = clock
        self._account = _Slot[AccountData]()
        self._customs = _Slot[list[CatalogExercise]]()

    def _read[T](self, slot: _Slot[T], load: Callable[[], T]) -> T:
        with slot.lock:
            if slot.value is None or self._clock() - slot.at > self._ttl:
                slot.value = load()
                slot.at = self._clock()
            return slot.value

    def data(self) -> AccountData:
        return self._read(
            self._account,
            lambda: parse_history_all(
                self._client.get(f"history/all/{self._client.user_id}/")
            ),
        )

    def custom_exercises(self) -> list[CatalogExercise]:
        return self._read(
            self._customs,
            lambda: parse_custom_exercises(self._client.get("exercise/allCustomExercises/")),
        )

    def invalidate(self) -> None:
        for slot in (self._account, self._customs):
            with slot.lock:
                slot.value = None

    def routine_raw(self, identifier: int) -> dict[str, Any]:
        body = self._client.get(f"routine/single/{identifier}/")
        routines = body.get("routines") or []
        if not routines:
            raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
        return dict(routines[0])

    def resolve(self, ref: str | int, *, data: AccountData | None = None) -> Routine:
        """`data` resolves against an account snapshot the caller already holds."""
        account = self.data() if data is None else data
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
            raise AmbiguousRoutine(
                f"Multiple routines match {ref!r}: {listed}. Pass the id to disambiguate."
            )
        return matches[0]

    def routine_of_exercise(self, exercise_id: int) -> Routine:
        for r in self.data().routines:
            if not r.removed and any(
                e.identifier == exercise_id and not e.removed for e in r.exercises
            ):
                return r
        raise RoutineNotFound(
            f"No routine contains exercise {exercise_id}. "
            "Use smartgym_get_routine to see exercise ids."
        )


__all__ = ["AccountStore", "AmbiguousRoutine", "RoutineNotFound"]
