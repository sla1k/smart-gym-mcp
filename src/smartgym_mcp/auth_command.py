"""`smartgym-mcp auth`: connect the MCP to the SmartGym account signed in on this Mac.

Walks the user through three steps: find the app's session in its cache
(`api.app_session`), check it against SmartGym's server, save it as the 0600 credentials
file the server reads (`api.auth`). Nothing secret is printed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import catalog
from .api import reads
from .api.app_session import (
    NoAppSession,
    UnreadableCache,
    cfnetwork_version,
    darwin_release,
    read_app_session,
    user_agent,
)
from .api.auth import Credentials, save_credentials
from .api.client import ApiCalls, ApiClient, ApiError, AuthError
from .config import Config

OPEN_APP = "Open SmartGym on this Mac, make sure you're signed in, and let it sync."
PRIVACY = (
    "macOS blocked access to SmartGym's data. If a dialog asks whether your terminal may "
    "access data from other apps, choose Allow. Otherwise turn your terminal on in System "
    "Settings > Privacy & Security > App Management (or Full Disk Access)."
)


SAME_SESSION = (
    "SmartGym hasn't cached a new session yet. Use the app for a moment (or sign out and in "
    "again) so it syncs. If SmartGym itself works and this keeps failing, the server may not "
    "accept the user-agent rebuilt for this Mac: see 'If auth fails' in the README."
)


class Checker(ApiCalls, Protocol):
    def close(self) -> None: ...


@dataclass
class Io:
    say: Callable[[str], None] = print
    ask: Callable[[str], str] = input


def _client(creds: Credentials, app_version: str) -> Checker:
    return ApiClient(creds, app_version=app_version)


def _retry(io: Io, problem: str) -> bool:
    """Show what to fix; True when the user wants another try."""
    io.say(f"  {problem}")
    try:
        io.ask("  Press Enter to try again, or Ctrl-C to stop. ")
    except EOFError:
        return False
    return True


def run(
    cfg: Config,
    io: Io | None = None,
    *,
    connect: Callable[[Credentials, str], Checker] = _client,
    darwin: Callable[[], str] = darwin_release,
) -> int:
    """The exit status: 0 when the credentials file was saved."""
    io = io or Io()
    app_version = catalog.installed_app_version(cfg)
    if app_version is None:
        io.say(f"SmartGym isn't installed at {cfg.app_bundle}. Install it from the App Store.")
        return 1
    cf = cfnetwork_version(cfg.cfnetwork_plist)
    if cf is None:
        io.say("Can't read this Mac's CFNetwork version, which SmartGym's user-agent needs.")
        return 1
    agent = user_agent(app_version, cf, darwin())
    rejected: str | None = None
    try:
        while True:
            io.say("Step 1/3: Looking for SmartGym's session on this Mac...")
            try:
                creds = read_app_session(cfg.app_cache, user_agent=agent)
            except NoAppSession:
                if _retry(io, f"No signed-in session found yet. {OPEN_APP}"):
                    continue
                return 1
            except UnreadableCache:
                if _retry(
                    io, f"Couldn't read SmartGym's cache at {cfg.app_cache}. {OPEN_APP}"
                ):
                    continue
                return 1
            except PermissionError:
                if _retry(io, PRIVACY):
                    continue
                return 1
            except OSError as exc:
                if _retry(io, f"Couldn't read {cfg.app_cache}: {exc.strerror or exc}."):
                    continue
                return 1
            if creds.authorization == rejected:
                if _retry(io, SAME_SESSION):
                    continue
                return 1
            io.say("  Found it.")
            io.say("Step 2/3: Checking it with SmartGym's server...")
            client = connect(creds, app_version)
            try:
                reads.history_all(client)
            except AuthError:
                rejected = creds.authorization
                if _retry(
                    io, f"SmartGym rejected that session; it may have expired. {OPEN_APP}"
                ):
                    continue
                return 1
            except ApiError as exc:
                if _retry(io, str(exc)):
                    continue
                return 1
            finally:
                client.close()
            io.say("  Your account answered.")
            break
    except KeyboardInterrupt:
        io.say("")
        return 1
    try:
        save_credentials(cfg.credentials_path, creds)
    except OSError as exc:
        io.say(f"Can't write {cfg.credentials_path}: {exc.strerror or exc}.")
        return 1
    io.say(f"Step 3/3: Saved to {cfg.credentials_path} (only you can read it).")
    io.say("Done. Ask your assistant to call smartgym_health to check the connection.")
    return 0
