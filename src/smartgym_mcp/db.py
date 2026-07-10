"""Foundation DB primitives: connections, epoch math, PK allocation, hashids,
process check, backup.

This module owns SQLite mechanics only. Actual queries (spec 01) and write
procedures (spec 02) live in sibling modules and build on these primitives.

CRITICAL — WAL freshness:
    SmartGym's DB runs in WAL mode and the `-wal` file is routinely *newer*
    than the main `.sqlite`. Reads therefore use `mode=ro` (which applies the
    WAL) and run in AUTOCOMMIT so every SELECT starts a fresh read snapshot and
    sees the app's latest committed writes. NEVER use `immutable=1` — it ignores
    the WAL and silently serves stale data.
"""

from __future__ import annotations

import logging
import random
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timezone
from pathlib import Path

from .config import Config

logger = logging.getLogger("smartgym_mcp.db")

# CoreData stores timestamps as seconds since 2001-01-01 00:00:00 UTC.
CORE_DATA_EPOCH_OFFSET = 978307200

# SQL fragment for converting a CoreData timestamp column to local ISO datetime.
# Used by queries.py, e.g. f"datetime(v.ZDATEADDED {EPOCH_SQL})".
EPOCH_SQL = f"+ {CORE_DATA_EPOCH_OFFSET}, 'unixepoch', 'localtime'"

# The three entities that carry a sync hashid (ZWORKOUT does not).
_HASHID_TABLES = ("ZROUTINE", "ZUNIQEXERCISE", "ZVALUES")


def _enable_autocommit(conn: sqlite3.Connection) -> None:
    """Put the connection in manual/autocommit mode across Python versions.

    On 3.12+ the PEP 249 ``autocommit`` attribute is preferred; on 3.11 fall
    back to the legacy ``isolation_level = None``. In both modes statements run
    without an implicit wrapping transaction, which is what guarantees read
    freshness (RO) and lets us control transactions with explicit SQL (RW).
    """
    if sys.version_info >= (3, 12):
        conn.autocommit = True
    else:
        conn.isolation_level = None


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
def open_ro_connection(db_path: Path) -> sqlite3.Connection:
    """Open a long-lived, WAL-visible, read-only connection (held in lifespan)."""
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    _enable_autocommit(conn)  # fresh read snapshot per SELECT — see module docstring
    return conn


@contextmanager
def open_rw_connection(cfg: Config) -> Iterator[sqlite3.Connection]:
    """On-demand read-write connection for a single write op.

    Preflight (refuse-if-running) → backup → one explicit transaction →
    checkpoint(TRUNCATE) on success. NOT held in the lifespan.
    """
    # Check the override first so the escape hatch never triggers the process probe.
    if not cfg.allow_write_while_running and smartgym_is_running():
        raise RuntimeError(
            "SmartGym is running — close it and retry, or set "
            "SMARTGYM_ALLOW_WRITE_WHILE_RUNNING=true to override (discouraged)."
        )

    backup_db(cfg)

    conn = sqlite3.connect(
        f"file:{cfg.db_path}?mode=rw",  # never rwc — we never create a DB
        uri=True,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    _enable_autocommit(conn)  # explicit transaction control below
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal).lower() != "wal":
            raise RuntimeError(f"expected WAL journal mode, got {journal!r}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
        # Fold WAL frames into the main file and reset the WAL — safe hand-off to
        # the (required-closed) app. Uncontended thanks to the refuse-if-running
        # preflight; may achieve only PASSIVE under the escape hatch. The data is
        # already durable post-COMMIT; a blocked checkpoint just leaves frames in
        # the WAL, so warn rather than fail.
        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            logger.warning(
                "WAL checkpoint did not fully truncate (busy=%s); the WAL still "
                "holds frames. Ensure SmartGym is closed before it reopens the DB.",
                busy,
            )
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            if "no transaction is active" not in str(exc).lower():
                logger.error("ROLLBACK failed after write error: %s", exc)
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Epoch helpers
# --------------------------------------------------------------------------- #
def coredata_to_datetime(ts: float, tz: timezone = UTC) -> datetime:
    return datetime.fromtimestamp(ts + CORE_DATA_EPOCH_OFFSET, tz)


def datetime_to_coredata(dt: datetime) -> float:
    # Naive datetimes are interpreted as local time (matches datetime.timestamp).
    return dt.timestamp() - CORE_DATA_EPOCH_OFFSET


def now_coredata() -> float:
    return datetime.now(UTC).timestamp() - CORE_DATA_EPOCH_OFFSET


# --------------------------------------------------------------------------- #
# Primary-key allocation (Core Data Z_PRIMARYKEY)
# --------------------------------------------------------------------------- #
def next_pk(conn: sqlite3.Connection, entity_name: str) -> tuple[int, int]:
    """Allocate the next Z_PK for an entity, bumping Z_MAX. Returns (z_pk, z_ent).

    Z_ENT is read at runtime — never hardcoded. Must run on a RW connection
    inside the caller's transaction.
    """
    row = conn.execute(
        "SELECT Z_ENT, Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = ?", (entity_name,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown Core Data entity: {entity_name!r}")
    z_ent, z_max = row[0], row[1]
    z_pk = z_max + 1
    conn.execute("UPDATE Z_PRIMARYKEY SET Z_MAX = Z_MAX + 1 WHERE Z_NAME = ?", (entity_name,))
    return z_pk, z_ent


# --------------------------------------------------------------------------- #
# Unique-id minting (ZUNIQUEHASHID + placeholder ZIDENTIFIER)
# --------------------------------------------------------------------------- #
def _mint_unique_int(
    conn: sqlite3.Connection,
    column: str,
    make_candidate: Callable[[], int],
    *,
    max_tries: int = 50,
) -> int:
    """Draw candidates until one is unused in `column` across the three carrier
    tables. Shared engine for both id kinds — the column name is ours, never
    caller input."""
    check_sql = (
        " UNION ALL ".join(f"SELECT 1 FROM {t} WHERE {column} = ?" for t in _HASHID_TABLES)
        + " LIMIT 1"
    )
    for _ in range(max_tries):
        candidate = make_candidate()
        if conn.execute(check_sql, (candidate,) * len(_HASHID_TABLES)).fetchone() is None:
            return candidate
    raise RuntimeError(f"could not mint a unique {column} after {max_tries} tries")


def generate_uniquehashid(conn: sqlite3.Connection, when: datetime) -> int:
    """Mint a ZUNIQUEHASHID: a 14-digit int `YYMMDD` + 8 random digits. The
    suffix is not a reproducible hash — only its shape and uniqueness matter
    (spec 02 §A). Client-generated identity; survives the backend push."""
    prefix = when.strftime("%y%m%d")
    return _mint_unique_int(
        conn, "ZUNIQUEHASHID", lambda: int(prefix + f"{random.randint(0, 99_999_999):08d}")
    )


def generate_identifier(conn: sqlite3.Connection) -> int:
    """Mint a placeholder ZIDENTIFIER. The backend assigns the real one on push
    and the app overwrites ours (spec 03 §A); uniqueness just guarantees a
    pre-push row can never shadow an existing one."""
    return _mint_unique_int(
        conn, "ZIDENTIFIER", lambda: random.randint(10_000_000, 999_999_999)
    )


# --------------------------------------------------------------------------- #
# Process check + backup
# --------------------------------------------------------------------------- #
def smartgym_is_running(match: str = "SmartGym") -> bool:
    """Whether the SmartGym app is running. Fails CLOSED: if the state cannot be
    determined (pgrep missing, spawn error, or a fatal pgrep exit), raise so the
    write path refuses rather than silently proceeding into a live DB.

    The match is EXACT (`pgrep -x`) — a substring match would also hit this MCP
    server's own `smartgym-mcp` process and wedge every write/quit path."""
    try:
        result = subprocess.run(["pgrep", "-ix", match], capture_output=True)
    except OSError as exc:  # FileNotFoundError is an OSError subclass
        raise RuntimeError(
            f"Could not check whether SmartGym is running ({exc}). Refusing to "
            "write — close SmartGym or ensure pgrep is available."
        ) from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:  # pgrep: no matching process
        return False
    raise RuntimeError(
        f"pgrep exited {result.returncode}: "
        f"{result.stderr.decode(errors='replace').strip()}. "
        "Cannot confirm SmartGym is closed; refusing to write."
    )


def backup_db(cfg: Config) -> Path:
    """Copy the DB and its sidecar WAL/SHM to a timestamped dir, verifying the
    main DB was actually captured. A backup without the main file is not a
    backup — refuse the write rather than return a hollow path."""
    if not cfg.db_path.exists():
        raise FileNotFoundError(
            f"Cannot back up: SmartGym DB missing at {cfg.db_path}. Refusing to write."
        )
    dest = cfg.backup_dir / datetime.now().strftime("%Y%m%dT%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    base = str(cfg.db_path)
    for suffix in ("", "-wal", "-shm"):
        src = Path(base + suffix)
        if src.exists():
            shutil.copy2(src, dest / src.name)
    main_copy = dest / cfg.db_path.name
    if not main_copy.exists() or main_copy.stat().st_size == 0:
        raise RuntimeError(f"Backup verification failed at {dest}; refusing to write.")
    return dest
