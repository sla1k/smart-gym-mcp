"""What tool calls run on (API-client spec §5; DESIGN invariant 7).

`Services` are the client, the account cache, the routine service and the bundle's
equipment, built on first use by `build_services`. `AppContext` owns them and runs tool
calls one at a time: one `anyio.Lock`, the blocking work on a worker thread.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import anyio
import anyio.to_thread

from . import catalog
from .account import AccountCache
from .api.auth import load_credentials
from .api.client import ApiClient, AuthError
from .config import VERIFIED_APP_VERSION, Config
from .matching import ExerciseCatalog
from .service import RoutineService

logger = logging.getLogger("smartgym_mcp")


def local_timezone_name() -> str:
    """The Mac's IANA time zone (what the app sends as `timezone`), "UTC" if unknown."""
    target = os.path.realpath("/etc/localtime")
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


@dataclass
class Services:
    client: ApiClient
    cache: AccountCache
    service: RoutineService
    equipment: list[catalog.CatalogEquipment]


def build_services(cfg: Config) -> Services:
    client = ApiClient(
        load_credentials(cfg.credentials_path),
        app_version=catalog.installed_app_version(cfg) or VERIFIED_APP_VERSION,
    )
    try:
        cache = AccountCache(client)
        service = RoutineService(
            client,
            cache,
            ExerciseCatalog(catalog.load_bundle_exercises(cfg)),
            backup_dir=cfg.backup_dir,
            timezone=local_timezone_name(),
        )
        return Services(
            client=client,
            cache=cache,
            service=service,
            equipment=catalog.load_bundle_equipment(cfg),
        )
    except BaseException:
        client.close()
        raise


# Longer than a write can run: one read is up to 3 attempts × 30 s plus backoff (~92 s),
# and an edit makes up to five reads and one 30 s post (~8 min). A call whose client
# cancels it or disconnects stops waiting at once.
CALL_WAIT_S = 600.0


class ServerBusy(RuntimeError):
    """Another tool call held the services for longer than the wait allows."""


class ServerClosed(RuntimeError):
    """The server is shutting down; no new call may start."""


@dataclass
class AppContext:
    """Built in `lifespan`, so the call lock belongs to the server's event loop."""

    cfg: Config
    factory: Callable[[Config], Services] = build_services
    call_wait_s: float = CALL_WAIT_S
    _calls: anyio.Lock = field(default_factory=anyio.Lock, init=False, repr=False)
    _services: Services | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def run[T](self, fn: Callable[[Services], T]) -> T:
        """`fn(services)` on a worker thread, one call at a time.

        The account cache, the client's lifetime and each write's re-read → rewrite assume
        calls don't overlap. A call that can't start fails before sending anything:
        ServerBusy after `call_wait_s`, ServerClosed after close(), or its cancellation
        while it waits. Once started, `fn` runs to the end even if the call is cancelled
        (a thread can't be stopped), and the lock is held until it has. The account cache
        is emptied when a call ends, so no call sees an earlier call's read.
        """
        try:
            with anyio.fail_after(self.call_wait_s):
                await self._calls.acquire()
        except TimeoutError:
            raise ServerBusy(
                "Another SmartGym call was still running after waiting "
                f"{self.call_wait_s:.0f} s; nothing was sent. Try again once it finishes."
            ) from None
        try:
            if self._closed:
                raise ServerClosed(
                    "The SmartGym MCP server is shutting down; nothing was sent."
                )
            return await anyio.to_thread.run_sync(self._call, fn)
        finally:
            self._calls.release()

    def _call[T](self, fn: Callable[[Services], T]) -> T:
        """One call on the worker thread, under the lock.

        Services are built on first use; a failed build is not cached, so the next call
        retries. An AuthError drops them (closing the client) so the next call re-reads the
        credentials file — a re-captured session takes effect without restarting the
        server. The error itself propagates unchanged.
        """
        if self._services is None:
            self._services = self.factory(self.cfg)
        services = self._services
        try:
            return fn(services)
        except AuthError:
            self._services = None
            services.client.close()
            raise
        finally:
            services.cache.invalidate()  # the reads live only as long as the call

    async def close(self) -> None:
        """Stop new calls, then close the client once the call in flight (if any) is done.
        If it is still running after `call_wait_s`, the client is left open rather than
        closed under its requests."""
        self._closed = True  # a call queued behind this one must not rebuild the services
        try:
            with anyio.fail_after(self.call_wait_s):
                await self._calls.acquire()
        except TimeoutError:
            logger.warning("a tool call is still running; leaving its client open")
            return
        try:
            services, self._services = self._services, None
            if services is not None:
                services.client.close()
        finally:
            self._calls.release()
