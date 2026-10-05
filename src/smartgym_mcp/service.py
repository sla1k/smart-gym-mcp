"""Write orchestration over the SmartGym API (API-client spec §5 invariant 2, §7).

Every edit: resolve → fetch the routine fresh → plan (diff) → dry run returns
the plan → snapshot → send (`routine/update/`, then `routine/updateExercise/`)
→ re-fetch → verify against the plan's expected view. Writes are never re-sent;
a failure part-way names what was already sent, which request failed or has an
unknown outcome, and where the snapshot is. After an unknown outcome the
account is re-read once to report whether the change landed (spec §7).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .api.client import ApiCalls, ApiError, WriteOutcomeUnknown
from .api.store import AccountStore, RoutineNotFound
from .catalog import CatalogExercise
from .diff import (
    DEFAULT_SET,
    ChangeSet,
    DesiredRoutine,
    DiffError,
    ExerciseView,
    RoutineView,
    diff_routine,
    moves_as_readd,
    resolution_warnings,
    round3,
    view_of,
)
from .matching import ExerciseCatalog, UnresolvedExercise
from .model import SECTION_BY_LIST_GROUP, ApiPayloadError, Routine, parse_routine
from .models import ExerciseResolution, RoutineSpec
from .payloads import (
    SECTION_MOVE_IN_PLACE,
    Mint,
    add_routines_form,
    archive_form,
    encode_change,
    mint_unique_hashid,
    new_routine_payload,
    routine_entries,
    unarchive_form,
)
from .snapshots import save_snapshot

_DRY = "Dry run — nothing sent. Re-run with dry_run=false to apply."
_APPLIED = (
    "Applied on the SmartGym server and verified; your iPhone and Mac show it after their "
    "next refresh."
)
_REREAD_FAILED = "Re-reading failed, so whether the change landed is unknown."


class WriteVerifyError(RuntimeError):
    """The server accepted a write but the routine now differs from the plan."""


class RoutineId(BaseModel):
    identifier: int
    name: str


class EditResult(BaseModel):
    dry_run: bool
    routine: RoutineId
    changes: ChangeSet
    requests: list[str]
    snapshot: str | None
    notice: str


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


class ArchiveResult(BaseModel):
    dry_run: bool
    archived: bool
    routines: list[RoutineId]
    skipped: list[RoutineId]
    notice: str


def _now_local() -> datetime:
    return datetime.now().astimezone()


def compare_views(expected: RoutineView, actual: RoutineView) -> list[str]:
    problems: list[str] = []
    for field in ("name", "days", "goal", "note"):
        exp, act = getattr(expected, field), getattr(actual, field)
        if exp != act:
            problems.append(f"{field}: expected {exp!r}, got {act!r}")
    if len(expected.exercises) != len(actual.exercises):
        problems.append(
            f"exercise count: expected {len(expected.exercises)}, got {len(actual.exercises)}"
        )
    for i, (e, a) in enumerate(zip(expected.exercises, actual.exercises, strict=False)):
        for field in ("catalog_id", "section", "rest_seconds", "note", "sets"):
            ev, av = getattr(e, field), getattr(a, field)
            if ev != av:
                problems.append(
                    f"exercise #{i + 1} (catalog {e.catalog_id}) {field}: "
                    f"expected {ev!r}, got {av!r}"
                )
    return problems


def _payload_view(payload: Mapping[str, Any]) -> RoutineView:
    return RoutineView(
        name=str(payload["name"]),
        days=payload.get("days") or None,
        goal=payload.get("goal") or None,
        note=payload.get("note") or None,
        exercises=[
            ExerciseView(
                catalog_id=int(e["id"]),
                section=SECTION_BY_LIST_GROUP[int(e["listGroup"])],
                rest_seconds=int(e["pause"]),
                note=e.get("note") or None,
                sets=[
                    (round3(float(s["secondValue"])), round3(float(s["thirdValue"])))
                    for s in e["sets"]
                ],
            )
            for e in payload["exercises"]
        ],
    )


def _with_context(exc: Exception, suffix: str) -> Exception:
    """The same exception class with `suffix` appended — callers match on the class."""
    if isinstance(exc, ApiError):
        return type(exc)(f"{exc}{suffix}", code=exc.code)
    return type(exc)(f"{exc}{suffix}")


def _progress(exc: ApiError, sent: Sequence[str], in_flight: str) -> str:
    verdict = "Outcome unknown" if isinstance(exc, WriteOutcomeUnknown) else "Failed"
    return f" Already sent: {', '.join(sent) or 'nothing'}. {verdict}: {in_flight}."


class RoutineService:
    def __init__(
        self,
        client: ApiCalls,
        store: AccountStore,
        catalog: ExerciseCatalog,
        bundle: Mapping[int, CatalogExercise],
        *,
        backup_dir: Path,
        timezone: str,
        now: Callable[[], datetime] = _now_local,
        mint: Mint = mint_unique_hashid,
        move_in_place: bool = SECTION_MOVE_IN_PLACE,
    ) -> None:
        self._client = client
        self._store = store
        self._catalog = catalog
        self._bundle = bundle
        self._backup_dir = backup_dir
        self._tz = timezone
        self._now = now
        self._mint = mint
        self._move_in_place = move_in_place

    # ------------------------------------------------------------------ edits
    def edit(
        self, ref: str | int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        return self._edit(self._store.resolve(ref).identifier, build, dry_run=dry_run)

    def edit_exercise(
        self, exercise_id: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        routine = self._store.routine_of_exercise(exercise_id)
        return self._edit(routine.identifier, build, dry_run=dry_run)

    def _edit(
        self, identifier: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        raw = self._store.routine_raw(identifier)
        current = parse_routine(raw)
        handle = RoutineId(identifier=current.identifier, name=current.name)
        cs = diff_routine(current, build(current), self._catalog)
        if not self._move_in_place:
            cs = moves_as_readd(cs, current)
        if cs.is_empty:
            return EditResult(
                dry_run=dry_run,
                routine=handle,
                changes=cs,
                requests=[],
                snapshot=None,
                notice="Nothing to change — the routine already looks like that.",
            )
        now = self._now()
        enc = encode_change(
            cs, current, self._bundle, timezone=self._tz, now=now, mint=self._mint
        )
        requests = [
            (path, form)
            for path, form in (
                ("routine/update/", enc.structure),
                ("routine/updateExercise/", enc.exercise_edits),
            )
            if form is not None
        ]
        planned = [path for path, _ in requests]
        if dry_run:
            return EditResult(
                dry_run=True,
                routine=handle,
                changes=cs,
                requests=planned,
                snapshot=None,
                notice=_DRY,
            )
        snapshot = save_snapshot(self._backup_dir, raw, now=now)
        sent: list[str] = []
        for path, form in requests:
            try:
                self._client.post(path, form)
            except ApiError as exc:
                self._store.invalidate()
                message = _progress(exc, sent, path)
                if isinstance(exc, WriteOutcomeUnknown):
                    message += f" {self._landed(current, cs.expected)}"
                message += (
                    f" Snapshot of the routine before this edit: {snapshot}. Re-read the "
                    "routine before retrying."
                )
                raise _with_context(exc, message) from None
            sent.append(path)
        self._store.invalidate()
        try:
            after = view_of(parse_routine(self._store.routine_raw(current.identifier)))
        except (ApiError, ApiPayloadError, RoutineNotFound) as exc:
            raise _with_context(
                exc,
                f" All requests were sent ({', '.join(sent)}) but re-reading the routine to "
                f"verify them failed. Snapshot of the routine before this edit: {snapshot}.",
            ) from None
        problems = compare_views(cs.expected, after)
        if problems:
            raise WriteVerifyError(
                "SmartGym accepted the edit, but the routine now differs from the plan: "
                + "; ".join(problems)
                + f". Snapshot of the routine before this edit: {snapshot}."
            )
        return EditResult(
            dry_run=False,
            routine=handle,
            changes=cs,
            requests=sent,
            snapshot=str(snapshot),
            notice=_APPLIED,
        )

    def _landed(self, current: Routine, expected: RoutineView) -> str:
        try:
            after = view_of(parse_routine(self._store.routine_raw(current.identifier)))
        except (ApiError, ApiPayloadError, RoutineNotFound):
            return _REREAD_FAILED
        if after == expected:
            return "Re-read: the change landed."
        if after == view_of(current):
            return "Re-read: the change did not land."
        return (
            "Re-read: the change landed only partially — the routine differs from the plan in: "
            + "; ".join(compare_views(expected, after))
            + "."
        )

    # ----------------------------------------------------------------- create
    def create(self, specs: Sequence[RoutineSpec], *, dry_run: bool) -> CreateResult:
        if not dry_run:
            self._store.invalidate()
        data = self._store.data()
        taken = {r.name.strip().lower() for r in data.routines if not r.removed}
        problems: list[str] = []
        plans: list[CreatePlan] = []
        resolved: list[list[CatalogExercise]] = []
        seen: set[str] = set()
        for spec in specs:
            key = spec.name.strip().lower()
            if key in taken:
                problems.append(
                    f"Routine {spec.name!r} already exists (archive or rename it)."
                )
            if key in seen:
                problems.append(f"Routine {spec.name!r} appears twice in this program.")
            seen.add(key)
            entries = routine_entries(spec)
            resolutions: list[ExerciseResolution] = []
            cats: list[CatalogExercise] = []
            warnings: list[str] = []
            for section, ex in entries:
                try:
                    res = self._catalog.resolve(ex.exercise)
                except UnresolvedExercise as exc:
                    problems.append(f"{spec.name} / {section}: {exc}")
                    continue
                cat = self._bundle.get(res.z_pk)
                if cat is None:
                    problems.append(
                        f"{spec.name} / {section}: {res.resolved_name!r} is not in the app's "
                        "exercise catalog."
                    )
                    continue
                warnings.extend(resolution_warnings(res, ex.sets))
                resolutions.append(res)
                cats.append(cat)
            plans.append(
                CreatePlan(
                    name=spec.name,
                    resolutions=resolutions,
                    exercise_count=len(entries),
                    set_count=sum(len(ex.sets or [DEFAULT_SET]) for _, ex in entries),
                    warnings=warnings,
                )
            )
            resolved.append(cats)
        if problems:
            raise DiffError("Rejected — nothing was created:\n- " + "\n- ".join(problems))
        if dry_run:
            return CreateResult(dry_run=True, plan=plans, created=[], notice=_DRY)

        now = self._now()
        top = max((r.number for r in data.routines), default=0)
        payloads = [
            new_routine_payload(spec, cats, number=top + i + 1, now=now, mint=self._mint)
            for i, (spec, cats) in enumerate(zip(specs, resolved, strict=True))
        ]
        try:
            self._client.post("routine/add/", add_routines_form(payloads, timezone=self._tz))
        except ApiError as exc:
            self._store.invalidate()
            message = _progress(exc, [], "routine/add/")
            if isinstance(exc, WriteOutcomeUnknown):
                message += f" {self._created(specs, payloads)}"
            raise _with_context(
                exc, message + " Re-read the routines before retrying."
            ) from None
        self._store.invalidate()
        by_hash = {r.unique_hashid: r for r in self._store.data().routines}
        created: list[RoutineId] = []
        mismatches: list[str] = []
        for spec, payload in zip(specs, payloads, strict=True):
            routine = by_hash.get(int(payload["uniqueHashID"]))
            if routine is None:
                mismatches.append(f"{spec.name!r} is missing after the create")
                continue
            created.append(RoutineId(identifier=routine.identifier, name=routine.name))
            mismatches += [
                f"{spec.name}: {p}"
                for p in compare_views(_payload_view(payload), view_of(routine))
            ]
        if mismatches:
            raise WriteVerifyError(
                "SmartGym accepted the create, but: " + "; ".join(mismatches)
            )
        return CreateResult(dry_run=False, plan=plans, created=created, notice=_APPLIED)

    def _created(
        self, specs: Sequence[RoutineSpec], payloads: Sequence[dict[str, Any]]
    ) -> str:
        try:
            by_hash = {r.unique_hashid: r for r in self._store.data().routines}
        except (ApiError, ApiPayloadError):
            return _REREAD_FAILED
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

    # ---------------------------------------------------------------- archive
    def set_archived(
        self, refs: Sequence[str | int], *, archived: bool, dry_run: bool
    ) -> ArchiveResult:
        if not dry_run:
            self._store.invalidate()
        targets: dict[int, Routine] = {}
        for ref in refs:
            routine = self._store.resolve(ref)
            targets.setdefault(routine.identifier, routine)
        todo = [t for t in targets.values() if t.archived != archived]
        handles = [RoutineId(identifier=t.identifier, name=t.name) for t in todo]
        skipped = [
            RoutineId(identifier=t.identifier, name=t.name)
            for t in targets.values()
            if t.archived == archived
        ]
        if dry_run or not todo:
            return ArchiveResult(
                dry_run=dry_run,
                archived=archived,
                routines=handles,
                skipped=skipped,
                notice=_DRY if dry_run else "Nothing to change.",
            )
        path = "routine/archive/" if archived else "routine/unarchive/"
        sent: list[str] = []
        for t in todo:
            label = f"{path} ({t.name})"
            form = archive_form(t.identifier) if archived else unarchive_form(t.identifier)
            try:
                self._client.post(path, form)
            except ApiError as exc:
                self._store.invalidate()
                message = _progress(exc, sent, label)
                if isinstance(exc, WriteOutcomeUnknown):
                    message += f" {self._archive_state(todo, archived)}"
                raise _with_context(
                    exc, message + " Re-read the routines before retrying."
                ) from None
            sent.append(label)
        self._store.invalidate()
        state = {r.identifier: r.archived for r in self._store.data().routines}
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
            notice=_APPLIED,
        )

    def _archive_state(self, todo: Sequence[Routine], archived: bool) -> str:
        try:
            state = {r.identifier: r.archived for r in self._store.data().routines}
        except (ApiError, ApiPayloadError):
            return _REREAD_FAILED
        target = "archived" if archived else "active"
        done = [t.name for t in todo if state.get(t.identifier) == archived]
        return f"Re-read: now {target}: {', '.join(done) or 'none'}."


__all__ = [
    "ArchiveResult",
    "CreatePlan",
    "CreateResult",
    "EditResult",
    "RoutineId",
    "RoutineService",
    "WriteVerifyError",
    "compare_views",
]
