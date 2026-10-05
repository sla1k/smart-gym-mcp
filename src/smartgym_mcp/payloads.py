"""Wire payloads for the SmartGym API (API-client spec §3, §10).

Shapes mirror the app's own captured requests (2026-10-05, v8.0.3; fixtures in
tests/fixtures/api/). New objects carry only a client `uniqueHashID`; the
server assigns identifiers and returns the mapping. Edits go to two endpoints:
`routine/update/` (routine fields, rest, section moves, added and removed exercises,
order) and `routine/updateExercise/` (exercise notes and template sets). An order
change rides in the same `routine/update/` as the adds it accompanies, as the app
does (spec §10.3, S7): `exercisesOrder` lists the kept exercises at their final
global index and each new exercise carries its own `idx`.
Common fields (appVersion, authID, requestDate) are added by api/client.py.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogExercise
from .diff import DEFAULT_SET, ChangeSet, ExerciseUpdate
from .model import LIST_GROUP_BY_SECTION, Routine, Section, TemplateSet
from .models import ExerciseSpec, RoutineSpec, SetSpec

_BAND_EQUIPMENT_ID = "38"
Mint = Callable[[datetime], int]

# Spec §10.3 / S7: True = the server moves an exercise between sections in place.
SECTION_MOVE_IN_PLACE = True


def mint_unique_hashid(now: datetime) -> int:
    return int(now.strftime("%y%m%d") + f"{random.randint(0, 99_999_999):08d}")


def local_timezone_name() -> str:
    target = os.path.realpath("/etc/localtime")
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _wire_date(server_date: str | None) -> str | None:
    return server_date.replace(" ", "T") if server_date else None


def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def routine_entries(spec: RoutineSpec) -> list[tuple[Section, ExerciseSpec]]:
    groups: tuple[tuple[Section, list[ExerciseSpec]], ...] = (
        ("warmup", spec.warmup),
        ("main", spec.exercises),
        ("cooldown", spec.cooldown),
    )
    return [(section, e) for section, items in groups for e in items]


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


def exercise_payload(
    cat: CatalogExercise,
    *,
    section: Section,
    idx: int,
    rest_seconds: int,
    note: str | None,
    sets: Sequence[SetSpec],
    now: datetime,
    mint: Mint,
    routine_identifier: int | None = None,
) -> dict[str, Any]:
    added = _utc(now)
    first, second, third, fourth, fifth, sixth = cat.images
    ex: dict[str, Any] = {
        "id": cat.id,
        "genericID": cat.id,
        "name": cat.name,
        "idx": idx,
        "index": idx,
        "pause": rest_seconds,
        "type": cat.type,
        "category": cat.category,
        "subCategories": cat.sub_categories,
        "twoSides": cat.two_sides,
        "stretch": cat.stretch,
        "isStretch": cat.stretch == 1,
        "isCustom": 0,
        "requiresBands": _BAND_EQUIPMENT_ID in cat.equipment_ids,
        "isSingleWeight": 0,
        "listGroup": LIST_GROUP_BY_SECTION[section],
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
    if routine_identifier is not None:
        ex["routineID"] = routine_identifier
    if note:
        ex["note"] = note
    ex["sets"] = [
        _set_payload(k, s, hashid=mint(now), added=added) for k, s in enumerate(sets)
    ]
    return ex


def new_routine_payload(
    spec: RoutineSpec,
    exercises: Sequence[CatalogExercise],
    *,
    number: int,
    now: datetime,
    mint: Mint = mint_unique_hashid,
) -> dict[str, Any]:
    entries = routine_entries(spec)
    if len(entries) != len(exercises):
        raise ValueError(f"{len(exercises)} catalog entries for {len(entries)} exercises.")
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    routine: dict[str, Any] = {
        "name": spec.name,
        "number": number,
        "reference": 0,
        "hasSynced": 0,
        "migratedSets": 1,
        "uniqueHashID": mint(now),
        "dateCreated": _utc(midnight),
        "exercises": [],
    }
    # The server 500s on JSON null here; the app omits unset optional fields.
    for key, value in (("days", spec.days), ("goal", spec.goal), ("note", spec.note)):
        if value and value.strip():
            routine[key] = value
    for i, ((section, ex_spec), cat) in enumerate(zip(entries, exercises, strict=True)):
        routine["exercises"].append(
            exercise_payload(
                cat,
                section=section,
                idx=i,
                rest_seconds=ex_spec.rest_seconds or 0,
                note=ex_spec.note,
                sets=ex_spec.sets or [DEFAULT_SET],
                now=now,
                mint=mint,
            )
        )
    return routine


def add_routines_form(routines: Sequence[dict[str, Any]], *, timezone: str) -> dict[str, str]:
    return {"routines": json.dumps(list(routines), ensure_ascii=False), "timezone": timezone}


def archive_form(routine_identifier: int) -> dict[str, str]:
    return {"routinesIDs": str(routine_identifier)}


def unarchive_form(routine_identifier: int) -> dict[str, str]:
    return {"routineID": str(routine_identifier)}


@dataclass(frozen=True)
class EncodedChange:
    """A ChangeSet as at most two requests; together they are the whole change.

    `structure` → `routine/update/`, `exercise_edits` → `routine/updateExercise/`.
    `added_hashids[k]` is the client `uniqueHashID` of `ChangeSet.added[k]`, which the
    server's response maps to its new identifier.
    """

    structure: dict[str, str] | None
    exercise_edits: dict[str, str] | None
    added_hashids: list[int]


def _routine_base(cs: ChangeSet, current: Routine, timezone: str) -> dict[str, str]:
    form = {"routineID": str(current.identifier), "timezone": timezone}
    if cs.expected.days is not None:
        form["days"] = cs.expected.days
    if cs.expected.goal is not None:
        form["goal"] = cs.expected.goal
    return form


def _update_entry(u: ExerciseUpdate) -> dict[str, Any] | None:
    entry: dict[str, Any] = {}
    for c in u.changes:
        if c.field == "rest_seconds":
            entry["pause"] = c.new
    if u.new_section is not None:
        if not SECTION_MOVE_IN_PLACE:
            raise ValueError(
                "Section moves must be expressed with diff.moves_as_readd: the server has no "
                "in-place move (spec §10.4)."
            )
        entry["listGroup"] = str(LIST_GROUP_BY_SECTION[u.new_section])
    if not entry:
        return None
    entry["exerciseID"] = u.identifier
    return entry


def _structure(
    cs: ChangeSet,
    current: Routine,
    catalog: Mapping[int, CatalogExercise],
    *,
    timezone: str,
    now: datetime,
    mint: Mint,
) -> tuple[dict[str, str] | None, list[int]]:
    form = _routine_base(cs, current, timezone)
    for c in cs.routine_changes:
        form[c.field] = c.new or ""
    entries = [e for e in (_update_entry(u) for u in cs.updated) if e is not None]
    if entries:
        form["updateExercises"] = _json(entries)
    if cs.removed:
        form["removeExercises"] = ",".join(str(r.identifier) for r in cs.removed)
    hashids: list[int] = []
    if cs.added:
        payloads = []
        for a in cs.added:
            cat = catalog.get(a.catalog_id)
            if cat is None:
                raise ValueError(f"Catalog exercise {a.catalog_id} is not in the app bundle.")
            p = exercise_payload(
                cat,
                section=a.section,
                idx=a.position,
                rest_seconds=a.rest_seconds,
                note=a.note,
                sets=a.sets,
                now=now,
                mint=mint,
                routine_identifier=current.identifier,
            )
            payloads.append(p)
            hashids.append(int(p["uniqueHashID"]))
        form["exercises"] = _json(payloads)
    if cs.final_order is not None:
        form["exercisesOrder"] = ",".join(
            f"{key[3:]}:{pos}"
            for pos, key in enumerate(cs.final_order)
            if key.startswith("id:")
        )
    meaningful = (
        cs.routine_changes or entries or cs.removed or cs.added or cs.final_order is not None
    )
    return (form if meaningful else None), hashids


def _existing_set(
    cur: TemplateSet, *, index: int, reps: float, weight_kg: float
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "thirdValue": _num(weight_kg),
        "uniqueHashID": cur.unique_hashid,
        "firstValue": 1,
        "identifier": cur.identifier,
        "secondValue": _num(reps),
        "type": 0,
        "index": index,
    }
    date = _wire_date(cur.date_added)
    if date:
        out["dateAdded"] = date
    return out


def _exercise_edits(
    cs: ChangeSet, current: Routine, *, timezone: str, now: datetime, mint: Mint
) -> dict[str, str] | None:
    by_id = {e.identifier: e for e in current.exercises}
    items: list[dict[str, Any]] = []
    for u in cs.updated:
        sets_by_id = {s.identifier: s for s in by_id[u.identifier].template_sets}
        item: dict[str, Any] = {}
        for c in u.changes:
            if c.field == "note":
                item["note"] = c.new or ""
        if u.added_sets:
            item["addedSets"] = [
                {
                    "routineID": current.identifier,
                    "index": a.index,
                    "uniqueHashID": mint(now),
                    "firstValue": 1,
                    "secondValue": _num(a.reps),
                    "type": 0,
                    "uniqueExerciseID": u.identifier,
                    "thirdValue": _num(a.weight_kg),
                }
                for a in u.added_sets
            ]
        if u.updated_sets:
            item["updatedSets"] = [
                _existing_set(
                    sets_by_id[s.identifier], index=s.index, reps=s.reps, weight_kg=s.weight_kg
                )
                for s in u.updated_sets
            ]
        if u.removed_set_ids:
            item["removedSets"] = [
                _existing_set(cur, index=cur.index, reps=cur.reps, weight_kg=cur.weight_kg)
                for cur in (sets_by_id[i] for i in u.removed_set_ids)
            ]
        if item:
            item["exerciseID"] = u.identifier
            items.append(item)
    return {"timezone": timezone, "exercises": _json(items)} if items else None


def encode_change(
    cs: ChangeSet,
    current: Routine,
    catalog: Mapping[int, CatalogExercise],
    *,
    timezone: str,
    now: datetime,
    mint: Mint = mint_unique_hashid,
) -> EncodedChange:
    structure, hashids = _structure(
        cs, current, catalog, timezone=timezone, now=now, mint=mint
    )
    return EncodedChange(
        structure=structure,
        exercise_edits=_exercise_edits(cs, current, timezone=timezone, now=now, mint=mint),
        added_hashids=hashids,
    )


__all__ = [
    "SECTION_MOVE_IN_PLACE",
    "EncodedChange",
    "Mint",
    "add_routines_form",
    "archive_form",
    "encode_change",
    "exercise_payload",
    "local_timezone_name",
    "mint_unique_hashid",
    "new_routine_payload",
    "routine_entries",
    "unarchive_form",
]
