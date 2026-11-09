"""The SmartGym app's own session, read from its HTTP cache (spec §4 S1).

macOS keeps every request the app sends in its URL cache (`Cache.db`, SQLite), headers
included. The newest SmartGym API request gives the `Authorization`, `accept` and
`accept-language` values and the account id (`authID`). The `user-agent` is not stored there
(CFNetwork adds it), so it is rebuilt the way CFNetwork builds it:
`SmartGym/<app version> CFNetwork/<CFNetwork version> Darwin/<kernel release>`.

The database is copied (with its -wal / -shm) and the copy is read, so the running app is
never touched. Values are never logged or put in messages.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .auth import Credentials

API_HOST = "https://api.smartgymapp.com/"
CACHED_APP_HEADERS = ("accept", "accept-language")  # user-agent is not cached
CFNETWORK_PLIST = Path("/System/Library/Frameworks/CFNetwork.framework/Resources/Info.plist")
_ACCOUNT_IN_URL = (re.compile(r"[?&]authID=(\d+)"), re.compile(r"/user/info/(\d+)"))


class AppSessionError(RuntimeError):
    """No usable session in the app's cache; the message says what to do."""


def user_agent(app_version: str, cfnetwork_version: str, darwin_release: str) -> str:
    return f"SmartGym/{app_version} CFNetwork/{cfnetwork_version} Darwin/{darwin_release}"


def cfnetwork_version(plist: Path = CFNETWORK_PLIST) -> str | None:
    try:
        with plist.open("rb") as f:
            value = plistlib.load(f).get("CFBundleVersion")
    except (OSError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None


def _headers(obj: Any) -> Mapping[str, Any] | None:
    """The header dict inside an archived request: the dict that has `Authorization`."""
    if isinstance(obj, dict):
        if any(str(k).lower() == "authorization" for k in obj):
            return obj
        children: Iterator[Any] = iter(obj.values())
    elif isinstance(obj, list):
        children = iter(obj)
    else:
        return None
    for child in children:
        found = _headers(child)
        if found is not None:
            return found
    return None


def _account(url: str) -> str | None:
    for pattern in _ACCOUNT_IN_URL:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def _requests(cache_db: Path) -> list[tuple[str, bytes]]:
    """(url, archived request) of SmartGym API requests, newest first, from a copy."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / cache_db.name
        shutil.copyfile(cache_db, copy)
        for suffix in ("-wal", "-shm"):
            side = cache_db.with_name(cache_db.name + suffix)
            if side.exists():
                shutil.copyfile(side, copy.with_name(copy.name + suffix))
        conn = sqlite3.connect(copy)
        try:
            rows = conn.execute(
                "SELECT r.request_key, b.request_object"
                " FROM cfurl_cache_response r"
                " JOIN cfurl_cache_blob_data b ON b.entry_ID = r.entry_ID"
                " WHERE r.request_key LIKE ? ORDER BY r.time_stamp DESC, r.entry_ID DESC",
                (API_HOST + "%",),
            ).fetchall()
        except sqlite3.DatabaseError:
            raise AppSessionError(
                f"{cache_db} is not a readable SmartGym cache. Open SmartGym and let it sync."
            ) from None
        finally:
            conn.close()
    return [(str(url), bytes(blob)) for url, blob in rows if blob is not None]


def read_app_session(cache_db: Path, *, user_agent: str) -> Credentials:
    """The session of the app's newest cached API request. PermissionError propagates:
    macOS guards another app's data, and the caller explains how to allow it."""
    if not cache_db.exists():
        raise AppSessionError(
            "SmartGym has no cached session on this Mac yet. Open SmartGym, sign in and let "
            "it sync."
        )
    auth: str | None = None
    app: dict[str, str] = {}
    account: str | None = None
    for url, blob in _requests(cache_db):
        try:
            headers = _headers(plistlib.loads(blob))
        except (plistlib.InvalidFileException, ValueError):
            continue
        if headers is None:
            continue
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        if auth is None:
            app = {h: lower[h] for h in CACHED_APP_HEADERS if lower.get(h)}
            if len(app) != len(CACHED_APP_HEADERS):
                continue
            auth = lower["authorization"]
        if lower["authorization"] == auth:
            account = _account(url)
            if account:
                break
    if auth is None or account is None:
        raise AppSessionError(
            "SmartGym's cache holds no signed-in request yet. Open SmartGym, sign in and let "
            "it sync."
        )
    return Credentials(
        user_id=account,
        authorization=auth,
        app_headers={**app, "user-agent": user_agent},
    )


def darwin_release() -> str:
    return os.uname().release
