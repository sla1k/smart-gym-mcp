"""Pre-write snapshots of the fetched routine JSON (API-client spec §7).

Restore = smartgym_apply_routine from the snapshot's content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


def save_snapshot(backup_dir: Path, routine_raw: Mapping[str, Any], *, now: datetime) -> Path:
    folder = backup_dir / now.strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"routine-{routine_raw.get('identifier', 'unknown')}.json"
    path.write_text(
        json.dumps(routine_raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


__all__ = ["save_snapshot"]
