"""api/client.py on httpx.MockTransport — never touches the network."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
import pytest

from smartgym_mcp.api.client import ApiClient, ApiError, AuthError, WriteOutcomeUnknown

SECRET = "Bearer s3cr3t-token"
PHRASE = "ph-r4se-value"


class FakeCreds:
    def auth_headers(self) -> Mapping[str, str]:
        return {"Authorization": SECRET, "phrase": PHRASE}

    @property
    def user_id(self) -> str:
        return "1"


def _client(handler: Callable[[httpx.Request], httpx.Response], **kw: object) -> ApiClient:
    return ApiClient(
        FakeCreds(),
        app_version="8.0.3",
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 10, 5, 11, 30, 0, tzinfo=UTC),
        sleep=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


def test_get_adds_common_params_and_auth_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"code": "SUCCESS", "routines": []})

    body = _client(handler).get("routine/single/42/")
    assert body["code"] == "SUCCESS"
    req = seen[0]
    assert req.url.path == "/v1.1/routine/single/42/"
    assert req.url.params["appVersion"] == "8.0.3"
    assert req.url.params["authID"] == "1"
    assert req.url.params["requestDate"] == "2026-10-05T11:30:00"
    assert req.headers["Authorization"] == SECRET and req.headers["phrase"] == PHRASE


def test_post_is_utf8_multipart_with_common_fields() -> None:
    seen: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.read())
        assert req.headers["content-type"].startswith("multipart/form-data")
        return httpx.Response(200, json={"code": "SUCCESS"})

    _client(handler).post("routine/archive/", {"routinesIDs": "7", "note": "FB-A — W1"})
    body = seen[0].decode("utf-8")
    for field in (
        'name="routinesIDs"',
        'name="authID"',
        'name="appVersion"',
        'name="requestDate"',
    ):
        assert field in body
    assert "FB-A — W1" in body


def test_http_200_with_error_code_raises() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"code": "ROUTINE_NOT_FOUND"}))
    with pytest.raises(ApiError) as exc:
        client.post("routine/archive/", {"routinesIDs": "7"})
    assert exc.value.code == "ROUTINE_NOT_FOUND"


def test_require_success_false_returns_body_without_code() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"options": []}))
    assert client.get("options.json", require_success=False) == {"options": []}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_auth_error_without_secrets(status: int) -> None:
    client = _client(lambda _r: httpx.Response(status, text=f"denied {SECRET} {PHRASE}"))
    with pytest.raises(AuthError) as exc:
        client.get("user/info/1")
    assert SECRET not in str(exc.value) and PHRASE not in str(exc.value)


def test_reads_retry_on_5xx_then_succeed() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": "SUCCESS"})

    assert _client(handler).get("routine/all/1/")["code"] == "SUCCESS"
    assert calls["n"] == 3


def test_reads_give_up_after_max_attempts() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("offline")

    with pytest.raises(ApiError, match="network"):
        _client(handler, max_read_attempts=2).get("routine/all/1/")
    assert calls["n"] == 2


def test_writes_never_retry_and_report_unknown_outcome() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(WriteOutcomeUnknown):
        _client(handler).post("routine/update/", {"routineID": "7"})
    assert calls["n"] == 1


def test_non_json_body_raises_api_error() -> None:
    client = _client(lambda _r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ApiError, match="non-JSON"):
        client.get("routine/all/1/")


def test_client_repr_hides_credentials() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"code": "SUCCESS"}))
    assert SECRET not in repr(client) and PHRASE not in repr(client)


@pytest.mark.parametrize("status", [500, 502, 504])
def test_write_5xx_reports_unknown_outcome(status: int) -> None:
    client = _client(lambda _r: httpx.Response(status))
    with pytest.raises(WriteOutcomeUnknown, match="re-fetch"):
        client.post("routine/add/", {"routines": "[]"})


def test_write_decoding_error_reports_unknown_outcome() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("bad gzip")

    with pytest.raises(WriteOutcomeUnknown):
        _client(handler).post("routine/add/", {"routines": "[]"})


def test_read_decoding_error_raises_api_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("bad gzip")

    with pytest.raises(ApiError):
        _client(handler, max_read_attempts=1).get("routine/all/1/")


def test_http_error_passes_server_code_through() -> None:
    client = _client(lambda _r: httpx.Response(400, json={"code": "TOO_MANY_ROUTINES"}))
    with pytest.raises(ApiError, match="TOO_MANY_ROUTINES") as exc:
        client.post("routine/add/", {"routines": "[]"})
    assert exc.value.code == "TOO_MANY_ROUTINES"


def test_auth_error_keeps_code_without_secrets() -> None:
    client = _client(lambda _r: httpx.Response(401, json={"code": "INVALID_TOKEN"}))
    with pytest.raises(AuthError) as exc:
        client.get("user/info/1")
    assert exc.value.code == "INVALID_TOKEN"
    assert SECRET not in str(exc.value)


def test_missing_code_stays_none() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"routines": []}))
    with pytest.raises(ApiError) as exc:
        client.get("routine/all/1/")
    assert exc.value.code is None


def test_user_id_is_exposed() -> None:
    assert _client(lambda _r: httpx.Response(200, json={})).user_id == "1"
