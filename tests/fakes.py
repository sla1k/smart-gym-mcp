"""Offline stand-in for api.client.ApiClient: scripted responses, recorded posts.

A scripted response may be an Exception, which that call raises instead. A body whose
`code` isn't SUCCESS raises ApiError, as ApiClient._decode does: the parsers rely on it.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from smartgym_mcp.api.client import ApiError

# Sequence and Mapping are covariant, so a list[dict[str, Any]] literal type-checks as a script.
Script = Mapping[str, Mapping[str, Any] | Exception | Sequence[Mapping[str, Any] | Exception]]


class FakeClient:
    def __init__(
        self,
        gets: Script | None = None,
        posts: Sequence[Mapping[str, Any] | Exception] | None = None,
        user_id: str = "1",
    ) -> None:
        self._gets: dict[str, list[Mapping[str, Any] | Exception]] = {
            k: (list(v) if isinstance(v, Sequence) else [v]) for k, v in (gets or {}).items()
        }
        self._posts = list(posts or [])
        self.user_id = user_id
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.get_calls: list[str] = []
        self.closed = False

    def get(self, path: str) -> dict[str, Any]:
        self.get_calls.append(path)
        queue = self._gets[path]
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(body, Exception):
            raise body
        return _decoded(path, copy.deepcopy(dict(body)))

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        self.sent.append((path, dict(form)))
        if not self._posts:
            return {"code": "SUCCESS"}
        nxt = self._posts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _decoded(path, dict(nxt))

    def close(self) -> None:
        self.closed = True


def _decoded(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("code") != "SUCCESS":
        raise ApiError(f"SmartGym answered {body.get('code')!r} on {path}.")
    return body


CUSTOM_EXERCISES = "exercise/allCustomExercises/"
NO_CUSTOM_EXERCISES: dict[str, Any] = {"code": "SUCCESS", "exercises": []}


def account_client(
    gets: Script | None = None,
    posts: Sequence[Mapping[str, Any] | Exception] | None = None,
) -> FakeClient:
    """A FakeClient whose account has no custom exercises unless `gets` scripts them."""
    return FakeClient({CUSTOM_EXERCISES: NO_CUSTOM_EXERCISES, **(gets or {})}, posts)
