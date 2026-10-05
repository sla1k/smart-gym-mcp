"""Desired-vs-current routine diff (API-client spec §5). Pure — no I/O, no wire format.

Single-field tools and apply_routine both describe a *desired* routine; this
module turns it into one ChangeSet that payloads.py later encodes for
routine/update/. Exercises are matched by server identifier only, so a
routine holding the same catalog exercise twice stays unambiguous. Sets are
matched by position: overlap → update, extra desired → add, extra current →
remove. Validation is all-or-nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .matching import ExerciseCatalog, UnresolvedExercise
from .model import Routine, RoutineExercise
from .models import FieldChange, SetSpec

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
    days: str | None = None
    goal: str | None = None
    note: str | None = None
    exercises: list[DesiredExercise] | None = None


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
    changes: list[FieldChange]
    added_sets: list[AddedSet]
    updated_sets: list[UpdatedSet]
    removed_set_ids: list[int]


class AddedExercise(BaseModel):
    catalog_id: int
    name: str
    position: int
    rest_seconds: int
    note: str | None
    sets: list[SetSpec]


class RemovedExercise(BaseModel):
    identifier: int
    name: str


class ChangeSet(BaseModel):
    routine_identifier: int
    routine_name: str
    routine_changes: list[FieldChange]
    added: list[AddedExercise]
    removed: list[RemovedExercise]
    updated: list[ExerciseUpdate]
    final_order: list[str] | None
    warnings: list[str]

    @property
    def is_empty(self) -> bool:
        return not (
            self.routine_changes
            or self.added
            or self.removed
            or self.updated
            or self.final_order is not None
        )


def _clean(value: str) -> str | None:
    return value.strip() or None


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


def _exercise_update(ex: RoutineExercise, want: DesiredExercise) -> ExerciseUpdate | None:
    changes: list[FieldChange] = []
    if want.rest_seconds is not None and want.rest_seconds != ex.rest_seconds:
        changes.append(
            FieldChange(
                field="rest_seconds", old=str(ex.rest_seconds), new=str(want.rest_seconds)
            )
        )
    if want.note is not None and _clean(want.note) != ex.note:
        changes.append(FieldChange(field="note", old=ex.note, new=_clean(want.note)))
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    removed: list[int] = []
    if want.sets is not None:
        current = ex.template_sets
        for i, s in enumerate(want.sets):
            if i >= len(current):
                added.append(AddedSet(index=i, reps=s.reps, weight_kg=s.weight_kg))
            elif (current[i].reps, current[i].weight_kg) != (s.reps, s.weight_kg):
                updated.append(
                    UpdatedSet(
                        identifier=current[i].identifier,
                        index=i,
                        reps=s.reps,
                        weight_kg=s.weight_kg,
                    )
                )
        removed = [s.identifier for s in current[len(want.sets) :]]
    if not (changes or added or updated or removed):
        return None
    return ExerciseUpdate(
        identifier=ex.identifier,
        name=ex.name,
        changes=changes,
        added_sets=added,
        updated_sets=updated,
        removed_set_ids=removed,
    )


def diff_routine(
    current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog
) -> ChangeSet:
    problems: list[str] = []
    warnings: list[str] = []
    routine_changes = _routine_changes(current, desired, problems)
    added: list[AddedExercise] = []
    removed: list[RemovedExercise] = []
    updated: list[ExerciseUpdate] = []
    final_order: list[str] | None = None

    if desired.exercises is not None:
        active = current.active_exercises()
        by_id = {e.identifier: e for e in active}
        if not desired.exercises:
            problems.append("A routine needs at least one exercise.")
        seen: set[int] = set()
        order: list[str] = []
        for pos, want in enumerate(desired.exercises):
            label = f"Exercise #{pos + 1}"
            if want.sets is not None and not want.sets:
                problems.append(f"{label}: sets must not be empty (omit sets to keep them).")
                continue
            if want.exercise_id is not None:
                ex = by_id.get(want.exercise_id)
                if ex is None:
                    problems.append(
                        f"{label}: exercise_id {want.exercise_id} is not an active exercise "
                        f"of {current.name!r}."
                    )
                    continue
                if ex.identifier in seen:
                    problems.append(
                        f"{label}: exercise_id {ex.identifier} appears more than once."
                    )
                    continue
                seen.add(ex.identifier)
                order.append(f"id:{ex.identifier}")
                upd = _exercise_update(ex, want)
                if upd is not None:
                    updated.append(upd)
                continue
            assert want.exercise is not None
            try:
                res = catalog.resolve(want.exercise)
            except UnresolvedExercise as exc:
                problems.append(f"{label}: {exc}")
                continue
            if res.fuzzy:
                warnings.append(
                    f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
                    f"(confidence {res.confidence})."
                )
            if not want.sets:
                warnings.append(
                    f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
                    f"{DEFAULT_SET.reps:g} reps (bodyweight)."
                )
            order.append(f"new:{len(added)}")
            added.append(
                AddedExercise(
                    catalog_id=res.z_pk,
                    name=res.resolved_name,
                    position=pos,
                    rest_seconds=want.rest_seconds or 0,
                    note=_clean(want.note or ""),
                    sets=want.sets or [DEFAULT_SET],
                )
            )
        removed = [
            RemovedExercise(identifier=e.identifier, name=e.name)
            for e in active
            if e.identifier not in seen
        ]
        kept_in_current_order = [f"id:{e.identifier}" for e in active if e.identifier in seen]
        if order != kept_in_current_order:
            final_order = order

    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))
    return ChangeSet(
        routine_identifier=current.identifier,
        routine_name=current.name,
        routine_changes=routine_changes,
        added=added,
        removed=removed,
        updated=updated,
        final_order=final_order,
        warnings=warnings,
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
    "RemovedExercise",
    "UpdatedSet",
    "diff_routine",
]
