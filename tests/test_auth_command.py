"""auth_command.py: `smartgym-mcp auth` steps with a fake app, cache and server."""

from __future__ import annotations

import json
import plistlib
import stat
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fakes import SECRET, FakeClient
from smartgym_mcp import auth_command
from smartgym_mcp.api.auth import Credentials, load_credentials
from smartgym_mcp.api.client import ApiCalls, ApiError, AuthError
from smartgym_mcp.config import Config, load_config
from test_app_session import API, HEADERS, UA, _cache, _request

OK: dict[str, Any] = {"code": "SUCCESS", "routines": []}


class Script:
    """Records what the command says and answers its prompts in order."""

    def __init__(self, *answers: str | type[BaseException]) -> None:
        self.lines: list[str] = []
        self._answers: Iterator[str | type[BaseException]] = iter(answers)

    def say(self, line: str) -> None:
        self.lines.append(line)

    def ask(self, prompt: str) -> str:
        self.lines.append(prompt)
        answer = next(self._answers, EOFError)
        if isinstance(answer, type):
            raise answer
        return answer

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _cfg(tmp_path: Path, *, app: bool = True) -> Config:
    bundle = tmp_path / "SmartGym.app"
    if app:
        (bundle / "Contents").mkdir(parents=True)
        (bundle / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleShortVersionString": "8.0.3"})
        )
    return replace(
        load_config(),
        app_bundle=bundle,
        app_cache=tmp_path / "cache" / "Cache.db",
        credentials_path=tmp_path / "home" / "credentials.json",
    )


def _signed_in(cfg: Config) -> None:
    cfg.app_cache.parent.mkdir(exist_ok=True)
    _cache(
        cfg.app_cache.parent,
        ("2026-11-07 16:00:00", f"{API}user/info/42", _request("x", HEADERS)),
    )


def _run(
    cfg: Config, io: Script, *bodies: Mapping[str, Any] | Exception
) -> tuple[int, list[tuple[Credentials, str]]]:
    seen: list[tuple[Credentials, str]] = []
    queue = list(bodies) or [OK]

    def connect(creds: Credentials, version: str) -> ApiCalls:
        seen.append((creds, version))
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeClient({"history/all/42/": body}, user_id=creds.user_id)

    code = auth_command.run(
        cfg,
        auth_command.Io(say=io.say, ask=io.ask),
        connect=connect,
        cfnetwork=lambda: "3896.100.1.1.1",
        darwin=lambda: "27.0.0",
    )
    return code, seen


def test_saves_a_checked_session_as_a_private_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _signed_in(cfg)
    io = Script()
    code, seen = _run(cfg, io)
    assert code == 0
    assert [(c.user_id, v) for c, v in seen] == [("42", "8.0.3")]
    saved = load_credentials(cfg.credentials_path)
    assert (saved.user_id, saved.authorization) == ("42", SECRET)
    assert saved.app_headers["user-agent"] == UA
    assert stat.S_IMODE(cfg.credentials_path.stat().st_mode) == 0o600
    assert "Step 3/3" in io.text and SECRET not in io.text and "42" not in io.text


def test_waits_for_the_app_to_sign_in_then_continues(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    io = Script()

    def ask(prompt: str) -> str:
        io.lines.append(prompt)
        _signed_in(cfg)
        return ""

    io.ask = ask  # type: ignore[method-assign]
    code, _ = _run(cfg, io)
    assert code == 0
    assert "Open SmartGym" in io.text and "Press Enter" in io.text


def test_an_expired_session_is_retried_after_the_app_syncs(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _signed_in(cfg)
    io = Script("")
    code, seen = _run(cfg, io, AuthError("expired"), OK)
    assert (code, len(seen)) == (0, 2)
    assert "expired" in io.text


def test_stopping_saves_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    for answer in (EOFError, KeyboardInterrupt):
        code, _ = _run(cfg, Script(answer))
        assert code == 1
    assert not cfg.credentials_path.exists()


def test_privacy_block_explains_how_to_allow_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)

    def blocked(*_a: object, **_k: object) -> Credentials:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(auth_command, "read_app_session", blocked)
    io = Script()
    assert _run(cfg, io)[0] == 1
    assert "Privacy & Security" in io.text


def test_server_error_stops_without_saving(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _signed_in(cfg)
    io = Script()
    assert _run(cfg, io, ApiError("SmartGym unreachable"))[0] == 1
    assert "unreachable" in io.text and not cfg.credentials_path.exists()


def test_missing_app_or_cfnetwork_stops_early(tmp_path: Path) -> None:
    io = Script()
    assert _run(_cfg(tmp_path, app=False), io)[0] == 1
    assert "isn't installed" in io.text
    io = Script()
    code = auth_command.run(
        _cfg(tmp_path), auth_command.Io(say=io.say, ask=io.ask), cfnetwork=lambda: None
    )
    assert code == 1 and "CFNetwork" in io.text


def test_saved_file_matches_what_the_server_reads(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _signed_in(cfg)
    _run(cfg, Script())
    raw = json.loads(cfg.credentials_path.read_text(encoding="utf-8"))
    assert set(raw) == {"authorization", "authID", "app_headers"}
