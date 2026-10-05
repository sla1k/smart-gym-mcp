"""payloads.py: routine/add/ JSON in the app's captured shape + archive forms."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.payloads import (
    add_routines_form,
    archive_form,
    mint_unique_hashid,
    new_routine_payload,
    unarchive_form,
)

MADRID = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 5, 13, 30, 15, tzinfo=MADRID)

PUSH_UP = CatalogExercise(
    id=194,
    name="Push Up",
    type=1,
    category=11,
    sub_categories="9,12",
    two_sides=0,
    stretch=0,
    equipment_ids=(),
    images=("0194-1", "0194-2", "", "", "", ""),
)
BAND = CatalogExercise(
    id=363,
    name="Resistance Band Pull Apart",
    type=0,
    category=5,
    sub_categories="101",
    two_sides=0,
    stretch=0,
    equipment_ids=("38",),
    images=("0363-1", "0363-2", "", "", "", ""),
)


def _counter() -> object:
    n = iter(range(1, 100))
    return lambda _now: 26100500000000 + next(n)


def test_new_routine_payload_matches_captured_shape() -> None:
    spec = RoutineSpec(
        name="ZZ-FB — Test",
        days="0001",
        note="note",
        exercises=[
            ExerciseSpec(
                exercise="Push Up",
                rest_seconds=60,
                sets=[SetSpec(reps=10), SetSpec(reps=8.5, weight_kg=12.5)],
            ),
            ExerciseSpec(exercise="band", note="per arm"),
        ],
    )
    p = new_routine_payload(spec, [PUSH_UP, BAND], number=21, now=NOW, mint=_counter())

    assert {
        k: p[k]
        for k in (
            "name",
            "days",
            "goal",
            "note",
            "number",
            "reference",
            "hasSynced",
            "migratedSets",
            "uniqueHashID",
            "dateCreated",
        )
    } == {
        "name": "ZZ-FB — Test",
        "days": "0001",
        "goal": None,
        "note": "note",
        "number": 21,
        "reference": 0,
        "hasSynced": 0,
        "migratedSets": 1,
        "uniqueHashID": 26100500000001,
        "dateCreated": "2026-10-04T22:00:00",  # local midnight, sent as UTC
    }
    assert "identifier" not in p

    push, band = p["exercises"]
    assert {
        k: push[k]
        for k in (
            "id",
            "genericID",
            "name",
            "idx",
            "index",
            "pause",
            "type",
            "category",
            "subCategories",
            "twoSides",
            "stretch",
            "isStretch",
            "isCustom",
            "requiresBands",
            "isSingleWeight",
            "listGroup",
            "mode",
            "totalSets",
            "totalRealSets",
            "firstImage",
            "sixthImage",
            "dateAdded",
        )
    } == {
        "id": 194,
        "genericID": 194,
        "name": "Push Up",
        "idx": 0,
        "index": 0,
        "pause": 60,
        "type": 1,
        "category": 11,
        "subCategories": "9,12",
        "twoSides": 0,
        "stretch": 0,
        "isStretch": False,
        "isCustom": 0,
        "requiresBands": False,
        "isSingleWeight": 0,
        "listGroup": 0,
        "mode": 0,
        "totalSets": 2,
        "totalRealSets": 2,
        "firstImage": "0194-1",
        "sixthImage": "",
        "dateAdded": "2026-10-05T11:30:15",
    }
    assert "note" not in push and "identifier" not in push and "routineID" not in push
    assert push["sets"] == [
        {
            "index": 0,
            "type": 0,
            "firstValue": 1,
            "secondValue": 10,
            "thirdValue": 0,
            "uniqueHashID": 26100500000003,
            "dateAdded": "2026-10-05T11:30:15",
        },
        {
            "index": 1,
            "type": 0,
            "firstValue": 1,
            "secondValue": 8.5,
            "thirdValue": 12.5,
            "uniqueHashID": 26100500000004,
            "dateAdded": "2026-10-05T11:30:15",
        },
    ]
    assert band["requiresBands"] is True
    assert band["note"] == "per arm"
    assert band["pause"] == 0
    assert [(s["secondValue"], s["thirdValue"]) for s in band["sets"]] == [(10, 0)]


def test_add_routines_form_is_utf8_json() -> None:
    form = add_routines_form([{"name": "FB-A — Return W1"}], timezone="Europe/Madrid")
    assert form["timezone"] == "Europe/Madrid"
    assert json.loads(form["routines"]) == [{"name": "FB-A — Return W1"}]
    assert "—" in form["routines"]  # not —-escaped


def test_archive_forms() -> None:
    assert archive_form(3681004) == {"routinesIDs": "3681004"}
    assert unarchive_form(3681004) == {"routineID": "3681004"}


def test_mint_unique_hashid_shape() -> None:
    h = mint_unique_hashid(datetime(2026, 10, 5, tzinfo=UTC))
    assert str(h).startswith("261005") and len(str(h)) == 14
