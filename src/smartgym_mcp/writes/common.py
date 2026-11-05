"""What every write shares (API-client spec §7): the context it runs in, sending each
request once, the verifying re-read, snapshots, and the messages around them."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..account import AccountCache
from ..api import reads
from ..api.client import ApiCalls, ApiError, WriteOutcomeUnknown
from ..diff import RoutineView, view_of
from ..lookups import RoutineNotFound
from ..matching import ExerciseCatalog
from ..model import ApiPayloadError, Routine, parse_routine
from ..payloads import Mint

DRY = "Dry run — nothing sent. Re-run with dry_run=false to apply."
APPLIED = (
    "Applied on the SmartGym server and verified; your iPhone and Mac show it after their "
    "next refresh."
)
RETRY_ROUTINES = " Re-read the routines before retrying."
_REREAD_FAILED = "Re-reading failed, so whether the change landed is unknown."
_REREAD_ERRORS = (ApiError, ApiPayloadError, RoutineNotFound)
Post = tuple[str, str, Mapping[str, str]]  # (label in messages, path, form)


class WriteVerifyError(RuntimeError):
    """The server accepted a write but the routine now differs from the plan."""


class RoutineId(BaseModel):
    identifier: int
    name: str


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


def unverified(path: str, sent: str) -> str:
    return (
        f" {path} was sent and accepted, but re-reading to verify failed. Sent: {sent}."
        + RETRY_ROUTINES
    )


def active_names(routines: Sequence[Routine], *, besides: int | None = None) -> set[str]:
    """Lower-cased names of active routines — archived and removed ones may be reused."""
    return {
        r.name.strip().lower()
        for r in routines
        if not r.removed and not r.archived and r.identifier != besides
    }


@dataclass(frozen=True)
class WriteContext:
    """What a write runs against. `cache` lives for one tool call
    (`runtime.AppContext._call` empties it when the call ends), so a write plans against the
    account as the call found it; every send clears it for the verifying re-read."""

    client: ApiCalls
    cache: AccountCache
    bundle: ExerciseCatalog
    backup_dir: Path
    timezone: str
    now: Callable[[], datetime]
    mint: Mint

    def send(
        self, posts: Sequence[Post], *, on_unknown: Callable[[], str], retry_hint: str
    ) -> list[str]:
        """Post each request once, in order (never re-sent); return the sent labels.

        A failure re-raises its class naming what was sent, failed and not sent; an unknown
        outcome adds `on_unknown()`, a re-read of whether it landed; `retry_hint` ends it.
        """
        labels = [label for label, _, _ in posts]
        sent: list[str] = []
        for i, (label, path, form) in enumerate(posts):
            try:
                self.client.post(path, form)
            except ApiError as exc:
                self.cache.invalidate()
                message = _progress(exc, sent, label, labels[i + 1 :])
                if isinstance(exc, WriteOutcomeUnknown):
                    try:
                        found = on_unknown()
                    except _REREAD_ERRORS:
                        found = _REREAD_FAILED
                    message += f" {found}"
                raise _with_context(exc, message + retry_hint) from None
            sent.append(label)
        self.cache.invalidate()
        return sent

    @staticmethod
    def reread[T](read: Callable[[], T], failed: str) -> T:
        """The re-read that verifies a sent write; a failure keeps its class, + `failed`."""
        try:
            return read()
        except _REREAD_ERRORS as exc:
            raise _with_context(exc, failed) from None

    def routine_raw(self, identifier: int) -> dict[str, Any]:
        """The routine's JSON from `routine/single/<id>/`, read fresh every time."""
        routines = reads.routine_single(self.client, identifier).get("routines") or []
        if not routines:
            raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
        return dict(routines[0])

    def view(self, identifier: int) -> RoutineView:
        return view_of(parse_routine(self.routine_raw(identifier)))

    def catalog(self, *, custom: bool = True) -> ExerciseCatalog:
        """The app bundle plus (with `custom`) the account's custom exercises."""
        if not custom:
            return self.bundle
        return self.bundle.extended(self.cache.custom_exercises())
