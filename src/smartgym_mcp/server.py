"""MCPServer app over the SmartGym API (API-client spec §5, §6).

Tools are thin: parse input → store / service / reads. Credentials and the API
client are built on first use, so a missing credentials file becomes a clear
tool error (and a smartgym_health report) instead of a server that won't start.
Tool calls run one at a time (DESIGN invariant 7).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, builders, catalog, reads
from .api.auth import CredentialsError, FileCredentials
from .api.client import ApiClient, ApiError, AuthError
from .api.store import AccountStore
from .config import VERIFIED_APP_VERSION, Config, load_config
from .diff import DesiredExercise, DesiredRoutine
from .model import Section
from .models import Days, Goal, RoutineSpec, SetSpec
from .payloads import local_timezone_name
from .service import (
    ArchiveResult,
    CreateResult,
    EditResult,
    RoutineService,
    WriteVerifyError,
)

logger = logging.getLogger("smartgym_mcp")


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=True)


def _destructive(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    )


@dataclass
class Services:
    client: ApiClient
    store: AccountStore
    service: RoutineService
    equipment: list[catalog.CatalogEquipment]


def build_services(cfg: Config) -> Services:
    credentials = FileCredentials(cfg.credentials_path)
    client = ApiClient(
        credentials, app_version=catalog.installed_app_version(cfg) or VERIFIED_APP_VERSION
    )
    try:
        store = AccountStore(client)
        exercises = catalog.load_bundle_exercises(cfg)
        service = RoutineService(
            client,
            store,
            {e.id: e for e in exercises},
            backup_dir=cfg.backup_dir,
            timezone=local_timezone_name(),
        )
        return Services(
            client=client,
            store=store,
            service=service,
            equipment=catalog.load_bundle_equipment(cfg),
        )
    except BaseException:
        client.close()
        raise


# Covers a slow write: each request has a 30 s timeout and reads retry.
CALL_WAIT_S = 60.0


class ServerBusy(RuntimeError):
    """Another tool call held the services for longer than the wait allows."""


@dataclass
class AppContext:
    cfg: Config
    factory: Callable[[Config], Services] = build_services
    call_wait_s: float = CALL_WAIT_S
    _calls: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _services: Services | None = field(default=None, init=False, repr=False)

    @contextmanager
    def use(self) -> Iterator[Services]:
        """Services for one tool call, one call at a time.

        MCP SDK 2 runs sync tools on worker threads, but the account cache, the client's
        lifetime and each write's re-read → rewrite assume calls don't overlap. A call that
        can't start within `call_wait_s` fails with ServerBusy before sending anything.

        Services are built on first use; a failed build is not cached, so the next call
        retries. An AuthError drops them (closing the client) so the next call re-reads the
        credentials file — a re-captured session takes effect without restarting the
        server. The error itself propagates unchanged.
        """
        if not self._calls.acquire(timeout=self.call_wait_s):
            raise ServerBusy(
                f"Another SmartGym call has been running for over {self.call_wait_s:.0f} s; "
                "nothing was sent. Try again once it finishes."
            )
        try:
            if self._services is None:
                self._services = self.factory(self.cfg)
            services = self._services
            try:
                yield services
            except AuthError:
                self._services = None
                services.client.close()
                raise
        finally:
            self._calls.release()

    def close(self) -> None:
        """Close the client once the call in flight (if any) is done. If it is still running
        after `call_wait_s`, the client is left open rather than closed under its requests."""
        if not self._calls.acquire(timeout=self.call_wait_s):
            logger.warning("a tool call is still running; leaving its client open")
            return
        try:
            services, self._services = self._services, None
            if services is not None:
                services.client.close()
        finally:
            self._calls.release()


@asynccontextmanager
async def lifespan(_server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
    app = AppContext(cfg=load_config())
    logger.info("smartgym_mcp up; credentials=%s", app.cfg.credentials_path)
    try:
        yield app
    finally:
        app.close()


mcp = MCPServer("smartgym_mcp", version=__version__, lifespan=lifespan)


def _app(ctx: Context[AppContext]) -> AppContext:
    return ctx.request_context.lifespan_context


# Failures whose message tells the model what to do next (fix the credentials, pick another
# name, re-read before retrying a write of unknown outcome). MCP SDK 2 replaces the message
# of any other exception with "Error executing tool <name>", so these become ToolError.
_ANTICIPATED = (ApiError, CredentialsError, WriteVerifyError, ServerBusy, ValueError, OSError)


@contextmanager
def _services(ctx: Context[AppContext]) -> Iterator[Services]:
    """The one way tools reach the services (see AppContext.use). Keep the whole tool body
    inside it, so its anticipated errors reach the model with their message."""
    try:
        with _app(ctx).use() as services:
            yield services
    except _ANTICIPATED as exc:
        raise ToolError(str(exc)) from exc


class HealthStatus(BaseModel):
    ok: bool
    routines: int | None
    app_version: str | None
    verified_app_version: str
    warning: str | None
    problem: str | None
    version: str


@mcp.tool(annotations=_read_only("Health check"))
def smartgym_health(ctx: Context[AppContext]) -> HealthStatus:
    """Check the credentials file, the SmartGym server, and the installed app version.

    ok=true means the server answered with your account data. `problem` explains
    what to fix otherwise (e.g. create the credentials file). `warning` appears when
    the installed SmartGym version differs from the one the MCP was verified against.
    """
    app = _app(ctx)
    installed = catalog.installed_app_version(app.cfg)
    warning = None
    if installed and installed != VERIFIED_APP_VERSION:
        warning = (
            f"SmartGym {installed} is installed; the MCP was verified against "
            f"{VERIFIED_APP_VERSION}. Re-run the live check on a ZZ- routine before "
            "trusting edits."
        )
    try:
        with app.use() as services:
            data = services.store.data()
    except (CredentialsError, ApiError, ServerBusy, ValueError, KeyError, OSError) as exc:
        return HealthStatus(
            ok=False,
            routines=None,
            app_version=installed,
            verified_app_version=VERIFIED_APP_VERSION,
            warning=warning,
            problem=str(exc),
            version=__version__,
        )
    return HealthStatus(
        ok=True,
        routines=sum(1 for r in data.routines if not r.removed and not r.archived),
        app_version=installed,
        verified_app_version=VERIFIED_APP_VERSION,
        warning=warning,
        problem=None,
        version=__version__,
    )


@mcp.tool(annotations=_read_only("List routines"))
def smartgym_list_routines(
    ctx: Context[AppContext], include_archived: bool = False
) -> reads.RoutineListResult:
    """List routines: id, name, days, and exercise counts per section (warm-up/main/cool-down).

    Archived routines are listed only with include_archived=true. Use the id (or the
    name) in the other tools.
    """
    with _services(ctx) as services:
        data = services.store.data()
    return reads.list_routines(data, include_archived=include_archived)


@mcp.tool(annotations=_read_only("Get routine detail"))
def smartgym_get_routine(
    ctx: Context[AppContext], routine: str, history_depth: Annotated[int, Field(ge=0)] = 5
) -> reads.RoutineDetail:
    """Get a routine split into warmup, main and cooldown sections.

    `routine` is a name (case-insensitive, partial allowed) or id. Each exercise shows
    its exercise_id (what the edit tools take), rest, note, planned template sets, and
    up to `history_depth` recent sessions (reps + weight_kg; 0.0 = bodyweight) with the
    latest session's top set and total volume.
    """
    with _services(ctx) as services:
        data = services.store.data()
        return reads.routine_detail(
            services.store.resolve(routine, data=data), data, history_depth=history_depth
        )


@mcp.tool(annotations=_read_only("Get workout history"))
def smartgym_get_workout_history(
    ctx: Context[AppContext],
    days: Literal[7, 14, 30] = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: str | None = None,
    limit: Annotated[int, Field(ge=1)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> reads.WorkoutHistoryResult:
    """List past workouts (newest first) with duration, calories and heart rate.

    Defaults to the last 7 days (or 14/30 via `days`); explicit `date_from` / `date_to`
    (YYYY-MM-DD, inclusive, local time) override it. Optional `routine` filter (name or
    id). Paginated: total, has_more, next_offset.
    """
    with _services(ctx) as services:
        data = services.store.data()
        return reads.workout_history(
            data,
            days=days,
            date_from=date_from,
            date_to=date_to,
            routine=services.store.resolve(routine, data=data) if routine else None,
            limit=limit,
            offset=offset,
        )


@mcp.tool(annotations=_read_only("Get equipment"))
def smartgym_get_equipment(
    ctx: Context[AppContext], owned_only: bool = True
) -> reads.EquipmentListResult:
    """List equipment (owned_only=true: only what you selected) plus dumbbell/kettlebell weights."""
    with _services(ctx) as services:
        return reads.equipment(
            services.store.data(), services.equipment, owned_only=owned_only
        )


@mcp.tool(annotations=_destructive("Create program"))
def smartgym_create_program(
    ctx: Context[AppContext],
    routines: Annotated[list[RoutineSpec], Field(min_length=1)],
    dry_run: bool = True,
) -> CreateResult:
    """Create one or more routines (a program) on the SmartGym server — all devices get them.

    Each routine: name (must not match an active routine), goal (required free text, e.g.
    "Strength" — the app can't save a routine without one), optional days/note, and three
    ordered sections — `warmup` (optional), `exercises` (= main, required), `cooldown`
    (optional). Exercises are catalog names — built-in or the account's custom exercises
    (fuzzy-matched, deterministic) — or catalog ids, with optional rest_seconds, note and
    template sets (reps + weight_kg; omitted = one 1x10 set, flagged). `days` is
    comma-separated weekday numbers, 1 = Sunday, 2 = Monday … 7 = Saturday (e.g. "2,4,6").
    Validation is all-or-nothing.
    dry_run=true (default) returns the plan and sends NOTHING; dry_run=false creates the
    routines and verifies them on the server.
    """
    with _services(ctx) as services:
        return services.service.create(routines, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Update routine"))
def smartgym_update_routine(
    ctx: Context[AppContext],
    routine: str,
    name: str | None = None,
    days: Days = None,
    goal: Goal | None = None,
    note: str | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Edit a routine's name, days, goal or note (only the fields you pass; "" clears).

    The goal can be changed but not cleared (the app can't save a routine without one).
    `days` is comma-separated weekday numbers, 1 = Sunday, 2 = Monday … 7 = Saturday
    (e.g. "2,4,6"). A new name must not match another active routine.

    dry_run=true (default) shows old → new and sends NOTHING; dry_run=false snapshots the
    routine, sends the edit, and verifies it on the server.
    """
    with _services(ctx) as services:
        desired = builders.update_routine(name=name, days=days, goal=goal, note=note)
        return services.service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Add exercise"))
def smartgym_add_exercise(
    ctx: Context[AppContext],
    routine: str,
    exercise: str,
    section: Section = "main",
    position: Annotated[int | None, Field(ge=0)] = None,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Add a catalog exercise (built-in or custom) to a routine section (warmup / main /
    cooldown).

    `position` counts within the section (0 = first; omitted = last). Optional rest,
    note and template sets (omitted = one 1x10 set, flagged). dry_run=true (default)
    sends NOTHING.
    """
    with _services(ctx) as services:
        return services.service.edit(
            routine,
            lambda r: builders.add_exercise(
                r,
                exercise,
                section=section,
                position=position,
                rest_seconds=rest_seconds,
                note=note,
                sets=sets,
            ),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_destructive("Move exercise"))
def smartgym_move_exercise(
    ctx: Context[AppContext],
    exercise_id: int,
    section: Section,
    position: Annotated[int | None, Field(ge=0)] = None,
    dry_run: bool = True,
) -> EditResult:
    """Move an exercise to another section (or to another position in its section).

    `exercise_id` comes from smartgym_get_routine; `position` counts within the target
    section (omitted = last). dry_run=true (default) sends NOTHING.
    """
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id,
            lambda r: builders.move_exercise(
                r, exercise_id, section=section, position=position
            ),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_destructive("Remove exercise"))
def smartgym_remove_exercise(
    ctx: Context[AppContext], exercise_id: int, dry_run: bool = True
) -> EditResult:
    """Remove an exercise from its routine (logged history is kept).

    `exercise_id` comes from smartgym_get_routine. dry_run=true (default) sends NOTHING.
    """
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id, lambda r: builders.remove_exercise(r, exercise_id), dry_run=dry_run
        )


@mcp.tool(annotations=_destructive("Reorder routine"))
def smartgym_reorder_routine(
    ctx: Context[AppContext],
    routine: str,
    warmup: list[int] | None = None,
    main: list[int] | None = None,
    cooldown: list[int] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Reorder exercises inside sections.

    Each list you pass must hold exactly that section's current exercise_ids, in the new
    order; omitted sections stay as they are. To change an exercise's section use
    smartgym_move_exercise. dry_run=true (default) sends NOTHING.
    """
    with _services(ctx) as services:
        return services.service.edit(
            routine,
            lambda r: builders.reorder(r, warmup=warmup, main=main, cooldown=cooldown),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_destructive("Update exercise"))
def smartgym_update_exercise(
    ctx: Context[AppContext],
    exercise_id: int,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Change an exercise's rest time, note ("" clears) or template sets.

    `sets` is the FULL planned list [{reps, weight_kg}] — sets beyond it are removed,
    extra ones added. Logged history is never touched. dry_run=true (default) sends
    NOTHING.
    """
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id,
            lambda r: builders.update_exercise(
                r, exercise_id, rest_seconds=rest_seconds, note=note, sets=sets
            ),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_destructive("Apply routine"))
def smartgym_apply_routine(
    ctx: Context[AppContext],
    routine: str,
    name: str | None = None,
    days: Days = None,
    goal: Goal | None = None,
    note: str | None = None,
    warmup: list[DesiredExercise] | None = None,
    main: list[DesiredExercise] | None = None,
    cooldown: list[DesiredExercise] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Rewrite a routine in one go (e.g. "week 2 of FB-A").

    Each section you pass is the complete ordered list for that section: existing
    exercises by exercise_id (optionally with new rest/note/sets), new ones by catalog
    name (built-in or custom). An existing exercise listed under another section moves
    there; one you leave out of a passed section is removed. Omitted sections stay as they
    are. `days` is comma-separated weekday numbers, 1 = Sunday, 2 = Monday … 7 = Saturday
    (e.g. "2,4,6").
    dry_run=true (default) shows the full plan and sends NOTHING.
    """
    with _services(ctx) as services:
        desired = DesiredRoutine(
            name=name,
            days=days,
            goal=goal,
            note=note,
            warmup=warmup,
            main=main,
            cooldown=cooldown,
        )
        return services.service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Archive routines"))
def smartgym_archive_routines(
    ctx: Context[AppContext], routines: list[str], dry_run: bool = True
) -> ArchiveResult:
    """Archive routines (names or ids) on every device.

    Reversible with smartgym_unarchive_routine. dry_run=true (default) sends NOTHING.
    """
    with _services(ctx) as services:
        return services.service.set_archived(routines, archived=True, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Unarchive routine"))
def smartgym_unarchive_routine(
    ctx: Context[AppContext], routine: str, dry_run: bool = True
) -> ArchiveResult:
    """Bring an archived routine back to the active list. dry_run=true sends NOTHING."""
    with _services(ctx) as services:
        return services.service.set_archived([routine], archived=False, dry_run=dry_run)


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
