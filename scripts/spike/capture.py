"""mitmproxy addon for the Phase 0 spike. RUN BY THE USER, not the agent.

Logs SmartGym API bodies to spike-flows.jsonl. Credential headers are never
written there — only a verdict per request: whether `phrase` / `Authorization`
equal the previous request's value. With SPIKE_SAVE_CREDENTIALS=1 the latest
headers are written to ~/.smartgym-mcp/credentials.json (mode 0600) for
send.py; that file never leaves the user's machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from mitmproxy import http

OUT = Path(__file__).with_name("spike-flows.jsonl")
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"
SECRET_HEADERS = ("authorization", "phrase")
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


def response(flow: http.HTTPFlow) -> None:
    if "smartgymapp.com" not in flow.request.pretty_host:
        return
    rec = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": flow.request.method,
        "path": flow.request.path.split("?")[0],
        "query_keys": sorted(flow.request.query.keys()),
        "header_names": sorted(flow.request.headers.keys()),
        "secret_verdicts": _verdicts(flow),
        "req": flow.request.get_content().decode("utf-8", errors="replace"),
        "status": flow.response.status_code if flow.response else None,
        "resp": flow.response.get_content().decode("utf-8", errors="replace")
        if flow.response
        else "",
    }
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if os.environ.get("SPIKE_SAVE_CREDENTIALS") == "1":
        CRED.parent.mkdir(parents=True, exist_ok=True)
        payload = {h: flow.request.headers[h] for h in SECRET_HEADERS if h in flow.request.headers}
        CRED.write_text(json.dumps(payload))
        CRED.chmod(0o600)
