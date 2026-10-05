"""Configuration: environment overrides with defaults. No filesystem side effects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP_BUNDLE = "/Applications/SmartGym.app"
DEFAULT_BACKUP_DIR = "~/.smartgym-mcp/backups"
DEFAULT_CREDENTIALS = "~/.smartgym-mcp/credentials.json"
# SmartGym version whose wire format the API client was verified against (spec §4).
VERIFIED_APP_VERSION = "8.0.3"


@dataclass(frozen=True)
class Config:
    app_bundle: Path
    backup_dir: Path
    credentials_path: Path


def _resolve(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config() -> Config:
    return Config(
        app_bundle=_resolve(os.environ.get("SMARTGYM_APP_BUNDLE", DEFAULT_APP_BUNDLE)),
        backup_dir=_resolve(os.environ.get("SMARTGYM_BACKUP_DIR", DEFAULT_BACKUP_DIR)),
        credentials_path=_resolve(os.environ.get("SMARTGYM_CREDENTIALS", DEFAULT_CREDENTIALS)),
    )
