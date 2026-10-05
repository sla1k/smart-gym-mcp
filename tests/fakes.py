"""Offline stand-in for api.client.ApiClient: scripted responses, recorded posts."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


class FakeClient:
    def __init__(
        self,
        gets: Mapping[str, dict[str, Any] | list[dict[str, Any]]] | None = None,
        posts: list[dict[str, Any] | Exception] | None = None,
        user_id: str = "1",
    ) -> None:
        self._gets = {
            k: (list(v) if isinstance(v, list) else [v]) for k, v in (gets or {}).items()
        }
        self._posts = list(posts or [])
        self.user_id = user_id
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.get_calls: list[str] = []
        self.closed = False

    def get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        require_success: bool = True,
    ) -> dict[str, Any]:
        self.get_calls.append(path)
        queue = self._gets[path]
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        return copy.deepcopy(body)

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        self.sent.append((path, dict(form)))
        if not self._posts:
            return {"code": "SUCCESS"}
        nxt = self._posts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def close(self) -> None:
        self.closed = True
