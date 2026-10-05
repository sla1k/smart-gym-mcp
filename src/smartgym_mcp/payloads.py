"""Wire payloads for the SmartGym API (API-client spec §3) — create and archive.

Shapes mirror the app's own captured requests (2026-10-05, v8.0.3). New
objects carry only a client `uniqueHashID`; the server assigns identifiers
and returns the mapping. Common fields (appVersion, authID, requestDate) are
added by api/client.py, not here.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogExercise
from .diff import DEFAULT_SET
from .models import RoutineSpec, SetSpec

_BAND_EQUIPMENT_ID = "38"


def mint_unique_hashid(now: datetime) -> int:
    return int(now.strftime("%y%m%d") + f"{random.randint(0, 99_999_999):08d}")


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def _set_payload(index: int, s: SetSpec, *, hashid: int, added: str) -> dict[str, Any]:
    return {
        "index": index,
        "type": 0,
        "firstValue": 1,
        "secondValue": _num(s.reps),
        "thirdValue": _num(s.weight_kg),
        "uniqueHashID": hashid,
        "dateAdded": added,
    }


def new_routine_payload(
    spec: RoutineSpec,
    exercises: Sequence[CatalogExercise],
    *,
    number: int,
    now: datetime,
    mint: Callable[[datetime], int] = mint_unique_hashid,
) -> dict[str, Any]:
    added = _utc(now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    routine: dict[str, Any] = {
        "name": spec.name,
        "days": spec.days,
        "goal": spec.goal,
        "note": spec.note,
        "number": number,
        "reference": 0,
        "hasSynced": 0,
        "migratedSets": 1,
        "uniqueHashID": mint(now),
        "dateCreated": _utc(midnight),
        "exercises": [],
    }
    for i, (ex_spec, cat) in enumerate(zip(spec.exercises, exercises, strict=True)):
        sets = ex_spec.sets or [DEFAULT_SET]
        first, second, third, fourth, fifth, sixth = cat.images
        ex: dict[str, Any] = {
            "id": cat.id,
            "genericID": cat.id,
            "name": cat.name,
            "idx": i,
            "index": i,
            "pause": ex_spec.rest_seconds or 0,
            "type": cat.type,
            "category": cat.category,
            "subCategories": cat.sub_categories,
            "twoSides": cat.two_sides,
            "stretch": cat.stretch,
            "isStretch": cat.stretch == 1,
            "isCustom": 0,
            "requiresBands": _BAND_EQUIPMENT_ID in cat.equipment_ids,
            "isSingleWeight": 0,
            "listGroup": 0,
            "mode": 0,
            "totalSets": len(sets),
            "totalRealSets": len(sets),
            "firstImage": first,
            "secondImage": second,
            "thirdImage": third,
            "fourthImage": fourth,
            "fifthImage": fifth,
            "sixthImage": sixth,
            "uniqueHashID": mint(now),
            "dateAdded": added,
        }
        if ex_spec.note:
            ex["note"] = ex_spec.note
        ex["sets"] = [
            _set_payload(k, s, hashid=mint(now), added=added) for k, s in enumerate(sets)
        ]
        routine["exercises"].append(ex)
    return routine


def add_routines_form(routines: Sequence[dict[str, Any]], *, timezone: str) -> dict[str, str]:
    return {"routines": json.dumps(list(routines), ensure_ascii=False), "timezone": timezone}


def archive_form(routine_identifier: int) -> dict[str, str]:
    return {"routinesIDs": str(routine_identifier)}


def unarchive_form(routine_identifier: int) -> dict[str, str]:
    return {"routineID": str(routine_identifier)}


__all__ = [
    "add_routines_form",
    "archive_form",
    "mint_unique_hashid",
    "new_routine_payload",
    "unarchive_form",
]
