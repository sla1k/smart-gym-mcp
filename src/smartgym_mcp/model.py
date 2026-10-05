"""Server-side routine model (API-client spec §3), parsed from SmartGym API JSON.

The API sends every scalar as a string ("pause": "10") and mixes logged and
removed sets into `sets[]`. Parsing coerces and filters once, here, so nothing
downstream ever sees raw API strings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class ApiPayloadError(ValueError):
    """The server answered with an unexpected shape or a non-SUCCESS code."""


class TemplateSet(BaseModel):
    identifier: int
    unique_hashid: int
    index: int
    reps: float
    weight_kg: float


class RoutineExercise(BaseModel):
    identifier: int
    unique_hashid: int
    catalog_id: int
    name: str
    index: int
    rest_seconds: int
    note: str | None
    removed: bool
    template_sets: list[TemplateSet]


class Routine(BaseModel):
    identifier: int
    unique_hashid: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    archived: bool
    removed: bool
    exercises: list[RoutineExercise]

    def active_exercises(self) -> list[RoutineExercise]:
        return sorted((e for e in self.exercises if not e.removed), key=lambda e: e.index)


def _req(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise ApiPayloadError(f"API object is missing required field {key!r}.")
    return raw[key]


def _int(raw: Mapping[str, Any], key: str) -> int:
    return int(str(_req(raw, key)))


def _float(raw: Mapping[str, Any], key: str) -> float:
    return float(str(_req(raw, key)))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def _parse_set(raw: Mapping[str, Any]) -> TemplateSet:
    return TemplateSet(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        index=_int(raw, "index"),
        reps=_float(raw, "secondValue"),
        weight_kg=_float(raw, "thirdValue"),
    )


def _parse_exercise(raw: Mapping[str, Any]) -> RoutineExercise:
    sets = [
        _parse_set(s)
        for s in raw.get("sets") or []
        if s.get("dateLogged") is None and s.get("dateRemoved") is None
    ]
    return RoutineExercise(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        catalog_id=_int(raw, "id"),
        name=str(_req(raw, "name")),
        index=_int(raw, "idx"),
        rest_seconds=int(str(raw.get("pause") or 0)),
        note=_text(raw.get("note")),
        removed=raw.get("dateRemoved") is not None,
        template_sets=sorted(sets, key=lambda s: s.index),
    )


def parse_routine(raw: Mapping[str, Any]) -> Routine:
    return Routine(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        name=str(_req(raw, "name")),
        days=_text(raw.get("days")),
        goal=_text(raw.get("goal")),
        note=_text(raw.get("note")),
        archived=raw.get("dateArchived") is not None,
        removed=raw.get("dateRemoved") is not None,
        exercises=[_parse_exercise(e) for e in raw.get("exercises") or []],
    )


def parse_routines_response(raw: Mapping[str, Any]) -> list[Routine]:
    code = raw.get("code")
    if code != "SUCCESS":
        raise ApiPayloadError(f"SmartGym API answered {code!r} instead of SUCCESS.")
    return [parse_routine(r) for r in raw.get("routines") or []]


__all__ = [
    "ApiPayloadError",
    "Routine",
    "RoutineExercise",
    "TemplateSet",
    "parse_routine",
    "parse_routines_response",
]
