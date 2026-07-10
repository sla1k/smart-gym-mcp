"""Write procedures, granular by entity (specs 02/03).

Layering mirrors the domain: Program → Routine → Exercise → Set.
    insert_routine / insert_exercise / insert_set    one entity each
    insert_routine_tree                              composes the three
    apply_program                                    validate + loop the trees
    mark_routine_pending                             THE sync primitive — every
                                                     mutation of an existing
                                                     routine must call it
Each granular function is the reuse surface for the remaining spec 02 tools
(add_exercise → insert_exercise, etc.). All run inside the caller's open RW
transaction (see lifecycle.managed_write).

Insert recipe verified live by the Phase 0 spike (2026-07-10, app v7.10.1):
- `ZHASSYNCED = 0` marks the routine pending; on next launch the app's
  ResyncRoutinesManager POSTs it to `routine/add/` and flips the flag to 1.
- `ZIDENTIFIER` is server-assigned on push — we insert a placeholder
  (db.generate_identifier) the app replaces.
- `ZUNIQUEHASHID` (db.generate_uniquehashid) is client identity; survives push.
- Template sets are ZVALUES rows with ZDATELOGGED = NULL.
- `ZDATEADDED` is midnight-local; ZPRECISEDATEADDED carries the real time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import db, queries
from .matching import ExerciseCatalog, UnresolvedExercise
from .models import (
    AddExercisePlan,
    CreatedRoutine,
    ExerciseResolution,
    FieldChange,
    RemoveExercisePlan,
    ReorderEntry,
    ReorderRoutinePlan,
    RoutinePlan,
    RoutineRef,
    RoutineSpec,
    SetSpec,
    UpdateExercisePlan,
    UpdateRoutinePlan,
)

# One default template set when the caller omits sets (surfaced as a warning —
# spec 03 §E: every exercise in observed routines carries >= 1 template set).
_DEFAULT_SET = SetSpec(reps=10, weight_kg=0.0)


class ProgramValidationError(ValueError):
    pass


class WriteValidationError(ValueError):
    """A single-routine write tool rejected the whole call — nothing was written."""


# --------------------------------------------------------------------------- #
# Per-transaction write context
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WriteContext:
    """Timestamps + sampled defaults, computed once per write transaction."""

    now: datetime
    now_cd: float
    midnight_cd: float
    listgroup: int


def build_write_context(conn: sqlite3.Connection) -> WriteContext:
    now = datetime.now().astimezone()
    row = conn.execute(
        # Most common ZLISTGROUP among active exercises — mandatory column whose
        # semantics are undecoded (spec 02: sample, never invent).
        "SELECT ZLISTGROUP FROM ZUNIQEXERCISE WHERE ZDATEREMOVED IS NULL "
        "AND ZLISTGROUP IS NOT NULL GROUP BY ZLISTGROUP ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return WriteContext(
        now=now,
        now_cd=db.datetime_to_coredata(now),
        midnight_cd=db.datetime_to_coredata(
            now.replace(hour=0, minute=0, second=0, microsecond=0)
        ),
        listgroup=int(row[0]) if row else 0,
    )


# --------------------------------------------------------------------------- #
# Sync primitive
# --------------------------------------------------------------------------- #
def mark_routine_pending(conn: sqlite3.Connection, routine_pk: int, now_cd: float) -> None:
    """Flag a routine for the app's next sync push (`0` = pending, spec 03 §A)
    and stamp the AI-edit timestamp. Every mutation of an existing routine —
    field edits, exercise add/remove/reorder, set changes — must call this or
    the change never leaves the Mac."""
    conn.execute(
        "UPDATE ZROUTINE SET ZHASSYNCED = 0, ZDATELASTUPDATEDBYAI = ? WHERE Z_PK = ?",
        (now_cd, routine_pk),
    )


# ZEXERCISESTATEQUEUE.ZSTATE for a newly added exercise (verified live 2026-07-10:
# with this row the exercise survives the push; without it the routine/update/
# response prunes any exercise the server does not know).
_EXERCISE_STATE_ADDED = 1


def enqueue_exercise_added(
    conn: sqlite3.Connection, ctx: WriteContext, routine_pk: int, ue_pk: int
) -> int:
    """Queue the 'exercise added' sync event for an EXISTING routine.

    The dirty flag alone is not enough for adds: the app pushes the routine and
    then applies the server's authoritative content back, deleting exercises the
    server does not know. This ZEXERCISESTATEQUEUE row is what makes the app
    announce the new exercise (routine/updateExercise/). Field edits, reorders,
    and removals do NOT need it (verified live). Fresh routines (routine/add/)
    do not need it either — their whole tree is pushed at birth.

    Caveat: ZROUTINEID carries the routine's current ZIDENTIFIER; for a routine
    that has never been pushed this is still the local placeholder, and the
    queue row is a harmless server-side no-op (the routine/add/ push carries the
    exercise anyway)."""
    routine_identifier = conn.execute(
        "SELECT ZIDENTIFIER FROM ZROUTINE WHERE Z_PK = ?", (routine_pk,)
    ).fetchone()[0]
    ue_hashid = conn.execute(
        "SELECT ZUNIQUEHASHID FROM ZUNIQEXERCISE WHERE Z_PK = ?", (ue_pk,)
    ).fetchone()[0]
    q_pk, q_ent = db.next_pk(conn, "ExerciseStateQueue")
    conn.execute(
        "INSERT INTO ZEXERCISESTATEQUEUE (Z_PK, Z_ENT, Z_OPT, ZEXERCISEHASHID, "
        "ZROUTINEID, ZSTATE, ZDATEADDED, ZHISTORYDATE) VALUES (?, ?, 1, ?, ?, ?, ?, NULL)",
        (q_pk, q_ent, ue_hashid, routine_identifier, _EXERCISE_STATE_ADDED, ctx.now_cd),
    )
    return q_pk


# --------------------------------------------------------------------------- #
# Granular inserts — one entity level each
# --------------------------------------------------------------------------- #
def insert_routine(
    conn: sqlite3.Connection, ctx: WriteContext, spec: RoutineSpec
) -> tuple[int, int]:
    """Insert the ZROUTINE row only (born pending sync).

    Returns (z_pk, unique_hashid)."""
    routine_pk, routine_ent = db.next_pk(conn, "Routine")
    hashid = db.generate_uniquehashid(conn, ctx.now)
    next_number = int(
        conn.execute("SELECT COALESCE(MAX(ZNUMBER), 0) + 1 FROM ZROUTINE").fetchone()[0]
    )
    conn.execute(
        """INSERT INTO ZROUTINE (Z_PK, Z_ENT, Z_OPT, ZHASSYNCED, ZHIDDEN,
           ZIDENTIFIER, ZISTEMP, ZMIGRATEDSETS, ZNUMBER, ZPREMADEWORKOUTID,
           ZREFERENCE, ZUNIQUEHASHID, ZSMARTTRAINERWORKOUT, ZSTUDENT,
           ZDATECREATED, ZDATELASTUPDATEDBYAI, ZDATEREMOVED, ZDATETORENEW,
           ZDAYS, ZGOAL, ZNAME, ZNOTE, ZSTRETCH)
           VALUES (?, ?, 1, 0, 0, ?, 0, 1, ?, NULL, 0, ?, NULL, NULL,
           ?, ?, NULL, NULL, ?, ?, ?, ?, NULL)""",
        (
            routine_pk,
            routine_ent,
            db.generate_identifier(conn),
            next_number,
            hashid,
            ctx.now_cd,
            ctx.now_cd,
            spec.days,
            spec.goal,
            spec.name,
            spec.note,
        ),
    )
    return routine_pk, hashid


def insert_exercise(
    conn: sqlite3.Connection,
    ctx: WriteContext,
    routine_pk: int,
    index: int,
    exercise_pk: int,
    *,
    rest_seconds: int | None = None,
    note: str | None = None,
) -> int:
    """Insert one ZUNIQEXERCISE row linking a catalog exercise into a routine
    at `index`. Returns its Z_PK. Does NOT mark the routine pending — creates
    do that at birth; spec 02's add_exercise must call mark_routine_pending."""
    ue_pk, ue_ent = db.next_pk(conn, "UniqExercise")
    conn.execute(
        """INSERT INTO ZUNIQEXERCISE (Z_PK, Z_ENT, Z_OPT, ZIDENTIFIER,
           ZINDEX, ZMODE, ZPAUSE, ZREPLACEDEXERCISEID, ZUNIQUEHASHID,
           ZALTERNATIVEHISTORY, ZEXERCISE, ZROUTINE, ZDATEADDED,
           ZDATEREMOVED, ZPRECISEDATEADDED, ZPRECISEDATEREMOVED,
           ZNOTE, ZLISTGROUP)
           VALUES (?, ?, 1, ?, ?, 0, ?, NULL, ?, NULL, ?, ?, ?, NULL,
           ?, NULL, ?, ?)""",
        (
            ue_pk,
            ue_ent,
            db.generate_identifier(conn),
            index,
            rest_seconds or 0,
            db.generate_uniquehashid(conn, ctx.now),
            exercise_pk,
            routine_pk,
            ctx.midnight_cd,
            ctx.now_cd,
            note,
            ctx.listgroup,
        ),
    )
    return ue_pk


def insert_set(
    conn: sqlite3.Connection,
    ctx: WriteContext,
    ue_pk: int,
    index: int,
    set_spec: SetSpec,
) -> int:
    """Insert one template set (ZVALUES row, ZDATELOGGED = NULL). Returns its Z_PK."""
    values_pk, values_ent = db.next_pk(conn, "Values")
    conn.execute(
        """INSERT INTO ZVALUES (Z_PK, Z_ENT, Z_OPT, ZIDENTIFIER,
           ZINDEX, ZTYPE, ZUNIQUEHASHID, ZEXERCISE, ZUPDATEQUEUEADDED,
           ZUPDATEQUEUEREMOVED, ZUPDATEQUEUEUPDATED, ZDATEADDED,
           ZDATELOGGED, ZDATEREMOVED, ZFIRSTVALUE, ZPRECISEDATEADDED,
           ZPRECISEDATELOGGED, ZPRECISEDATEREMOVED, ZSECONDVALUE,
           ZTHIRDVALUE)
           VALUES (?, ?, 1, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, NULL,
           NULL, 1.0, ?, NULL, NULL, ?, ?)""",
        (
            values_pk,
            values_ent,
            db.generate_identifier(conn),
            index,
            db.generate_uniquehashid(conn, ctx.now),
            ue_pk,
            ctx.midnight_cd,
            ctx.now_cd,
            set_spec.reps,
            set_spec.weight_kg,
        ),
    )
    return values_pk


def insert_routine_tree(
    conn: sqlite3.Connection,
    ctx: WriteContext,
    spec: RoutineSpec,
    resolutions: list[ExerciseResolution],
) -> CreatedRoutine:
    """Compose the three levels for one routine: routine → exercises → sets."""
    routine_pk, hashid = insert_routine(conn, ctx, spec)
    for index, (ex, res) in enumerate(zip(spec.exercises, resolutions, strict=True)):
        ue_pk = insert_exercise(
            conn,
            ctx,
            routine_pk,
            index,
            res.z_pk,
            rest_seconds=ex.rest_seconds,
            note=ex.note,
        )
        for set_index, set_spec in enumerate(ex.sets or [_DEFAULT_SET]):
            insert_set(conn, ctx, ue_pk, set_index, set_spec)
    return CreatedRoutine(z_pk=routine_pk, name=spec.name, unique_hashid=hashid)


# --------------------------------------------------------------------------- #
# Program planning + composition
# --------------------------------------------------------------------------- #
def _plan_routine(
    catalog: ExerciseCatalog,
    spec: RoutineSpec,
    existing_names: set[str],
    problems: list[str],
) -> RoutinePlan:
    """Plan one routine, appending any blocking problems to `problems`."""
    warnings: list[str] = []
    if spec.name.strip().lower() in existing_names:
        problems.append(
            f"An active routine named {spec.name!r} already exists "
            "(program creation is additive-only; archive it first)."
        )
    resolutions: list[ExerciseResolution] = []
    set_rows = 0
    for ex in spec.exercises:
        try:
            res = catalog.resolve(ex.exercise)
        except UnresolvedExercise as exc:
            problems.append(str(exc))
            continue
        if res.fuzzy:
            warnings.append(
                f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
                f"(z_pk={res.z_pk}, confidence {res.confidence})."
            )
        resolutions.append(res)
        set_rows += len(ex.sets) if ex.sets else 1
        if not ex.sets:
            warnings.append(
                f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
                f"{_DEFAULT_SET.reps:g} reps (bodyweight)."
            )
    return RoutinePlan(
        name=spec.name,
        resolutions=resolutions,
        exercise_rows=len(spec.exercises),
        set_rows=set_rows,
        warnings=warnings,
    )


def _active_routine_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0]).lower()
        for r in conn.execute(
            "SELECT ZNAME FROM ZROUTINE "
            "WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 AND ZNAME IS NOT NULL"
        )
    }


def plan_program(conn: sqlite3.Connection, routines: list[RoutineSpec]) -> list[RoutinePlan]:
    """Validate the whole program and return the per-routine plan.

    All-or-nothing: any unresolved exercise or name collision rejects the whole
    program (spec 03 §C decision 4). Works on RO or RW connections.
    """
    problems: list[str] = []

    seen: set[str] = set()
    for spec in routines:
        key = spec.name.strip().lower()
        if key in seen:
            problems.append(f"Duplicate routine name in program: {spec.name!r}.")
        seen.add(key)

    existing = _active_routine_names(conn)
    catalog = ExerciseCatalog.load(conn)
    plans = [_plan_routine(catalog, spec, existing, problems) for spec in routines]

    if problems:
        raise ProgramValidationError(
            "Program rejected — nothing was written:\n- " + "\n- ".join(problems)
        )
    return plans


def apply_program(
    conn: sqlite3.Connection, routines: list[RoutineSpec]
) -> tuple[list[RoutinePlan], list[CreatedRoutine]]:
    """Insert the program inside the caller's open RW transaction.

    Validates against this connection (TOCTOU-safe) first; returns the plan it
    validated plus the created routines.
    """
    plans = plan_program(conn, routines)
    ctx = build_write_context(conn)
    created = [
        insert_routine_tree(conn, ctx, spec, plan.resolutions)
        for spec, plan in zip(routines, plans, strict=True)
    ]
    return plans, created


# --------------------------------------------------------------------------- #
# Spec 02 single-routine tools — shared lookups
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ActiveExercise:
    ue_pk: int
    routine: RoutineRef
    exercise_name: str
    index: int
    rest_seconds: int | None
    note: str | None


def _routine_ref(conn: sqlite3.Connection, routine: str | int) -> RoutineRef:
    z_pk = queries.resolve_routine(conn, routine)
    name = conn.execute("SELECT ZNAME FROM ZROUTINE WHERE Z_PK = ?", (z_pk,)).fetchone()[0]
    return RoutineRef(z_pk=z_pk, name=str(name))


def _get_active_exercise(conn: sqlite3.Connection, ue_pk: int) -> _ActiveExercise:
    row = conn.execute(
        "SELECT ue.ZROUTINE, r.ZNAME, e.ZNAME, ue.ZINDEX, ue.ZPAUSE, ue.ZNOTE "
        "FROM ZUNIQEXERCISE ue "
        "JOIN ZEXERCISE e ON ue.ZEXERCISE = e.Z_PK "
        "JOIN ZROUTINE r ON ue.ZROUTINE = r.Z_PK "
        "WHERE ue.Z_PK = ? AND ue.ZDATEREMOVED IS NULL",
        (ue_pk,),
    ).fetchone()
    if row is None:
        raise WriteValidationError(
            f"No active exercise with ue_pk={ue_pk} (unknown or removed). "
            "Use smartgym_get_routine to list a routine's ue_pks."
        )
    return _ActiveExercise(
        ue_pk=ue_pk,
        routine=RoutineRef(z_pk=int(row[0]), name=str(row[1])),
        exercise_name=str(row[2]),
        index=int(row[3]),
        rest_seconds=int(row[4]) if row[4] is not None else None,
        note=str(row[5]) if row[5] is not None else None,
    )


def _next_index(conn: sqlite3.Connection, routine_pk: int) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(MAX(ZINDEX), -1) + 1 FROM ZUNIQEXERCISE "
            "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL",
            (routine_pk,),
        ).fetchone()[0]
    )


# --------------------------------------------------------------------------- #
# add_exercise
# --------------------------------------------------------------------------- #
def plan_add_exercise(
    conn: sqlite3.Connection,
    routine: str | int,
    exercise: str,
    *,
    index: int | None = None,
    sets: list[SetSpec] | None = None,
) -> AddExercisePlan:
    """Validate + describe one exercise insertion. Works on RO or RW connections."""
    ref = _routine_ref(conn, routine)
    try:
        res = ExerciseCatalog.load(conn).resolve(exercise)
    except UnresolvedExercise as exc:
        raise WriteValidationError(str(exc)) from exc

    warnings: list[str] = []
    if res.fuzzy:
        warnings.append(
            f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
            f"(z_pk={res.z_pk}, confidence {res.confidence})."
        )
    append_index = _next_index(conn, ref.z_pk)
    if index is None:
        target, shifted = append_index, 0
    else:
        if not 0 <= index <= append_index:
            raise WriteValidationError(
                f"index must be between 0 and {append_index} (append); got {index}."
            )
        target = index
        shifted = int(
            conn.execute(
                "SELECT COUNT(*) FROM ZUNIQEXERCISE "
                "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL AND ZINDEX >= ?",
                (ref.z_pk, index),
            ).fetchone()[0]
        )
    if not sets:
        warnings.append(
            f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
            f"{_DEFAULT_SET.reps:g} reps (bodyweight)."
        )
    return AddExercisePlan(
        routine=ref,
        resolution=res,
        index=target,
        shifted=shifted,
        set_rows=len(sets) if sets else 1,
        warnings=warnings,
    )


def apply_add_exercise(
    conn: sqlite3.Connection,
    routine: str | int,
    exercise: str,
    *,
    index: int | None = None,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
) -> tuple[AddExercisePlan, int]:
    """Insert the exercise (+ template sets) inside the caller's open RW
    transaction. Revalidates on this connection (TOCTOU-safe). Returns
    (plan, new ue_pk)."""
    plan = plan_add_exercise(conn, routine, exercise, index=index, sets=sets)
    ctx = build_write_context(conn)
    if plan.shifted:
        conn.execute(
            "UPDATE ZUNIQEXERCISE SET ZINDEX = ZINDEX + 1 "
            "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL AND ZINDEX >= ?",
            (plan.routine.z_pk, plan.index),
        )
    ue_pk = insert_exercise(
        conn,
        ctx,
        plan.routine.z_pk,
        plan.index,
        plan.resolution.z_pk,
        rest_seconds=rest_seconds,
        note=note,
    )
    for set_index, set_spec in enumerate(sets or [_DEFAULT_SET]):
        insert_set(conn, ctx, ue_pk, set_index, set_spec)
    enqueue_exercise_added(conn, ctx, plan.routine.z_pk, ue_pk)
    mark_routine_pending(conn, plan.routine.z_pk, ctx.now_cd)
    return plan, ue_pk


# --------------------------------------------------------------------------- #
# update_exercise
# --------------------------------------------------------------------------- #
def plan_update_exercise(
    conn: sqlite3.Connection,
    ue_pk: int,
    *,
    note: str | None = None,
    rest_seconds: int | None = None,
    index: int | None = None,
) -> UpdateExercisePlan:
    ex = _get_active_exercise(conn, ue_pk)
    changes: list[FieldChange] = []
    if note is not None:
        changes.append(FieldChange(field="note", old=ex.note, new=note or None))
    if rest_seconds is not None:
        if rest_seconds < 0:
            raise WriteValidationError(f"rest_seconds must be >= 0; got {rest_seconds}.")
        changes.append(
            FieldChange(
                field="rest_seconds",
                old=str(ex.rest_seconds) if ex.rest_seconds is not None else None,
                new=str(rest_seconds),
            )
        )
    if index is not None:
        if index < 0:
            raise WriteValidationError(f"index must be >= 0; got {index}.")
        changes.append(FieldChange(field="index", old=str(ex.index), new=str(index)))
    if not changes:
        raise WriteValidationError(
            "Nothing to update — pass at least one of note, rest_seconds, index."
        )
    return UpdateExercisePlan(
        ue_pk=ue_pk, exercise_name=ex.exercise_name, routine=ex.routine, changes=changes
    )


def apply_update_exercise(
    conn: sqlite3.Connection,
    ue_pk: int,
    *,
    note: str | None = None,
    rest_seconds: int | None = None,
    index: int | None = None,
) -> UpdateExercisePlan:
    plan = plan_update_exercise(conn, ue_pk, note=note, rest_seconds=rest_seconds, index=index)
    ctx = build_write_context(conn)
    assignments: list[str] = []
    params: list[str | int | None] = []
    if note is not None:
        assignments.append("ZNOTE = ?")
        params.append(note or None)  # empty string clears the note
    if rest_seconds is not None:
        assignments.append("ZPAUSE = ?")
        params.append(rest_seconds)
    if index is not None:
        assignments.append("ZINDEX = ?")
        params.append(index)
    conn.execute(
        f"UPDATE ZUNIQEXERCISE SET {', '.join(assignments)} WHERE Z_PK = ?",
        (*params, ue_pk),
    )
    mark_routine_pending(conn, plan.routine.z_pk, ctx.now_cd)
    return plan


# --------------------------------------------------------------------------- #
# reorder_routine
# --------------------------------------------------------------------------- #
def plan_reorder_routine(
    conn: sqlite3.Connection, routine: str | int, ordered_ue_pks: list[int]
) -> ReorderRoutinePlan:
    ref = _routine_ref(conn, routine)
    current: dict[int, tuple[str, int]] = {
        int(r[0]): (str(r[1]), int(r[2]))
        for r in conn.execute(
            "SELECT ue.Z_PK, e.ZNAME, ue.ZINDEX FROM ZUNIQEXERCISE ue "
            "JOIN ZEXERCISE e ON ue.ZEXERCISE = e.Z_PK "
            "WHERE ue.ZROUTINE = ? AND ue.ZDATEREMOVED IS NULL",
            (ref.z_pk,),
        )
    }
    problems: list[str] = []
    seen: set[int] = set()
    for pk in ordered_ue_pks:
        if pk in seen:
            problems.append(f"ue_pk {pk} appears more than once.")
        seen.add(pk)
        if pk not in current:
            problems.append(
                f"ue_pk {pk} is not an active exercise of routine {ref.name!r} "
                f"(z_pk={ref.z_pk})."
            )
    for pk, (name, _) in current.items():
        if pk not in seen:
            problems.append(f"Missing active exercise: {name!r} (ue_pk={pk}).")
    if problems:
        raise WriteValidationError(
            f"ordered_ue_pks must equal routine {ref.name!r}'s active exercise set "
            "exactly — nothing was written:\n- " + "\n- ".join(problems)
        )
    return ReorderRoutinePlan(
        routine=ref,
        order=[
            ReorderEntry(
                ue_pk=pk,
                exercise_name=current[pk][0],
                old_index=current[pk][1],
                new_index=new_index,
            )
            for new_index, pk in enumerate(ordered_ue_pks)
        ],
    )


def apply_reorder_routine(
    conn: sqlite3.Connection, routine: str | int, ordered_ue_pks: list[int]
) -> ReorderRoutinePlan:
    plan = plan_reorder_routine(conn, routine, ordered_ue_pks)
    ctx = build_write_context(conn)
    conn.executemany(
        "UPDATE ZUNIQEXERCISE SET ZINDEX = ? WHERE Z_PK = ?",
        [(entry.new_index, entry.ue_pk) for entry in plan.order],
    )
    mark_routine_pending(conn, plan.routine.z_pk, ctx.now_cd)
    return plan


# --------------------------------------------------------------------------- #
# remove_exercise
# --------------------------------------------------------------------------- #
def plan_remove_exercise(conn: sqlite3.Connection, ue_pk: int) -> RemoveExercisePlan:
    ex = _get_active_exercise(conn, ue_pk)
    template_sets = int(
        conn.execute(
            "SELECT COUNT(*) FROM ZVALUES "
            "WHERE ZEXERCISE = ? AND ZDATELOGGED IS NULL AND ZDATEREMOVED IS NULL",
            (ue_pk,),
        ).fetchone()[0]
    )
    return RemoveExercisePlan(
        ue_pk=ue_pk,
        exercise_name=ex.exercise_name,
        routine=ex.routine,
        template_sets_removed=template_sets,
    )


def apply_remove_exercise(conn: sqlite3.Connection, ue_pk: int) -> RemoveExercisePlan:
    """Soft-delete the exercise + its unlogged template sets (never SQL DELETE).

    Removal stamps mirror the app's own pattern (verified live): ZDATEREMOVED is
    midnight-local, ZPRECISEDATEREMOVED the real timestamp. Logged sets keep
    their history; ZINDEX gaps are left as-is (the app's removals leave them too)."""
    plan = plan_remove_exercise(conn, ue_pk)
    ctx = build_write_context(conn)
    conn.execute(
        "UPDATE ZUNIQEXERCISE SET ZDATEREMOVED = ?, ZPRECISEDATEREMOVED = ? WHERE Z_PK = ?",
        (ctx.midnight_cd, ctx.now_cd, ue_pk),
    )
    conn.execute(
        "UPDATE ZVALUES SET ZDATEREMOVED = ?, ZPRECISEDATEREMOVED = ? "
        "WHERE ZEXERCISE = ? AND ZDATELOGGED IS NULL AND ZDATEREMOVED IS NULL",
        (ctx.midnight_cd, ctx.now_cd, ue_pk),
    )
    mark_routine_pending(conn, plan.routine.z_pk, ctx.now_cd)
    return plan


# --------------------------------------------------------------------------- #
# update_routine
# --------------------------------------------------------------------------- #
def plan_update_routine(
    conn: sqlite3.Connection,
    routine: str | int,
    *,
    name: str | None = None,
    days: str | None = None,
    goal: str | None = None,
    note: str | None = None,
) -> UpdateRoutinePlan:
    ref = _routine_ref(conn, routine)
    row = conn.execute(
        "SELECT ZNAME, ZDAYS, ZGOAL, ZNOTE FROM ZROUTINE WHERE Z_PK = ?", (ref.z_pk,)
    ).fetchone()
    changes: list[FieldChange] = []
    if name is not None:
        if not name.strip():
            raise WriteValidationError("New routine name must be non-empty.")
        collision = conn.execute(
            "SELECT Z_PK FROM ZROUTINE WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 "
            "AND LOWER(ZNAME) = LOWER(?) AND Z_PK != ?",
            (name.strip(), ref.z_pk),
        ).fetchone()
        if collision:
            raise WriteValidationError(
                f"An active routine named {name!r} already exists "
                f"(z_pk={int(collision[0])}). Pick another name or archive it first."
            )
        changes.append(FieldChange(field="name", old=str(row[0]), new=name.strip()))
    for field, new_value, old_value in (
        ("days", days, row[1]),
        ("goal", goal, row[2]),
        ("note", note, row[3]),
    ):
        if new_value is not None:
            changes.append(
                FieldChange(
                    field=field,
                    old=str(old_value) if old_value is not None else None,
                    new=new_value or None,  # empty string clears the field
                )
            )
    if not changes:
        raise WriteValidationError(
            "Nothing to update — pass at least one of name, days, goal, note."
        )
    return UpdateRoutinePlan(routine=ref, changes=changes)


def apply_update_routine(
    conn: sqlite3.Connection,
    routine: str | int,
    *,
    name: str | None = None,
    days: str | None = None,
    goal: str | None = None,
    note: str | None = None,
) -> UpdateRoutinePlan:
    plan = plan_update_routine(conn, routine, name=name, days=days, goal=goal, note=note)
    ctx = build_write_context(conn)
    column_by_field = {"name": "ZNAME", "days": "ZDAYS", "goal": "ZGOAL", "note": "ZNOTE"}
    assignments = ", ".join(f"{column_by_field[c.field]} = ?" for c in plan.changes)
    conn.execute(
        f"UPDATE ZROUTINE SET {assignments} WHERE Z_PK = ?",
        (*(c.new for c in plan.changes), plan.routine.z_pk),
    )
    mark_routine_pending(conn, plan.routine.z_pk, ctx.now_cd)
    return plan


# NOTE — archive_routine was implemented and then REMOVED after live verification
# (2026-07-10): ZHIDDEN=1 + mark_routine_pending is self-defeating — the app's
# routine/update/ push applies the server's authoritative response back, which
# resets ZHIDDEN to 0 (archived state lives server-side, writable only via the
# app's own routine/archive/ endpoint). See spec 02's archive observation block.


__all__ = [
    "ProgramValidationError",
    "WriteContext",
    "WriteValidationError",
    "apply_add_exercise",
    "apply_program",
    "apply_remove_exercise",
    "apply_reorder_routine",
    "apply_update_exercise",
    "apply_update_routine",
    "build_write_context",
    "enqueue_exercise_added",
    "insert_exercise",
    "insert_routine",
    "insert_routine_tree",
    "insert_set",
    "mark_routine_pending",
    "plan_add_exercise",
    "plan_program",
    "plan_remove_exercise",
    "plan_reorder_routine",
    "plan_update_exercise",
    "plan_update_routine",
]
