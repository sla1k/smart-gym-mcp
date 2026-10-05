"""Desired-vs-current routine diff (API-client spec §5, §10). Pure — no I/O, no wire format.

Tools describe a *desired* routine as three ordered sections (warm-up, main,
cool-down); this module turns it into one ChangeSet that payloads.py encodes.
Exercises are matched by server identifier only, so a routine holding the same
catalog exercise twice stays unambiguous. A section left as None keeps its
current members; a given list defines its section completely, so an existing
exercise listed under another section is a move and one listed nowhere (and
not in a kept section) is removed. Sets are matched by position: overlap →
update, extra desired → add, extra current → remove; kept sets are renumbered
0..n-1. Reps/weights compare at 3 decimals. Validation is all-or-nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from .matching import ExerciseCatalog, UnresolvedExercise
from .model import SECTIONS, Routine, RoutineExercise, Section
from .models import Days, ExerciseResolution, FieldChange, SetSpec

DEFAULT_SET = SetSpec(reps=10, weight_kg=0.0)


class DiffError(ValueError):
    """The desired routine was rejected — nothing should be sent."""


class DesiredExercise(BaseModel):
    exercise_id: int | None = None
    exercise: str | None = None
    rest_seconds: int | None = Field(default=None, ge=0)
    note: str | None = None
    sets: list[SetSpec] | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> DesiredExercise:
        if (self.exercise_id is None) == (self.exercise is None):
            raise ValueError(
                "Each exercise needs exactly one of exercise_id (existing) or "
                "exercise (catalog name or id, for a new one)."
            )
        return self


class DesiredRoutine(BaseModel):
    name: str | None = None
    days: Days = None
    goal: str | None = None
    note: str | None = None
    warmup: list[DesiredExercise] | None = None
    main: list[DesiredExercise] | None = None
    cooldown: list[DesiredExercise] | None = None

    def section_list(self, section: Section) -> list[DesiredExercise] | None:
        value: list[DesiredExercise] | None = getattr(self, section)
        return value


class AddedSet(BaseModel):
    index: int
    reps: float
    weight_kg: float


class UpdatedSet(BaseModel):
    identifier: int
    index: int
    reps: float
    weight_kg: float


class ExerciseUpdate(BaseModel):
    identifier: int
    name: str
    new_section: Section | None
    changes: list[FieldChange]
    added_sets: list[AddedSet]
    updated_sets: list[UpdatedSet]
    removed_set_ids: list[int]


class AddedExercise(BaseModel):
    catalog_id: int
    name: str
    section: Section
    position: int
    rest_seconds: int
    note: str | None
    sets: list[SetSpec]


class RemovedExercise(BaseModel):
    identifier: int
    name: str


class ExerciseView(BaseModel):
    catalog_id: int
    section: Section
    rest_seconds: int
    note: str | None
    sets: list[tuple[float, float]]


class RoutineView(BaseModel):
    name: str
    days: str | None
    goal: str | None
    note: str | None
    exercises: list[ExerciseView]


class ChangeSet(BaseModel):
    routine_identifier: int
    routine_name: str
    routine_changes: list[FieldChange]
    added: list[AddedExercise]
    removed: list[RemovedExercise]
    updated: list[ExerciseUpdate]
    order: list[str]
    final_order: list[str] | None
    warnings: list[str]
    expected: RoutineView

    @property
    def is_empty(self) -> bool:
        return not (
            self.routine_changes
            or self.added
            or self.removed
            or self.updated
            or self.final_order is not None
        )


@dataclass(frozen=True)
class _Slot:
    existing: RoutineExercise | None
    want: DesiredExercise | None
    resolution: ExerciseResolution | None


def round3(value: float) -> float:
    return round(float(value), 3)


def _clean(value: str) -> str | None:
    return value.strip() or None


def resolution_warnings(res: ExerciseResolution, sets: list[SetSpec] | None) -> list[str]:
    """Dry-run warnings for a newly resolved catalog exercise."""
    warnings: list[str] = []
    if res.fuzzy:
        warnings.append(
            f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
            f"(confidence {res.confidence})."
        )
    if not sets:
        warnings.append(
            f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
            f"{DEFAULT_SET.reps:g} reps (bodyweight)."
        )
    return warnings


def view_of(routine: Routine) -> RoutineView:
    return RoutineView(
        name=routine.name,
        days=routine.days,
        goal=routine.goal,
        note=routine.note,
        exercises=[
            ExerciseView(
                catalog_id=e.catalog_id,
                section=e.section,
                rest_seconds=e.rest_seconds,
                note=e.note,
                sets=[(round3(s.reps), round3(s.weight_kg)) for s in e.template_sets],
            )
            for e in routine.active_exercises()
        ],
    )


def _routine_changes(
    current: Routine, desired: DesiredRoutine, problems: list[str]
) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for field in ("name", "days", "goal", "note"):
        requested = getattr(desired, field)
        if requested is None:
            continue
        new = _clean(requested)
        if field == "name" and new is None:
            problems.append("Routine name must be non-empty.")
            continue
        old = getattr(current, field)
        if new != old:
            changes.append(FieldChange(field=field, old=old, new=new))
    return changes


def _set_edits(
    ex: RoutineExercise, sets: list[SetSpec]
) -> tuple[list[AddedSet], list[UpdatedSet], list[int]]:
    current = ex.template_sets
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    for i, s in enumerate(sets):
        if i >= len(current):
            added.append(AddedSet(index=i, reps=s.reps, weight_kg=s.weight_kg))
            continue
        cur = current[i]
        same = (cur.index, round3(cur.reps), round3(cur.weight_kg)) == (
            i,
            round3(s.reps),
            round3(s.weight_kg),
        )
        if not same:
            updated.append(
                UpdatedSet(
                    identifier=cur.identifier, index=i, reps=s.reps, weight_kg=s.weight_kg
                )
            )
    return added, updated, [s.identifier for s in current[len(sets) :]]


def _exercise_update(
    ex: RoutineExercise, want: DesiredExercise | None, section: Section
) -> ExerciseUpdate | None:
    changes: list[FieldChange] = []
    new_section = section if section != ex.section else None
    if new_section is not None:
        changes.append(FieldChange(field="section", old=ex.section, new=section))
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    removed: list[int] = []
    if want is not None:
        if want.rest_seconds is not None and want.rest_seconds != ex.rest_seconds:
            changes.append(
                FieldChange(
                    field="rest_seconds", old=str(ex.rest_seconds), new=str(want.rest_seconds)
                )
            )
        if want.note is not None and _clean(want.note) != ex.note:
            changes.append(FieldChange(field="note", old=ex.note, new=_clean(want.note)))
        if want.sets is not None:
            added, updated, removed = _set_edits(ex, want.sets)
    if not (changes or added or updated or removed):
        return None
    return ExerciseUpdate(
        identifier=ex.identifier,
        name=ex.name,
        new_section=new_section,
        changes=changes,
        added_sets=added,
        updated_sets=updated,
        removed_set_ids=removed,
    )


def _expected_existing(
    ex: RoutineExercise, want: DesiredExercise | None, section: Section
) -> ExerciseView:
    rest = ex.rest_seconds
    note = ex.note
    sets = [(round3(s.reps), round3(s.weight_kg)) for s in ex.template_sets]
    if want is not None:
        if want.rest_seconds is not None:
            rest = want.rest_seconds
        if want.note is not None:
            note = _clean(want.note)
        if want.sets is not None:
            sets = [(round3(s.reps), round3(s.weight_kg)) for s in want.sets]
    return ExerciseView(
        catalog_id=ex.catalog_id, section=section, rest_seconds=rest, note=note, sets=sets
    )


def _claims(
    current: Routine,
    given: dict[Section, list[DesiredExercise] | None],
    by_id: dict[int, RoutineExercise],
    problems: list[str],
) -> dict[int, Section]:
    claimed: dict[int, Section] = {}
    for section in SECTIONS:
        for pos, want in enumerate(given[section] or []):
            if want.exercise_id is None:
                continue
            label = f"{section} #{pos + 1}"
            if want.exercise_id not in by_id:
                problems.append(
                    f"{label}: exercise_id {want.exercise_id} is not an active exercise "
                    f"of {current.name!r}."
                )
            elif want.exercise_id in claimed:
                problems.append(
                    f"{label}: exercise_id {want.exercise_id} appears more than once."
                )
            else:
                claimed[want.exercise_id] = section
    return claimed


def _slots(
    section: Section,
    wanted: list[DesiredExercise] | None,
    current: Routine,
    claimed: dict[int, Section],
    catalog: ExerciseCatalog,
    problems: list[str],
    warnings: list[str],
) -> list[_Slot]:
    if wanted is None:
        return [
            _Slot(e, None, None)
            for e in current.section(section)
            if e.identifier not in claimed
        ]
    by_id = {e.identifier: e for e in current.active_exercises()}
    slots: list[_Slot] = []
    for pos, want in enumerate(wanted):
        label = f"{section} #{pos + 1}"
        if want.sets is not None and not want.sets:
            problems.append(f"{label}: sets must not be empty (omit sets to keep them).")
            continue
        if want.exercise_id is not None:
            if claimed.get(want.exercise_id) == section:
                slots.append(_Slot(by_id[want.exercise_id], want, None))
            continue
        assert want.exercise is not None
        try:
            res = catalog.resolve(want.exercise)
        except UnresolvedExercise as exc:
            problems.append(f"{label}: {exc}")
            continue
        warnings.extend(resolution_warnings(res, want.sets))
        slots.append(_Slot(None, want, res))
    return slots


def _final_order(
    order: list[str],
    added: list[AddedExercise],
    active: list[RoutineExercise],
    sections: dict[str, Section] | None = None,
) -> list[str] | None:
    """`order` when the server must renumber `idx`, None when every kept exercise keeps it.

    Renumber when the kept exercises' relative order changes, or when any of them changes
    section (`sections` maps a key to its section after the change) while their current idx,
    taken in final order, are not strictly increasing — a move on a routine whose idx
    disagree with its sections would otherwise re-read in a different order. An append
    counts as "no renumbering" only when the kept idx are exactly 0..n-1 and every add
    lands after all kept; otherwise the new exercise's idx would collide with an existing one.
    """
    by_key = {f"id:{e.identifier}": e for e in active}
    kept = [k for k in order if k in by_key]
    kept_set = set(kept)
    if kept != [k for k in by_key if k in kept_set]:
        return order
    idxs = [by_key[k].index for k in kept]
    moved = (
        any(sections.get(k, by_key[k].section) != by_key[k].section for k in kept)
        if sections
        else False
    )
    if moved and any(a >= b for a, b in zip(idxs, idxs[1:], strict=False)):
        return order
    if not added:
        return None
    contiguous = idxs == list(range(len(kept)))
    appended = all(a.position >= len(kept) for a in added)
    return None if contiguous and appended else order


def diff_routine(
    current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog
) -> ChangeSet:
    problems: list[str] = []
    warnings: list[str] = []
    routine_changes = _routine_changes(current, desired, problems)
    active = current.active_exercises()
    by_id = {e.identifier: e for e in active}
    given = {s: desired.section_list(s) for s in SECTIONS}
    claimed = _claims(current, given, by_id, problems)
    slots = {
        s: _slots(s, given[s], current, claimed, catalog, problems, warnings) for s in SECTIONS
    }
    if any(v is not None for v in given.values()) and not any(slots.values()):
        problems.append("A routine needs at least one exercise.")
    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))

    added: list[AddedExercise] = []
    updated: list[ExerciseUpdate] = []
    expected: list[ExerciseView] = []
    order: list[str] = []
    new_sections: dict[str, Section] = {}
    for section in SECTIONS:
        for slot in slots[section]:
            if slot.existing is not None:
                upd = _exercise_update(slot.existing, slot.want, section)
                if upd is not None:
                    updated.append(upd)
                key = f"id:{slot.existing.identifier}"
                order.append(key)
                new_sections[key] = section
                expected.append(_expected_existing(slot.existing, slot.want, section))
                continue
            assert slot.want is not None and slot.resolution is not None
            sets = slot.want.sets or [DEFAULT_SET]
            new = AddedExercise(
                catalog_id=slot.resolution.catalog_id,
                name=slot.resolution.resolved_name,
                section=section,
                position=len(order),
                rest_seconds=slot.want.rest_seconds or 0,
                note=_clean(slot.want.note or ""),
                sets=sets,
            )
            order.append(f"new:{len(added)}")
            added.append(new)
            expected.append(
                ExerciseView(
                    catalog_id=new.catalog_id,
                    section=section,
                    rest_seconds=new.rest_seconds,
                    note=new.note,
                    sets=[(round3(s.reps), round3(s.weight_kg)) for s in sets],
                )
            )

    kept = set(order)
    removed = [
        RemovedExercise(identifier=e.identifier, name=e.name)
        for e in active
        if f"id:{e.identifier}" not in kept
    ]
    fields = {c.field: c.new for c in routine_changes}

    def pick(field: str, old: str | None) -> str | None:
        return fields.get(field, old)

    return ChangeSet(
        routine_identifier=current.identifier,
        routine_name=current.name,
        routine_changes=routine_changes,
        added=added,
        removed=removed,
        updated=updated,
        order=order,
        final_order=_final_order(order, added, active, new_sections),
        warnings=warnings,
        expected=RoutineView(
            name=pick("name", current.name) or current.name,
            days=pick("days", current.days),
            goal=pick("goal", current.goal),
            note=pick("note", current.note),
            exercises=expected,
        ),
    )


def moves_as_readd(cs: ChangeSet, current: Routine) -> ChangeSet:
    """Spec §10.4 fallback: express every section move as remove + re-add.

    The re-added exercise takes the moved one's expected state (rest, note, template sets),
    so any other edit of it rides along; it gets a new server identifier.
    """
    moved = [u for u in cs.updated if u.new_section is not None]
    if not moved:
        return cs
    active = current.active_exercises()
    by_id = {e.identifier: e for e in active}
    added = list(cs.added)
    removed = list(cs.removed)
    updated = [u for u in cs.updated if u.new_section is None]
    order = list(cs.order)
    for u in moved:
        ex = by_id[u.identifier]
        pos = order.index(f"id:{u.identifier}")
        view = cs.expected.exercises[pos]
        removed.append(RemovedExercise(identifier=ex.identifier, name=ex.name))
        order[pos] = f"new:{len(added)}"
        added.append(
            AddedExercise(
                catalog_id=ex.catalog_id,
                name=ex.name,
                section=view.section,
                position=pos,
                rest_seconds=view.rest_seconds,
                note=view.note,
                sets=[SetSpec.model_construct(reps=r, weight_kg=w) for r, w in view.sets],
            )
        )
    warnings = cs.warnings + [
        f"{u.name!r} moves by remove + re-add (no in-place section move); its recent "
        "sessions in this routine restart."
        for u in moved
    ]
    return cs.model_copy(
        update={
            "added": added,
            "removed": removed,
            "updated": updated,
            "order": order,
            "final_order": _final_order(order, added, active),
            "warnings": warnings,
        }
    )


__all__ = [
    "DEFAULT_SET",
    "AddedExercise",
    "AddedSet",
    "ChangeSet",
    "DesiredExercise",
    "DesiredRoutine",
    "DiffError",
    "ExerciseUpdate",
    "ExerciseView",
    "RemovedExercise",
    "RoutineView",
    "UpdatedSet",
    "diff_routine",
    "moves_as_readd",
    "resolution_warnings",
    "round3",
    "view_of",
]
