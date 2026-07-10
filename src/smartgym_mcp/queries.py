"""Read queries — the canonical SQL with the critical join baked in.

All functions take a read-only sqlite3.Connection and return Pydantic models.
The join `ZVALUES.ZEXERCISE → ZUNIQEXERCISE.Z_PK` and CoreData epoch math live
here so callers never re-derive them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import db
from .models import (
    EquipmentItem,
    EquipmentListResult,
    ExerciseEntry,
    RoutineDetail,
    RoutineListResult,
    RoutineSummary,
    SessionEntry,
    SetEntry,
    WorkoutHistoryResult,
    WorkoutSession,
)

ALLOWED_RANGE_DAYS = (7, 14, 30)


class RoutineNotFound(ValueError):
    pass


class AmbiguousRoutine(ValueError):
    pass


def _iso_local(ts: float | None) -> str | None:
    if ts is None:
        return None
    return db.coredata_to_datetime(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# Routine resolution (name OR z_pk)
# --------------------------------------------------------------------------- #
def resolve_routine(conn: sqlite3.Connection, routine: str | int) -> int:
    """Resolve a routine reference to a Z_PK. Numeric → Z_PK; else exact (then
    substring) case-insensitive name match. Raises actionable errors."""
    s = str(routine).strip()
    if s.isdigit():
        row = conn.execute(
            "SELECT Z_PK FROM ZROUTINE WHERE Z_PK = ? AND ZDATEREMOVED IS NULL",
            (int(s),),
        ).fetchone()
        if row:
            return int(row[0])
        raise RoutineNotFound(f"No active routine with z_pk={s}.")

    rows = conn.execute(
        "SELECT Z_PK, ZNAME FROM ZROUTINE "
        "WHERE ZDATEREMOVED IS NULL AND LOWER(ZNAME) = LOWER(?)",
        (s,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT Z_PK, ZNAME FROM ZROUTINE "
            "WHERE ZDATEREMOVED IS NULL AND LOWER(ZNAME) LIKE '%' || LOWER(?) || '%'",
            (s,),
        ).fetchall()
    if not rows:
        raise RoutineNotFound(
            f"No routine matching {routine!r}. Use smartgym_list_routines to see names."
        )
    if len(rows) > 1:
        listed = ", ".join(f"{r['ZNAME']} (z_pk={r['Z_PK']})" for r in rows)
        raise AmbiguousRoutine(
            f"Multiple routines match {routine!r}: {listed}. Pass a z_pk to disambiguate."
        )
    return int(rows[0]["Z_PK"])


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def list_routines(conn: sqlite3.Connection, include_hidden: bool = False) -> RoutineListResult:
    sql = (
        "SELECT r.Z_PK, r.ZNAME, r.ZDAYS, r.ZHIDDEN, r.ZHASSYNCED, r.ZDATELASTUPDATEDBYAI, "
        "(SELECT COUNT(*) FROM ZUNIQEXERCISE ue "
        " WHERE ue.ZROUTINE = r.Z_PK AND ue.ZDATEREMOVED IS NULL) AS exercise_count "
        "FROM ZROUTINE r WHERE r.ZDATEREMOVED IS NULL"
    )
    if not include_hidden:
        sql += " AND r.ZHIDDEN = 0"
    sql += " ORDER BY r.ZNAME"

    routines = [
        RoutineSummary(
            z_pk=row["Z_PK"],
            name=row["ZNAME"],
            days=row["ZDAYS"],
            hidden=bool(row["ZHIDDEN"]),
            has_synced=bool(row["ZHASSYNCED"]),
            last_updated_by_ai=_iso_local(row["ZDATELASTUPDATEDBYAI"]),
            exercise_count=row["exercise_count"],
        )
        for row in conn.execute(sql)
    ]
    return RoutineListResult(count=len(routines), routines=routines)


def get_routine(
    conn: sqlite3.Connection, routine: str | int, history_depth: int = 5
) -> RoutineDetail:
    z_pk = resolve_routine(conn, routine)
    rinfo = conn.execute(
        "SELECT ZNAME, ZDAYS, ZHIDDEN, ZHASSYNCED, ZDATELASTUPDATEDBYAI "
        "FROM ZROUTINE WHERE Z_PK = ?",
        (z_pk,),
    ).fetchone()
    if rinfo is None:  # resolved a moment ago — only None on a concurrent delete
        raise RoutineNotFound(f"Routine z_pk={z_pk} disappeared mid-read.")

    exercises = conn.execute(
        "SELECT ue.Z_PK AS ue_pk, ue.ZINDEX AS idx, e.ZNAME AS exercise_name, "
        "ue.ZPAUSE AS rest_seconds, ue.ZNOTE AS note "
        "FROM ZUNIQEXERCISE ue JOIN ZEXERCISE e ON ue.ZEXERCISE = e.Z_PK "
        "WHERE ue.ZROUTINE = ? AND ue.ZDATEREMOVED IS NULL ORDER BY ue.ZINDEX",
        (z_pk,),
    ).fetchall()

    # All sets for the routine's exercises, newest first (join: ZVALUES → ZUNIQEXERCISE.Z_PK).
    set_rows = conn.execute(
        "SELECT v.ZEXERCISE AS ue_pk, v.ZINDEX AS set_no, "
        "CAST(v.ZSECONDVALUE AS INTEGER) AS reps, v.ZTHIRDVALUE AS weight_kg, "
        f"date(v.ZDATEADDED {db.EPOCH_SQL}) AS session_date "
        "FROM ZVALUES v JOIN ZUNIQEXERCISE ue ON v.ZEXERCISE = ue.Z_PK "
        "WHERE ue.ZROUTINE = ? AND ue.ZDATEREMOVED IS NULL AND v.ZDATEREMOVED IS NULL "
        "ORDER BY v.ZEXERCISE, v.ZDATEADDED DESC, v.ZINDEX",
        (z_pk,),
    ).fetchall()

    # Group sets → sessions (by calendar day, newest first), keeping the most
    # recent `history_depth` sessions per exercise. Accumulate in plain dicts and
    # construct the models once, so we never mutate a built Pydantic instance.
    # set_rows are ordered (ue_pk, date DESC, set ASC), so dict key order is date-desc.
    grouped: dict[int, dict[str, list[SetEntry]]] = {}
    for row in set_rows:
        sessions = grouped.setdefault(row["ue_pk"], {})
        date = row["session_date"]
        if date not in sessions:
            if len(sessions) >= history_depth:
                continue  # older session beyond the requested depth
            sessions[date] = []
        sessions[date].append(
            SetEntry(set_no=row["set_no"], reps=row["reps"], weight_kg=row["weight_kg"])
        )

    out = []
    for ex in exercises:
        ex_sessions = [
            SessionEntry(date=date, sets=sets)
            for date, sets in grouped.get(ex["ue_pk"], {}).items()
        ]
        top_set: SetEntry | None = None
        total_volume = 0.0
        if ex_sessions:
            latest = ex_sessions[0].sets
            top_set = max(latest, key=lambda s: (s.weight_kg, s.reps))
            total_volume = sum(s.reps * s.weight_kg for s in latest)
        out.append(
            ExerciseEntry(
                ue_pk=ex["ue_pk"],
                index=ex["idx"],
                exercise_name=ex["exercise_name"],
                rest_seconds=ex["rest_seconds"],
                note=ex["note"],
                sessions=ex_sessions,
                top_set=top_set,
                total_volume=total_volume,
            )
        )

    return RoutineDetail(
        z_pk=z_pk,
        name=rinfo["ZNAME"],
        days=rinfo["ZDAYS"],
        hidden=bool(rinfo["ZHIDDEN"]),
        has_synced=bool(rinfo["ZHASSYNCED"]),
        last_updated_by_ai=_iso_local(rinfo["ZDATELASTUPDATEDBYAI"]),
        exercises=out,
    )


def _local_midnight_coredata(date_str: str) -> float:
    return db.datetime_to_coredata(datetime.strptime(date_str, "%Y-%m-%d"))


def get_workout_history(
    conn: sqlite3.Connection,
    *,
    days: int = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: str | int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> WorkoutHistoryResult:
    where = ["w.ZSTARTDATE IS NOT NULL"]
    params: list[float | int] = []

    if date_from or date_to:
        if date_from:
            where.append("w.ZSTARTDATE >= ?")
            params.append(_local_midnight_coredata(date_from))
        if date_to:  # inclusive end-of-day → strictly before next midnight
            where.append("w.ZSTARTDATE < ?")
            params.append(_local_midnight_coredata(date_to) + 86400)
    else:
        if days not in ALLOWED_RANGE_DAYS:
            raise ValueError(f"days must be one of {ALLOWED_RANGE_DAYS}, got {days}")
        where.append("w.ZSTARTDATE >= ?")
        params.append(db.now_coredata() - days * 86400)

    if routine is not None:
        where.append("h.ZROUTINE = ?")
        params.append(resolve_routine(conn, routine))

    clause = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(DISTINCT w.Z_PK) FROM ZWORKOUT w "
        f"JOIN ZHISTORY h ON h.ZWORKOUT = w.Z_PK WHERE {clause}",
        params,
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT w.Z_PK AS workout_pk, "
        f"date(w.ZSTARTDATE {db.EPOCH_SQL}) AS date, MAX(r.ZNAME) AS routine, "
        "CAST(w.ZDURATION / 60 AS INTEGER) AS duration_min, "
        "CAST(w.ZCALORIES AS INTEGER) AS calories, "
        "CAST(w.ZAVERAGEHEARTRATE AS INTEGER) AS avg_hr, "
        "CAST(w.ZMAXHEARTRATE AS INTEGER) AS max_hr "
        "FROM ZWORKOUT w JOIN ZHISTORY h ON h.ZWORKOUT = w.Z_PK "
        "LEFT JOIN ZROUTINE r ON h.ZROUTINE = r.Z_PK "
        f"WHERE {clause} GROUP BY w.Z_PK ORDER BY w.ZSTARTDATE DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    sessions = [
        WorkoutSession(
            workout_pk=r["workout_pk"],
            date=r["date"],
            routine=r["routine"],
            duration_min=r["duration_min"] or 0,
            calories=r["calories"],
            avg_hr=r["avg_hr"],
            max_hr=r["max_hr"],
        )
        for r in rows
    ]
    has_more = offset + len(sessions) < total
    return WorkoutHistoryResult(
        total=total,
        count=len(sessions),
        offset=offset,
        has_more=has_more,
        next_offset=offset + limit if has_more else None,
        sessions=sessions,
    )


def get_equipment(conn: sqlite3.Connection, owned_only: bool = True) -> EquipmentListResult:
    sql = (
        "SELECT ZNAME AS name, ZCATEGORY AS category_id, ZSELECTED AS owned, "
        "ZSELECTEDWEIGHTS AS selected_weights FROM ZEQUIPMENT"
    )
    if owned_only:
        sql += " WHERE ZSELECTED = 1"
    sql += " ORDER BY ZCATEGORY, ZNAME"

    items = [
        EquipmentItem(
            name=row["name"],
            category_id=row["category_id"],
            owned=bool(row["owned"]),
            selected_weights=row["selected_weights"] or None,
        )
        for row in conn.execute(sql)
    ]
    return EquipmentListResult(count=len(items), equipment=items)
