"""Tool input specs shared by the diff and the service (API-client spec §6)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetSpec(BaseModel):
    reps: float = Field(gt=0, description="Target reps for this set")
    weight_kg: float = Field(default=0.0, ge=0, description="Target weight; 0 = bodyweight")


class ExerciseSpec(BaseModel):
    exercise: str = Field(
        description="Catalog exercise name (fuzzy-matched) or a numeric catalog id"
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    note: str | None = None
    sets: list[SetSpec] | None = Field(
        default=None,
        min_length=1,
        description="Template sets; omitted = one default set (flagged in dry-run)",
    )


class RoutineSpec(BaseModel):
    name: str = Field(min_length=1)
    days: str | None = None
    goal: str | None = None
    note: str | None = None
    warmup: list[ExerciseSpec] = Field(
        default_factory=list, description="Warm-up section, in order (optional)"
    )
    exercises: list[ExerciseSpec] = Field(min_length=1)
    cooldown: list[ExerciseSpec] = Field(
        default_factory=list, description="Cool-down section, in order (optional)"
    )


class ExerciseResolution(BaseModel):
    input: str
    resolved_name: str
    catalog_id: int
    confidence: float
    fuzzy: bool


class FieldChange(BaseModel):
    field: str
    old: str | None
    new: str | None
