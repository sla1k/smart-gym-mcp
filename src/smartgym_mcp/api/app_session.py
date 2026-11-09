"""The SmartGym app's own session, read from its HTTP cache (spec §4 S1).

macOS keeps every request the app sends in its URL cache (`Cache.db`, SQLite), headers
included. The newest SmartGym API request (under `client.BASE_URL`) gives the
`Authorization` header, the other app headers `api.auth` needs, and the account id (the
`authID` parameter). The `user-agent` is not stored there
(CFNetwork adds it); `SmartGym/<app version>` is enough for the server (spec §4 S1).

The database is copied (with its -wal) and the copy is read, so the running app is never
touched. Values are never logged or put in messages.
"""

from __future__ import annotations

import contextlib
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


def user_agent(app_version: str) -> str:
    return f"SmartGym/{app_version}"


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


_SNAPSHOT_TRIES = 3


def _snapshot(cache_db: Path, into: Path) -> sqlite3.Connection:
    """A consistent copy of the live cache: Cache.db plus its -wal (the -shm is rebuilt).
    The running app can checkpoint between the two copies, which tears the copy; that
    shows as a DatabaseError, so the copy is taken again."""
    copy = into / cache_db.name
    wal = cache_db.with_name(cache_db.name + "-wal")
    for attempt in range(_SNAPSHOT_TRIES):
        for leftover in into.iterdir():
            leftover.unlink()
        try:  # not exists(): since 3.13 it hides the PermissionError macOS raises
            shutil.copyfile(cache_db, copy)
        except FileNotFoundError:
            raise NoAppSession from None
        with contextlib.suppress(FileNotFoundError):  # the app may checkpoint it away
            shutil.copyfile(wal, copy.with_name(copy.name + "-wal"))
        conn = sqlite3.connect(copy)
        try:
            conn.execute("SELECT 1 FROM cfurl_cache_response LIMIT 1").fetchall()
        except sqlite3.DatabaseError:
            conn.close()
            if attempt + 1 == _SNAPSHOT_TRIES:
                raise UnreadableCache from None
            continue
        return conn
    raise AssertionError("unreachable")


def _requests(cache_db: Path) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """(url, header dict) of cached SmartGym API requests that carry `Authorization`,
    newest first, read lazily from a snapshot. Rows that don't parse are skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _snapshot(cache_db, Path(tmp))
        try:
            rows = conn.execute(
                "SELECT r.request_key, b.request_object"
                " FROM cfurl_cache_response r"
                " JOIN cfurl_cache_blob_data b ON b.entry_ID = r.entry_ID"
                " WHERE r.request_key LIKE ? ORDER BY r.time_stamp DESC, r.entry_ID DESC",
                (BASE_URL + "%",),
            )
            for url, blob in rows:
                if blob is None:
                    continue
                try:
                    headers = _headers(plistlib.loads(bytes(blob)))
                except (plistlib.InvalidFileException, ValueError, ExpatError):
                    continue
                if headers is not None:
                    yield str(url), headers
        except sqlite3.DatabaseError:
            raise UnreadableCache from None
        finally:
            conn.close()


def read_app_session(cache_db: Path, *, user_agent: str) -> Credentials:
    """The token of the app's newest cached API request, with the other app headers and
    the account id (`authID`) taken from requests sent with that same token. The
    user-agent is the caller's: the cache doesn't keep it. PermissionError propagates:
    macOS guards another app's data, and the caller explains how to allow it."""
    cached = [h for h in REQUIRED_APP_HEADERS if h != "user-agent"]
    auth: str | None = None
    app: dict[str, str] = {}
    account: str | None = None
    for url, headers in _requests(cache_db):
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        auth = auth or lower["authorization"]
        if lower["authorization"] != auth:
            continue
        for h in cached:
            if h not in app and lower.get(h):
                app[h] = lower[h]
        ids = parse_qs(urlsplit(url).query).get("authID")
        if account is None and ids and ids[0].isdecimal():
            account = ids[0]
        if account is not None and len(app) == len(cached):
            return Credentials(
                user_id=account,
                authorization=auth,
                app_headers={**app, "user-agent": user_agent},
            )
    raise NoAppSession
