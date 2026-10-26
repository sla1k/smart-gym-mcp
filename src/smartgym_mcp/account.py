"""The account cache: the reads one tool call shares (API-client spec §5, §11).

AccountCache keeps the parsed `history/all` and custom-exercises reads for one tool call
(`AppContext.use` clears it as each call starts) and drops them after every write, so a
write's verifying re-read sees the server's answer. `routine/single` is never cached.
"""

from __future__ import annotations

from .api import reads
from .api.client import ApiCalls
from .catalog import CatalogExercise
from .model import AccountData, parse_custom_exercises, parse_history_all


class AccountCache:
    """Not thread-safe: the server runs tool calls one at a time (DESIGN invariant 7)."""

    def __init__(self, client: ApiCalls) -> None:
        self._client = client
        self._account: AccountData | None = None
        self._customs: list[CatalogExercise] | None = None

    def account(self) -> AccountData:
        if self._account is None:
            self._account = parse_history_all(reads.history_all(self._client))
        return self._account

    def custom_exercises(self) -> list[CatalogExercise]:
        if self._customs is None:
            self._customs = parse_custom_exercises(reads.custom_exercises(self._client))
        return self._customs

    def invalidate(self) -> None:
        self._account = self._customs = None
