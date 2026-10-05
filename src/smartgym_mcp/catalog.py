"""Static read-only catalog from the SmartGym app bundle, exposed as resources."""

from __future__ import annotations

import json
import plistlib
from dataclasses import dataclass

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


def installed_app_version(cfg: Config) -> str | None:
    """CFBundleShortVersionString of the installed SmartGym app, None if unreadable."""
    plist = cfg.app_bundle / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as f:
            value = plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None


@dataclass(frozen=True)
class CatalogExercise:
    """One bundle catalog exercise; `id` is the server's exercise `id`/`genericID`."""

    id: int
    name: str
    type: int
    category: int
    sub_categories: str
    two_sides: int
    stretch: int
    equipment_ids: tuple[str, ...]
    images: tuple[str, str, str, str, str, str]


def load_bundle_exercises(cfg: Config) -> list[CatalogExercise]:
    raw = json.loads(read_catalog(cfg, "exercises"))["exercises"]
    return [
        CatalogExercise(
            id=int(e["id"]),
            name=str(e["name"]),
            type=int(e["type"]),
            category=int(e["category"]),
            sub_categories=str(e.get("subCategories") or ""),
            two_sides=int(e.get("twoSides") or 0),
            stretch=int(e.get("stretch") or 0),
            equipment_ids=tuple(x for x in str(e.get("equipments") or "").split(",") if x),
            images=(
                str(e.get("firstImage") or ""),
                str(e.get("secondImage") or ""),
                str(e.get("thirdImage") or ""),
                str(e.get("fourthImage") or ""),
                str(e.get("fifthImage") or ""),
                str(e.get("sixthImage") or ""),
            ),
        )
        for e in raw
    ]
