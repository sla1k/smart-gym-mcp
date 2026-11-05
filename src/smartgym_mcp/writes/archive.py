"""Archive / unarchive routines (`routine/archive/`, `routine/unarchive/`), verified by
re-reading the account. Routines are never deleted (DESIGN decisions)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from ..diff import DiffError, nameless_problem
from ..model import Routine
from ..payloads import archive_form, unarchive_form
from .common import (
    APPLIED,
    DRY,
    RETRY_ROUTINES,
    RoutineId,
    WriteContext,
    WriteVerifyError,
    unverified,
)


class ArchiveResult(BaseModel):
    dry_run: bool
    archived: bool
    routines: list[RoutineId]
    skipped: list[RoutineId]
    notice: str


def set_archived(
    ctx: WriteContext, targets: Sequence[Routine], *, archived: bool, dry_run: bool
) -> ArchiveResult:
    """`targets` are distinct routines; those already in the wanted state are skipped."""
    todo = [t for t in targets if t.archived != archived]
    if not archived:
        nameless = [p for p in map(nameless_problem, todo) if p]
        if nameless:
            raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(nameless))
    handles = [RoutineId(identifier=t.identifier, name=t.name) for t in todo]
    skipped = [
        RoutineId(identifier=t.identifier, name=t.name)
        for t in targets
        if t.archived == archived
    ]
    if dry_run or not todo:
        return ArchiveResult(
            dry_run=dry_run,
            archived=archived,
            routines=handles,
            skipped=skipped,
            notice=DRY if dry_run else "Nothing to change.",
        )
    path = "routine/archive/" if archived else "routine/unarchive/"
    form = archive_form if archived else unarchive_form
    sent = ctx.send(
        [(f"{path} ({t.name})", path, form(t.identifier)) for t in todo],
        on_unknown=lambda: _archive_state(ctx, todo, archived),
        retry_hint=RETRY_ROUTINES,
    )
    state = ctx.reread(lambda: _archived_by_id(ctx), unverified(path, ", ".join(sent)))
    wrong = [t.name for t in todo if state.get(t.identifier) != archived]
    if wrong:
        raise WriteVerifyError(
            f"SmartGym accepted the request, but these routines are not "
            f"{'archived' if archived else 'active'}: {', '.join(wrong)}."
        )
    return ArchiveResult(
        dry_run=False,
        archived=archived,
        routines=handles,
        skipped=skipped,
        notice=APPLIED,
    )


def _archived_by_id(ctx: WriteContext) -> dict[int, bool]:
    return {r.identifier: r.archived for r in ctx.cache.account().routines}


def _archive_state(ctx: WriteContext, todo: Sequence[Routine], archived: bool) -> str:
    state = _archived_by_id(ctx)
    target = "archived" if archived else "active"
    done = [t.name for t in todo if state.get(t.identifier) == archived]
    return f"Re-read: now {target}: {', '.join(done) or 'none'}."
