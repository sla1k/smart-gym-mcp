"""mitmproxy addon for the Phase 0 spike. RUN BY THE USER, not the agent.

Logs SmartGym API traffic to spike-flows.jsonl with credential values redacted:
the current `phrase` / `Authorization` values are replaced by <REDACTED>
everywhere, and bodies of login/token endpoints are dropped. Per request only
a verdict is kept: whether each secret header equals the previous request's.

With SPIKE_SAVE_CREDENTIALS=1 the latest complete header pair plus the account
id are written to ~/.smartgym-mcp/credentials.json (mode 0600) for send.py;
that file never leaves the user's machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from mitmproxy import http

OUT = Path(__file__).with_name("spike-flows.jsonl")
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"
SECRET_HEADERS = ("authorization", "phrase")
AUTH_PATH = re.compile(r"login|logout|auth(?!ID)|token|register|password|session", re.I)
REDACTED = "<REDACTED>"
_last: dict[str, str] = {}


def _verdicts(flow: http.HTTPFlow) -> dict[str, str]:
    out = {}
    for h in SECRET_HEADERS:
        value = flow.request.headers.get(h)
        if value is None:
            out[h] = "absent"
            continue
        digest = hashlib.sha256(value.encode()).hexdigest()
        out[h] = "first" if h not in _last else ("same" if _last[h] == digest else "CHANGED")
        _last[h] = digest
    return out


def _secrets(flow: http.HTTPFlow) -> list[str]:
    values = [flow.request.headers.get(h) for h in SECRET_HEADERS]
    found = [v for v in values if v]
    # "Bearer <token>": also redact the bare token.
    found += [v.split(" ", 1)[1] for v in found if " " in v]
    return sorted(set(found), key=len, reverse=True)


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, REDACTED)
    return text


def _form(req: http.Request) -> dict[str, str]:
    for attr in ("multipart_form", "urlencoded_form"):
        try:
            items = getattr(req, attr).items()
        except (AttributeError, ValueError):
            continue
        form = {
            (k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)): (
                v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
            )
            for k, v in items
        }
        if form:
            return form
    return {}


def _save_credentials(flow: http.HTTPFlow, account: str | None) -> None:
    headers = {h: flow.request.headers.get(h) for h in SECRET_HEADERS}
    if not all(headers.values()) or not account:
        return
    CRED.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({**headers, "authID": account}, f)
    os.replace(tmp, CRED)


def response(flow: http.HTTPFlow) -> None:
    if "smartgymapp.com" not in flow.request.pretty_host:
        return
    path = flow.request.path.split("?")[0]
    secrets = _secrets(flow)
    form = _form(flow.request)
    query = {str(k): str(v) for k, v in flow.request.query.items()}
    account = form.get("authID") or query.get("authID")
    rec: dict[str, Any] = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": flow.request.method,
        "path": path,
        "query": query,
        "header_names": sorted(flow.request.headers.keys()),
        "secret_verdicts": _verdicts(flow),
        "form": form,
        "req": flow.request.get_content().decode("utf-8", errors="replace"),
        "status": flow.response.status_code if flow.response else None,
        "resp": flow.response.get_content().decode("utf-8", errors="replace")
        if flow.response
        else "",
    }
    if AUTH_PATH.search(path):
        rec["form"], rec["req"], rec["resp"] = {}, REDACTED, REDACTED
    line = _redact(json.dumps(rec, ensure_ascii=False), secrets)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if os.environ.get("SPIKE_SAVE_CREDENTIALS") == "1":
        _save_credentials(flow, account)
