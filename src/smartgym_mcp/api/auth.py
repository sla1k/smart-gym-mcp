"""Credentials for the SmartGym backend (API-client spec §4 S1).

The server authenticates with the static `Authorization` header; the CDN in
front of it rejects requests without the app's own `user-agent` / `accept` /
`accept-language`. `phrase` is not checked and is never sent. Values come from
a 0600 JSON file that `smartgym-mcp auth` writes from the app's own session
(`api.app_session`) and are never logged, printed, or put in exception messages.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_APP_HEADERS = ("user-agent", "accept", "accept-language")
_HINT = "Run `smartgym-mcp auth` in a terminal (README: 'Connecting to your account')."


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


def save_credentials(path: Path, creds: Credentials) -> None:
    """Write the credentials file atomically, mode 0600, in the shape `load_credentials`
    reads."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # new file, 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "authorization": creds.authorization,
                    "authID": creds.user_id,
                    "app_headers": dict(creds.app_headers),
                },
                f,
            )
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
