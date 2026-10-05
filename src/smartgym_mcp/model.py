"""Server-side data model (API-client spec §3, §10), parsed from SmartGym API JSON.

The API sends every scalar as a string ("pause": "10"), mixes template, logged
and removed sets in one `sets[]`, and marks absent dates as null or "". Parsing
coerces and filters once, here, so nothing downstream sees raw API strings.
Sections: listGroup 1 = warm-up, 0 = main, 2 = cool-down (spec §10.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

Section = Literal["warmup", "main", "cooldown"]
SECTIONS: tuple[Section, ...] = ("warmup", "main", "cooldown")
LIST_GROUP_BY_SECTION: dict[Section, int] = {"warmup": 1, "main": 0, "cooldown": 2}
SECTION_BY_LIST_GROUP: dict[int, Section] = {g: s for s, g in LIST_GROUP_BY_SECTION.items()}


class ApiPayloadError(ValueError):
    """The server answered with an unexpected shape or a non-SUCCESS code."""


class TemplateSet(BaseModel):
    identifier: int
    unique_hashid: int
    index: int
    reps: float
    weight_kg: float
    date_added: str | None


class LoggedSet(BaseModel):
    identifier: int
    index: int
    reps: float
    weight_kg: float
    logged_at: str


class RoutineExercise(BaseModel):
    identifier: int
    unique_hashid: int
    catalog_id: int
    name: str
    section: Section
    index: int
    rest_seconds: int
    note: str | None
    removed: bool
    template_sets: list[TemplateSet]
    logged_sets: list[LoggedSet]


class Routine(BaseModel):
    identifier: int
    unique_hashid: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    number: int
    archived: bool
    removed: bool
    exercises: list[RoutineExercise]

    def active_exercises(self) -> list[RoutineExercise]:
        return sorted(
            (e for e in self.exercises if not e.removed),
            key=lambda e: (SECTIONS.index(e.section), e.index),
        )

    def section(self, section: Section) -> list[RoutineExercise]:
        return [e for e in self.active_exercises() if e.section == section]


class Workout(BaseModel):
    identifier: int
    routine_identifier: int | None
    start: str
    end: str | None
    duration_s: int
    calories: int | None
    avg_hr: int | None
    max_hr: int | None
    set_ids: list[int]


class EquipmentList(BaseModel):
    identifier: int
    name: str
    selected: bool
    equipment_ids: list[int]
    dumbbell_weights: str | None
    kettlebell_weights: str | None


class AccountData(BaseModel):
    routines: list[Routine]
    workouts: list[Workout]
    equipment_lists: list[EquipmentList]
    last_modified: str | None


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _req(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise ApiPayloadError(f"API object is missing required field {key!r}.")
    return raw[key]


def _float_value(key: str, value: Any) -> float:
    try:
        return float(str(value))
    except ValueError:
        raise ApiPayloadError(f"API field {key!r} = {value!r} is not a number.") from None


def _int_value(key: str, value: Any) -> int:
    number = _float_value(key, value)
    if not number.is_integer():
        raise ApiPayloadError(f"API field {key!r} = {value!r} is not a whole number.")
    return int(number)


def _int(raw: Mapping[str, Any], key: str) -> int:
    return _int_value(key, _req(raw, key))


def _float(raw: Mapping[str, Any], key: str) -> float:
    return _float_value(key, _req(raw, key))


def _opt_round(raw: Mapping[str, Any], key: str) -> int | None:
    value = raw.get(key)
    return round(_float_value(key, value)) if _present(value) else None


def _text(value: Any) -> str | None:
    return str(value) if _present(value) else None


def _section(raw: Mapping[str, Any]) -> Section:
    group = _int(raw, "listGroup")
    if group not in SECTION_BY_LIST_GROUP:
        raise ApiPayloadError(f"API field 'listGroup' = {group!r} is not a known section.")
    return SECTION_BY_LIST_GROUP[group]


def _parse_exercise(raw: Mapping[str, Any]) -> RoutineExercise:
    template: list[TemplateSet] = []
    logged: list[LoggedSet] = []
    for s in raw.get("sets") or []:
        if _present(s.get("dateRemoved")):
            continue
        if _present(s.get("dateLogged")):
            logged.append(
                LoggedSet(
                    identifier=_int(s, "identifier"),
                    index=_int(s, "index"),
                    reps=_float(s, "secondValue"),
                    weight_kg=_float(s, "thirdValue"),
                    logged_at=str(s["dateLogged"]),
                )
            )
        else:
            template.append(
                TemplateSet(
                    identifier=_int(s, "identifier"),
                    unique_hashid=_int(s, "uniqueHashID"),
                    index=_int(s, "index"),
                    reps=_float(s, "secondValue"),
                    weight_kg=_float(s, "thirdValue"),
                    date_added=_text(s.get("dateAdded")),
                )
            )
    return RoutineExercise(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        catalog_id=_int(raw, "id"),
        name=str(_req(raw, "name")),
        section=_section(raw),
        index=_int(raw, "idx"),
        rest_seconds=_int_value("pause", raw.get("pause") or 0),
        note=_text(raw.get("note")),
        removed=_present(raw.get("dateRemoved")),
        template_sets=sorted(template, key=lambda s: s.index),
        logged_sets=sorted(logged, key=lambda s: (s.logged_at, s.index)),
    )


def parse_routine(raw: Mapping[str, Any]) -> Routine:
    return Routine(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        name=str(_req(raw, "name")),
        days=_text(raw.get("days")),
        goal=_text(raw.get("goal")),
        note=_text(raw.get("note")),
        number=_int_value("number", raw.get("number") or 0),
        archived=_present(raw.get("dateArchived")),
        removed=_present(raw.get("dateRemoved")),
        exercises=[_parse_exercise(e) for e in raw.get("exercises") or []],
    )


def _check_success(raw: Mapping[str, Any]) -> None:
    code = raw.get("code")
    if code != "SUCCESS":
        raise ApiPayloadError(f"SmartGym API answered {code!r} instead of SUCCESS.")


def parse_routines_response(raw: Mapping[str, Any]) -> list[Routine]:
    _check_success(raw)
    return [parse_routine(r) for r in raw.get("routines") or []]


def _parse_workout(raw: Mapping[str, Any]) -> Workout:
    workout = raw.get("workout") or {}
    routine = raw.get("routine") or {}
    set_ids = [
        _int(s, "identifier")
        for e in routine.get("exercises") or []
        for s in e.get("sets") or []
    ]
    duration = workout.get("duration")
    return Workout(
        identifier=_int(raw, "identifier"),
        routine_identifier=_int(routine, "identifier") if routine.get("identifier") else None,
        start=str(_req(workout, "startDate")),
        end=_text(workout.get("endDate")),
        duration_s=int(_float_value("duration", duration)) if _present(duration) else 0,
        calories=_opt_round(workout, "calories"),
        avg_hr=_opt_round(workout, "averageHeartRate"),
        max_hr=_opt_round(workout, "maxHeartRate"),
        set_ids=set_ids,
    )


def _parse_equipment_list(raw: Mapping[str, Any]) -> EquipmentList:
    selected = str(raw.get("selectedEquipment") or "")
    return EquipmentList(
        identifier=_int(raw, "identifier"),
        name=str(raw.get("name") or ""),
        selected=str(raw.get("isSelected") or "0") == "1",
        equipment_ids=[_int_value("selectedEquipment", x) for x in selected.split(",") if x],
        dumbbell_weights=_text(raw.get("dumbbellWeights")),
        kettlebell_weights=_text(raw.get("kettlebellWeights")),
    )


def _has_workout_start(history: Mapping[str, Any]) -> bool:
    workout = history.get("workout")
    return isinstance(workout, Mapping) and _present(workout.get("startDate"))


def parse_history_all(raw: Mapping[str, Any]) -> AccountData:
    _check_success(raw)
    if str(raw.get("hasMore", False)).lower() in ("true", "1"):
        raise ApiPayloadError(
            "SmartGym returned a partial history (hasMore=true); paging is not supported, "
            "so nothing is shown rather than an incomplete picture."
        )
    return AccountData(
        routines=[parse_routine(r) for r in raw.get("routines") or []],
        workouts=[
            _parse_workout(h)
            for h in raw.get("histories") or []
            if not _present(h.get("dateRemoved")) and _has_workout_start(h)
        ],
        equipment_lists=[_parse_equipment_list(e) for e in raw.get("equipmentLists") or []],
        last_modified=_text(raw.get("lastModified")),
    )


__all__ = [
    "LIST_GROUP_BY_SECTION",
    "SECTIONS",
    "SECTION_BY_LIST_GROUP",
    "AccountData",
    "ApiPayloadError",
    "EquipmentList",
    "LoggedSet",
    "Routine",
    "RoutineExercise",
    "Section",
    "TemplateSet",
    "Workout",
    "parse_history_all",
    "parse_routine",
    "parse_routines_response",
]
