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

from pydantic import BaseModel

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


class ExerciseResolution(BaseModel):
    input: str
    resolved_name: str
    catalog_id: int
    confidence: float
    fuzzy: bool


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
    catalog_id: int
    name: str
    tokens: str


class ExerciseCatalog:
    """Immutable catalog snapshot with tokens precomputed once per load."""

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        self._index([_Entry(catalog_id, name, _tokens(name)) for catalog_id, name in entries])

    def _index(self, entries: list[_Entry]) -> None:
        self._entries = entries
        self._by_id = {e.catalog_id: e for e in entries}
        self._by_lower_name: dict[str, list[_Entry]] = {}
        for e in entries:
            self._by_lower_name.setdefault(e.name.lower(), []).append(e)

    @classmethod
    def from_exercises(cls, exercises: Iterable[CatalogExercise]) -> ExerciseCatalog:
        return cls([(e.id, e.name) for e in exercises])

    def extended(self, exercises: Iterable[CatalogExercise]) -> ExerciseCatalog:
        """This catalog plus `exercises`, reusing the tokens already computed."""
        out = ExerciseCatalog([])
        out._index(self._entries + [_Entry(e.id, e.name, _tokens(e.name)) for e in exercises])
        return out

    def resolve(self, ref: str) -> ExerciseResolution:
        """Resolve one exercise reference (name or numeric catalog id).

        Raises UnresolvedExercise (with closest candidates) below threshold.
        """
        s = ref.strip()
        if s.isdigit():
            entry = self._by_id.get(int(s))
            if entry is None:
                raise UnresolvedExercise(f"No catalog exercise with id={s}.")
            return self._exact(ref, entry)

        same_name = self._by_lower_name.get(s.lower())
        if same_name is not None:
            return self._exact(ref, self._only(ref, same_name))

        ref_tokens = _tokens(s)
        scored = sorted(
            ((_ratio(ref_tokens, e.tokens), e) for e in self._entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0]
        if best_score >= FUZZY_THRESHOLD:
            best = self._only(ref, self._by_lower_name[best.name.lower()])
            return ExerciseResolution(
                input=ref,
                resolved_name=best.name,
                catalog_id=best.catalog_id,
                confidence=round(best_score, 3),
                fuzzy=True,
            )
        candidates = ", ".join(
            f"{e.name!r} (id={e.catalog_id}, {score:.2f})"
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
            listed = ", ".join(f"{e.name!r} (id={e.catalog_id})" for e in same_name)
            raise UnresolvedExercise(
                f"{ref!r} matches several exercises with the same name: {listed}. "
                "Pass the id instead."
            )
        return same_name[0]

    @staticmethod
    def _exact(ref: str, entry: _Entry) -> ExerciseResolution:
        return ExerciseResolution(
            input=ref,
            resolved_name=entry.name,
            catalog_id=entry.catalog_id,
            confidence=1.0,
            fuzzy=False,
        )
