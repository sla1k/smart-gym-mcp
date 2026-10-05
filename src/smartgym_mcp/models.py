"""Tool input specs shared by the diff and the service (API-client spec §6)."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

DAYS_FORMAT = (
    'comma-separated weekday numbers, 1 = Sunday, 2 = Monday … 7 = Saturday (e.g. "2,4,6"); '
    '"" clears'
)
_DAYS = re.compile(r"[1-7](,[1-7])*")


def _check_days(value: str | None) -> str | None:
    if value is None or value.strip() == "" or _DAYS.fullmatch(value):
        return value
    raise ValueError(f"days must be {DAYS_FORMAT}; got {value!r}.")


Days = Annotated[
    str | None, AfterValidator(_check_days), Field(description=f"Days: {DAYS_FORMAT}")
]


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
    days: Days = None
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
