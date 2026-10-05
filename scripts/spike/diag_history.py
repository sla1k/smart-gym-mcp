"""Count history/workout shapes in the full history/all answer. RUN BY THE USER.

usage: uv run python scripts/spike/diag_history.py
Prints only key names, counts and value kinds — never values.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import httpx

BASE = "https://api.smartgymapp.com/v1.1/"
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"


def _kind(v: object) -> str:
    if v is None:
        return "null"
    if v == "":
        return "empty"
    return type(v).__name__


def main() -> None:
    creds = json.loads(CRED.read_text())
    headers = {**creds.get("app_headers", {}), "authorization": creds["authorization"]}
    params = {
        "appVersion": "8.0.3",
        "authID": creds["authID"],
        "requestDate": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    r = httpx.get(f"{BASE}history/all/{creds['authID']}/", params=params, headers=headers, timeout=60)
    body = r.json()
    print("status", r.status_code, "code", body.get("code"), "hasMore", body.get("hasMore"))
    shapes: Counter[str] = Counter()
    for h in body.get("histories") or []:
        removed = "removed" if h.get("dateRemoved") else "live"
        w = h.get("workout")
        if not isinstance(w, dict):
            shapes[f"{removed} workout={_kind(w)}"] += 1
            continue
        start = _kind(w.get("startDate")) if "startDate" in w else "absent"
        key = f"{removed} startDate={start}"
        if start != "str":
            key += f" workout_keys={sorted(w)} history_keys={sorted(h)}"
            key += f" preciseDateAdded={_kind(h.get('preciseDateAdded'))}"
        shapes[key] += 1
    for k, n in shapes.most_common():
        print(n, k)


if __name__ == "__main__":
    main()
