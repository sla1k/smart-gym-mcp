"""Read-tool checks against the live DB (read-only, safe while SmartGym is open)."""

from __future__ import annotations

import json

import pytest

from smartgym_mcp import catalog, db, queries
from smartgym_mcp.config import load_config


@pytest.fixture
def ro(live_cfg):
    conn = db.open_ro_connection(live_cfg.db_path)
    yield conn
    conn.close()


def test_list_routines(ro):
    res = queries.list_routines(ro, include_hidden=False)
    assert res.count == len(res.routines) >= 3
    assert all(r.z_pk and r.name and not r.hidden for r in res.routines)
    incl = queries.list_routines(ro, include_hidden=True)
    assert incl.count >= res.count


def test_resolve_routine_by_pk_name_and_substring(ro):
    first = queries.list_routines(ro).routines[0]
    assert queries.resolve_routine(ro, first.z_pk) == first.z_pk
    assert queries.resolve_routine(ro, first.name) == first.z_pk
    # leading token of the name resolves via substring match — either uniquely to this
    # routine, or as an explicit ambiguity when several live routines share the prefix
    token = first.name.split(" ")[0]
    try:
        assert queries.resolve_routine(ro, token) == first.z_pk
    except queries.AmbiguousRoutine as exc:
        assert first.name in str(exc)


def test_resolve_routine_not_found(ro):
    with pytest.raises(queries.RoutineNotFound):
        queries.resolve_routine(ro, "definitely-no-such-routine-xyz")


def test_get_routine_shape(ro):
    name = queries.list_routines(ro).routines[0].name
    detail = queries.get_routine(ro, name, history_depth=3)
    assert detail.exercises, "routine should have exercises"
    # exercises ordered by index
    idxs = [e.index for e in detail.exercises]
    assert idxs == sorted(idxs)
    for e in detail.exercises:
        assert len(e.sessions) <= 3
        for sess in e.sessions:
            assert sess.sets and all(s.set_no is not None for s in sess.sets)
        if e.sessions:
            latest = e.sessions[0].sets
            assert e.top_set in latest
            assert e.total_volume == pytest.approx(sum(s.reps * s.weight_kg for s in latest))


def test_workout_history_dedup_and_pagination(ro):
    res = queries.get_workout_history(ro, days=30, limit=2, offset=0)
    assert res.count <= 2 and res.total >= res.count
    pks = [s.workout_pk for s in res.sessions]
    assert len(pks) == len(set(pks)), "sessions must be deduped by workout"
    if res.total > 2:
        assert res.has_more and res.next_offset == 2
        page2 = queries.get_workout_history(ro, days=30, limit=2, offset=2)
        assert set(p.workout_pk for p in page2.sessions).isdisjoint(pks)


def test_workout_history_rejects_bad_range(ro):
    with pytest.raises(ValueError):
        queries.get_workout_history(ro, days=5)


def test_workout_history_explicit_date_range(ro):
    # Bound every returned session to an explicit inclusive window.
    res = queries.get_workout_history(
        ro, date_from="2026-05-01", date_to="2026-05-20", limit=100
    )
    for s in res.sessions:
        assert "2026-05-01" <= s.date <= "2026-05-20"
    # date_to is inclusive (strictly-before-next-midnight under the hood).
    only_end = queries.get_workout_history(
        ro, date_from="2026-05-20", date_to="2026-05-20", limit=100
    )
    assert all(s.date == "2026-05-20" for s in only_end.sessions)


def test_get_equipment_owned(ro):
    res = queries.get_equipment(ro, owned_only=True)
    assert res.count == len(res.equipment)
    assert all(e.owned for e in res.equipment)


def test_catalog_resources_are_valid_json(live_cfg):
    cfg = load_config()
    for name in catalog.CATALOG_FILES:
        json.loads(catalog.read_catalog(cfg, name))  # raises if malformed
