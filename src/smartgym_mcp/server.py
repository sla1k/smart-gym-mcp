"""MCPServer app over the SmartGym API (API-client spec §5, §6).

Tools are thin: parse input → store / service / reads. Credentials and the API
client are built on first use, so a missing credentials file becomes a clear
tool error (and a smartgym_health report) instead of a server that won't start.
Tool calls run one at a time (DESIGN invariant 7).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from typing import Annotated, Literal

import anyio.from_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
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


def _write(title: str, *, idempotent: bool) -> ToolAnnotations:
    """Every write may overwrite or remove the user's data, so all are destructive.

    idempotent=True: repeating the call with the same arguments leaves the same state (set
    a value, archive an archived routine), so a client may retry it. False: a repeat may
    change the account again (add_exercise adds a second copy, create_program a second
    program), or is not guaranteed not to — the conservative default.
    """
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=idempotent,
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


# Longer than a write can run: one read is up to 3 attempts × 30 s plus backoff (~92 s),
# and an edit makes up to five reads and one 30 s post (~8 min). A call whose client
# cancels it or disconnects stops waiting at once (see _stop_if_cancelled).
CALL_WAIT_S = 600.0
_POLL_S = 0.2  # how often a waiting call checks whether it was cancelled


class ServerBusy(RuntimeError):
    """Another tool call held the services for longer than the wait allows."""


class ServerClosed(RuntimeError):
    """The server is shutting down; no new call may start."""


def _stop_if_cancelled() -> None:
    """Raise if the client cancelled this tool call or disconnected.

    MCP SDK 2 runs sync tools via anyio.to_thread.run_sync, which can't stop the thread:
    cancelling the call only cancels the host task's scope, and the thread runs on. A call
    waiting for the lock checks that scope, so a call nobody waits for never sends anything.
    """
    with suppress(RuntimeError):  # not on an AnyIO worker thread (e.g. a direct call)
        anyio.from_thread.check_cancelled()


@dataclass
class AppContext:
    cfg: Config
    factory: Callable[[Config], Services] = build_services
    call_wait_s: float = CALL_WAIT_S
    _calls: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _services: Services | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def _acquire(self) -> None:
        """Take the call lock, or raise before anything is sent: ServerBusy after
        `call_wait_s`, ServerClosed after close(), or the cancellation of this call."""
        deadline = time.monotonic() + self.call_wait_s
        while not self._calls.acquire(
            timeout=max(0.0, min(_POLL_S, deadline - time.monotonic()))
        ):
            _stop_if_cancelled()
            if time.monotonic() >= deadline:
                raise ServerBusy(
                    "Another SmartGym call was still running after waiting "
                    f"{self.call_wait_s:.0f} s; nothing was sent. Try again once it finishes."
                )
        try:
            _stop_if_cancelled()
            if self._closed:
                raise ServerClosed(
                    "The SmartGym MCP server is shutting down; nothing was sent."
                )
        except BaseException:
            self._calls.release()
            raise

    @contextmanager
    def use(self) -> Iterator[Services]:
        """Services for one tool call, one call at a time.

        MCP SDK 2 runs sync tools on worker threads, but the account cache, the client's
        lifetime and each write's re-read → rewrite assume calls don't overlap. A call that
        can't start (see _acquire) fails before sending anything.

        Services are built on first use; a failed build is not cached, so the next call
        retries. An AuthError drops them (closing the client) so the next call re-reads the
        credentials file — a re-captured session takes effect without restarting the
        server. The error itself propagates unchanged.
        """
        self._acquire()
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
        """Stop new calls, then close the client once the call in flight (if any) is done.
        If it is still running after `call_wait_s`, the client is left open rather than
        closed under its requests."""
        self._closed = True  # a call queued behind this one must not rebuild the services
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
        # close() may wait for the call in flight; keep the event loop free meanwhile.
        await asyncio.to_thread(app.close)


INSTRUCTIONS = """\
SmartGym routines, exercises and workout history, read from and written to the SmartGym \
server. This is the user's live training data: a write reaches every device.

- Every write tool defaults to dry_run=true: it returns the plan (old -> new) and sends \
nothing. Run it that way first, show the user the plan, and repeat the call with \
dry_run=false only once they confirm it.
- `routine` (and each entry of `routines`) is a routine name (case-insensitive, partial \
allowed) or its id from smartgym_list_routines; an ambiguous name is refused.
- exercise_id (and the ids in reorder/apply lists) comes from smartgym_get_routine.
- New exercises are catalog names (built-in or the account's custom exercises, \
fuzzy-matched deterministically) or catalog ids.
- Template sets are [{reps, weight_kg}], weight_kg 0.0 = bodyweight; omitted sets become \
one 1x10 set, flagged in the plan. Logged workout history is never changed.
- When a write's outcome is unknown, re-read the routine before retrying it.
- If calls fail on credentials, smartgym_health says what to fix.
"""

mcp = MCPServer(
    "smartgym_mcp",
    title="SmartGym",
    website_url="https://github.com/sla1k/smart-gym-mcp",
    instructions=INSTRUCTIONS,
    version=__version__,
    lifespan=lifespan,
)


def _app(ctx: Context[AppContext]) -> AppContext:
    return ctx.request_context.lifespan_context


# Failures whose message tells the model what to do next (fix the credentials, pick another
# name, re-read before retrying a write of unknown outcome). MCP SDK 2 replaces the message
# of any other exception with "Error executing tool <name>", so these become ToolError.
_ANTICIPATED = (
    ApiError,
    CredentialsError,
    WriteVerifyError,
    ServerBusy,
    ServerClosed,
    ValueError,
    OSError,
)


@contextmanager
def _tool_errors() -> Iterator[None]:
    """Anticipated errors inside reach the model with their message. For input checks that
    need no services, so they run before the call lock and the services build."""
    try:
        yield
    except _ANTICIPATED as exc:
        raise ToolError(str(exc)) from exc


@contextmanager
def _services(ctx: Context[AppContext]) -> Iterator[Services]:
    """The one way tools reach the services (see AppContext.use). Keep the rest of the tool
    body inside it, so its anticipated errors reach the model with their message."""
    with _tool_errors(), _app(ctx).use() as services:
        yield services


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
    except (*_ANTICIPATED, KeyError) as exc:
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

    Each exercise shows its exercise_id (what the edit tools take), rest, note, planned
    template sets, and up to `history_depth` recent sessions (reps + weight_kg) with the
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


@mcp.tool(annotations=_write("Create program", idempotent=False))
def smartgym_create_program(
    ctx: Context[AppContext],
    routines: Annotated[list[RoutineSpec], Field(min_length=1)],
    dry_run: bool = True,
) -> CreateResult:
    """Create one or more routines (a program) on the SmartGym server — all devices get them.

    Each routine: name (must not match an active routine), goal (required free text, e.g.
    "Strength" — the app can't save a routine without one), optional days/note, and three
    ordered sections — `warmup` (optional), `exercises` (= main, required), `cooldown`
    (optional). Each exercise: catalog name or id, optional rest_seconds, note and template
    sets. `days` is comma-separated weekday numbers, 1 = Sunday, 2 = Monday … 7 = Saturday
    (e.g. "2,4,6"). Validation is all-or-nothing; dry_run=false creates the routines and
    verifies them on the server.
    """
    with _services(ctx) as services:
        return services.service.create(routines, dry_run=dry_run)


@mcp.tool(annotations=_write("Update routine", idempotent=True))
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
    (e.g. "2,4,6"). A new name must not match another active routine. dry_run=false
    snapshots the routine, sends the edit, and verifies it on the server.
    """
    with _tool_errors():
        desired = builders.update_routine(name=name, days=days, goal=goal, note=note)
    with _services(ctx) as services:
        return services.service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_write("Add exercise", idempotent=False))
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
    note and template sets.
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


@mcp.tool(annotations=_write("Move exercise", idempotent=False))
def smartgym_move_exercise(
    ctx: Context[AppContext],
    exercise_id: int,
    section: Section,
    position: Annotated[int | None, Field(ge=0)] = None,
    dry_run: bool = True,
) -> EditResult:
    """Move an exercise to another section (or to another position in its section).

    `position` counts within the target section (0 = first; omitted = last).
    """
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id,
            lambda r: builders.move_exercise(
                r, exercise_id, section=section, position=position
            ),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_write("Remove exercise", idempotent=False))
def smartgym_remove_exercise(
    ctx: Context[AppContext], exercise_id: int, dry_run: bool = True
) -> EditResult:
    """Remove an exercise from its routine (logged history is kept)."""
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id, lambda r: builders.remove_exercise(r, exercise_id), dry_run=dry_run
        )


@mcp.tool(annotations=_write("Reorder routine", idempotent=False))
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
    smartgym_move_exercise.
    """
    with _services(ctx) as services:
        return services.service.edit(
            routine,
            lambda r: builders.reorder(r, warmup=warmup, main=main, cooldown=cooldown),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_write("Update exercise", idempotent=True))
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
    extra ones added.
    """
    with _services(ctx) as services:
        return services.service.edit_exercise(
            exercise_id,
            lambda r: builders.update_exercise(
                r, exercise_id, rest_seconds=rest_seconds, note=note, sets=sets
            ),
            dry_run=dry_run,
        )


@mcp.tool(annotations=_write("Apply routine", idempotent=False))
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
    """
    with _tool_errors():
        desired = DesiredRoutine(
            name=name,
            days=days,
            goal=goal,
            note=note,
            warmup=warmup,
            main=main,
            cooldown=cooldown,
        )
    with _services(ctx) as services:
        return services.service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_write("Archive routines", idempotent=True))
def smartgym_archive_routines(
    ctx: Context[AppContext], routines: list[str], dry_run: bool = True
) -> ArchiveResult:
    """Archive routines (names or ids) on every device.

    Reversible with smartgym_unarchive_routine.
    """
    with _services(ctx) as services:
        return services.service.set_archived(routines, archived=True, dry_run=dry_run)


@mcp.tool(annotations=_write("Unarchive routine", idempotent=True))
def smartgym_unarchive_routine(
    ctx: Context[AppContext], routine: str, dry_run: bool = True
) -> ArchiveResult:
    """Bring an archived routine back to the active list."""
    with _services(ctx) as services:
        return services.service.set_archived([routine], archived=False, dry_run=dry_run)


def _read_catalog(name: str) -> str:
    """MCP SDK 2 hides the message of any non-ResourceError, so a missing or unreadable
    bundle file surfaces as one (its message names the file and where it was looked for)."""
    try:
        return catalog.read_catalog(load_config(), name)
    except (OSError, ValueError) as exc:
        raise ResourceError(str(exc)) from exc


@mcp.resource("smartgym://catalog/exercises", mime_type="application/json")
def catalog_exercises() -> str:
    """Read-only exercise catalog from the SmartGym app bundle (Exercises.json)."""
    return _read_catalog("exercises")


@mcp.resource("smartgym://catalog/equipment", mime_type="application/json")
def catalog_equipment() -> str:
    """Read-only equipment catalog from the SmartGym app bundle (Equipments.json)."""
    return _read_catalog("equipment")


@mcp.resource("smartgym://catalog/categories", mime_type="application/json")
def catalog_categories() -> str:
    """Read-only category catalog from the SmartGym app bundle (Categories.json)."""
    return _read_catalog("categories")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is the stdio protocol channel — never log there
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
