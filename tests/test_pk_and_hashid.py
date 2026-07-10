"""PK allocation and hashid generation — temp-copy RW, no mutation of the live DB."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from smartgym_mcp import db

# Reference Z_ENT values — drift guards only; production reads them at runtime.
REFERENCE_Z_ENT = {
    "Routine": 15,
    "UniqExercise": 20,
    "Values": 21,
    "Workout": 22,
    "History": 11,
}


def _open_manual(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    db._enable_autocommit(conn)
    return conn


def test_z_ent_resolves_at_runtime(temp_db_cfg):
    conn = _open_manual(temp_db_cfg.db_path)
    try:
        for name, expected in REFERENCE_Z_ENT.items():
            row = conn.execute(
                "SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = ?", (name,)
            ).fetchone()
            assert row is not None and row[0] == expected, name
    finally:
        conn.close()


def test_next_pk_allocates_and_rolls_back(temp_db_cfg):
    conn = _open_manual(temp_db_cfg.db_path)
    try:
        before = conn.execute(
            "SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'"
        ).fetchone()[0]

        conn.execute("BEGIN")
        z_pk, z_ent = db.next_pk(conn, "Routine")
        assert z_pk == before + 1
        assert z_ent == REFERENCE_Z_ENT["Routine"]
        conn.execute("ROLLBACK")

        after = conn.execute(
            "SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'"
        ).fetchone()[0]
        assert after == before  # rolled back — Z_MAX unchanged
    finally:
        conn.close()


def test_next_pk_unknown_entity(temp_db_cfg):
    conn = _open_manual(temp_db_cfg.db_path)
    try:
        with pytest.raises(KeyError):
            db.next_pk(conn, "NoSuchEntity")
    finally:
        conn.close()


def test_generate_hashid_shape_and_uniqueness(live_cfg):
    conn = db.open_ro_connection(live_cfg.db_path)
    try:
        when = datetime(2026, 6, 8)
        seen = set()
        for _ in range(100):
            h = db.generate_uniquehashid(conn, when)
            s = str(h)
            assert len(s) == 14 and s.startswith("260608")
            assert h not in seen  # distinct within the batch
            seen.add(h)
            # not colliding with any existing row
            for table in db._HASHID_TABLES:
                assert (
                    conn.execute(
                        f"SELECT 1 FROM {table} WHERE ZUNIQUEHASHID = ?", (h,)
                    ).fetchone()
                    is None
                )
    finally:
        conn.close()
