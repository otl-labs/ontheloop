"""Real Postgres 16 proof of the owner role per catalog (Decision 230 cl.2 and cl.4).

pytestmark integration: excluded from --pre and the full tier, run before merge by the implementer (real-engine CI
gating is rec-4071). It runs the REAL verbs (provision_catalog_owner, provision_telemetry_catalog,
provision_telemetry_login) on the shared stage (tests/fixtures/ducklake_pg_stage.py), with and without
pg_read_all_data, so adoption, release, the maintainer's session role, the live sitting order and the lock timeout are
proven on a Neon-shaped non-superuser owner login."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
psycopg2 = pytest.importorskip("psycopg2", reason="env: psycopg2 is not installed in the fast tier")
pytest.importorskip("yaml", reason="env: yaml is not installed in the fast tier")

import src.lambdas.ducklake_maintenance_smoke.handler as smoke_handler  # noqa: E402
import src.lambdas.ducklake_maintenance_smoke.telemetry_actions as smoke_ta  # noqa: E402
from src.common import ducklake_runtime as rt  # noqa: E402
from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError, load_field_semantics  # noqa: E402
from src.common.ducklake_tables import create_scd2_tables  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from src.lambdas.ducklake_maintenance import catalog_logins as cl  # noqa: E402
from tests.fixtures import ducklake_pg_stage as pg_stage  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
OWNER, SMOKE, TELEMETRY = pg_stage.OWNER, pg_stage.SMOKE, pg_stage.TELEMETRY
SMOKE_OWNER = "ducklake_smoke_owner"
MAINTAINER = "ducklake_smoke_maintainer"
_ALIAS = rt.CATALOG_ALIAS


def _fixture(bare: bool) -> Any:
    @pytest.fixture(params=["read_all", "no_read_all"])
    def stage(
        request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _allow_network_for_integration: None
    ) -> Any:
        built = pg_stage.build_stage(tmp_path, monkeypatch, read_all=request.param == "read_all", bare=bare)
        yield built
        built.close()

    return stage


stage = _fixture(bare=False)
bare = _fixture(bare=True)


def _owners(s: Any, schema: str) -> set[str]:
    rows = s.sql(
        "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'S', 'v', 'm')"
    )
    return {r[0] for r in rows}


def _schema_owner(s: Any, schema: str) -> str:
    return s.sql(f"SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = '{schema}'")[0][0]


def _report(con: Any, schema: str, logins: tuple[str, ...] = ("ducklake_telemetry_writer",)) -> prov.OwnershipReport:
    return prov.ownership_report(con, meta_schema=schema, scoped_logins=logins)


def _create_tables(con: Any, semantics: dict[str, Any] = SEM) -> prov.CreateResult:
    return run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=semantics)).result


def _legacy_smoke(s: Any) -> Any:
    """The state before adoption: ducklake_smoke and its tables created by the shared owner login."""
    s.sql(f"CREATE SCHEMA {SMOKE}")
    owner = s.duck(OWNER, SMOKE)
    owner.execute(f"CREATE TABLE {_ALIAS}.warm(a INT)")
    _create_tables(owner)
    return owner


def test_owner_role_adopts_an_existing_smoke_schema_and_owner_paths_still_work(bare: Any) -> None:
    owner = _legacy_smoke(bare)
    assert _owners(bare, SMOKE) == {OWNER} and _schema_owner(bare, SMOKE) == OWNER
    out = bare.adopt(SMOKE)
    assert out["schema_adopted"] is True and out["schema_created"] is False and out["relations_reowned"] > 0
    assert out["member_inherit"] is True and out["member_set"] is True
    assert _owners(bare, SMOKE) == {SMOKE_OWNER} and _schema_owner(bare, SMOKE) == SMOKE_OWNER
    again = bare.adopt(SMOKE)
    assert again["schema_adopted"] is False and again["relations_reowned"] == 0

    pg_dump = shutil.which("pg_dump") or next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_dump"))), None)
    assert pg_dump, "pg_dump not found"
    dump = subprocess.run(
        [str(pg_dump), "--format=custom", "-h", bare.params.get("host", "127.0.0.1"), "-p", bare.params.get("port", "5432"),
         "-U", OWNER, "-f", str(bare.tmp / "dump"), OWNER],
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert dump.returncode == 0, dump.stderr.decode()[-300:]
    owner.close()
    owner = bare.duck(OWNER, SMOKE)
    owner.execute(f"CALL ducklake_flush_inlined_data('{_ALIAS}')").fetchall()
    owner.execute(f"CALL ducklake_merge_adjacent_files('{_ALIAS}')").fetchall()
    bare.login("ducklake_telemetry_writer")
    writer = bare.duck("ducklake_telemetry_writer", SMOKE)
    run_telemetry_txn(writer, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM))
    assert _report(owner, SMOKE).foreign_owned == 0


def test_maintainer_session_role_is_the_smoke_owner_role(stage: Any) -> None:
    conn = psycopg2.connect(**{**stage.params, "dbname": OWNER, "user": MAINTAINER})
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, session_user")
            assert cur.fetchone() == (SMOKE_OWNER, MAINTAINER)
    finally:
        conn.close()
    out = stage.login(MAINTAINER)
    assert out["scope"] == dict(cl.LOGINS[MAINTAINER].expected_scope)
    assert out["owner_is_neon_superuser_member"] is True


def test_maintainer_force_recreate_after_adoption_leaves_no_foreign_owned_relation(stage: Any) -> None:
    con = stage.duck(MAINTAINER, SMOKE)
    create_scd2_tables(con, force_recreate=True)
    report = _report(con, SMOKE)
    assert report.foreign_owned == 0 and report.schema_owner == SMOKE_OWNER and report.schema_owner_held is True
    assert _owners(stage, SMOKE) == {SMOKE_OWNER}


def test_smoke_maintainer_ddl_is_owned_by_the_smoke_owner_role(stage: Any) -> None:
    import copy  # noqa: PLC0415

    reduced = copy.deepcopy(SEM)
    del reduced["ops_tables"]["telemetry_sessions"]["columns"]["checkout_sha"]
    con = stage.duck(MAINTAINER, SMOKE)
    assert len(_create_tables(con, reduced).created) == 9
    added = _create_tables(con)
    assert "telemetry_sessions.checkout_sha" in added.added
    assert _report(con, SMOKE).foreign_owned == 0 and _owners(stage, SMOKE) == {SMOKE_OWNER}


def test_release_returns_an_adopted_smoke_schema_to_the_owner_login(stage: Any) -> None:
    _create_tables(stage.duck(MAINTAINER, SMOKE))
    assert _owners(stage, SMOKE) == {SMOKE_OWNER}
    out = stage.adopt(SMOKE, "release")
    assert out["schema_released"] is True and out["relations_reowned"] > 0
    assert _schema_owner(stage, SMOKE) == OWNER and _owners(stage, SMOKE) == {OWNER}
    owner = stage.duck(OWNER, SMOKE)
    create_scd2_tables(owner, force_recreate=True)
    report = _report(owner, SMOKE)
    assert report.foreign_owned == 0 and report.schema_owner == OWNER and report.schema_owner_is_current_user is True
    assert smoke_ta._check(report, "post")["schema_owner_held"] is True


def test_live_order_catalog_verb_runs_before_any_production_login_exists(bare: Any) -> None:
    out = bare.telemetry_catalog()
    assert out["ok"] is True and len(out["created"]) == 9 and out["added"] == []
    assert out["ownership"] == {
        "foreign_owned": 0,
        "schema_owner": "ducklake_telemetry_owner",
        "schema_owner_held": True,
        "schema_owner_is_current_user": False,
        "scoped_create_on_schema": False,
        "scoped_create_on_public": False,
    }
    assert out["counters"] == {"ops_tenants": 0, "ops_projects": 0}
    assert _owners(bare, TELEMETRY) == {"ducklake_telemetry_owner"}
    absent = bare.sql(
        "SELECT count(*) FROM pg_roles WHERE rolname IN ('ducklake_telemetry_writer_prod', 'ducklake_telemetry_reader')"
    )
    assert absent[0][0] == 0
    again = bare.telemetry_catalog()
    assert again["created"] == [] and again["ownership"]["foreign_owned"] == 0


def test_live_order_production_logins_provision_before_the_smoke_owner_exists(bare: Any) -> None:
    bare.telemetry_catalog()
    assert bare.sql(f"SELECT count(*) FROM pg_roles WHERE rolname = '{SMOKE_OWNER}'")[0][0] == 0
    for name in ("ducklake_telemetry_writer_prod", "ducklake_telemetry_reader"):
        out = bare.login(name)
        assert out["scope"] == dict(cl.LOGINS[name].expected_scope), name
        assert out["metadata_readable"] is True
    assert bare.login("ducklake_telemetry_reader")["read_only_attach"] == "accepted"
    report = _report(bare.duck(OWNER, TELEMETRY), TELEMETRY, ("ducklake_telemetry_writer_prod", "ducklake_telemetry_reader"))
    assert report.scoped_create_on_schema is False and report.foreign_owned == 0


@pytest.mark.parametrize("first", ["sitting1_first", "sitting2_first"])
def test_login_scopes_are_identical_in_either_sitting_order(bare: Any, first: str) -> None:
    _legacy_smoke(bare)

    def sitting1() -> dict[str, Any]:
        bare.telemetry_catalog()
        return {name: bare.login(name) for name in ("ducklake_telemetry_writer_prod", "ducklake_telemetry_reader")}

    def sitting2() -> dict[str, Any]:
        bare.adopt(SMOKE)
        return {name: bare.login(name) for name in (MAINTAINER, "ducklake_telemetry_writer")}

    results: dict[str, Any] = {}
    for step in (sitting1, sitting2) if first == "sitting1_first" else (sitting2, sitting1):
        results.update(step())
    assert set(results) == set(cl.LOGINS)
    for name, out in results.items():
        assert out["scope"] == dict(cl.LOGINS[name].expected_scope), (first, name)


def test_owner_sql_waits_at_most_its_lock_timeout(stage: Any) -> None:
    stage.sql(f"CREATE TABLE {SMOKE}.plain_probe(a int)")
    assert OWNER in _owners(stage, SMOKE)
    holder = psycopg2.connect(**{**stage.params, "dbname": OWNER})
    try:
        with holder.cursor() as cur:
            cur.execute(f"LOCK TABLE {SMOKE}.plain_probe IN ACCESS SHARE MODE")
        start = time.monotonic()
        with pytest.raises(DuckLakeRuntimeError) as err:
            stage.adopt(SMOKE)
        waited = time.monotonic() - start
        assert "sqlstate=55P03" in str(err.value) and 4.0 < waited < 15.0
        assert OWNER in _owners(stage, SMOKE)
    finally:
        holder.rollback()
        holder.close()
    out = stage.adopt(SMOKE)
    assert out["relations_reowned"] > 0 and _owners(stage, SMOKE) == {SMOKE_OWNER}


def test_maintainer_peak_sessions_across_the_five_smoke_actions(stage: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    h = smoke_handler
    monkeypatch.setattr(h, "DSN_SECRET_ID", cl.LOGINS[MAINTAINER].secret_id)
    monkeypatch.setattr(h, "DATA_PATH", stage.data_paths[SMOKE])
    monkeypatch.setattr(h, "META_SCHEMA", SMOKE)
    monkeypatch.setattr(h, "EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(h, "_emit_maintenance_metric", lambda *a, **k: None)
    data_root = Path(stage.data_paths[SMOKE])

    def local_lister(data_path: str, **_: Any) -> dict[str, int]:
        return {str(p.relative_to(data_root)): p.stat().st_size for p in data_root.rglob("*") if p.is_file()}

    monkeypatch.setattr(h.maint, "_default_list_storage", local_lister)
    peaks: dict[str, int] = {}
    stop = threading.Event()
    current = {"action": ""}

    def sample() -> None:
        conn = psycopg2.connect(**{**stage.params, "dbname": OWNER})
        conn.autocommit = True
        with conn.cursor() as cur:
            while not stop.is_set():
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (MAINTAINER,))
                peaks[current["action"]] = max(peaks.get(current["action"], 0), cur.fetchone()[0])
                time.sleep(0.02)
        conn.close()

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    expected = {"merge": 200, "gc": 200, "hot_merge": 200, "create_telemetry_tables": 200, "breaker_probe": 500}
    try:
        for action, status in expected.items():
            current["action"] = action
            event = {"action": action, **({"force_recreate_tables": True} if action in ("merge", "gc", "hot_merge") else {})}
            response = h.handler(event)
            assert response["statusCode"] == status, (action, response["body"][:300])
    finally:
        stop.set()
        sampler.join(timeout=5)
    peak = max(peaks.values())
    print(f"MAINTAINER_PEAK_SESSIONS peak={peak} per_action={peaks}")
    assert peak >= 1
    assert cl.LOGINS[MAINTAINER].connection_limit >= max(2, 2 * peak), f"raise the limit: 2 x peak {peak}"
