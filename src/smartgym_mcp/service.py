"""The one entry point for writes (DESIGN invariant 2, API-client spec §7).

`RoutineService` resolves which routine a call names and hands the write to its module:
`writes.edit`, `writes.create` or `writes.archive`, all on `writes.common`'s send and
verify core. Writes are never re-sent; a failure part-way names what was already sent,
which request failed or has an unknown outcome, and where the snapshot is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from .account import AccountCache
from .api.client import ApiCalls
from .inputs import DesiredRoutine, RoutineSpec
from .lookups import find_routine, routine_of_exercise
from .matching import ExerciseCatalog
from .model import Routine
from .payloads import Mint, mint_unique_hashid
from .writes import archive, create, edit
from .writes.archive import ArchiveResult
from .writes.common import WriteContext
from .writes.create import CreateResult
from .writes.edit import EditResult


def _now_local() -> datetime:
    return datetime.now().astimezone()


class RoutineService:
    def __init__(
        self,
        client: ApiCalls,
        cache: AccountCache,
        bundle: ExerciseCatalog,
        *,
        backup_dir: Path,
        timezone: str,
        now: Callable[[], datetime] = _now_local,
        mint: Mint = mint_unique_hashid,
    ) -> None:
        self._cache = cache
        self._ctx = WriteContext(
            client=client,
            cache=cache,
            bundle=bundle,
            backup_dir=backup_dir,
            timezone=timezone,
            now=now,
            mint=mint,
        )

    def edit(
        self, ref: str | int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        routine = find_routine(self._cache.account(), ref)
        return edit.edit_routine(self._ctx, routine.identifier, build, dry_run=dry_run)

    def edit_exercise(
        self, exercise_id: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        routine = routine_of_exercise(self._cache.account(), exercise_id)
        return edit.edit_routine(self._ctx, routine.identifier, build, dry_run=dry_run)

    def create(self, specs: Sequence[RoutineSpec], *, dry_run: bool) -> CreateResult:
        return create.create_routines(self._ctx, specs, dry_run=dry_run)

    def set_archived(
        self, refs: Sequence[str | int], *, archived: bool, dry_run: bool
    ) -> ArchiveResult:
        account = self._cache.account()
        targets: dict[int, Routine] = {}
        for ref in refs:
            routine = find_routine(account, ref)
            targets.setdefault(routine.identifier, routine)
        return archive.set_archived(
            self._ctx, list(targets.values()), archived=archived, dry_run=dry_run
        )
