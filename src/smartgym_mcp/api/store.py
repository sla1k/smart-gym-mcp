"""Account reads over the SmartGym API (API-client spec §5).

`history/all/<id>/` returns every routine, workout and equipment list in one
answer (S4); it is cached briefly and dropped after every write. Writes always
work on a fresh `routine/single/<id>/` instead.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ..model import AccountData, Routine, parse_history_all
from .client import ApiCalls


class RoutineNotFound(ValueError):
    pass


class AmbiguousRoutine(ValueError):
    pass


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
        self._lock = threading.Lock()
        self._cached: AccountData | None = None
        self._at = 0.0

    def data(self) -> AccountData:
        with self._lock:
            if self._cached is None or self._clock() - self._at > self._ttl:
                raw = self._client.get(f"history/all/{self._client.user_id}/")
                self._cached = parse_history_all(raw)
                self._at = self._clock()
            return self._cached

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def routine_raw(self, identifier: int) -> dict[str, Any]:
        body = self._client.get(f"routine/single/{identifier}/")
        routines = body.get("routines") or []
        if not routines:
            raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
        return dict(routines[0])

    def resolve(self, ref: str | int) -> Routine:
        live = [r for r in self.data().routines if not r.removed]
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
