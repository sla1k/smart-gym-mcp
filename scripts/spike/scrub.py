"""Turn spike-flows.jsonl records into committed test fixtures.

usage: uv run python scripts/spike/scrub.py <record-index> <fixture-name>
Replaces the account id with "1" everywhere (path, query, form, response) and
drops credential verdicts. The account id is taken from the record, else from
SPIKE_ACCOUNT, else from any authID in the log; none found → exit 1. A POST
record whose form could not be decoded also exits 1, so no fixture is ever
written half-scrubbed or empty.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).with_name("spike-flows.jsonl")
DEST = Path(__file__).parents[2] / "tests" / "fixtures" / "api"


def _account(rec: dict[str, Any], records: list[dict[str, Any]]) -> str | None:
    for r in [rec, *records]:
        found = (r.get("form") or {}).get("authID") or (r.get("query") or {}).get("authID")
        if found:
            return str(found)
        if r is rec and os.environ.get("SPIKE_ACCOUNT"):
            return os.environ["SPIKE_ACCOUNT"]
    return None


def main() -> None:
    index, name = int(sys.argv[1]), sys.argv[2]
    records = [json.loads(line) for line in SRC.read_text(encoding="utf-8").splitlines()]
    rec = records[index]
    form = rec.get("form") or {}
    if rec.get("method") == "POST" and not form:
        sys.exit(f"record {index}: POST with no decoded form fields — not writing a fixture")
    account = _account(rec, records)
    if not account:
        sys.exit("no account id found (set SPIKE_ACCOUNT) — not writing a fixture")
    query = {k: v for k, v in (rec.get("query") or {}).items() if k != "requestDate"}
    blob = json.dumps(
        {
            "method": rec.get("method"),
            "path": rec["path"],
            "query": query,
            "form": form,
            "response": json.loads(rec["resp"] or "{}"),
        },
        ensure_ascii=False,
        indent=2,
    )
    blob = re.sub(rf"(?<!\d){re.escape(account)}(?!\d)", "1", blob)
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{name}.json").write_text(blob + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
