"""Credentials for the SmartGym backend (API-client spec §4 S1).

The server authenticates with the static `Authorization` header; the CDN in
front of it rejects requests without the app's own `user-agent` / `accept` /
`accept-language`. `phrase` is not checked and is never sent. Values come from
a 0600 JSON file the user creates with scripts/capture_credentials.py and are
never logged, printed, or put in exception messages.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path

REQUIRED_APP_HEADERS = ("user-agent", "accept", "accept-language")
_HINT = "Create it with scripts/capture_credentials.py (README: 'Connecting to your account')."


class CredentialsError(RuntimeError):
    """Credentials file missing, unreadable, too permissive, or incomplete."""


class FileCredentials:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._headers, self._user_id = self._load()

    def __repr__(self) -> str:
        return f"FileCredentials(path={str(self._path)!r})"

    def _load(self) -> tuple[dict[str, str], str]:
        if not self._path.exists():
            raise CredentialsError(f"SmartGym credentials not found at {self._path}. {_HINT}")
        mode = stat.S_IMODE(self._path.stat().st_mode)
        if mode & 0o077:
            raise CredentialsError(
                f"{self._path} is readable by other users (mode {mode:o}); "
                f"run: chmod 600 {self._path}"
            )
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CredentialsError(f"{self._path} is not valid JSON. {_HINT}") from None
        if not isinstance(raw, dict):
            raise CredentialsError(f"{self._path} is not valid JSON. {_HINT}")
        app_raw = raw.get("app_headers")
        app: dict[str, object] = app_raw if isinstance(app_raw, dict) else {}
        missing = [k for k in ("authorization", "authID") if not raw.get(k)]
        missing += [f"app_headers.{h}" for h in REQUIRED_APP_HEADERS if not app.get(h)]
        if missing:
            raise CredentialsError(f"{self._path} lacks {', '.join(missing)}. {_HINT}")
        headers = {h: str(app[h]) for h in REQUIRED_APP_HEADERS}
        headers["Authorization"] = str(raw["authorization"])
        return headers, str(raw["authID"])

    def auth_headers(self) -> Mapping[str, str]:
        return dict(self._headers)

    @property
    def user_id(self) -> str:
        return self._user_id


__all__ = ["REQUIRED_APP_HEADERS", "CredentialsError", "FileCredentials"]
