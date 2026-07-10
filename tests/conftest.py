"""Shared fixtures: locate the live DB (read-only) and make temp copies."""

from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartgym_mcp.config import Config, load_config  # noqa: E402


@pytest.fixture(scope="session")
def live_cfg() -> Config:
    cfg = load_config()
    if not cfg.db_path.exists():
        pytest.skip(f"live SmartGym DB not present at {cfg.db_path}")
    return cfg


@pytest.fixture
def temp_db_cfg(live_cfg: Config, tmp_path: Path) -> Config:
    """A Config whose db_path is a temp copy of the live DB (+ WAL/SHM).

    Safe to mutate — never points at the real DB.
    """
    base = str(live_cfg.db_path)
    dest = tmp_path / "GymModel.sqlite"
    for suffix in ("", "-wal", "-shm"):
        src = Path(base + suffix)
        if src.exists():
            shutil.copy2(src, str(dest) + suffix)
    return replace(
        live_cfg,
        db_path=dest.resolve(),
        backup_dir=(tmp_path / "backups").resolve(),
        allow_write_while_running=True,  # SmartGym may be running during tests
    )
