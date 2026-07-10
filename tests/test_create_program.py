"""Spec 03 create-program: matching, validation, and insert against a temp copy."""

from __future__ import annotations

import sqlite3

import pytest

from smartgym_mcp import db, writes
from smartgym_mcp.config import Config
from smartgym_mcp.matching import ExerciseCatalog, UnresolvedExercise
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.writes import ProgramValidationError


@pytest.fixture
def ro_conn(live_cfg: Config):
    conn = db.open_ro_connection(live_cfg.db_path)
    yield conn
    conn.close()


def _spec(name: str = "ZZ-TEST-PROGRAM-A") -> RoutineSpec:
    return RoutineSpec(
        name=name,
        goal="Test goal",
        note="test program",
        exercises=[
            ExerciseSpec(
                exercise="Push Up",
                rest_seconds=90,
                sets=[SetSpec(reps=10), SetSpec(reps=8, weight_kg=0)],
            ),
            ExerciseSpec(exercise="Plank"),  # no sets → default, warned
        ],
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def test_exact_match_case_insensitive(ro_conn: sqlite3.Connection) -> None:
    catalog = ExerciseCatalog.load(ro_conn)
    res = catalog.resolve("push up")
    assert res.resolved_name == "Push Up"
    assert res.confidence == 1.0
    assert not res.fuzzy


def test_numeric_ref_resolves_by_pk(ro_conn: sqlite3.Connection) -> None:
    catalog = ExerciseCatalog.load(ro_conn)
    exact = catalog.resolve("Push Up")
    res = catalog.resolve(str(exact.z_pk))
    assert res.resolved_name == "Push Up"


def test_alias_fuzzy_match(ro_conn: sqlite3.Connection) -> None:
    catalog = ExerciseCatalog.load(ro_conn)
    res = catalog.resolve("DB Bench Press")
    assert "Dumbbell" in res.resolved_name and "Bench Press" in res.resolved_name
    assert res.fuzzy


def test_unresolvable_lists_candidates(ro_conn: sqlite3.Connection) -> None:
    catalog = ExerciseCatalog.load(ro_conn)
    with pytest.raises(UnresolvedExercise, match="Closest:"):
        catalog.resolve("Quantum Flux Curl")


# --------------------------------------------------------------------------- #
# Validation (plan)
# --------------------------------------------------------------------------- #
def test_plan_rejects_active_name_collision(ro_conn: sqlite3.Connection) -> None:
    existing = ro_conn.execute(
        "SELECT ZNAME FROM ZROUTINE WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(ProgramValidationError, match="already exists"):
        writes.plan_program(ro_conn, [_spec(name=existing)])


def test_plan_rejects_duplicate_names_in_program(ro_conn: sqlite3.Connection) -> None:
    with pytest.raises(ProgramValidationError, match="Duplicate routine name"):
        writes.plan_program(ro_conn, [_spec("ZZ-DUP"), _spec("zz-dup")])


def test_plan_rejects_whole_program_on_one_bad_exercise(ro_conn: sqlite3.Connection) -> None:
    bad = _spec()
    bad.exercises.append(ExerciseSpec(exercise="Quantum Flux Curl"))
    with pytest.raises(ProgramValidationError, match="nothing was written"):
        writes.plan_program(ro_conn, [bad])


def test_plan_reports_default_set_warning(ro_conn: sqlite3.Connection) -> None:
    plans = writes.plan_program(ro_conn, [_spec()])
    assert len(plans) == 1
    assert plans[0].set_rows == 3  # 2 explicit + 1 default
    assert any("no sets given" in w for w in plans[0].warnings)


# --------------------------------------------------------------------------- #
# Apply (temp copy of the live DB — never the real one)
# --------------------------------------------------------------------------- #
def test_apply_program_inserts_and_marks_pending(temp_db_cfg: Config) -> None:
    program = [_spec("ZZ-TEST-PROGRAM-A"), _spec("ZZ-TEST-PROGRAM-B")]
    with db.open_rw_connection(temp_db_cfg) as conn:
        plans, created = writes.apply_program(conn, program)

    assert [c.name for c in created] == ["ZZ-TEST-PROGRAM-A", "ZZ-TEST-PROGRAM-B"]
    check = sqlite3.connect(f"file:{temp_db_cfg.db_path}?mode=ro", uri=True)
    try:
        for c, plan in zip(created, plans, strict=True):
            row = check.execute(
                "SELECT ZHASSYNCED, ZDATELASTUPDATEDBYAI, ZUNIQUEHASHID, ZNUMBER "
                "FROM ZROUTINE WHERE Z_PK = ?",
                (c.z_pk,),
            ).fetchone()
            assert row[0] == 0  # pending → app pushes on next launch
            assert row[1] is not None
            assert len(str(row[2])) == 14
            joined = check.execute(
                "SELECT COUNT(DISTINCT ue.Z_PK), COUNT(v.Z_PK) FROM ZUNIQEXERCISE ue "
                "JOIN ZVALUES v ON v.ZEXERCISE = ue.Z_PK "
                "WHERE ue.ZROUTINE = ? AND v.ZDATELOGGED IS NULL",
                (c.z_pk,),
            ).fetchone()
            assert joined[0] == plan.exercise_rows
            assert joined[1] == plan.set_rows
        # Allocators must cover the created PKs.
        z_max = check.execute(
            "SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Routine'"
        ).fetchone()[0]
        assert z_max >= max(c.z_pk for c in created)
    finally:
        check.close()


def test_granular_add_exercise_to_existing_routine(temp_db_cfg: Config) -> None:
    """The spec 02 add_exercise flow, built from the granular layer:
    insert_exercise + insert_set into an EXISTING routine + mark_routine_pending."""
    with db.open_rw_connection(temp_db_cfg) as conn:
        routine_pk = int(
            conn.execute(
                "SELECT Z_PK FROM ZROUTINE WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 LIMIT 1"
            ).fetchone()[0]
        )
        ctx = writes.build_write_context(conn)
        catalog = ExerciseCatalog.load(conn)
        res = catalog.resolve("Push Up")
        next_index = int(
            conn.execute(
                "SELECT COALESCE(MAX(ZINDEX), -1) + 1 FROM ZUNIQEXERCISE "
                "WHERE ZROUTINE = ? AND ZDATEREMOVED IS NULL",
                (routine_pk,),
            ).fetchone()[0]
        )
        ue_pk = writes.insert_exercise(
            conn, ctx, routine_pk, next_index, res.z_pk, rest_seconds=45
        )
        writes.insert_set(conn, ctx, ue_pk, 0, SetSpec(reps=15))
        writes.mark_routine_pending(conn, routine_pk, ctx.now_cd)

    check = sqlite3.connect(f"file:{temp_db_cfg.db_path}?mode=ro", uri=True)
    try:
        ue = check.execute(
            "SELECT ZROUTINE, ZINDEX, ZPAUSE FROM ZUNIQEXERCISE WHERE Z_PK = ?", (ue_pk,)
        ).fetchone()
        assert ue == (routine_pk, next_index, 45)
        sets = check.execute(
            "SELECT COUNT(*) FROM ZVALUES WHERE ZEXERCISE = ? AND ZDATELOGGED IS NULL",
            (ue_pk,),
        ).fetchone()[0]
        assert sets == 1
        pending = check.execute(
            "SELECT ZHASSYNCED, ZDATELASTUPDATEDBYAI FROM ZROUTINE WHERE Z_PK = ?",
            (routine_pk,),
        ).fetchone()
        assert pending[0] == 0 and pending[1] is not None
    finally:
        check.close()


def test_apply_is_rejected_on_rerun_collision(temp_db_cfg: Config) -> None:
    program = [_spec("ZZ-TEST-RERUN")]
    with db.open_rw_connection(temp_db_cfg) as conn:
        writes.apply_program(conn, program)
    with (
        pytest.raises(ProgramValidationError, match="already exists"),
        db.open_rw_connection(temp_db_cfg) as conn,
    ):
        writes.apply_program(conn, program)
