"""Deterministic exercise-name resolution against the app-bundle catalog.

Numeric → catalog id; else exact case-insensitive name; else normalized
token-set fuzzy match with threshold 0.85. Anything below threshold fails with
top candidates — nothing silently wrong is ever resolved. No LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from .catalog import CatalogExercise
from .models import ExerciseResolution

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
        self._entries = [
            _Entry(catalog_id, name, _tokens(name)) for catalog_id, name in entries
        ]
        self._by_id = {e.catalog_id: e for e in self._entries}
        self._by_lower_name = {e.name.lower(): e for e in self._entries}

    @classmethod
    def from_bundle(cls, exercises: Sequence[CatalogExercise]) -> ExerciseCatalog:
        """Catalog from the app bundle."""
        return cls([(e.id, e.name) for e in exercises])

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

        entry = self._by_lower_name.get(s.lower())
        if entry is not None:
            return self._exact(ref, entry)

        ref_tokens = _tokens(s)
        scored = sorted(
            ((_ratio(ref_tokens, e.tokens), e) for e in self._entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0]
        if best_score >= FUZZY_THRESHOLD:
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
    def _exact(ref: str, entry: _Entry) -> ExerciseResolution:
        return ExerciseResolution(
            input=ref,
            resolved_name=entry.name,
            catalog_id=entry.catalog_id,
            confidence=1.0,
            fuzzy=False,
        )
