"""api/app_session.py: the app's URL cache → Credentials. A synthetic Cache.db, no app."""

from __future__ import annotations

import plistlib
import sqlite3
from pathlib import Path

import pytest

from fakes import SECRET
from smartgym_mcp.api.app_session import (
    NoAppSession,
    UnreadableCache,
    cfnetwork_version,
    read_app_session,
    user_agent,
)

API = "https://api.smartgymapp.com/v1.1/"
UA = "SmartGym/8.0.3 CFNetwork/3896.100.1.1.1 Darwin/27.0.0"
HEADERS = {
    "phrase": "p",
    "Accept": "*/*",
    "Authorization": SECRET,
    "Accept-Language": "en-GB",
    "Accept-Encoding": "gzip",
}


def _request(url: str, headers: dict[str, str] | None) -> bytes:
    """The shape CFNetwork archives a request in (spec §4 S1)."""
    array: list[object] = [True, {"_CFURLStringType": 15, "_CFURLString": url}, 60.0, 0]
    if headers is not None:
        array += ["GET", headers]
    return plistlib.dumps({"Version": 9, "Array": array}, fmt=plistlib.FMT_BINARY)


def _cache(tmp_path: Path, *rows: tuple[str, str, bytes | None]) -> Path:
    db = tmp_path / "Cache.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cfurl_cache_response (entry_ID INTEGER PRIMARY KEY, request_key TEXT,"
        " time_stamp TEXT)"
    )
    conn.execute(
        "CREATE TABLE cfurl_cache_blob_data (entry_ID INTEGER, response_object BLOB,"
        " request_object BLOB)"
    )
    for i, (ts, url, blob) in enumerate(rows, 1):
        conn.execute("INSERT INTO cfurl_cache_response VALUES (?, ?, ?)", (i, url, ts))
        conn.execute("INSERT INTO cfurl_cache_blob_data VALUES (?, NULL, ?)", (i, blob))
    conn.commit()
    conn.close()
    return db


def test_newest_signed_in_request_gives_the_session(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        ("2026-11-01 10:00:00", f"{API}history/all/42/?authID=42", _request("x", HEADERS)),
        (
            "2026-11-07 16:00:00",
            f"{API}user/info/42?appVersion=8.0.3&authID=42",
            _request("x", {**HEADERS, "Authorization": "Bearer newer"}),
        ),
        ("2026-11-08 09:00:00", "http://smartgymprod.smartgymapp.com.s3/x.jpg", b"img"),
    )
    creds = read_app_session(db, user_agent=UA)
    assert creds.authorization == "Bearer newer"
    assert creds.user_id == "42"
    assert dict(creds.app_headers) == {
        "accept": "*/*",
        "accept-language": "en-GB",
        "user-agent": UA,
    }
    assert "phrase" not in {k.lower() for k in creds.headers}


def test_account_id_comes_from_an_older_request_with_the_same_token(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        (
            "2026-11-01 10:00:00",
            f"{API}goal/all/?appVersion=8.0.3&authID=7",
            _request("x", HEADERS),
        ),
        ("2026-11-02 10:00:00", f"{API}routine/update/", _request("x", HEADERS)),
    )
    assert read_app_session(db, user_agent=UA).user_id == "7"


def test_requests_without_headers_are_skipped(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        (
            "2026-11-01 10:00:00",
            f"{API}user/info/42?appVersion=8.0.3&authID=42",
            _request("x", HEADERS),
        ),
        ("2026-11-02 10:00:00", f"{API}options.json", _request("x", None)),
        ("2026-11-03 10:00:00", f"{API}quote/todaysQuote/", b"not a plist"),
    )
    assert read_app_session(db, user_agent=UA).authorization == SECRET


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("2026-11-01 10:00:00", f"{API}options.json", _request("x", None))],
        [("2026-11-01 10:00:00", f"{API}routine/update/", _request("x", HEADERS))],
    ],
)
def test_no_signed_in_request_is_no_session(
    tmp_path: Path, rows: list[tuple[str, str, bytes]]
) -> None:
    db = _cache(tmp_path, *rows)
    with pytest.raises(NoAppSession):
        read_app_session(db, user_agent=UA)


def test_missing_or_foreign_cache_is_a_session_error(tmp_path: Path) -> None:
    with pytest.raises(NoAppSession):
        read_app_session(tmp_path / "Cache.db", user_agent=UA)
    (tmp_path / "Cache.db").write_bytes(b"not sqlite")
    with pytest.raises(UnreadableCache):
        read_app_session(tmp_path / "Cache.db", user_agent=UA)


def test_the_app_cache_is_never_written(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        (
            "2026-11-01 10:00:00",
            f"{API}user/info/42?appVersion=8.0.3&authID=42",
            _request("x", HEADERS),
        ),
    )
    before = sorted(p.name for p in tmp_path.iterdir()), db.read_bytes()
    read_app_session(db, user_agent=UA)
    assert (sorted(p.name for p in tmp_path.iterdir()), db.read_bytes()) == before


def test_user_agent_is_built_like_cfnetworks(tmp_path: Path) -> None:
    plist = tmp_path / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleVersion": "3896.100.1.1.1"}))
    assert user_agent("8.0.3", cfnetwork_version(plist) or "", "27.0.0") == UA
    assert cfnetwork_version(tmp_path / "missing.plist") is None


def test_the_account_is_the_authid_parameter_not_an_id_in_the_path(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        ("2026-11-01 10:00:00", f"{API}goal/all/?authID=42", _request("x", HEADERS)),
        ("2026-11-02 10:00:00", f"{API}user/info/99", _request("x", HEADERS)),
    )
    assert read_app_session(db, user_agent=UA).user_id == "42"


def test_a_malformed_xml_plist_row_is_skipped(tmp_path: Path) -> None:
    db = _cache(
        tmp_path,
        (
            "2026-11-01 10:00:00",
            f"{API}user/info/42?appVersion=8.0.3&authID=42",
            _request("x", HEADERS),
        ),
        (
            "2026-11-02 10:00:00",
            f"{API}history/all/42/",
            b"<?xml version='1.0'?><plist><dict>",
        ),
    )
    assert read_app_session(db, user_agent=UA).user_id == "42"


def test_the_newest_token_wins_even_without_the_other_headers(tmp_path: Path) -> None:
    fresh = {"Authorization": "Bearer fresh"}
    db = _cache(
        tmp_path,
        ("2026-11-01 10:00:00", f"{API}goal/all/?authID=42", _request("x", HEADERS)),
        (
            "2026-11-02 10:00:00",
            f"{API}history/all/42/?authID=42",
            _request("x", {**HEADERS, **fresh}),
        ),
        ("2026-11-03 10:00:00", f"{API}routine/update/", _request("x", fresh)),
    )
    creds = read_app_session(db, user_agent=UA)
    assert (creds.authorization, creds.user_id) == ("Bearer fresh", "42")
    assert creds.app_headers["accept-language"] == "en-GB"
