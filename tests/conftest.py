"""Tests run against the installed (editable) package and never touch the network or the account.

Shared helpers live in `fakes.py`; `pythonpath = ["tests"]` (pyproject.toml) puts `tests/` on
`sys.path`, so test modules import them as `from fakes import ...`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_paths(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `load_config()` in a test points at an empty temp dir, never at the user's real
    credentials, app bundle, app cache or backups, even when a test's own injection of fake
    services stops working. A test that needs a path sets its env var itself."""
    root: Path = tmp_path_factory.mktemp("smartgym-env")
    for var, name in (
        ("SMARTGYM_CREDENTIALS", "credentials.json"),
        ("SMARTGYM_APP_BUNDLE", "SmartGym.app"),
        ("SMARTGYM_APP_CACHE", "Cache.db"),
        ("SMARTGYM_BACKUP_DIR", "backups"),
    ):
        monkeypatch.setenv(var, str(root / name))
