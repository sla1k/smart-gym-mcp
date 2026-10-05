"""HTTP core for the SmartGym backend (API-client spec §5, §7).

Adds the common fields every app request carries, maps failures to typed
errors, retries reads only, and never lets credential values reach a message,
log line, or repr. Writes are never retried: after a network failure the
outcome is unknown and the caller must re-fetch before trying again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger("smartgym_mcp.api")

# httpx/httpcore INFO/DEBUG request lines carry the full URL, including the account id.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

BASE_URL = "https://api.smartgymapp.com/v1.1/"
_TIMEOUT_S = 30.0


class CredentialProvider(Protocol):
    def auth_headers(self) -> Mapping[str, str]: ...

    @property
    def user_id(self) -> str: ...


class ApiCalls(Protocol):
    """What the store and the service need from a client (ApiClient or a test fake)."""

    @property
    def user_id(self) -> str: ...

    def get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        require_success: bool = True,
    ) -> dict[str, Any]: ...

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]: ...


class ApiError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthError(ApiError):
    pass


class WriteOutcomeUnknown(ApiError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiClient:
    def __init__(
        self,
        credentials: CredentialProvider,
        *,
        app_version: str,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        max_read_attempts: int = 3,
    ) -> None:
        self._credentials = credentials
        self._app_version = app_version
        self._clock = clock
        self._sleep = sleep
        self._max_read_attempts = max_read_attempts
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=_TIMEOUT_S)

    def __repr__(self) -> str:
        return f"ApiClient(base_url={str(self._http.base_url)!r})"

    def close(self) -> None:
        self._http.close()

    @property
    def user_id(self) -> str:
        return self._credentials.user_id

    def _safe_path(self, path: str) -> str:
        """Path for messages and logs: the account id never appears in them."""
        uid = self._credentials.user_id
        return "/".join("<user>" if seg == uid else seg for seg in path.split("/"))

    def _common(self) -> dict[str, str]:
        return {
            "appVersion": self._app_version,
            "authID": self._credentials.user_id,
            "requestDate": self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        require_success: bool = True,
    ) -> dict[str, Any]:
        shown = self._safe_path(path)
        query = {**(params or {}), **self._common()}
        last: Exception | None = None
        last_status: int | None = None
        for attempt in range(self._max_read_attempts):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            try:
                resp = self._http.get(
                    path, params=query, headers=dict(self._credentials.auth_headers())
                )
            except httpx.RequestError as exc:
                last, last_status = exc, None
                logger.info("GET %s network error (attempt %d)", shown, attempt + 1)
                continue
            if resp.status_code >= 500:
                last, last_status = None, resp.status_code
                logger.info(
                    "GET %s HTTP %d (attempt %d)", shown, resp.status_code, attempt + 1
                )
                continue
            return self._decode(shown, resp, require_success)
        if last_status is not None:
            raise ApiError(
                f"SmartGym server error HTTP {last_status} on {shown} after "
                f"{self._max_read_attempts} attempts."
            )
        raise ApiError(
            f"SmartGym unreachable (network error) for {shown} after "
            f"{self._max_read_attempts} attempts: {type(last).__name__}."
        )

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        shown = self._safe_path(path)
        fields = {**form, **self._common()}
        files = {k: (None, v.encode("utf-8")) for k, v in fields.items()}
        try:
            resp = self._http.post(
                path, files=files, headers=dict(self._credentials.auth_headers())
            )
        except httpx.RequestError as exc:
            raise WriteOutcomeUnknown(
                f"Network error during write to {shown} ({type(exc).__name__}); the change "
                "may or may not have landed — re-fetch before retrying."
            ) from None
        if resp.status_code >= 500:
            raise WriteOutcomeUnknown(
                f"SmartGym answered HTTP {resp.status_code} during write to {shown}; the "
                "change may or may not have landed — re-fetch before retrying."
            )
        if 200 <= resp.status_code < 300:
            try:
                resp.json()
            except ValueError:
                raise WriteOutcomeUnknown(
                    f"SmartGym answered HTTP {resp.status_code} with an unreadable (non-JSON) "
                    f"body during write to {shown}; the change may or may not have landed — "
                    "re-fetch before retrying."
                ) from None
        return self._decode(shown, resp, require_success=True)

    @staticmethod
    def _server_code(resp: httpx.Response) -> str | None:
        try:
            body = resp.json()
        except ValueError:
            return None
        code = body.get("code") if isinstance(body, dict) else None
        return str(code) if code is not None else None

    @classmethod
    def _decode(
        cls, shown: str, resp: httpx.Response, require_success: bool
    ) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            code = cls._server_code(resp)
            raise AuthError(
                f"SmartGym rejected the credentials (HTTP {resp.status_code}"
                f"{', ' + code if code else ''}) on {shown}. "
                "Re-run scripts/capture_credentials.py and retry.",
                code=code,
            )
        if not 200 <= resp.status_code < 300:
            code = cls._server_code(resp)
            raise ApiError(
                f"SmartGym answered HTTP {resp.status_code}{' ' + code if code else ''} "
                f"on {shown}.",
                code=code,
            )
        try:
            body = resp.json()
        except ValueError:
            raise ApiError(f"SmartGym returned a non-JSON response on {shown}.") from None
        if not isinstance(body, dict):
            raise ApiError(f"SmartGym returned an unexpected JSON shape on {shown}.")
        code = body.get("code")
        if require_success and code != "SUCCESS":
            raise ApiError(
                f"SmartGym answered {code!r} on {shown}.",
                code=str(code) if code is not None else None,
            )
        return body


__all__ = [
    "BASE_URL",
    "ApiCalls",
    "ApiClient",
    "ApiError",
    "AuthError",
    "CredentialProvider",
    "WriteOutcomeUnknown",
]
