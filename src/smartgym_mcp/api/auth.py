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
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_APP_HEADERS = ("user-agent", "accept", "accept-language")
_HINT = "Create it with scripts/capture_credentials.py (README: 'Connecting to your account')."


class CredentialsError(RuntimeError):
    """Credentials file missing, unreadable, too permissive, or incomplete."""


@dataclass(frozen=True)
class Credentials:
    """One captured app session; the token and the account id stay out of its repr."""

    user_id: str = field(repr=False)
    authorization: str = field(repr=False)
    app_headers: Mapping[str, str]

    @property
    def headers(self) -> dict[str, str]:
        """The headers every request carries."""
        return {**self.app_headers, "Authorization": self.authorization}


def load_credentials(path: Path) -> Credentials:
    """Read the credentials file; CredentialsError says what to fix, never a secret."""
    if not path.exists():
        raise CredentialsError(f"SmartGym credentials not found at {path}. {_HINT}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise CredentialsError(
            f"{path} is readable by other users (mode {mode:o}); run: chmod 600 {path}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CredentialsError(f"{path} is not valid JSON. {_HINT}") from None
    if not isinstance(raw, dict):
        raise CredentialsError(f"{path} is not valid JSON. {_HINT}")
    app_raw = raw.get("app_headers")
    app: dict[str, object] = app_raw if isinstance(app_raw, dict) else {}
    missing = [k for k in ("authorization", "authID") if not raw.get(k)]
    missing += [f"app_headers.{h}" for h in REQUIRED_APP_HEADERS if not app.get(h)]
    if missing:
        raise CredentialsError(f"{path} lacks {', '.join(missing)}. {_HINT}")
    return Credentials(
        user_id=str(raw["authID"]),
        authorization=str(raw["authorization"]),
        app_headers={h: str(app[h]) for h in REQUIRED_APP_HEADERS},
    )
