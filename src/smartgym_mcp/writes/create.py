"""Create routines — a program (spec §6): additive only, sent as one `routine/add/`, then
verified by re-reading the account."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from ..diff import DiffError, new_routine_view, resolution_warnings, view_of
from ..inputs import DEFAULT_SET, RoutineSpec, routine_entries
from ..matching import ExerciseResolution, UnresolvedExercise
from ..model import Routine
from ..payloads import add_routines_form, new_routine_payload
from .common import (
    APPLIED,
    DRY,
    RETRY_ROUTINES,
    RoutineId,
    WriteContext,
    WriteVerifyError,
    active_names,
    compare_views,
    unverified,
)


class CreatePlan(BaseModel):
    name: str
    resolutions: list[ExerciseResolution]
    exercise_count: int
    set_count: int
    warnings: list[str]


class CreateResult(BaseModel):
    dry_run: bool
    plan: list[CreatePlan]
    created: list[RoutineId]
    notice: str


def create_routines(
    ctx: WriteContext, specs: Sequence[RoutineSpec], *, dry_run: bool
) -> CreateResult:
    account = ctx.cache.account()
    catalog = ctx.catalog()
    taken = active_names(account.routines)
    problems: list[str] = []
    plans: list[CreatePlan] = []
    seen: set[str] = set()
    for spec in specs:
        key = spec.name.strip().lower()
        if key in taken:
            problems.append(f"Routine {spec.name!r} already exists — rename it.")
        if key in seen:
            problems.append(f"Routine {spec.name!r} appears twice in this program.")
        seen.add(key)
        entries = routine_entries(spec)
        resolutions: list[ExerciseResolution] = []
        warnings: list[str] = []
        for section, ex in entries:
            try:
                res = catalog.resolve(ex.exercise)
            except UnresolvedExercise as exc:
                problems.append(f"{spec.name} / {section}: {exc}")
                continue
            warnings.extend(resolution_warnings(res, ex.sets))
            resolutions.append(res)
        plans.append(
            CreatePlan(
                name=spec.name,
                resolutions=resolutions,
                exercise_count=len(entries),
                set_count=sum(len(ex.sets or [DEFAULT_SET]) for _, ex in entries),
                warnings=warnings,
            )
        )
    if problems:
        raise DiffError("Rejected — nothing was created:\n- " + "\n- ".join(problems))
    if dry_run:
        return CreateResult(dry_run=True, plan=plans, created=[], notice=DRY)

    now = ctx.now()
    top = max((r.number for r in account.routines), default=0)
    payloads = [
        new_routine_payload(
            spec,
            [r.exercise for r in plan.resolutions],
            number=top + i + 1,
            now=now,
            mint=ctx.mint,
        )
        for i, (spec, plan) in enumerate(zip(specs, plans, strict=True))
    ]
    path = "routine/add/"
    ctx.send(
        [(path, path, add_routines_form(payloads, timezone=ctx.timezone))],
        on_unknown=lambda: _created(ctx, specs, payloads),
        retry_hint=RETRY_ROUTINES,
    )
    by_hash = ctx.reread(
        lambda: _routines_by_hash(ctx),
        unverified(path, ", ".join(repr(spec.name) for spec in specs)),
    )
    created: list[RoutineId] = []
    mismatches: list[str] = []
    for spec, plan, payload in zip(specs, plans, payloads, strict=True):
        routine = by_hash.get(int(payload["uniqueHashID"]))
        if routine is None:
            mismatches.append(f"{spec.name!r} is missing after the create")
            continue
        created.append(RoutineId(identifier=routine.identifier, name=routine.name))
        expected = new_routine_view(spec, plan.resolutions)
        # Without days in the payload the server picks its own default ("0001").
        ignore = frozenset() if expected.days else frozenset({"days"})
        mismatches += [
            f"{spec.name}: {p}"
            for p in compare_views(expected, view_of(routine), ignore=ignore)
        ]
    if mismatches:
        raise WriteVerifyError("SmartGym accepted the create, but: " + "; ".join(mismatches))
    return CreateResult(dry_run=False, plan=plans, created=created, notice=APPLIED)


def _routines_by_hash(ctx: WriteContext) -> dict[int, Routine]:
    return {r.unique_hashid: r for r in ctx.cache.account().routines}


def _created(
    ctx: WriteContext, specs: Sequence[RoutineSpec], payloads: Sequence[dict[str, Any]]
) -> str:
    by_hash = _routines_by_hash(ctx)
    found: list[str] = []
    missing: list[str] = []
    for spec, payload in zip(specs, payloads, strict=True):
        routine = by_hash.get(int(payload["uniqueHashID"]))
        if routine is None:
            missing.append(repr(spec.name))
        else:
            found.append(f"{routine.name!r} (id {routine.identifier})")
    parts = []
    if found:
        parts.append(f"created {', '.join(found)}")
    if missing:
        parts.append(f"not created {', '.join(missing)}")
    return f"Re-read: {'; '.join(parts)}."
