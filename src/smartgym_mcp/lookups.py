"""Routine lookups in a parsed account (API-client spec §5): pure, no I/O.

A routine is named by its id, its exact name or a unique part of its name; a routine
exercise id names the routine holding it. Removed routines and exercises never match.
"""

from __future__ import annotations

from .model import AccountData, Routine


class RoutineNotFound(ValueError):
    pass


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
