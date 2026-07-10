"""Static read-only catalog from the SmartGym app bundle, exposed as resources."""

from __future__ import annotations

from .config import Config

# Resource name → bundle JSON filename.
CATALOG_FILES = {
    "exercises": "Exercises.json",
    "equipment": "Equipments.json",
    "categories": "Categories.json",
}


def read_catalog(cfg: Config, name: str) -> str:
    """Return the raw JSON text of a bundle catalog. Raises if unknown/missing."""
    filename = CATALOG_FILES.get(name)
    if filename is None:
        raise KeyError(f"unknown catalog {name!r}; known: {sorted(CATALOG_FILES)}")
    path = cfg.app_bundle / "Contents" / "Resources" / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Catalog {filename} not found at {path}. "
            "Is SmartGym installed at SMARTGYM_APP_BUNDLE?"
        )
    return path.read_text(encoding="utf-8")
