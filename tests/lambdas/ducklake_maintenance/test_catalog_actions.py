"""MIRROR for src/lambdas/ducklake_maintenance/catalog_actions.py -- the two catalog owner verbs (Decision 230 cl.4).

psycopg2 is replaced by a local stub (the fast CI tier installs no native driver), the owner DSN fetch and the DuckDB
connection are patched at the boundary, and fixture hosts are composed at runtime so no host-shaped literal lands in the tree.
"""

from __future__ import annotations

import json
import secrets
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

import src.lambdas.ducklake_maintenance.catalog_actions as ca
from src.common import ducklake_runtime as rt
from src.common import ducklake_telemetry_provision as prov
from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.lambdas.ducklake_maintenance import catalog_logins as cl

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_HOST = "ep-" + secrets.token_hex(6) + ".example.invalid"
_DSN = {"host": _HOST, "dbname": "ducklake_ops", "username": "ducklake_ops", "password": "o" + secrets.token_urlsafe(18)}
_TELEMETRY = {"confirm": "ducklake_telemetry", "meta_schema": "ducklake_telemetry", "data_path": rt.TELEMETRY_PROD_DATA_PATH}


class Error(Exception):
    def __init__(self, message: str = "", pgcode: str | None = None) -> None:
        super().__init__(message)
        self.pgcode = pgcode


_PG = types.ModuleType("psycopg2")
_PG.Error = Error  # type: ignore[attr-defined]
_PG.connect = MagicMock()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _stub_psycopg2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg2", _PG)


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._row: tuple | None = None

    def execute(self, sql: str, params: object = None) -> None:
        self._conn.log.append((sql, params))
        if self._conn.fail_on and self._conn.fail_on in sql:
            raise self._conn.error
        if "nspowner::regrole::text" in sql:
            self._row = (self._conn.owner_before,) if self._conn.owner_before else None
        elif "SELECT count(*) FROM pg_class" in sql:
            self._row = (self._conn.to_reown,)
        elif "pg_auth_members" in sql:
            self._row = (True, True)
        else:
            self._row = None

    def fetchone(self) -> tuple | None:
        return self._row

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Conn:
    def __init__(self, owner_before: str | None = None, to_reown: int = 0) -> None:
        self.log: list[tuple[str, object]] = []
        self.owner_before, self.to_reown = owner_before, to_reown
        self.fail_on: str | None = None
        self.error: Exception = Error("boom", "XX000")
        self.committed = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed = True


class _DuckCon:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    def execute(self, sql: str, params: object = None) -> MagicMock:
        self.events.append(sql)
        return MagicMock()

    def close(self) -> None:
        self.closed = True


def _clean_report(**over: Any) -> prov.OwnershipReport:
    fields = {
        "foreign_owned": 0,
        "schema_owner_is_current_user": False,
        "scoped_create_on_schema": False,
        "scoped_create_on_public": False,
        "schema_owner_held": True,
        "schema_owner": "ducklake_telemetry_owner",
    }
    return prov.OwnershipReport(**{**fields, **over})


class _Harness:
    """Records the order of owner SQL runs, DuckDB opens, create, seed and report for provision_telemetry_catalog."""

    def __init__(self, report: prov.OwnershipReport | None = None) -> None:
        self.events: list[str] = []
        self.conns: list[_Conn] = []
        self.report = report or _clean_report()
        self.duck = _DuckCon(self.events)

    def connect(self, *_a: Any, **_k: Any) -> _Conn:
        conn = _Conn(owner_before="ducklake_ops" if self.conns else None)
        self.conns.append(conn)
        self.events.append("owner_sql")
        return conn

    def run(self, event: dict[str, Any]) -> dict[str, Any]:
        created = prov.CreateResult(("t_a", "t_b"), ("t_a",), ())
        seeded = prov.ProvisionResult(("t_a", "t_b"), {"ops_tenants": 0, "ops_projects": 0})
        report_calls: list[dict[str, Any]] = []

        def make_report(con: Any, **kwargs: Any) -> prov.OwnershipReport:
            self.events.append("report")
            report_calls.append(kwargs)
            return self.report

        self.report_calls = report_calls
        with (
            patch.object(ca.rt, "fetch_dsn", return_value=_DSN),
            patch("psycopg2.connect", side_effect=self.connect),
            patch.object(
                ca.rt, "open_connection", side_effect=lambda **kw: (self.events.append("open"), self.duck)[1]
            ) as opened,
            patch.object(
                ca.prov, "create_telemetry_tables", side_effect=lambda con, **kw: (self.events.append("create"), created)[1]
            ),
            patch.object(
                ca.prov, "provision_telemetry_catalog", side_effect=lambda con, **kw: (self.events.append("seed"), seeded)[1]
            ),
            patch.object(ca.prov, "ownership_report", side_effect=make_report),
        ):
            self.opened = opened
            return ca.action_provision_telemetry_catalog(event, None)


def test_provision_telemetry_catalog_refuses_any_other_catalog_or_data_path_before_any_connection():
    for bad in (
        {**_TELEMETRY, "meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"},
        {**_TELEMETRY, "meta_schema": "ducklake_ops", "confirm": "ducklake_ops"},
        {**_TELEMETRY, "data_path": "s3://agent-platform-data-lake/ducklake/"},
        {**_TELEMETRY, "data_path": rt.SMOKE_DATA_PATH},
        {**_TELEMETRY, "confirm": "ducklake_smoke"},
        {"confirm": "ducklake_telemetry"},
        {},
    ):
        with (
            patch.object(ca.rt, "fetch_dsn") as fetch,
            patch("psycopg2.connect") as connect,
            patch.object(ca.rt, "open_connection") as op,
        ):
            with pytest.raises(DuckLakeRuntimeError, match="provision_telemetry_catalog"):
                ca.action_provision_telemetry_catalog(bad, None)
        fetch.assert_not_called()
        connect.assert_not_called()
        op.assert_not_called()


def test_provision_telemetry_catalog_runs_owner_sql_create_seed_then_report_in_order():
    h = _Harness()
    out = h.run(_TELEMETRY)
    markers = {"owner_sql", "open", "create", "seed", "report"}
    assert [e for e in h.events if e in markers] == ["owner_sql", "open", "owner_sql", "create", "owner_sql", "seed", "report"]
    assert out["ok"] is True and out["created"] == ["t_a"] and out["tables"] == ["t_a", "t_b"] and out["added"] == []
    assert out["counters"] == {"ops_tenants": 0, "ops_projects": 0}
    assert out["owner_sql_sha256"] == cl.template_sha256("owner")
    assert out["ownership"]["schema_owner_is_current_user"] is False
    assert {"foreign_owned", "schema_owner", "schema_owner_held", "scoped_create_on_schema", "scoped_create_on_public"} <= set(
        out["ownership"]
    )
    assert h.opened.call_args.kwargs["data_path"] == rt.TELEMETRY_PROD_DATA_PATH
    assert h.opened.call_args.kwargs["meta_schema"] == "ducklake_telemetry"
    assert h.duck.closed is True
    assert h.duck.events.count("BEGIN TRANSACTION") == 2 and h.duck.events.count("COMMIT") == 2


def test_provision_telemetry_catalog_never_creates_the_stats_index():
    text = Path(ca.__file__).read_text(encoding="utf-8")
    for forbidden in ("catalog_stats_index", "CREATE INDEX", "index_actions", "merge_ops", "gc_ops"):
        assert forbidden not in text
    h = _Harness()
    h.run(_TELEMETRY)
    assert not any("INDEX" in e.upper() for e in h.events + h.duck.events)
    for conn in h.conns:
        assert not any(
            "INDEX" in sql.upper() for sql, _ in conn.log if "CREATE" in sql.upper() and "ducklake_file_column_stats" in sql
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"foreign_owned": 1},
        {"schema_owner": "ducklake_ops"},
        {"schema_owner_held": False},
        {"scoped_create_on_schema": True},
        {"scoped_create_on_public": True},
    ],
)
def test_provision_telemetry_catalog_refuses_unless_the_owner_role_owns_the_schema(bad):
    h = _Harness(report=_clean_report(**bad))
    with pytest.raises(DuckLakeRuntimeError, match="single-owner check failed"):
        h.run(_TELEMETRY)
    assert h.duck.closed is True


def test_provision_telemetry_catalog_reads_its_scoped_logins_through_to_regrole():
    h = _Harness()
    h.run(_TELEMETRY)
    kwargs = h.report_calls[0]
    assert kwargs["meta_schema"] == "ducklake_telemetry"
    assert kwargs["scoped_logins"] == ("ducklake_telemetry_writer_prod", "ducklake_telemetry_reader")
    con = types.SimpleNamespace(executed=[])
    con.execute = lambda sql, params=None: (
        con.executed.append(sql),
        MagicMock(fetchone=lambda: (0, True, False, False, True, "r")),
    )[1]
    prov.ownership_report(con, meta_schema="ducklake_telemetry", scoped_logins=kwargs["scoped_logins"])
    sql = con.executed[0]
    for login in kwargs["scoped_logins"]:
        assert f"to_regrole(''{login}'')" in sql


def _owner_run(event: dict[str, Any], conn: _Conn) -> dict[str, Any]:
    with patch.object(ca.rt, "fetch_dsn", return_value=_DSN), patch("psycopg2.connect", return_value=conn):
        return ca.action_provision_catalog_owner(event, None)


def test_provision_catalog_owner_adopt_creates_the_role_then_adopts_and_normalises():
    for schema, role in cl.OWNER_ROLES.items():
        conn = _Conn(owner_before=None, to_reown=0)
        out = _owner_run({"meta_schema": schema, "confirm": schema}, conn)
        sqls = [sql for sql, _ in conn.log]
        assert cl.render_owner_sql(schema, "adopt") in sqls
        assert conn.committed == 1 and conn.closed is True
        assert out == {
            "ok": True,
            "meta_schema": schema,
            "owner_role": role,
            "mode": "adopt",
            "schema_created": True,
            "schema_adopted": False,
            "schema_released": False,
            "relations_reowned": 0,
            "owner_sql_sha256": cl.template_sha256("owner"),
            "member_inherit": True,
            "member_set": True,
        }
    adopted = _owner_run({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, _Conn("ducklake_ops", to_reown=41))
    assert adopted["schema_created"] is False and adopted["schema_adopted"] is True and adopted["relations_reowned"] == 41
    again = _owner_run({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, _Conn("ducklake_smoke_owner"))
    assert again["schema_adopted"] is False and again["relations_reowned"] == 0


def test_provision_catalog_owner_refuses_an_unknown_catalog_mode_or_wrong_confirm():
    cases = (
        {"meta_schema": "ducklake_ops", "confirm": "ducklake_ops"},
        {"meta_schema": "x'; DROP SCHEMA y; --", "confirm": "x'; DROP SCHEMA y; --"},
        {"meta_schema": "ducklake_smoke"},
        {"meta_schema": "ducklake_smoke", "confirm": "ducklake_telemetry"},
        {"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke", "mode": "drop"},
        {"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke", "mode": None},
        {},
    )
    for event in cases:
        with patch.object(ca.rt, "fetch_dsn") as fetch, patch("psycopg2.connect") as connect:
            with pytest.raises(DuckLakeRuntimeError, match="provision_catalog_owner"):
                ca.action_provision_catalog_owner(event, None)
        fetch.assert_not_called()
        connect.assert_not_called()


def test_provision_catalog_owner_release_is_refused_for_any_catalog_but_smoke():
    with patch.object(ca.rt, "fetch_dsn") as fetch, patch("psycopg2.connect") as connect:
        with pytest.raises(DuckLakeRuntimeError, match="release is refused for any catalog but ducklake_smoke"):
            ca.action_provision_catalog_owner(
                {"meta_schema": "ducklake_telemetry", "confirm": "ducklake_telemetry", "mode": "release"}, None
            )
    fetch.assert_not_called()
    connect.assert_not_called()


def test_provision_catalog_owner_release_runs_the_release_section_and_reports_it():
    conn = _Conn(owner_before="ducklake_smoke_owner", to_reown=40)
    out = _owner_run({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke", "mode": "release"}, conn)
    assert cl.render_owner_sql("ducklake_smoke", "release") in [sql for sql, _ in conn.log]
    assert cl.render_owner_sql("ducklake_smoke", "adopt") not in [sql for sql, _ in conn.log]
    assert out["mode"] == "release" and out["schema_released"] is True and out["schema_adopted"] is False
    assert out["schema_created"] is False and out["relations_reowned"] == 40
    assert out["member_inherit"] is None and out["member_set"] is None
    again = _owner_run(
        {"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke", "mode": "release"}, _Conn("ducklake_ops")
    )
    assert again["schema_released"] is False and again["relations_reowned"] == 0


@pytest.mark.parametrize(("sqlstate", "name"), [("55P03", "LockNotAvailable"), ("40P01", "DeadlockDetected")])
def test_lock_timeout_and_deadlock_surface_as_typed_errors_with_their_sqlstate(sqlstate, name):
    conn = _Conn()
    conn.fail_on = "SET LOCAL lock_timeout"
    conn.error = type(name, (Error,), {})(f"canceling statement on {_HOST}", sqlstate)
    with pytest.raises(DuckLakeRuntimeError) as err:
        _owner_run({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, conn)
    assert name in str(err.value) and f"sqlstate={sqlstate}" in str(err.value)
    assert _HOST not in str(err.value) and conn.committed == 0 and conn.closed is True


def test_catalog_verbs_emit_no_metric_and_report_counts_only():
    with patch.object(ca.rt, "emit_metric") as emit, patch("src.common.ducklake_runtime.emit_metric") as emit2:
        out = _owner_run({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, _Conn("ducklake_ops", 3))
        full = _Harness().run(_TELEMETRY)
    emit.assert_not_called()
    emit2.assert_not_called()
    rendered = json.dumps(out) + json.dumps(full)
    assert _HOST not in rendered and _DSN["password"] not in rendered and "s3://" not in rendered
    assert all(isinstance(v, (bool, int, str, type(None))) for v in out.values())
    text = Path(ca.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.startswith(("    ", "Both refuse", "mutation")) or "def " in line
    )
    assert "ducklake_maintenance as maint" not in text and "raise maint." not in text and "except maint." not in text
    assert "emit_metric" not in code.replace("emit no metric", "") and "_emit_maintenance_metric" not in text


def test_database_errors_surface_only_class_and_sqlstate():
    boom = Error(f"connection to server at {_HOST} failed: {_DSN['password']}", "08001")
    with patch.object(ca.rt, "fetch_dsn", return_value=_DSN), patch("psycopg2.connect", side_effect=boom):
        with pytest.raises(DuckLakeRuntimeError) as err:
            ca.action_provision_catalog_owner({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, None)
    assert str(err.value) == "catalog verb failed: Error sqlstate=08001"
    assert err.value.__cause__ is None and err.value.__suppress_context__ is True
    h = _Harness()
    with patch.object(ca, "_run_owner_sql", side_effect=RuntimeError(f"attach to {_HOST} failed")):
        with pytest.raises(DuckLakeRuntimeError) as err:
            h.run(_TELEMETRY)
    assert str(err.value) == "catalog verb failed: RuntimeError" and _HOST not in str(err.value)
    with patch.object(ca, "_run_owner_sql", side_effect=DuckLakeRuntimeError("typed refusal")):
        with pytest.raises(DuckLakeRuntimeError, match="typed refusal"):
            h.run(_TELEMETRY)


def test_contract_documents_both_catalog_verbs():
    contract = yaml.safe_load((_REPO / "docs/contracts/ducklake_maintenance.yaml").read_text(encoding="utf-8"))
    for verb in ("provision_catalog_owner", "provision_telemetry_catalog"):
        entry = contract["verbs"][verb]
        text = json.dumps(entry)
        assert "Decision 230" in text and "confirm" in text
    owner = json.dumps(contract["verbs"]["provision_catalog_owner"])
    assert "release" in owner and "ducklake_smoke" in owner and "adopt" in owner
    catalog = json.dumps(contract["verbs"]["provision_telemetry_catalog"])
    assert "ducklake_telemetry" in catalog and "never" in catalog and "stats index" in catalog
    assert "deploy-paths.yaml" in catalog


def test_owner_verb_reraises_a_typed_refusal_unchanged():
    with patch.object(ca, "_run_owner_sql", side_effect=DuckLakeRuntimeError("typed refusal")):
        with pytest.raises(DuckLakeRuntimeError, match="typed refusal"):
            ca.action_provision_catalog_owner({"meta_schema": "ducklake_smoke", "confirm": "ducklake_smoke"}, None)
    with pytest.raises(DuckLakeRuntimeError, match="provision_catalog_owner"):
        ca.action_provision_catalog_owner({"meta_schema": 7, "confirm": 7}, None)
