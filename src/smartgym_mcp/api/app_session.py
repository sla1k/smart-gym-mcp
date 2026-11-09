"""The SmartGym app's own session, read from its HTTP cache (spec §4 S1).

macOS keeps every request the app sends in its URL cache (`Cache.db`, SQLite), headers
included. The newest SmartGym API request (under `client.BASE_URL`) gives the
`Authorization` header, the other app headers `api.auth` needs, and the account id (the
`authID` parameter). The `user-agent` is not stored there
(CFNetwork adds it), so it is rebuilt the way CFNetwork builds it:
`SmartGym/<app version> CFNetwork/<CFNetwork version> Darwin/<kernel release>`.

The database is copied (with its -wal / -shm) and the copy is read, so the running app is
never touched. Values are never logged or put in messages.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.parsers.expat import ExpatError

from .auth import REQUIRED_APP_HEADERS, Credentials
from .client import BASE_URL


class NoAppSession(Exception):
    """The app's cache holds no signed-in SmartGym request (yet)."""


class UnreadableCache(Exception):
    """The cache file is not a URL cache this module can read."""


def user_agent(app_version: str, cfnetwork_version: str, darwin_release: str) -> str:
    return f"SmartGym/{app_version} CFNetwork/{cfnetwork_version} Darwin/{darwin_release}"


def cfnetwork_version(plist: Path) -> str | None:
    """CFNetwork's CFBundleVersion, the middle part of the app's user-agent."""
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


def _requests(cache_db: Path) -> list[tuple[str, bytes]]:
    """(url, archived request) of SmartGym API requests, newest first, from a copy."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / cache_db.name
        try:  # not exists(): since 3.13 it hides the PermissionError macOS raises
            shutil.copyfile(cache_db, copy)
        except FileNotFoundError:
            raise NoAppSession from None
        for suffix in ("-wal", "-shm"):
            # the running app may checkpoint them away at any moment
            with contextlib.suppress(FileNotFoundError):
                shutil.copyfile(
                    cache_db.with_name(cache_db.name + suffix),
                    copy.with_name(copy.name + suffix),
                )
        conn = sqlite3.connect(copy)
        try:
            rows = conn.execute(
                "SELECT r.request_key, b.request_object"
                " FROM cfurl_cache_response r"
                " JOIN cfurl_cache_blob_data b ON b.entry_ID = r.entry_ID"
                " WHERE r.request_key LIKE ? ORDER BY r.time_stamp DESC, r.entry_ID DESC",
                (BASE_URL + "%",),
            ).fetchall()
        except sqlite3.DatabaseError:
            raise UnreadableCache from None
        finally:
            conn.close()
    return [(str(url), bytes(blob)) for url, blob in rows if blob is not None]


def read_app_session(cache_db: Path, *, user_agent: str) -> Credentials:
    """The session of the app's newest cached API request, with the account id (`authID`)
    the app sent alongside that token. The user-agent is the caller's: the cache doesn't
    keep it. PermissionError propagates: macOS guards another app's data, and the caller
    explains how to allow it."""
    cached = [h for h in REQUIRED_APP_HEADERS if h != "user-agent"]
    auth: str | None = None
    app: dict[str, str] = {}
    account: str | None = None
    for url, blob in _requests(cache_db):
        try:
            headers = _headers(plistlib.loads(blob))
        except (plistlib.InvalidFileException, ValueError, ExpatError):
            continue
        if headers is None:
            continue
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        if auth is None:
            app = {h: lower[h] for h in cached if lower.get(h)}
            if len(app) != len(cached):
                continue
            auth = lower["authorization"]
        if lower["authorization"] != auth:
            continue
        ids = parse_qs(urlsplit(url).query).get("authID")
        if ids and ids[0].isdecimal():
            account = ids[0]
            break
    if auth is None or account is None:
        raise NoAppSession
    return Credentials(
        user_id=account,
        authorization=auth,
        app_headers={**app, "user-agent": user_agent},
    )


def darwin_release() -> str:
    return os.uname().release
