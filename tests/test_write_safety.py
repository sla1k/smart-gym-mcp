"""Write-path safety: preflight, backup round-trip, RW transaction semantics.

All writes target a temp copy. A guard refuses any cfg whose db_path is not
under tmp_path, so a misconfigured test can never touch the real DB.
"""

from __future__ import annotations

import filecmp
import sqlite3
from dataclasses import replace

import pytest

from smartgym_mcp import db


@pytest.fixture(autouse=True)
def _never_touch_live(temp_db_cfg, tmp_path):
    assert str(temp_db_cfg.db_path).startswith(str(tmp_path)), (
        "refusing to run a write test against a non-temp DB"
    )


def test_refuse_if_running_default(temp_db_cfg, monkeypatch):
    # Force "running" so the refuse path is exercised regardless of the host.
    monkeypatch.setattr(db, "smartgym_is_running", lambda *a, **k: True)
    cfg = replace(temp_db_cfg, allow_write_while_running=False)
    with pytest.raises(RuntimeError, match="SmartGym is running"), db.open_rw_connection(cfg):
        pass


def test_running_check_matches_exact_process_name(monkeypatch):
    # Regression: `pgrep -i smartgym` (substring) also matches this server's own
    # `smartgym-mcp` process, wedging every write/quit path. Must use exact -x.
    captured: dict[str, list[str]] = {}

    class _Result:
        returncode = 1
        stderr = b""

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(db.subprocess, "run", fake_run)
    assert db.smartgym_is_running() is False
    assert "-ix" in captured["cmd"], "process probe must use exact name matching"
    assert captured["cmd"][-1] == "SmartGym"


def test_running_check_fails_closed(monkeypatch, temp_db_cfg):
    # If the running-state cannot be determined, writes must refuse, not proceed.
    monkeypatch.setattr(
        db.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no pgrep"))
    )
    with pytest.raises(RuntimeError):
        db.smartgym_is_running()


def test_backup_roundtrip(temp_db_cfg):
    dest = db.backup_db(temp_db_cfg)
    backed = dest / temp_db_cfg.db_path.name
    assert backed.exists()
    assert filecmp.cmp(str(temp_db_cfg.db_path), str(backed), shallow=False)


def test_rw_commit_bumps_z_max(temp_db_cfg):
    before = (
        sqlite3.connect(str(temp_db_cfg.db_path))
        .execute("SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'")
        .fetchone()[0]
    )

    with db.open_rw_connection(temp_db_cfg) as conn:
        db.next_pk(conn, "Routine")

    after = (
        sqlite3.connect(str(temp_db_cfg.db_path))
        .execute("SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'")
        .fetchone()[0]
    )
    assert after == before + 1


def test_rw_rollback_on_error_leaves_z_max(temp_db_cfg):
    before = (
        sqlite3.connect(str(temp_db_cfg.db_path))
        .execute("SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'")
        .fetchone()[0]
    )

    with pytest.raises(ValueError), db.open_rw_connection(temp_db_cfg) as conn:
        db.next_pk(conn, "Routine")
        raise ValueError("boom")  # force rollback

    after = (
        sqlite3.connect(str(temp_db_cfg.db_path))
        .execute("SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'")
        .fetchone()[0]
    )
    assert after == before


def test_rw_checkpoint_truncates_wal(temp_db_cfg):
    with db.open_rw_connection(temp_db_cfg) as conn:
        db.next_pk(conn, "Routine")
    wal = temp_db_cfg.db_path.parent / (temp_db_cfg.db_path.name + "-wal")
    # TRUNCATE checkpoint resets the WAL to (near) empty after commit.
    assert not wal.exists() or wal.stat().st_size == 0
