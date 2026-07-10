"""Spec 02 write tools: plan/apply pairs against a temp copy of the live DB.

Every apply must leave the parent routine marked pending (ZHASSYNCED=0 +
ZDATELASTUPDATEDBYAI stamped) — the seed helper deliberately resets the flag
to "synced" so the assertion is meaningful.
"""

from __future__ import annotations

import sqlite3

import pytest

from smartgym_mcp import db, writes
from smartgym_mcp.config import Config
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.writes import WriteValidationError

SEED_NAME = "ZZ-WRITE-TEST"


def _connect(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_routine(cfg: Config) -> tuple[int, list[int]]:
    """Create ZZ-WRITE-TEST (3 exercises: 2 explicit sets, 1 default, 1 default)
    in the temp DB, then reset its sync flag to 'synced' so mutations must flip
    it back. Returns (routine_pk, ue_pks ordered by ZINDEX)."""
    with db.open_rw_connection(cfg) as conn:
        names = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT ZNAME FROM ZEXERCISE WHERE ZNAME IS NOT NULL "
                "ORDER BY Z_PK LIMIT 3"
            )
        ]
        assert len(names) == 3
        spec = RoutineSpec(
            name=SEED_NAME,
            note="seed routine note",
            exercises=[
                ExerciseSpec(
                    exercise=names[0],
                    rest_seconds=60,
                    sets=[SetSpec(reps=10), SetSpec(reps=8, weight_kg=20)],
                ),
                ExerciseSpec(exercise=names[1]),
                ExerciseSpec(exercise=names[2], note="seed note"),
            ],
        )
        _, created = writes.apply_program(conn, [spec])
        routine_pk = created[0].z_pk
        ue_pks = [
            int(r[0])
            for r in conn.execute(
                "SELECT Z_PK FROM ZUNIQEXERCISE "
                "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL ORDER BY ZINDEX",
                (routine_pk,),
            )
        ]
    check = _connect(cfg)
    try:
        check.execute(
            "UPDATE ZROUTINE SET ZHASSYNCED = 1, ZDATELASTUPDATEDBYAI = NULL WHERE Z_PK = ?",
            (routine_pk,),
        )
        check.commit()
    finally:
        check.close()
    return routine_pk, ue_pks


def _assert_pending(cfg: Config, routine_pk: int) -> None:
    check = _connect(cfg)
    try:
        row = check.execute(
            "SELECT ZHASSYNCED, ZDATELASTUPDATEDBYAI FROM ZROUTINE WHERE Z_PK = ?",
            (routine_pk,),
        ).fetchone()
        assert row[0] == 0 and row[1] is not None
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# add_exercise
# --------------------------------------------------------------------------- #
def test_add_exercise_appends_by_default(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        plan, new_ue = writes.apply_add_exercise(conn, routine_pk, "Push Up", rest_seconds=45)
    assert plan.index == len(ue_pks)
    assert plan.shifted == 0
    assert plan.set_rows == 1
    assert any("no sets given" in w for w in plan.warnings)

    check = _connect(temp_db_cfg)
    try:
        row = check.execute(
            "SELECT ZROUTINE, ZINDEX, ZPAUSE FROM ZUNIQEXERCISE WHERE Z_PK = ?", (new_ue,)
        ).fetchone()
        assert tuple(row) == (routine_pk, len(ue_pks), 45)
        sets = check.execute(
            "SELECT COUNT(*) FROM ZVALUES WHERE ZEXERCISE = ? AND ZDATELOGGED IS NULL",
            (new_ue,),
        ).fetchone()[0]
        assert sets == 1
        # The add must announce itself to the sync engine (verified live: without
        # this queue row the app's push deletes the exercise again).
        queue = check.execute(
            "SELECT q.ZSTATE, q.ZROUTINEID FROM ZEXERCISESTATEQUEUE q "
            "JOIN ZUNIQEXERCISE ue ON q.ZEXERCISEHASHID = ue.ZUNIQUEHASHID "
            "WHERE ue.Z_PK = ?",
            (new_ue,),
        ).fetchall()
        routine_identifier = check.execute(
            "SELECT ZIDENTIFIER FROM ZROUTINE WHERE Z_PK = ?", (routine_pk,)
        ).fetchone()[0]
        assert len(queue) == 1
        assert tuple(queue[0]) == (1, routine_identifier)
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_add_exercise_at_index_shifts_later_rows(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        plan, new_ue = writes.apply_add_exercise(
            conn, routine_pk, "Push Up", index=1, sets=[SetSpec(reps=12), SetSpec(reps=12)]
        )
    assert plan.index == 1
    assert plan.shifted == 2
    assert plan.set_rows == 2

    check = _connect(temp_db_cfg)
    try:
        indices = {
            int(r[0]): int(r[1])
            for r in check.execute(
                "SELECT Z_PK, ZINDEX FROM ZUNIQEXERCISE "
                "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL",
                (routine_pk,),
            )
        }
        assert indices[new_ue] == 1
        assert indices[ue_pks[0]] == 0
        assert indices[ue_pks[1]] == 2
        assert indices[ue_pks[2]] == 3
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_add_exercise_rejects_out_of_range_index(temp_db_cfg: Config) -> None:
    routine_pk, _ = _seed_routine(temp_db_cfg)
    with (
        pytest.raises(WriteValidationError, match="index must be between"),
        db.open_rw_connection(temp_db_cfg) as conn,
    ):
        writes.apply_add_exercise(conn, routine_pk, "Push Up", index=99)


def test_add_exercise_rejects_unresolvable_exercise(temp_db_cfg: Config) -> None:
    routine_pk, _ = _seed_routine(temp_db_cfg)
    with (
        pytest.raises(WriteValidationError, match="Closest:"),
        db.open_rw_connection(temp_db_cfg) as conn,
    ):
        writes.apply_add_exercise(conn, routine_pk, "Quantum Flux Curl")


# --------------------------------------------------------------------------- #
# update_exercise
# --------------------------------------------------------------------------- #
def test_update_exercise_updates_only_passed_fields(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        plan = writes.apply_update_exercise(conn, ue_pks[0], note="new note", rest_seconds=120)
    assert {c.field for c in plan.changes} == {"note", "rest_seconds"}
    assert plan.routine.z_pk == routine_pk

    check = _connect(temp_db_cfg)
    try:
        row = check.execute(
            "SELECT ZNOTE, ZPAUSE, ZINDEX FROM ZUNIQEXERCISE WHERE Z_PK = ?", (ue_pks[0],)
        ).fetchone()
        assert tuple(row) == ("new note", 120, 0)  # index untouched
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_update_exercise_empty_note_clears_field(temp_db_cfg: Config) -> None:
    _, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        writes.apply_update_exercise(conn, ue_pks[2], note="")  # seeded with "seed note"
    check = _connect(temp_db_cfg)
    try:
        note = check.execute(
            "SELECT ZNOTE FROM ZUNIQEXERCISE WHERE Z_PK = ?", (ue_pks[2],)
        ).fetchone()[0]
        assert note is None
    finally:
        check.close()


def test_update_exercise_rejects_no_fields(temp_db_cfg: Config) -> None:
    _, ue_pks = _seed_routine(temp_db_cfg)
    with (
        pytest.raises(WriteValidationError, match="Nothing to update"),
        db.open_rw_connection(temp_db_cfg) as conn,
    ):
        writes.apply_update_exercise(conn, ue_pks[0])


def test_update_exercise_rejects_unknown_and_removed_pk(temp_db_cfg: Config) -> None:
    _, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        with pytest.raises(WriteValidationError, match="No active exercise"):
            writes.plan_update_exercise(conn, 99_999_999, note="x")
        writes.apply_remove_exercise(conn, ue_pks[1])
        with pytest.raises(WriteValidationError, match="No active exercise"):
            writes.plan_update_exercise(conn, ue_pks[1], note="x")


# --------------------------------------------------------------------------- #
# reorder_routine
# --------------------------------------------------------------------------- #
def test_reorder_routine_rewrites_indices(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    new_order = list(reversed(ue_pks))
    with db.open_rw_connection(temp_db_cfg) as conn:
        plan = writes.apply_reorder_routine(conn, routine_pk, new_order)
    assert [e.ue_pk for e in plan.order] == new_order
    assert [e.new_index for e in plan.order] == [0, 1, 2]

    check = _connect(temp_db_cfg)
    try:
        rows = check.execute(
            "SELECT Z_PK FROM ZUNIQEXERCISE "
            "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL ORDER BY ZINDEX",
            (routine_pk,),
        ).fetchall()
        assert [int(r[0]) for r in rows] == new_order
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_reorder_routine_rejects_set_mismatches(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        foreign = int(
            conn.execute(
                "SELECT Z_PK FROM ZUNIQEXERCISE "
                "WHERE ZROUTINE != ? AND ZDATEREMOVED IS NULL LIMIT 1",
                (routine_pk,),
            ).fetchone()[0]
        )
        with pytest.raises(WriteValidationError, match="Missing active exercise"):
            writes.plan_reorder_routine(conn, routine_pk, ue_pks[:2])
        with pytest.raises(WriteValidationError, match="more than once"):
            writes.plan_reorder_routine(conn, routine_pk, [ue_pks[0], *ue_pks])
        with pytest.raises(WriteValidationError, match="not an active exercise"):
            writes.plan_reorder_routine(conn, routine_pk, [foreign, *ue_pks[1:]])


# --------------------------------------------------------------------------- #
# remove_exercise
# --------------------------------------------------------------------------- #
def test_remove_exercise_soft_deletes_and_keeps_logged_sets(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    target = ue_pks[0]  # seeded with 2 template sets
    with db.open_rw_connection(temp_db_cfg) as conn:
        # Simulate one logged set on the target exercise — it must survive.
        ctx = writes.build_write_context(conn)
        logged_pk = writes.insert_set(conn, ctx, target, 9, SetSpec(reps=5, weight_kg=50))
        conn.execute(
            "UPDATE ZVALUES SET ZDATELOGGED = ?, ZPRECISEDATELOGGED = ? WHERE Z_PK = ?",
            (ctx.now_cd, ctx.now_cd, logged_pk),
        )
        plan = writes.apply_remove_exercise(conn, target)
    assert plan.template_sets_removed == 2

    check = _connect(temp_db_cfg)
    try:
        ue = check.execute(
            "SELECT ZDATEREMOVED, ZPRECISEDATEREMOVED FROM ZUNIQEXERCISE WHERE Z_PK = ?",
            (target,),
        ).fetchone()
        assert ue[0] is not None and ue[1] is not None
        removed_templates = check.execute(
            "SELECT COUNT(*) FROM ZVALUES WHERE ZEXERCISE = ? AND ZDATELOGGED IS NULL "
            "AND ZDATEREMOVED IS NOT NULL AND ZPRECISEDATEREMOVED IS NOT NULL",
            (target,),
        ).fetchone()[0]
        assert removed_templates == 2
        logged = check.execute(
            "SELECT ZDATEREMOVED FROM ZVALUES WHERE Z_PK = ?", (logged_pk,)
        ).fetchone()
        assert logged[0] is None
        # ZINDEX gaps are intentionally left as-is.
        remaining = check.execute(
            "SELECT ZINDEX FROM ZUNIQEXERCISE "
            "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL ORDER BY ZINDEX",
            (routine_pk,),
        ).fetchall()
        assert [int(r[0]) for r in remaining] == [1, 2]
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_remove_exercise_rejects_already_removed(temp_db_cfg: Config) -> None:
    _, ue_pks = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        writes.apply_remove_exercise(conn, ue_pks[0])
        with pytest.raises(WriteValidationError, match="No active exercise"):
            writes.apply_remove_exercise(conn, ue_pks[0])


# --------------------------------------------------------------------------- #
# update_routine
# --------------------------------------------------------------------------- #
def test_update_routine_edits_fields(temp_db_cfg: Config) -> None:
    routine_pk, _ = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        plan = writes.apply_update_routine(
            conn, routine_pk, name="ZZ-WRITE-TEST-2", goal="Hypertrophy", days=""
        )
    assert {c.field for c in plan.changes} == {"name", "goal", "days"}

    check = _connect(temp_db_cfg)
    try:
        row = check.execute(
            "SELECT ZNAME, ZGOAL, ZDAYS, ZNOTE FROM ZROUTINE WHERE Z_PK = ?", (routine_pk,)
        ).fetchone()
        assert tuple(row) == ("ZZ-WRITE-TEST-2", "Hypertrophy", None, "seed routine note")
    finally:
        check.close()
    _assert_pending(temp_db_cfg, routine_pk)


def test_update_routine_rejects_name_collision(temp_db_cfg: Config) -> None:
    routine_pk, _ = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        other = str(
            conn.execute(
                "SELECT ZNAME FROM ZROUTINE WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 "
                "AND Z_PK != ? LIMIT 1",
                (routine_pk,),
            ).fetchone()[0]
        )
        with pytest.raises(WriteValidationError, match="already exists"):
            writes.plan_update_routine(conn, routine_pk, name=other.upper())
        # Renaming to its own name is not a collision.
        plan = writes.plan_update_routine(conn, routine_pk, name=SEED_NAME)
        assert plan.changes[0].new == SEED_NAME


def test_update_routine_rejects_no_fields_and_empty_name(temp_db_cfg: Config) -> None:
    routine_pk, _ = _seed_routine(temp_db_cfg)
    with db.open_rw_connection(temp_db_cfg) as conn:
        with pytest.raises(WriteValidationError, match="Nothing to update"):
            writes.plan_update_routine(conn, routine_pk)
        with pytest.raises(WriteValidationError, match="non-empty"):
            writes.plan_update_routine(conn, routine_pk, name="   ")


# --------------------------------------------------------------------------- #
# Plans serve dry_run on a read-only connection
# --------------------------------------------------------------------------- #
def test_plans_work_on_read_only_connection(temp_db_cfg: Config) -> None:
    routine_pk, ue_pks = _seed_routine(temp_db_cfg)
    ro = db.open_ro_connection(temp_db_cfg.db_path)
    try:
        add = writes.plan_add_exercise(ro, routine_pk, "Push Up")
        assert add.index == len(ue_pks)
        update = writes.plan_update_exercise(ro, ue_pks[0], rest_seconds=90)
        assert update.changes[0].field == "rest_seconds"
        reorder = writes.plan_reorder_routine(ro, routine_pk, list(reversed(ue_pks)))
        assert len(reorder.order) == len(ue_pks)
        remove = writes.plan_remove_exercise(ro, ue_pks[0])
        assert remove.template_sets_removed == 2
        routine = writes.plan_update_routine(ro, routine_pk, goal="Strength")
        assert routine.changes[0].field == "goal"
    finally:
        ro.close()
