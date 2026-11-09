"""Configuration: environment overrides with defaults. No filesystem side effects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP_BUNDLE = "/Applications/SmartGym.app"
DEFAULT_BACKUP_DIR = "~/.smartgym-mcp/backups"
DEFAULT_CREDENTIALS = "~/.smartgym-mcp/credentials.json"
# The app's URL cache, where `smartgym-mcp auth` finds its session (spec §4 S1).
DEFAULT_APP_CACHE = (
    "~/Library/Containers/com.smartgymapp.smartgym/Data/Library/Caches/"
    "com.smartgymapp.smartgym/Cache.db"
)
# SmartGym version whose wire format the API client was verified against (spec §4).
VERIFIED_APP_VERSION = "8.0.3"


# CFNetwork's Info.plist: its version is part of the app's user-agent (spec §4 S1).
DEFAULT_CFNETWORK_PLIST = "/System/Library/Frameworks/CFNetwork.framework/Resources/Info.plist"


@dataclass(frozen=True)
class Config:
    app_bundle: Path
    app_cache: Path
    backup_dir: Path
    cfnetwork_plist: Path
    credentials_path: Path


def _resolve(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config() -> Config:
    return Config(
        app_bundle=_resolve(os.environ.get("SMARTGYM_APP_BUNDLE", DEFAULT_APP_BUNDLE)),
        app_cache=_resolve(os.environ.get("SMARTGYM_APP_CACHE", DEFAULT_APP_CACHE)),
        backup_dir=_resolve(os.environ.get("SMARTGYM_BACKUP_DIR", DEFAULT_BACKUP_DIR)),
        cfnetwork_plist=Path(DEFAULT_CFNETWORK_PLIST),
        credentials_path=_resolve(os.environ.get("SMARTGYM_CREDENTIALS", DEFAULT_CREDENTIALS)),
    )
