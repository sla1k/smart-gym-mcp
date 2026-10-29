"""Write orchestration over the SmartGym API (DESIGN invariant 2, API-client spec §7).

Every edit: resolve → fetch the routine fresh → plan (diff) → dry run returns
the plan → snapshot → send (`routine/update/`, then `routine/updateExercise/`)
→ re-fetch → verify against the plan's expected view. Writes are never re-sent;
a failure part-way names what was already sent, which request failed or has an
unknown outcome, and where the snapshot is. After an unknown outcome the
account is re-read once to report whether the change landed (spec §7).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .account import AccountCache
from .api import reads
from .api.client import ApiCalls, ApiError, WriteOutcomeUnknown
from .diff import (
    ChangeSet,
    DesiredRoutine,
    DiffError,
    ExerciseView,
    RoutineView,
    diff_routine,
    resolution_warnings,
    round3,
    view_of,
)
from .inputs import DEFAULT_SET, RoutineSpec
from .lookups import RoutineNotFound, find_routine, routine_of_exercise
from .matching import ExerciseCatalog, ExerciseResolution, UnresolvedExercise
from .model import SECTION_BY_LIST_GROUP, SECTIONS, ApiPayloadError, Routine, parse_routine
from .payloads import (
    Mint,
    add_routines_form,
    archive_form,
    encode_change,
    mint_unique_hashid,
    new_routine_payload,
    routine_entries,
    unarchive_form,
)

_DRY = "Dry run — nothing sent. Re-run with dry_run=false to apply."
_APPLIED = (
    "Applied on the SmartGym server and verified; your iPhone and Mac show it after their "
    "next refresh."
)
_REREAD_FAILED = "Re-reading failed, so whether the change landed is unknown."
_RETRY_ROUTINES = " Re-read the routines before retrying."
_REREAD_ERRORS = (ApiError, ApiPayloadError, RoutineNotFound)
_Post = tuple[str, str, Mapping[str, str]]  # (label in messages, path, form)


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


def compare_views(
    expected: RoutineView, actual: RoutineView, *, ignore: frozenset[str] = frozenset()
) -> list[str]:
    problems: list[str] = []
    for field in ("name", "days", "goal", "note"):
        if field in ignore:
            continue
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


def save_snapshot(backup_dir: Path, routine_raw: Mapping[str, Any], *, now: datetime) -> Path:
    """Save the fetched routine JSON verbatim before a write (spec §7).

    Restore = smartgym_apply_routine from the snapshot's content.
    """
    folder = backup_dir / now.strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"routine-{routine_raw['identifier']}.json"
    path.write_text(
        json.dumps(routine_raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _with_context(exc: Exception, suffix: str) -> Exception:
    """The same exception class with `suffix` appended — callers match on the class."""
    return type(exc)(f"{exc}{suffix}")


def _progress(
    exc: ApiError, sent: Sequence[str], in_flight: str, not_sent: Sequence[str] = ()
) -> str:
    verdict = "Outcome unknown" if isinstance(exc, WriteOutcomeUnknown) else "Failed"
    message = f" Already sent: {', '.join(sent) or 'nothing'}. {verdict}: {in_flight}."
    if not_sent:
        message += f" Not sent: {', '.join(not_sent)}."
    return message


def _unverified(path: str, sent: str) -> str:
    return (
        f" {path} was sent and accepted, but re-reading to verify failed. Sent: {sent}."
        + _RETRY_ROUTINES
    )


def _active_names(routines: Sequence[Routine], *, besides: int | None = None) -> set[str]:
    """Lower-cased names of active routines — archived and removed ones may be reused."""
    return {
        r.name.strip().lower()
        for r in routines
        if not r.removed and not r.archived and r.identifier != besides
    }


class RoutineService:
    """`cache` lives for one tool call (AppContext.use empties it when the call ends), so a
    write plans against the account as the call found it; every send clears it for the
    verifying re-read."""

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
        self._client = client
        self._cache = cache
        self._bundle = bundle
        self._backup_dir = backup_dir
        self._tz = timezone
        self._now = now
        self._mint = mint

    # ------------------------------------------------------- send and re-read
    def _send(
        self, posts: Sequence[_Post], *, on_unknown: Callable[[], str], retry_hint: str
    ) -> list[str]:
        """Post each request once, in order (never re-sent); return the sent labels.

        A failure re-raises its class naming what was sent, failed and not sent; an unknown
        outcome adds `on_unknown()`, a re-read of whether it landed; `retry_hint` ends it.
        """
        labels = [label for label, _, _ in posts]
        sent: list[str] = []
        for i, (label, path, form) in enumerate(posts):
            try:
                self._client.post(path, form)
            except ApiError as exc:
                self._cache.invalidate()
                message = _progress(exc, sent, label, labels[i + 1 :])
                if isinstance(exc, WriteOutcomeUnknown):
                    try:
                        found = on_unknown()
                    except _REREAD_ERRORS:
                        found = _REREAD_FAILED
                    message += f" {found}"
                raise _with_context(exc, message + retry_hint) from None
            sent.append(label)
        self._cache.invalidate()
        return sent

    @staticmethod
    def _reread[T](read: Callable[[], T], failed: str) -> T:
        """The re-read that verifies a sent write; a failure keeps its class, + `failed`."""
        try:
            return read()
        except _REREAD_ERRORS as exc:
            raise _with_context(exc, failed) from None

    def _routine_raw(self, identifier: int) -> dict[str, Any]:
        """The routine's JSON from `routine/single/<id>/`, read fresh every time."""
        routines = reads.routine_single(self._client, identifier).get("routines") or []
        if not routines:
            raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
        return dict(routines[0])

    def _view(self, identifier: int) -> RoutineView:
        return view_of(parse_routine(self._routine_raw(identifier)))

    def _routines_by_hash(self) -> dict[int, Routine]:
        return {r.unique_hashid: r for r in self._cache.account().routines}

    def _archived_by_id(self) -> dict[int, bool]:
        return {r.identifier: r.archived for r in self._cache.account().routines}

    def _catalog(self, *, custom: bool = True) -> ExerciseCatalog:
        """The app bundle plus (with `custom`) the account's custom exercises."""
        if not custom:
            return self._bundle
        return self._bundle.extended(self._cache.custom_exercises())

    # ------------------------------------------------------------------ edits
    def edit(
        self, ref: str | int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        return self._edit(
            find_routine(self._cache.account(), ref).identifier, build, dry_run=dry_run
        )

    def edit_exercise(
        self, exercise_id: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        routine = routine_of_exercise(self._cache.account(), exercise_id)
        return self._edit(routine.identifier, build, dry_run=dry_run)

    def _edit(
        self, identifier: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        raw = self._routine_raw(identifier)
        current = parse_routine(raw)
        handle = RoutineId(identifier=current.identifier, name=current.name)
        desired = build(current)
        # Custom exercises are read only when a change adds one.
        adds_by_name = any(
            want.exercise is not None
            for section in SECTIONS
            for want in desired.section_list(section) or []
        )
        catalog = self._catalog(custom=adds_by_name)
        change = diff_routine(current, desired, catalog)
        renamed = next((c.new for c in change.routine_changes if c.field == "name"), None)
        if renamed is not None and renamed.strip().lower() in _active_names(
            self._cache.account().routines, besides=current.identifier
        ):
            raise DiffError(f"Routine {renamed!r} already exists — rename it.")
        now = self._now()
        requests = encode_change(change, current, timezone=self._tz, now=now, mint=self._mint)
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
                notice=_DRY,
            )
        snapshot = save_snapshot(self._backup_dir, raw, now=now)
        kept = f" Snapshot of the routine before this edit: {snapshot}."
        sent = self._send(
            [(path, path, form) for path, form in requests],
            on_unknown=lambda: self._landed(current, change.expected),
            retry_hint=kept + " Re-read the routine before retrying.",
        )
        after = self._reread(
            lambda: self._view(current.identifier),
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
            notice=_APPLIED,
        )

    def _landed(self, current: Routine, expected: RoutineView) -> str:
        after = self._view(current.identifier)
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
        account = self._cache.account()
        catalog = self._catalog()
        taken = _active_names(account.routines)
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
            return CreateResult(dry_run=True, plan=plans, created=[], notice=_DRY)

        now = self._now()
        top = max((r.number for r in account.routines), default=0)
        payloads = [
            new_routine_payload(
                spec,
                [r.exercise for r in plan.resolutions],
                number=top + i + 1,
                now=now,
                mint=self._mint,
            )
            for i, (spec, plan) in enumerate(zip(specs, plans, strict=True))
        ]
        path = "routine/add/"
        self._send(
            [(path, path, add_routines_form(payloads, timezone=self._tz))],
            on_unknown=lambda: self._created(specs, payloads),
            retry_hint=_RETRY_ROUTINES,
        )
        by_hash = self._reread(
            self._routines_by_hash,
            _unverified(path, ", ".join(repr(spec.name) for spec in specs)),
        )
        created: list[RoutineId] = []
        mismatches: list[str] = []
        for spec, payload in zip(specs, payloads, strict=True):
            routine = by_hash.get(int(payload["uniqueHashID"]))
            if routine is None:
                mismatches.append(f"{spec.name!r} is missing after the create")
                continue
            created.append(RoutineId(identifier=routine.identifier, name=routine.name))
            # Without days in the payload the server picks its own default ("0001").
            ignore = frozenset() if payload.get("days") else frozenset({"days"})
            mismatches += [
                f"{spec.name}: {p}"
                for p in compare_views(_payload_view(payload), view_of(routine), ignore=ignore)
            ]
        if mismatches:
            raise WriteVerifyError(
                "SmartGym accepted the create, but: " + "; ".join(mismatches)
            )
        return CreateResult(dry_run=False, plan=plans, created=created, notice=_APPLIED)

    def _created(
        self, specs: Sequence[RoutineSpec], payloads: Sequence[dict[str, Any]]
    ) -> str:
        by_hash = self._routines_by_hash()
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
        account = self._cache.account()
        targets: dict[int, Routine] = {}
        for ref in refs:
            routine = find_routine(account, ref)
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
        form = archive_form if archived else unarchive_form
        sent = self._send(
            [(f"{path} ({t.name})", path, form(t.identifier)) for t in todo],
            on_unknown=lambda: self._archive_state(todo, archived),
            retry_hint=_RETRY_ROUTINES,
        )
        state = self._reread(self._archived_by_id, _unverified(path, ", ".join(sent)))
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
        state = self._archived_by_id()
        target = "archived" if archived else "active"
        done = [t.name for t in todo if state.get(t.identifier) == archived]
        return f"Re-read: now {target}: {', '.join(done) or 'none'}."
