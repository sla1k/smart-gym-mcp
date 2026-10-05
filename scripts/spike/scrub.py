"""Turn spike-flows.jsonl records into committed test fixtures.

usage: uv run python scripts/spike/scrub.py <record-index> <fixture-name>
Replaces the account id with "1" everywhere and drops credential verdicts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).with_name("spike-flows.jsonl")
DEST = Path(__file__).parents[2] / "tests" / "fixtures" / "api"


def _form(body: str) -> dict[str, str]:
    fields = re.findall(r'name\s*=\s*"(\w+)"\s*\r?\n\r?\n(.*?)\r?\n-{5,}', body, re.S)
    return {k: v for k, v in fields}


def main() -> None:
    index, name = int(sys.argv[1]), sys.argv[2]
    rec = json.loads(SRC.read_text(encoding="utf-8").splitlines()[index])
    form = _form(rec["req"])
    account = form.get("authID", "")
    blob = json.dumps(
        {"path": rec["path"], "form": form, "response": json.loads(rec["resp"] or "{}")},
        ensure_ascii=False,
        indent=2,
    )
    if account:
        blob = re.sub(rf"(?<!\d){re.escape(account)}(?!\d)", "1", blob)
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{name}.json").write_text(blob + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
