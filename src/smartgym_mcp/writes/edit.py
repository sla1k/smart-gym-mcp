"""Edit an existing routine (DESIGN invariants 2 and 3).

Fetch the routine fresh → plan (diff) → dry run returns the plan → snapshot → send
(`routine/update/`, then `routine/updateExercise/`) → re-fetch → verify against the plan's
expected view. After an unknown outcome the routine is re-read once to report whether the
change landed (spec §7).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from ..diff import ChangeSet, DiffError, RoutineView, diff_routine, view_of
from ..inputs import DesiredRoutine
from ..model import SECTIONS, Routine, parse_routine
from ..payloads import encode_change
from .common import (
    APPLIED,
    DRY,
    RoutineId,
    WriteContext,
    WriteVerifyError,
    active_names,
    compare_views,
    save_snapshot,
)


class EditResult(BaseModel):
    dry_run: bool
    routine: RoutineId
    changes: ChangeSet
    requests: list[str]
    snapshot: str | None
    notice: str


def edit_routine(
    ctx: WriteContext,
    identifier: int,
    build: Callable[[Routine], DesiredRoutine],
    *,
    dry_run: bool,
) -> EditResult:
    raw = ctx.routine_raw(identifier)
    current = parse_routine(raw)
    handle = RoutineId(identifier=current.identifier, name=current.name)
    desired = build(current)
    # Custom exercises are read only when a change adds one.
    adds_by_name = any(
        want.exercise is not None
        for section in SECTIONS
        for want in desired.section_list(section) or []
    )
    change = diff_routine(current, desired, ctx.catalog(custom=adds_by_name))
    renamed = next((c.new for c in change.routine_changes if c.field == "name"), None)
    if renamed is not None and renamed.strip().lower() in active_names(
        ctx.cache.account().routines, besides=current.identifier
    ):
        raise DiffError(f"Routine {renamed!r} already exists — rename it.")
    now = ctx.now()
    requests = encode_change(change, current, timezone=ctx.timezone, now=now, mint=ctx.mint)
    if not requests:
        return EditResult(
            dry_run=dry_run,
            routine=handle,
            changes=change,
            requests=[],
            snapshot=None,
            notice="Nothing to change — the routine already looks like that.",
        )
    planned = [path for path, _ in requests]
    if dry_run:
        return EditResult(
            dry_run=True,
            routine=handle,
            changes=change,
            requests=planned,
            snapshot=None,
            notice=DRY,
        )
    snapshot = save_snapshot(ctx.backup_dir, raw, now=now)
    kept = f" Snapshot of the routine before this edit: {snapshot}."
    sent = ctx.send(
        [(path, path, form) for path, form in requests],
        on_unknown=lambda: _landed(ctx, current, change.expected),
        retry_hint=kept + " Re-read the routine before retrying.",
    )
    after = ctx.reread(
        lambda: ctx.view(current.identifier),
        f" All requests were sent ({', '.join(sent)}) but re-reading the routine to "
        "verify them failed." + kept,
    )
    problems = compare_views(change.expected, after)
    if problems:
        raise WriteVerifyError(
            "SmartGym accepted the edit, but the routine now differs from the plan: "
            + "; ".join(problems)
            + "."
            + kept
        )
    return EditResult(
        dry_run=False,
        routine=handle,
        changes=change,
        requests=sent,
        snapshot=str(snapshot),
        notice=APPLIED,
    )


def _landed(ctx: WriteContext, current: Routine, expected: RoutineView) -> str:
    after = ctx.view(current.identifier)
    if after == expected:
        return "Re-read: the change landed."
    if after == view_of(current):
        return "Re-read: the change did not land."
    return (
        "Re-read: the change landed only partially — the routine differs from the plan in: "
        + "; ".join(compare_views(expected, after))
        + "."
    )
