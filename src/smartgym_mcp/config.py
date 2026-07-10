"""Configuration: resolve environment overrides and validate the DB exists.

Pure resolution (`load_config`) is separated from the filesystem check
(`validate_db_exists`) so the former is usable in tests with no DB present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = (
    "~/Library/Containers/com.smartgymapp.smartgym/Data/Documents/GymModel.sqlite"
)
DEFAULT_APP_BUNDLE = "/Applications/SmartGym.app"
DEFAULT_BACKUP_DIR = "~/.smartgym-mcp/backups"

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    db_path: Path
    app_bundle: Path
    backup_dir: Path
    allow_write_while_running: bool


def _resolve(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config() -> Config:
    """Read env overrides with spec defaults. No filesystem side effects."""
    return Config(
        db_path=_resolve(os.environ.get("SMARTGYM_DB_PATH", DEFAULT_DB_PATH)),
        app_bundle=_resolve(os.environ.get("SMARTGYM_APP_BUNDLE", DEFAULT_APP_BUNDLE)),
        backup_dir=_resolve(os.environ.get("SMARTGYM_BACKUP_DIR", DEFAULT_BACKUP_DIR)),
        allow_write_while_running=os.environ.get("SMARTGYM_ALLOW_WRITE_WHILE_RUNNING", "")
        .strip()
        .lower()
        in _TRUTHY,
    )


def validate_db_exists(cfg: Config) -> None:
    """Loud, actionable startup check that the SmartGym DB is reachable."""
    if not cfg.db_path.exists():
        raise FileNotFoundError(
            f"SmartGym DB not found at {cfg.db_path}. "
            "Is SmartGym installed? Override the location with SMARTGYM_DB_PATH."
        )
