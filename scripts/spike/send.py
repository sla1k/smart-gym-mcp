"""Send ONE hand-built request with the user's captured credentials. RUN BY THE USER.

usage:
  uv run python scripts/spike/send.py <path> <form.json>   # POST
  uv run python scripts/spike/send.py <path> --get         # GET
  flags: --no-phrase, --shape (print response structure only, no values),
         --save=<name> (write tests/fixtures/api/<name>.json, account id scrubbed)
  "{authID}" in <path> is replaced by the account id.
form.json: {"field": "value", ...} — values sent as multipart form fields.
Credentials: the file scripts/capture_credentials.py writes
(~/.smartgym-mcp/credentials.json, override: SMARTGYM_CREDENTIALS). `authID`
always comes from it (fixtures carry the scrubbed "1"); requestDate is "now".
That file has no `phrase`, so the request is sent without one. Prints only the
HTTP status, the response `code` and top-level keys (bodies can hold personal data).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import httpx

BASE = "https://api.smartgymapp.com/v1.1/"
CRED = Path(
    os.environ.get("SMARTGYM_CREDENTIALS", "~/.smartgym-mcp/credentials.json")
).expanduser()
FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "api"
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


def _save_fixture(
    name: str,
    method: str,
    path: str,
    fields: dict[str, str],
    body: object,
    creds: dict[str, str],
) -> None:
    """Write a scrub.py-format fixture: account id → "1", no requestDate, no secrets."""
    form = {k: v for k, v in fields.items() if k != "requestDate"}
    blob = json.dumps(
        {
            "method": method,
            "path": "/v1.1/" + path,
            "query": {},
            "form": form,
            "response": body,
        },
        ensure_ascii=False,
        indent=2,
    )
    for secret in (creds["authorization"], creds.get("phrase")):
        if secret and secret in blob:
            sys.exit("credential value found in response — not writing a fixture")
    blob = re.sub(rf"(?<!\d){re.escape(creds['authID'])}(?!\d)", "1", blob)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(blob + "\n", encoding="utf-8")
    print("saved fixture", name)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    creds = json.loads(CRED.read_text())
    path = args[0].replace("{authID}", creds["authID"])
    headers = {
        **creds.get("app_headers", {}),
        **{h: creds[h] for h in SECRET_HEADERS if creds.get(h)},
    }
    if "--no-phrase" in flags:
        headers.pop("phrase", None)
    fields: dict[str, str] = {}
    if "--get" not in flags:
        fields = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    fields["authID"] = creds["authID"]
    fields.setdefault("appVersion", "8.0.3")
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
    save = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--save=")), None)
    if save and isinstance(body, dict):
        _save_fixture(
            save, "GET" if "--get" in flags else "POST", args[0], fields, body, creds
        )
    if isinstance(body, dict) and "--shape" in flags:
        print(r.status_code, "code =", body.get("code"))
        _shape(body, "")
    elif isinstance(body, dict):
        print(r.status_code, "code =", body.get("code"), "keys =", sorted(body)[:12])
    else:
        print(r.status_code, type(body).__name__)


if __name__ == "__main__":
    main()
