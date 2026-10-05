"""mitmproxy addon that writes the SmartGym credentials file. RUN BY THE USER.

  mitmdump --mode local:SmartGym -s scripts/capture_credentials.py --set confdir=<CA dir>

then open SmartGym. The first request carrying an Authorization header is saved
to ~/.smartgym-mcp/credentials.json (override: SMARTGYM_CREDENTIALS), mode
0600: the Authorization value, the account id, and the app's user-agent /
accept / accept-language. Nothing else is stored; no value is printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mitmproxy import ctx, http

CRED = Path(
    os.environ.get("SMARTGYM_CREDENTIALS", "~/.smartgym-mcp/credentials.json")
).expanduser()
APP_HEADERS = ("user-agent", "accept", "accept-language")
_saved = False


def _account(req: http.Request) -> str | None:
    if req.query.get("authID"):
        return str(req.query["authID"])
    try:
        value = req.multipart_form.get(b"authID")
    except ValueError:
        return None
    return value.decode() if value else None


def request(flow: http.HTTPFlow) -> None:
    global _saved
    req = flow.request
    if "smartgymapp.com" not in req.pretty_host:
        return
    auth = req.headers.get("authorization")
    account = _account(req)
    app = {h: req.headers.get(h) for h in APP_HEADERS}
    if not auth or not account or not all(app.values()):
        return
    CRED.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"authorization": auth, "authID": account, "app_headers": app}, f)
    os.replace(tmp, CRED)
    if not _saved:
        _saved = True
        ctx.log.info(f"SmartGym credentials saved to {CRED} — you can stop mitmdump now.")
