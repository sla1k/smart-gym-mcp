"""snapshots.py: the fetched routine JSON is saved verbatim before a write."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from smartgym_mcp.snapshots import save_snapshot


def test_snapshot_written_under_timestamped_folder(tmp_path: Path) -> None:
    raw = {"identifier": "3000001", "name": "FB-A — Return W1", "exercises": []}
    path = save_snapshot(tmp_path, raw, now=datetime(2026, 10, 6, 9, 30, 1, 5))
    assert path == tmp_path / "20261006-093001-000005" / "routine-3000001.json"
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    assert "—" in path.read_text(encoding="utf-8")
