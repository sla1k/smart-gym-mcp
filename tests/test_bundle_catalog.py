"""Bundle catalog: Exercises.json → CatalogExercise + name resolution by catalog id."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from smartgym_mcp.catalog import load_bundle_equipment, load_bundle_exercises
from smartgym_mcp.config import Config, load_config
from smartgym_mcp.matching import ExerciseCatalog, UnresolvedExercise

EXERCISES = {
    "exercises": [
        {
            "id": "194",
            "name": "Push Up",
            "type": "1",
            "category": "11",
            "subCategories": "9,12",
            "twoSides": "0",
            "stretch": "0",
            "equipments": "",
            "firstImage": "0194-1",
            "secondImage": "0194-2",
            "thirdImage": "",
            "fourthImage": "",
            "fifthImage": "",
            "sixthImage": "",
        },
        {
            "id": "363",
            "name": "Resistance Band Pull Apart",
            "type": "0",
            "category": "5",
            "subCategories": "101",
            "twoSides": "0",
            "stretch": "0",
            "equipments": "38",
            "firstImage": "0363-1",
            "secondImage": "0363-2",
            "thirdImage": "",
            "fourthImage": "",
            "fifthImage": "",
            "sixthImage": "",
        },
    ]
}


@pytest.fixture
def bundle_cfg(tmp_path: Path) -> Config:
    resources = tmp_path / "SmartGym.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "Exercises.json").write_text(json.dumps(EXERCISES), encoding="utf-8")
    return replace(load_config(), app_bundle=tmp_path / "SmartGym.app")


def test_load_bundle_exercises_coerces_fields(bundle_cfg: Config) -> None:
    push_up, band = load_bundle_exercises(bundle_cfg)
    assert (push_up.id, push_up.name, push_up.type, push_up.category) == (
        194,
        "Push Up",
        1,
        11,
    )
    assert push_up.sub_categories == "9,12"
    assert push_up.equipment_ids == ()
    assert band.equipment_ids == ("38",)
    assert push_up.images == ("0194-1", "0194-2", "", "", "", "")


def test_from_bundle_resolves_to_catalog_id(bundle_cfg: Config) -> None:
    catalog = ExerciseCatalog.from_bundle(load_bundle_exercises(bundle_cfg))
    assert catalog.resolve("push up").z_pk == 194
    assert catalog.resolve("363").resolved_name == "Resistance Band Pull Apart"
    with pytest.raises(UnresolvedExercise):
        catalog.resolve("Zercher squat")


def test_load_bundle_equipment_uses_english_names(bundle_cfg: Config) -> None:
    resources = bundle_cfg.app_bundle / "Contents" / "Resources"
    (resources / "Equipments.json").write_text(
        json.dumps(
            {
                "equipments": [
                    {
                        "identifier": "1",
                        "category": "1",
                        "name": {"en": "Barbell", "de": "Langhantel"},
                    },
                    {"identifier": "38", "category": "3", "name": "Resistance Band"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert [(e.id, e.name, e.category) for e in load_bundle_equipment(bundle_cfg)] == [
        (1, "Barbell", 1),
        (38, "Resistance Band", 3),
    ]
