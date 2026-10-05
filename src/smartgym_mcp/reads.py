"""Read-tool views over AccountData (API-client spec §6, §10). Pure — no I/O.

Server times are UTC strings; views show the Mac's local time. A routine
exercise's "sessions" are its logged sets grouped by the workout they belong
to (history → set ids), newest first.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, tzinfo

from pydantic import BaseModel

from .catalog import CatalogEquipment
from .model import SECTIONS, AccountData, LoggedSet, Routine, RoutineExercise

ALLOWED_RANGE_DAYS = (7, 14, 30)


class RoutineSummary(BaseModel):
    identifier: int
    name: str
    days: str | None
    archived: bool
    warmup: int
    main: int
    cooldown: int


class RoutineListResult(BaseModel):
    count: int
    routines: list[RoutineSummary]


class SetEntry(BaseModel):
    set_no: int
    reps: float
    weight_kg: float  # 0.0 = bodyweight / untracked


class SessionEntry(BaseModel):
    date: str
    sets: list[SetEntry]


class ExerciseEntry(BaseModel):
    exercise_id: int
    name: str
    rest_seconds: int
    note: str | None
    template_sets: list[SetEntry]
    sessions: list[SessionEntry]
    top_set: SetEntry | None
    total_volume: float


class RoutineDetail(BaseModel):
    identifier: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    archived: bool
    warmup: list[ExerciseEntry]
    main: list[ExerciseEntry]
    cooldown: list[ExerciseEntry]


class WorkoutSession(BaseModel):
    identifier: int
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
    id: int
    name: str
    owned: bool


class EquipmentListResult(BaseModel):
    count: int
    equipment: list[EquipmentItem]
    dumbbell_weights: str | None
    kettlebell_weights: str | None


def _local(server_time: str, tz: tzinfo | None) -> datetime:
    """UTC server time → `tz`; tz=None resolves the system zone per instant (DST-aware)."""
    parsed = datetime.strptime(server_time[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC).astimezone(tz)


def list_routines(data: AccountData, *, include_archived: bool = False) -> RoutineListResult:
    routines = sorted(
        (r for r in data.routines if not r.removed and (include_archived or not r.archived)),
        key=lambda r: r.name.lower(),
    )
    items = [
        RoutineSummary(
            identifier=r.identifier,
            name=r.name,
            days=r.days,
            archived=r.archived,
            **{s: len(r.section(s)) for s in SECTIONS},
        )
        for r in routines
    ]
    return RoutineListResult(count=len(items), routines=items)


def _sessions(
    ex: RoutineExercise,
    workout_of: dict[int, tuple[int, datetime]],
    depth: int,
    tz: tzinfo | None,
) -> list[SessionEntry]:
    groups: dict[tuple[str, int | str], tuple[datetime, list[LoggedSet]]] = {}
    for s in ex.logged_sets:
        if s.identifier in workout_of:
            wid, start = workout_of[s.identifier]
            key: tuple[str, int | str] = ("workout", wid)
        else:
            start = _local(s.logged_at, tz)
            key = ("day", start.date().isoformat())
        started, members = groups.setdefault(key, (start, []))
        members.append(s)
        groups[key] = (min(started, start), members)
    newest_first = sorted(groups.values(), key=lambda g: g[0], reverse=True)
    out: list[SessionEntry] = []
    for started, members in newest_first[: max(depth, 0)]:
        ordered = sorted(members, key=lambda s: (s.index, s.logged_at))
        out.append(
            SessionEntry(
                date=started.date().isoformat(),
                sets=[
                    SetEntry(set_no=i + 1, reps=s.reps, weight_kg=s.weight_kg)
                    for i, s in enumerate(ordered)
                ],
            )
        )
    return out


def routine_detail(
    routine: Routine, data: AccountData, *, history_depth: int = 5, tz: tzinfo | None = None
) -> RoutineDetail:
    workout_of = {
        sid: (w.identifier, _local(w.start, tz)) for w in data.workouts for sid in w.set_ids
    }

    def entry(e: RoutineExercise) -> ExerciseEntry:
        sessions = _sessions(e, workout_of, history_depth, tz)
        latest = sessions[0].sets if sessions else []
        top = max(latest, key=lambda s: (s.weight_kg, s.reps)) if latest else None
        return ExerciseEntry(
            exercise_id=e.identifier,
            name=e.name,
            rest_seconds=e.rest_seconds,
            note=e.note,
            template_sets=[
                SetEntry(set_no=i + 1, reps=s.reps, weight_kg=s.weight_kg)
                for i, s in enumerate(e.template_sets)
            ],
            sessions=sessions,
            top_set=top,
            total_volume=round(sum(s.reps * s.weight_kg for s in latest), 3),
        )

    return RoutineDetail(
        identifier=routine.identifier,
        name=routine.name,
        days=routine.days,
        goal=routine.goal,
        note=routine.note,
        archived=routine.archived,
        warmup=[entry(e) for e in routine.section("warmup")],
        main=[entry(e) for e in routine.section("main")],
        cooldown=[entry(e) for e in routine.section("cooldown")],
    )


def workout_history(
    data: AccountData,
    *,
    days: int = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: Routine | None = None,
    limit: int = 20,
    offset: int = 0,
    today: date | None = None,
    tz: tzinfo | None = None,
) -> WorkoutHistoryResult:
    if date_from or date_to:
        start = date.fromisoformat(date_from) if date_from else date.min
        end = date.fromisoformat(date_to) if date_to else date.max
    else:
        if days not in ALLOWED_RANGE_DAYS:
            raise ValueError(f"days must be one of {ALLOWED_RANGE_DAYS}, got {days}")
        end = today or datetime.now(tz).date()
        start = end - timedelta(days=days)
    names = {r.identifier: r.name for r in data.routines}
    picked: list[tuple[datetime, int]] = []
    for i, w in enumerate(data.workouts):
        local = _local(w.start, tz)
        if not start <= local.date() <= end:
            continue
        if routine is not None and w.routine_identifier != routine.identifier:
            continue
        picked.append((local, i))
    picked.sort(key=lambda p: p[0], reverse=True)
    page = picked[offset : offset + limit]
    sessions = []
    for local, i in page:
        w = data.workouts[i]
        sessions.append(
            WorkoutSession(
                identifier=w.identifier,
                date=local.strftime("%Y-%m-%d %H:%M"),
                routine=names.get(w.routine_identifier) if w.routine_identifier else None,
                duration_min=w.duration_s // 60,
                calories=w.calories,
                avg_hr=w.avg_hr,
                max_hr=w.max_hr,
            )
        )
    has_more = offset + len(sessions) < len(picked)
    return WorkoutHistoryResult(
        total=len(picked),
        count=len(sessions),
        offset=offset,
        has_more=has_more,
        next_offset=offset + len(sessions) if has_more else None,
        sessions=sessions,
    )


def equipment(
    data: AccountData, catalog: Sequence[CatalogEquipment], *, owned_only: bool = True
) -> EquipmentListResult:
    lists = [e for e in data.equipment_lists if e.selected] or data.equipment_lists[:1]
    owned = {i for e in lists for i in e.equipment_ids}
    items = [
        EquipmentItem(id=c.id, name=c.name, owned=c.id in owned)
        for c in sorted(catalog, key=lambda c: (c.category, c.name))
    ]
    if owned_only:
        items = [i for i in items if i.owned]
    first = lists[0] if lists else None
    return EquipmentListResult(
        count=len(items),
        equipment=items,
        dumbbell_weights=first.dumbbell_weights if first else None,
        kettlebell_weights=first.kettlebell_weights if first else None,
    )


__all__ = [
    "ALLOWED_RANGE_DAYS",
    "EquipmentItem",
    "EquipmentListResult",
    "ExerciseEntry",
    "RoutineDetail",
    "RoutineListResult",
    "RoutineSummary",
    "SessionEntry",
    "SetEntry",
    "WorkoutHistoryResult",
    "WorkoutSession",
    "equipment",
    "list_routines",
    "routine_detail",
    "workout_history",
]
