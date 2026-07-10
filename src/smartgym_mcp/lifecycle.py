"""SmartGym app lifecycle: graceful quit + write session + relaunch (spec 03 §C).

The write path quits the app (never kill -9) so direct DB writes cannot race
the live WAL, then relaunches it so the sync engine pushes pending routines
immediately (verified: ~17 s to backend round-trip, Phase 0 spike).

`managed_write` is the ONE orchestration every write tool goes through:
quit → backup+transaction (db.open_rw_connection) → relaunch. Spec 02 tools
must not re-implement this dance.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

from . import db
from .config import Config
from .models import AppLifecycleReport

logger = logging.getLogger("smartgym_mcp.lifecycle")

_QUIT_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.5


def ensure_quit() -> bool:
    """Gracefully quit SmartGym if running. Returns whether it was running.

    Raises if the app does not exit within the timeout — the caller must NOT
    proceed to write against a live DB.
    """
    if not db.smartgym_is_running():
        return False
    logger.info("quitting SmartGym (graceful AppleScript quit)")
    result = subprocess.run(
        ["osascript", "-e", 'tell application "SmartGym" to quit'],
        capture_output=True,
        timeout=_QUIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to quit SmartGym: "
            f"{result.stderr.decode(errors='replace').strip()}. Close it manually and retry."
        )
    deadline = time.monotonic() + _QUIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if not db.smartgym_is_running():
            return True
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(
        f"SmartGym did not exit within {_QUIT_TIMEOUT_S:.0f}s. Close it manually and retry."
    )


def launch(cfg: Config) -> None:
    """Relaunch SmartGym so its sync engine pushes pending routines."""
    logger.info("relaunching SmartGym (%s)", cfg.app_bundle)
    subprocess.run(["open", str(cfg.app_bundle)], check=True, timeout=_QUIT_TIMEOUT_S)


@contextmanager
def managed_write(cfg: Config) -> Iterator[tuple[sqlite3.Connection, AppLifecycleReport]]:
    """The write session shared by every mutating tool.

    Gracefully quits SmartGym (unless the escape hatch is set), opens the
    backed-up RW transaction, and relaunches the app afterwards so the sync
    push fires. If the write fails after we quit the app, it is relaunched
    anyway — we never leave the user's app closed over a rolled-back write.

    The yielded report is finalized (relaunched flag) when the block exits.
    """
    was_running = False
    if not cfg.allow_write_while_running:
        was_running = ensure_quit()
    report = AppLifecycleReport(was_running=was_running, quit=was_running, relaunched=False)
    try:
        with db.open_rw_connection(cfg) as conn:
            yield conn, report
    except BaseException:
        if was_running:
            launch(cfg)
        raise
    launch(cfg)
    report.relaunched = True
