"""Shared test helpers: the offline FakeClient plus constants and builders used by several tests.

FakeClient stands in for api.client.ApiClient: scripted responses, recorded posts.
A scripted response may be an Exception, which that call raises instead. A body whose
`code` isn't SUCCESS raises ApiError, as ApiClient._decode does: the parsers rely on it.
"""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from smartgym_mcp.api.client import ApiError
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.diff import DesiredExercise
from smartgym_mcp.payloads import Mint
from smartgym_mcp.service import RoutineService

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


# ---------------------------------------------------------------- shared test data

FIX = Path(__file__).parent / "fixtures" / "api"
ACCOUNT: dict[str, Any] = json.loads(
    (FIX / "history_all_synthetic.json").read_text(encoding="utf-8")
)
HISTORY = "history/all/1/"
SINGLE = "routine/single/3000001/"
UPDATE = "routine/update/"
EDIT = "routine/updateExercise/"
TZ = "Europe/Madrid"
SECRET = "Bearer s3cr3t-token"
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}


def catalog_exercise(ident: int, name: str) -> CatalogExercise:
    """A plain bundle exercise with placeholder metadata."""
    return CatalogExercise(
        id=ident,
        name=name,
        type=0,
        category=1,
        sub_categories="",
        two_sides=0,
        stretch=0,
        equipment_ids=(),
        images=(f"{ident}-1", "", "", "", "", ""),
    )


def by_id(*idents: int) -> list[DesiredExercise]:
    return [DesiredExercise(exercise_id=i) for i in idents]


def counter_mint(first: int) -> Mint:
    """A deterministic Mint: first, first + 1, … regardless of the clock."""
    n = itertools.count(first)
    return lambda _now: next(n)


def app_form(name: str) -> dict[str, Any]:
    """A captured fixture's form, minus per-request fields, with its JSON fields decoded."""
    form = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))["form"]
    out: dict[str, Any] = {}
    for k, v in form.items():
        if k in ("authID", "appVersion", "requestDate"):
            continue
        out[k] = json.loads(v) if k in ("routines", "exercises", "updateExercises") else v
    return out


def routine_service(
    client: FakeClient,
    backup_dir: Path,
    bundle: Mapping[int, CatalogExercise],
    *,
    now: datetime,
    first_hash: int,
) -> RoutineService:
    """A RoutineService on `client` with a fixed clock and deterministic hash ids."""
    return RoutineService(
        client,
        AccountStore(client),
        bundle,
        backup_dir=backup_dir,
        timezone=TZ,
        now=lambda: now,
        mint=counter_mint(first_hash),
    )
