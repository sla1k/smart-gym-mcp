"""Send ONE hand-built request with the user's captured credentials. RUN BY THE USER.

usage: uv run python scripts/spike/send.py <path> <form.json>
form.json: {"field": "value", ...} — values sent as multipart form fields.
Prints only the HTTP status and the JSON response body.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import httpx

BASE = "https://api.smartgymapp.com/v1.1/"
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"


def main() -> None:
    path, form_file = sys.argv[1], Path(sys.argv[2])
    creds = json.loads(CRED.read_text())
    form = json.loads(form_file.read_text(encoding="utf-8"))
    form.setdefault("requestDate", dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S"))
    files = {k: (None, str(v).encode("utf-8")) for k, v in form.items()}
    r = httpx.post(BASE + path, files=files, headers=creds, timeout=30)
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
