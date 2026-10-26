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
import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogExercise
from .diff import ChangeSet, ExerciseUpdate
from .inputs import DEFAULT_SET, ExerciseSpec, RoutineSpec, SetSpec
from .model import LIST_GROUP_BY_SECTION, Routine, Section, TemplateSet

_BAND_EQUIPMENT_ID = "38"
Mint = Callable[[datetime], int]


def mint_unique_hashid(now: datetime) -> int:
    return int(now.strftime("%y%m%d") + f"{random.randint(0, 99_999_999):08d}")


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
        "twoSides": cat.two_sides,
        "isStretch": cat.stretch == 1,
        "isCustom": int(cat.is_custom),
        "requiresBands": _BAND_EQUIPMENT_ID in cat.equipment_ids,
        "isSingleWeight": cat.single_weight,
        "listGroup": LIST_GROUP_BY_SECTION[section],
        "mode": 0,
        "totalSets": len(sets),
        "totalRealSets": len(sets),
        "firstImage": first,
        "uniqueHashID": mint(now),
        "dateAdded": added,
    }
    # The app sends a custom exercise without these (captured add_routine_custom).
    if not cat.is_custom:
        ex |= {
            "subCategories": cat.sub_categories,
            "stretch": cat.stretch,
            "secondImage": second,
            "thirdImage": third,
            "fourthImage": fourth,
            "fifthImage": fifth,
            "sixthImage": sixth,
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
    routine |= {
        key: value
        for key, value in (("days", spec.days), ("goal", spec.goal), ("note", spec.note))
        if value and value.strip()
    }
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


def _routine_base(change: ChangeSet, current: Routine, timezone: str) -> dict[str, str]:
    form = {"routineID": str(current.identifier), "timezone": timezone}
    if change.expected.days is not None:
        form["days"] = change.expected.days
    if change.expected.goal is not None:
        form["goal"] = change.expected.goal
    return form


def _update_entry(u: ExerciseUpdate) -> dict[str, Any] | None:
    entry: dict[str, Any] = {}
    for c in u.changes:
        if c.field == "rest_seconds":
            entry["pause"] = c.new
    if u.new_section is not None:
        entry["listGroup"] = str(LIST_GROUP_BY_SECTION[u.new_section])
    if not entry:
        return None
    entry["exerciseID"] = u.identifier
    return entry


def _structure(
    change: ChangeSet, current: Routine, *, timezone: str, now: datetime, mint: Mint
) -> dict[str, str] | None:
    form = _routine_base(change, current, timezone)
    for c in change.routine_changes:
        form[c.field] = c.new or ""
    entries = [e for e in (_update_entry(u) for u in change.updated) if e is not None]
    if entries:
        form["updateExercises"] = _json(entries)
    if change.removed:
        form["removeExercises"] = ",".join(str(r.identifier) for r in change.removed)
    if change.added:
        form["exercises"] = _json(
            [
                exercise_payload(
                    a.exercise,
                    section=a.section,
                    idx=a.position,
                    rest_seconds=a.rest_seconds,
                    note=a.note,
                    sets=a.sets,
                    now=now,
                    mint=mint,
                    routine_identifier=current.identifier,
                )
                for a in change.added
            ]
        )
    if change.final_order is not None:
        form["exercisesOrder"] = ",".join(
            f"{key[3:]}:{pos}"
            for pos, key in enumerate(change.final_order)
            if key.startswith("id:")
        )
    meaningful = (
        change.routine_changes
        or entries
        or change.removed
        or change.added
        or change.final_order is not None
    )
    return form if meaningful else None


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
    change: ChangeSet, current: Routine, *, timezone: str, now: datetime, mint: Mint
) -> dict[str, str] | None:
    by_id = {e.identifier: e for e in current.exercises}
    items: list[dict[str, Any]] = []
    for u in change.updated:
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
    change: ChangeSet,
    current: Routine,
    *,
    timezone: str,
    now: datetime,
    mint: Mint = mint_unique_hashid,
) -> list[tuple[str, dict[str, str]]]:
    """A ChangeSet as the `(path, form)` requests that together make the whole change.

    At most two, in this order: `routine/update/` (structure), `routine/updateExercise/`
    (notes and sets). An empty list means there is nothing to send.
    """
    requests = (
        (
            "routine/update/",
            _structure(change, current, timezone=timezone, now=now, mint=mint),
        ),
        (
            "routine/updateExercise/",
            _exercise_edits(change, current, timezone=timezone, now=now, mint=mint),
        ),
    )
    return [(path, form) for path, form in requests if form is not None]
