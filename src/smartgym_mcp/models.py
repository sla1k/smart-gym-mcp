"""Pydantic models: read-tool outputs + create-program inputs/outputs (spec 03)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoutineSummary(BaseModel):
    z_pk: int
    name: str
    days: str | None
    hidden: bool
    has_synced: bool = Field(
        description="False = pending push to the SmartGym backend (fires on next app launch)"
    )
    last_updated_by_ai: str | None
    exercise_count: int


class RoutineListResult(BaseModel):
    count: int
    routines: list[RoutineSummary]


class SetEntry(BaseModel):
    set_no: int
    reps: int
    weight_kg: float  # 0.0 = bodyweight / untracked, returned as-is


class SessionEntry(BaseModel):
    date: str
    sets: list[SetEntry]


class ExerciseEntry(BaseModel):
    ue_pk: int
    index: int
    exercise_name: str
    rest_seconds: int | None
    note: str | None
    sessions: list[SessionEntry]  # most recent first, up to history_depth
    top_set: SetEntry | None  # heaviest set of the latest session
    total_volume: float  # sum(reps*weight) of the latest session


class RoutineDetail(BaseModel):
    z_pk: int
    name: str
    days: str | None
    hidden: bool
    has_synced: bool = Field(
        description="False = pending push to the SmartGym backend (fires on next app launch)"
    )
    last_updated_by_ai: str | None
    exercises: list[ExerciseEntry]


class WorkoutSession(BaseModel):
    workout_pk: int
    date: str
    routine: str | None
    duration_min: int
    calories: int | None
    avg_hr: int | None
    max_hr: int | None


class WorkoutHistoryResult(BaseModel):
    total: int
    count: int
    offset: int
    has_more: bool
    next_offset: int | None
    sessions: list[WorkoutSession]


class EquipmentItem(BaseModel):
    name: str
    category_id: int | None
    owned: bool
    selected_weights: str | None


class EquipmentListResult(BaseModel):
    count: int
    equipment: list[EquipmentItem]


# --------------------------------------------------------------------------- #
# Program creation (spec 03)
# --------------------------------------------------------------------------- #
class SetSpec(BaseModel):
    reps: float = Field(gt=0, description="Target reps for this set")
    weight_kg: float = Field(default=0.0, ge=0, description="Target weight; 0 = bodyweight")


class ExerciseSpec(BaseModel):
    exercise: str = Field(
        description="Catalog exercise name (fuzzy-matched) or a numeric z_pk"
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    note: str | None = None
    sets: list[SetSpec] | None = Field(
        default=None,
        description="Template sets; omitted = one default set (flagged in dry-run)",
    )


class RoutineSpec(BaseModel):
    name: str = Field(min_length=1)
    days: str | None = None
    goal: str | None = None
    note: str | None = None
    exercises: list[ExerciseSpec] = Field(min_length=1)


class ExerciseResolution(BaseModel):
    input: str
    resolved_name: str
    z_pk: int
    confidence: float
    fuzzy: bool


class RoutinePlan(BaseModel):
    name: str
    resolutions: list[ExerciseResolution]
    exercise_rows: int
    set_rows: int
    warnings: list[str]


class CreatedRoutine(BaseModel):
    z_pk: int
    name: str
    unique_hashid: int


class AppLifecycleReport(BaseModel):
    was_running: bool
    quit: bool
    relaunched: bool


class CreateProgramResult(BaseModel):
    dry_run: bool
    plan: list[RoutinePlan]
    created: list[CreatedRoutine]
    app: AppLifecycleReport | None
    backup_dir: str | None
    notice: str


# --------------------------------------------------------------------------- #
# Spec 02 write tools (add/update/remove exercise, reorder/update/archive routine)
# --------------------------------------------------------------------------- #
class RoutineRef(BaseModel):
    z_pk: int
    name: str


class FieldChange(BaseModel):
    field: str
    old: str | None
    new: str | None


class WriteToolResult(BaseModel):
    """Shared shape of every spec 02 write-tool result (same as CreateProgramResult)."""

    dry_run: bool
    app: AppLifecycleReport | None
    backup_dir: str | None
    notice: str


class AddExercisePlan(BaseModel):
    routine: RoutineRef
    resolution: ExerciseResolution
    index: int
    shifted: int = Field(
        description="Existing active exercises whose ZINDEX moves up by one to make room"
    )
    set_rows: int
    warnings: list[str]


class AddExerciseResult(WriteToolResult):
    plan: AddExercisePlan
    created_ue_pk: int | None


class UpdateExercisePlan(BaseModel):
    ue_pk: int
    exercise_name: str
    routine: RoutineRef
    changes: list[FieldChange]


class UpdateExerciseResult(WriteToolResult):
    plan: UpdateExercisePlan


class ReorderEntry(BaseModel):
    ue_pk: int
    exercise_name: str
    old_index: int
    new_index: int


class ReorderRoutinePlan(BaseModel):
    routine: RoutineRef
    order: list[ReorderEntry]


class ReorderRoutineResult(WriteToolResult):
    plan: ReorderRoutinePlan


class RemoveExercisePlan(BaseModel):
    ue_pk: int
    exercise_name: str
    routine: RoutineRef
    template_sets_removed: int = Field(
        description="Unlogged template sets soft-deleted with the exercise; logged history stays"
    )


class RemoveExerciseResult(WriteToolResult):
    plan: RemoveExercisePlan


class UpdateRoutinePlan(BaseModel):
    routine: RoutineRef
    changes: list[FieldChange]


class UpdateRoutineResult(WriteToolResult):
    plan: UpdateRoutinePlan
