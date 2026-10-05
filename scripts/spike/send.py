"""Send ONE hand-built request with the user's captured credentials. RUN BY THE USER.

usage:
  uv run python scripts/spike/send.py <path> <form.json> [--keep-date]   # POST
  uv run python scripts/spike/send.py <path> --get [--keep-date]         # GET
  flags: --keep-date, --bad-phrase (phrase replaced by garbage), --no-phrase,
         --shape (print response structure only, no values)
  "{authID}" in <path> is replaced by the account id.
form.json: {"field": "value", ...} — values sent as multipart form fields.
`authID` always comes from the local credentials file (fixtures carry the
scrubbed "1"). --keep-date sends the requestDate the captured `phrase` was
made for; without it requestDate is "now". Prints only the HTTP status, the
response `code` and top-level keys (bodies can hold personal data).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import httpx

BASE = "https://api.smartgymapp.com/v1.1/"
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"
SECRET_HEADERS = ("authorization", "phrase")


SHOW_VALUES = {"code", "hasMore", "lastModified"}
MAX_DEPTH = 6


def _shape(value: object, path: str, depth: int = 0) -> None:
    """Print structure only — key names, types, list lengths; values only for SHOW_VALUES."""
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for k in sorted(value):
            v = value[k]
            kind = f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__
            if depth == 0 and k in SHOW_VALUES:
                kind += f" = {v!r}"
            print(f"{'  ' * depth}{path}{k}: {kind}")
            _shape(v, "", depth + 1)
    elif isinstance(value, list) and value:
        _shape(value[0], "[0].", depth)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    creds = json.loads(CRED.read_text())
    path = args[0].replace("{authID}", creds["authID"])
    headers = {**creds.get("app_headers", {}), **{h: creds[h] for h in SECRET_HEADERS}}
    if "--bad-phrase" in flags:
        headers["phrase"] = "0" * len(headers["phrase"])
    if "--no-phrase" in flags:
        del headers["phrase"]
    fields: dict[str, str] = {}
    if "--get" not in flags:
        fields = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    fields["authID"] = creds["authID"]
    fields.setdefault("appVersion", "8.0.3")
    if "--keep-date" in flags:
        fields["requestDate"] = creds["requestDate"]
    else:
        fields["requestDate"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    print("requestDate:", fields["requestDate"])
    if "--get" in flags:
        r = httpx.get(BASE + path, params=fields, headers=headers, timeout=30)
    else:
        files = {k: (None, str(v).encode("utf-8")) for k, v in fields.items()}
        r = httpx.post(BASE + path, files=files, headers=headers, timeout=30)
    try:
        body = r.json()
    except ValueError:
        print(
            r.status_code, "non-JSON response from", r.headers.get("server"), "|", r.text[:300]
        )
        return
    if isinstance(body, dict) and "--shape" in flags:
        print(r.status_code, "code =", body.get("code"))
        _shape(body, "")
    elif isinstance(body, dict):
        print(r.status_code, "code =", body.get("code"), "keys =", sorted(body)[:12])
    else:
        print(r.status_code, type(body).__name__)


if __name__ == "__main__":
    main()
