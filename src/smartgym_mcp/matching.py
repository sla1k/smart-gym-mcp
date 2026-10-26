"""Deterministic exercise-name resolution against the exercise catalog: the app bundle
plus the account's custom exercises.

Numeric → catalog id; else exact case-insensitive name; else normalized
token-set fuzzy match with threshold 0.85. Anything below threshold fails with
top candidates — nothing silently wrong is ever resolved. No LLM.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from .catalog import CatalogExercise

FUZZY_THRESHOLD = 0.85
_SUGGESTIONS = 3

# Common shorthand expanded before comparison. Values may be multi-token.
_ALIASES = {
    "db": "dumbbell",
    "bb": "barbell",
    "kb": "kettlebell",
    "ohp": "overhead press",
    "rdl": "romanian deadlift",
    "lat": "lateral",
}


class UnresolvedExercise(ValueError):
    pass


# The catalog exercise itself, carried for the payload encoder; never part of a tool's
# output (not serialized, not in the output schema).
Resolved = Annotated[SkipJsonSchema[CatalogExercise], Field(exclude=True, repr=False)]


class ExerciseResolution(BaseModel):
    input: str
    resolved_name: str
    catalog_id: int
    confidence: float
    fuzzy: bool
    exercise: Resolved


def _tokens(name: str) -> str:
    """Normalized sorted-unique-token string for token-set comparison."""
    words: list[str] = []
    for w in re.sub(r"[^a-z0-9]+", " ", name.lower()).split():
        words.extend(_ALIASES.get(w, w).split())
    return " ".join(sorted(set(words)))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class _Entry:
    exercise: CatalogExercise
    tokens: str


def _entry(exercise: CatalogExercise) -> _Entry:
    return _Entry(exercise, _tokens(exercise.name))


def _resolution(
    ref: str, entry: _Entry, *, confidence: float, fuzzy: bool
) -> ExerciseResolution:
    return ExerciseResolution(
        input=ref,
        resolved_name=entry.exercise.name,
        catalog_id=entry.exercise.id,
        confidence=confidence,
        fuzzy=fuzzy,
        exercise=entry.exercise,
    )


class ExerciseCatalog:
    """Immutable catalog snapshot with tokens precomputed once per load."""

    def __init__(self, exercises: Iterable[CatalogExercise]) -> None:
        self._index([_entry(e) for e in exercises])

    def _index(self, entries: list[_Entry]) -> None:
        self._entries = entries
        self._by_id = {e.exercise.id: e for e in entries}
        self._by_lower_name: dict[str, list[_Entry]] = {}
        for e in entries:
            self._by_lower_name.setdefault(e.exercise.name.lower(), []).append(e)

    def extended(self, exercises: Iterable[CatalogExercise]) -> ExerciseCatalog:
        """This catalog plus `exercises`, reusing the tokens already computed."""
        out = ExerciseCatalog(())
        out._index(self._entries + [_entry(e) for e in exercises])
        return out

    def resolve(self, ref: str) -> ExerciseResolution:
        """Resolve one exercise reference (name or numeric catalog id) to its exercise.

        Raises UnresolvedExercise (with closest candidates) below threshold.
        """
        s = ref.strip()
        if s.isdigit():
            entry = self._by_id.get(int(s))
            if entry is None:
                raise UnresolvedExercise(f"No catalog exercise with id={s}.")
            return _resolution(ref, entry, confidence=1.0, fuzzy=False)

        same_name = self._by_lower_name.get(s.lower())
        if same_name is not None:
            return _resolution(ref, self._only(ref, same_name), confidence=1.0, fuzzy=False)

        ref_tokens = _tokens(s)
        scored = sorted(
            ((_ratio(ref_tokens, e.tokens), e) for e in self._entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0]
        if best_score >= FUZZY_THRESHOLD:
            best = self._only(ref, self._by_lower_name[best.exercise.name.lower()])
            return _resolution(ref, best, confidence=round(best_score, 3), fuzzy=True)
        candidates = ", ".join(
            f"{e.exercise.name!r} (id={e.exercise.id}, {score:.2f})"
            for score, e in scored[:_SUGGESTIONS]
        )
        raise UnresolvedExercise(
            f"Could not resolve exercise {ref!r} (best match below "
            f"{FUZZY_THRESHOLD} threshold). Closest: {candidates}."
        )

    @staticmethod
    def _only(ref: str, same_name: list[_Entry]) -> _Entry:
        """The one exercise with this name; several (e.g. a custom exercise named like a
        built-in one) → rejected, never picked silently."""
        if len(same_name) > 1:
            listed = ", ".join(f"{e.exercise.name!r} (id={e.exercise.id})" for e in same_name)
            raise UnresolvedExercise(
                f"{ref!r} matches several exercises with the same name: {listed}. "
                "Pass the id instead."
            )
        return same_name[0]
