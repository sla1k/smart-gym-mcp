"""api/auth.py: credentials file → headers; secrets never leak into messages."""

from __future__ import annotations

import json
import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from smartgym_mcp.api.auth import CredentialsError, FileCredentials
from smartgym_mcp.catalog import installed_app_version
from smartgym_mcp.config import load_config

SECRET = "Bearer s3cr3t-token"
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}


def _write(path: Path, data: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def _good(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "credentials.json",
        {"authorization": SECRET, "authID": "42", "phrase": "p", "app_headers": APP},
    )


def test_headers_carry_authorization_and_app_headers_without_phrase(tmp_path: Path) -> None:
    creds = FileCredentials(_good(tmp_path))
    headers = creds.auth_headers()
    assert headers["Authorization"] == SECRET
    assert {k: headers[k] for k in APP} == APP
    assert "phrase" not in {k.lower() for k in headers}
    assert creds.user_id == "42"


def test_missing_file_names_path_and_capture_script(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="capture_credentials.py") as exc:
        FileCredentials(tmp_path / "nope.json")
    assert "nope.json" in str(exc.value)


def test_group_or_world_readable_file_is_refused(tmp_path: Path) -> None:
    path = _good(tmp_path)
    path.chmod(0o644)
    with pytest.raises(CredentialsError, match="chmod 600") as exc:
        FileCredentials(path)
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("drop", ["authorization", "authID", "user-agent"])
def test_incomplete_file_names_the_missing_key(tmp_path: Path, drop: str) -> None:
    data: dict[str, object] = {
        "authorization": SECRET,
        "authID": "42",
        "app_headers": dict(APP),
    }
    if drop == "user-agent":
        del data["app_headers"]["user-agent"]  # type: ignore[attr-defined]
    else:
        del data[drop]
    with pytest.raises(CredentialsError, match=drop) as exc:
        FileCredentials(_write(tmp_path / "c.json", data))
    assert SECRET not in str(exc.value)


def test_invalid_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(CredentialsError, match="not valid JSON"):
        FileCredentials(path)


def test_repr_hides_secret(tmp_path: Path) -> None:
    assert SECRET not in repr(FileCredentials(_good(tmp_path)))


def test_installed_app_version_reads_info_plist(tmp_path: Path) -> None:
    contents = tmp_path / "SmartGym.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "8.0.3"}, f)
    cfg = replace(load_config(), app_bundle=tmp_path / "SmartGym.app")
    assert installed_app_version(cfg) == "8.0.3"
    assert installed_app_version(replace(cfg, app_bundle=tmp_path / "Missing.app")) is None


def test_credentials_path_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SMARTGYM_CREDENTIALS", str(tmp_path / "x.json"))
    assert load_config().credentials_path == (tmp_path / "x.json").resolve()
