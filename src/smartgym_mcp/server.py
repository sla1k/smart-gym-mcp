"""FastMCP app: lifespan-held read-only connection + a health smoke tool.

Read tools (spec 01) read the shared RO connection from the lifespan context.
Write tools (spec 02) will open an on-demand RW connection via
``db.open_rw_connection(ctx.request_context.lifespan_context.cfg)``.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import __version__, catalog, db, lifecycle, queries, writes
from .config import Config, load_config, validate_db_exists
from .models import (
    AddExerciseResult,
    CreateProgramResult,
    EquipmentListResult,
    RemoveExerciseResult,
    ReorderRoutineResult,
    RoutineDetail,
    RoutineListResult,
    RoutineSpec,
    SetSpec,
    UpdateExerciseResult,
    UpdateRoutineResult,
    WorkoutHistoryResult,
)

logger = logging.getLogger("smartgym_mcp")


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=False)


def _destructive(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )


_DRY_RUN_NOTICE = "Dry run — nothing written. Re-run with dry_run=false to apply."
_APPLIED_NOTICE = (
    "Applied. SmartGym relaunched — the sync push fires within ~30 s; "
    "verify on your other device."
)


class HealthStatus(BaseModel):
    ok: bool
    db_path: str
    journal_mode: str
    active_routines: int
    version: str


@dataclass
class AppContext:
    cfg: Config
    ro: sqlite3.Connection  # long-lived, autocommit, WAL-visible
    # Serializes access to the single shared RO connection — FastMCP may dispatch
    # sync tools on a worker-thread pool, and one sqlite3 connection is not safe
    # for concurrent cursor use even with check_same_thread=False.
    lock: threading.Lock = field(default_factory=threading.Lock)


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    cfg = load_config()
    validate_db_exists(cfg)
    ro = db.open_ro_connection(cfg.db_path)
    logger.info("smartgym_mcp up; db=%s", cfg.db_path)
    try:
        yield AppContext(cfg=cfg, ro=ro)
    finally:
        ro.close()


mcp = FastMCP("smartgym_mcp", lifespan=lifespan)


@mcp.tool(annotations=_read_only("Health check"))
def smartgym_health(ctx: Context) -> HealthStatus:
    """Liveness probe: confirms the DB is reachable and WAL-live.

    Returns the DB path, SQLite journal mode, active routine count, and server
    version. Exercises the whole foundation (lifespan, RO connection, WAL read)
    through the real MCP transport.
    """
    app: AppContext = ctx.request_context.lifespan_context
    with app.lock:
        journal = app.ro.execute("PRAGMA journal_mode").fetchone()[0]
        active = app.ro.execute(
            "SELECT COUNT(*) FROM ZROUTINE WHERE ZDATEREMOVED IS NULL"
        ).fetchone()[0]
    # WAL is the contract that guarantees fresh reads; anything else is unhealthy.
    return HealthStatus(
        ok=str(journal).lower() == "wal",
        db_path=str(app.cfg.db_path),
        journal_mode=journal,
        active_routines=active,
        version=__version__,
    )


@mcp.tool(annotations=_read_only("List routines"))
def smartgym_list_routines(ctx: Context, include_hidden: bool = False) -> RoutineListResult:
    """List workout routines with id, name, scheduled days, sync flag, and exercise count.

    By default only active (non-hidden) routines are returned; set include_hidden=true
    to also list archived ones. Use the returned z_pk to disambiguate routines elsewhere.
    has_synced=false means the routine is still pending a push to the SmartGym backend
    (the app pushes it within ~30 s of its next launch).
    """
    app: AppContext = ctx.request_context.lifespan_context
    with app.lock:
        return queries.list_routines(app.ro, include_hidden)


@mcp.tool(annotations=_read_only("Get routine detail"))
def smartgym_get_routine(ctx: Context, routine: str, history_depth: int = 5) -> RoutineDetail:
    """Get a routine's exercises in order with rest time, note, and recent logged sets.

    `routine` is a routine name (case-insensitive, partial allowed) OR a z_pk. Each
    exercise includes up to `history_depth` recent sessions as per-set arrays
    (reps + weight_kg; 0.0 weight means bodyweight/untracked) plus the latest session's
    top set and total volume. Ambiguous names raise an error listing candidate z_pks.
    """
    app: AppContext = ctx.request_context.lifespan_context
    with app.lock:
        return queries.get_routine(app.ro, routine, history_depth)


@mcp.tool(annotations=_read_only("Get workout history"))
def smartgym_get_workout_history(
    ctx: Context,
    days: Literal[7, 14, 30] = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> WorkoutHistoryResult:
    """List past workout sessions (deduped, paginated) with duration, calories, and HR.

    Defaults to the last 7 days (or 14/30 via `days`). Pass explicit `date_from`/`date_to`
    (YYYY-MM-DD, inclusive) to override the preset. Optional `routine` filter (name or z_pk).
    Warm-up/main/cooldown entries sharing one workout are collapsed to a single session.
    Returns pagination metadata (total, has_more, next_offset).
    """
    app: AppContext = ctx.request_context.lifespan_context
    with app.lock:
        return queries.get_workout_history(
            app.ro,
            days=days,
            date_from=date_from,
            date_to=date_to,
            routine=routine,
            limit=limit,
            offset=offset,
        )


@mcp.tool(annotations=_read_only("Get equipment"))
def smartgym_get_equipment(ctx: Context, owned_only: bool = True) -> EquipmentListResult:
    """List equipment with available weight increments. owned_only filters to selected gear."""
    app: AppContext = ctx.request_context.lifespan_context
    with app.lock:
        return queries.get_equipment(app.ro, owned_only)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create program",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def smartgym_create_program(
    ctx: Context, routines: list[RoutineSpec], dry_run: bool = True
) -> CreateProgramResult:
    """Create one or more workout routines (a full program) in SmartGym, synced to all devices.

    Each routine: name (must not collide with an active routine), optional days/goal/note,
    and ordered exercises — catalog name (fuzzy-matched, deterministic) or z_pk, with optional
    rest_seconds, note, and template sets (reps + weight_kg; omitted = one default 1x10 set).
    Validation is all-or-nothing: any unresolved exercise or name collision rejects the whole
    program. Additive-only — existing routines are never touched (archive separately).

    dry_run=true (default) returns the resolution plan and writes NOTHING. With dry_run=false
    the server backs up the DB, gracefully quits SmartGym if running, inserts everything in one
    transaction, then relaunches the app — its sync engine pushes the new routines to the
    SmartGym backend (and thus your other devices) within ~30 seconds.
    """
    app: AppContext = ctx.request_context.lifespan_context

    if dry_run:
        with app.lock:
            plan = writes.plan_program(app.ro, routines)
        return CreateProgramResult(
            dry_run=True,
            plan=plan,
            created=[],
            app=None,
            backup_dir=None,
            notice="Dry run — nothing written. Re-run with dry_run=false to apply.",
        )

    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan, created = writes.apply_program(conn, routines)
    return CreateProgramResult(
        dry_run=False,
        plan=plan,
        created=created,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=(
            f"Created {len(created)} routine(s). SmartGym relaunched — the sync push "
            "fires within ~30 s; verify on your other device."
        ),
    )


@mcp.tool(annotations=_destructive("Add exercise to routine"))
def smartgym_add_exercise(
    ctx: Context,
    routine: str,
    exercise: str,
    index: int | None = None,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
    dry_run: bool = True,
) -> AddExerciseResult:
    """Add one exercise to an existing routine, synced to all devices.

    `routine` is a name (case-insensitive) or z_pk; `exercise` a catalog name
    (fuzzy-matched, deterministic) or z_pk. Default index appends at the end;
    an explicit index inserts at that position and shifts later exercises down.
    Optional template sets (reps + weight_kg); omitted = one default 1x10 set,
    flagged in the plan. dry_run=true (default) returns the plan and writes
    NOTHING; with dry_run=false the server backs up the DB, quits SmartGym,
    writes, and relaunches it so the change pushes to your other devices.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if dry_run:
        with app.lock:
            plan = writes.plan_add_exercise(app.ro, routine, exercise, index=index, sets=sets)
        return AddExerciseResult(
            dry_run=True,
            plan=plan,
            created_ue_pk=None,
            app=None,
            backup_dir=None,
            notice=_DRY_RUN_NOTICE,
        )
    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan, ue_pk = writes.apply_add_exercise(
            conn,
            routine,
            exercise,
            index=index,
            rest_seconds=rest_seconds,
            note=note,
            sets=sets,
        )
    return AddExerciseResult(
        dry_run=False,
        plan=plan,
        created_ue_pk=ue_pk,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=_APPLIED_NOTICE,
    )


@mcp.tool(annotations=_destructive("Update exercise"))
def smartgym_update_exercise(
    ctx: Context,
    ue_pk: int,
    note: str | None = None,
    rest_seconds: int | None = None,
    index: int | None = None,
    dry_run: bool = True,
) -> UpdateExerciseResult:
    """Edit an exercise's note, rest time, or position within its routine.

    `ue_pk` identifies the exercise row (from smartgym_get_routine). Only the
    fields you pass are changed; `note` OVERWRITES the whole field (pass an
    empty string to clear it). At least one field is required. dry_run=true
    (default) returns the old→new plan and writes NOTHING; dry_run=false
    applies via backup + app quit/relaunch so the change syncs.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if dry_run:
        with app.lock:
            plan = writes.plan_update_exercise(
                app.ro, ue_pk, note=note, rest_seconds=rest_seconds, index=index
            )
        return UpdateExerciseResult(
            dry_run=True, plan=plan, app=None, backup_dir=None, notice=_DRY_RUN_NOTICE
        )
    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan = writes.apply_update_exercise(
            conn, ue_pk, note=note, rest_seconds=rest_seconds, index=index
        )
    return UpdateExerciseResult(
        dry_run=False,
        plan=plan,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=_APPLIED_NOTICE,
    )


@mcp.tool(annotations=_destructive("Reorder routine"))
def smartgym_reorder_routine(
    ctx: Context,
    routine: str,
    ordered_ue_pks: list[int],
    dry_run: bool = True,
) -> ReorderRoutineResult:
    """Rewrite a routine's exercise order to match `ordered_ue_pks` exactly.

    The list must contain every active exercise of the routine exactly once
    (ue_pks from smartgym_get_routine) — any duplicate, missing, or foreign
    ue_pk rejects the whole call. dry_run=true (default) returns the old→new
    order and writes NOTHING; dry_run=false applies via backup + app
    quit/relaunch so the change syncs.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if dry_run:
        with app.lock:
            plan = writes.plan_reorder_routine(app.ro, routine, ordered_ue_pks)
        return ReorderRoutineResult(
            dry_run=True, plan=plan, app=None, backup_dir=None, notice=_DRY_RUN_NOTICE
        )
    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan = writes.apply_reorder_routine(conn, routine, ordered_ue_pks)
    return ReorderRoutineResult(
        dry_run=False,
        plan=plan,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=_APPLIED_NOTICE,
    )


@mcp.tool(annotations=_destructive("Remove exercise from routine"))
def smartgym_remove_exercise(
    ctx: Context, ue_pk: int, dry_run: bool = True
) -> RemoveExerciseResult:
    """Remove an exercise from its routine (soft-delete; logged history is kept).

    Soft-deletes the exercise row and its unlogged template sets — logged sets
    stay untouched, so past workouts keep their history. `ue_pk` comes from
    smartgym_get_routine. dry_run=true (default) returns the plan (incl. how
    many template sets go) and writes NOTHING; dry_run=false applies via
    backup + app quit/relaunch so the change syncs.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if dry_run:
        with app.lock:
            plan = writes.plan_remove_exercise(app.ro, ue_pk)
        return RemoveExerciseResult(
            dry_run=True, plan=plan, app=None, backup_dir=None, notice=_DRY_RUN_NOTICE
        )
    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan = writes.apply_remove_exercise(conn, ue_pk)
    return RemoveExerciseResult(
        dry_run=False,
        plan=plan,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=_APPLIED_NOTICE,
    )


@mcp.tool(annotations=_destructive("Update routine"))
def smartgym_update_routine(
    ctx: Context,
    routine: str,
    name: str | None = None,
    days: str | None = None,
    goal: str | None = None,
    note: str | None = None,
    dry_run: bool = True,
) -> UpdateRoutineResult:
    """Edit a routine's name, scheduled days, goal, or note.

    Only the fields you pass are changed; each OVERWRITES the whole field
    (empty string clears days/goal/note; the name must stay non-empty and not
    collide with another active routine). At least one field is required.
    dry_run=true (default) returns the old→new plan and writes NOTHING;
    dry_run=false applies via backup + app quit/relaunch so the change syncs.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if dry_run:
        with app.lock:
            plan = writes.plan_update_routine(
                app.ro, routine, name=name, days=days, goal=goal, note=note
            )
        return UpdateRoutineResult(
            dry_run=True, plan=plan, app=None, backup_dir=None, notice=_DRY_RUN_NOTICE
        )
    with lifecycle.managed_write(app.cfg) as (conn, report):
        plan = writes.apply_update_routine(
            conn, routine, name=name, days=days, goal=goal, note=note
        )
    return UpdateRoutineResult(
        dry_run=False,
        plan=plan,
        app=report,
        backup_dir=str(app.cfg.backup_dir),
        notice=_APPLIED_NOTICE,
    )


# NOTE: smartgym_archive_routine is deliberately NOT implemented. Verified live
# (2026-07-10): setting ZHIDDEN=1 + pending push is reverted by the app — archived
# state is server-side, writable only via the app's own routine/archive/ endpoint
# (spec 02 archive observation). Archive routines in-app; the change syncs down.


@mcp.resource("smartgym://catalog/exercises", mime_type="application/json")
def catalog_exercises() -> str:
    """Read-only exercise catalog from the SmartGym app bundle (Exercises.json)."""
    return catalog.read_catalog(load_config(), "exercises")


@mcp.resource("smartgym://catalog/equipment", mime_type="application/json")
def catalog_equipment() -> str:
    """Read-only equipment catalog from the SmartGym app bundle (Equipments.json)."""
    return catalog.read_catalog(load_config(), "equipment")


@mcp.resource("smartgym://catalog/categories", mime_type="application/json")
def catalog_categories() -> str:
    """Read-only category catalog from the SmartGym app bundle (Categories.json)."""
    return catalog.read_catalog(load_config(), "categories")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is the stdio protocol channel — never log there
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
