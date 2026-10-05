"""scripts/capture_credentials.py with a stub mitmproxy — no proxy, no network."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "capture_credentials.py"
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}


class _Headers(dict[str, str]):
    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)


class _Req:
    def __init__(self, headers: dict[str, str], query: dict[str, str]) -> None:
        self.pretty_host = "api.smartgymapp.com"
        self.headers = _Headers({k.lower(): v for k, v in headers.items()})
        self.query = query
        self.multipart_form: dict[bytes, bytes] = {}


class _Flow:
    def __init__(self, req: _Req) -> None:
        self.request = req


@pytest.fixture
def addon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    mitm = types.ModuleType("mitmproxy")
    mitm.http = types.SimpleNamespace(HTTPFlow=object, Request=object)  # type: ignore[attr-defined]
    mitm.ctx = types.SimpleNamespace(log=types.SimpleNamespace(info=lambda _m: None))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mitmproxy", mitm)
    monkeypatch.setenv("SMARTGYM_CREDENTIALS", str(tmp_path / "credentials.json"))
    spec = importlib.util.spec_from_file_location("capture_credentials", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_saves_complete_credentials_with_mode_600(addon: Any, tmp_path: Path) -> None:
    addon.request(_Flow(_Req({"Authorization": "Bearer t", **APP}, {"authID": "42"})))
    path = tmp_path / "credentials.json"
    assert json.loads(path.read_text()) == {
        "authorization": "Bearer t",
        "authID": "42",
        "app_headers": APP,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ignores_requests_without_authorization(addon: Any, tmp_path: Path) -> None:
    addon.request(_Flow(_Req(dict(APP), {"authID": "42"})))
    assert not (tmp_path / "credentials.json").exists()
