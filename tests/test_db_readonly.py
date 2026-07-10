"""Read-only checks against the live DB — safe to run while SmartGym is open."""

from __future__ import annotations

from smartgym_mcp import db


def test_ro_connection_settings(live_cfg):
    conn = db.open_ro_connection(live_cfg.db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        # The freshness guarantee: each SELECT is its own read snapshot.
        if hasattr(conn, "autocommit"):
            assert conn.autocommit is True  # Python 3.12+
        else:
            assert conn.isolation_level is None  # Python 3.11
    finally:
        conn.close()


def test_active_routines_present(live_cfg):
    conn = db.open_ro_connection(live_cfg.db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM ZROUTINE WHERE ZDATEREMOVED IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count >= 3


def test_epoch_roundtrip_and_sqlite_crosscheck(live_cfg):
    # ts=0 is the CoreData epoch itself: 2001-01-01 00:00:00 UTC.
    assert db.coredata_to_datetime(0).strftime("%Y-%m-%d %H:%M:%S") == "2001-01-01 00:00:00"

    conn = db.open_ro_connection(live_cfg.db_path)
    try:
        ts = 795826800.0  # a real ZDATEADDED value from the DB
        sqlite_utc = conn.execute(
            "SELECT datetime(? + 978307200, 'unixepoch')", (ts,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert db.coredata_to_datetime(ts).strftime("%Y-%m-%d %H:%M:%S") == sqlite_utc


def test_immutable_is_not_used():
    # Guard against a regression to immutable=1, which would ignore the WAL.
    import inspect

    src = inspect.getsource(db.open_ro_connection)
    assert "immutable" not in src
