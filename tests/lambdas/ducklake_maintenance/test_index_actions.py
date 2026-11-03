"""ensure_catalog_indexes (src/lambdas/ducklake_maintenance/index_actions.py), Decision 214.

psycopg2 is replaced by a local stub (the fast CI tier installs no native driver) and the status helpers are scripted,
so the tests pin the verb's control flow and statements, not Postgres. Fixture hosts are composed at runtime.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

import src.lambdas.ducklake_maintenance.index_actions as ia
from src.common import ducklake_catalog_index_status as st
from src.common.ducklake_runtime import DuckLakeRuntimeError

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_HOST = "ep-" + secrets.token_hex(6) + ".example.invalid"
_CONFIRM = "ducklake_file_column_stats"
_SQL = Path(ia.__file__).with_name("catalog_stats_index.sql")


class Error(Exception):
    def __init__(self, message: str = "", pgcode: str | None = None) -> None:
        super().__init__(message)
        self.pgcode = pgcode


class OperationalError(Error):
    pass


_PG = types.ModuleType("psycopg2")
_PG.Error = Error  # type: ignore[attr-defined]
_PG.OperationalError = OperationalError  # type: ignore[attr-defined]
_PG.connect = MagicMock()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _stub_psycopg2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg2", _PG)
    monkeypatch.setattr(ia.rt, "fetch_dsn", lambda: {"host": _HOST})
    monkeypatch.setattr(ia.rt, "libpq_conninfo", lambda dsn: "conninfo")
    monkeypatch.setattr(ia.time, "sleep", lambda s: None)
    monkeypatch.setattr(ia._shared, "DATA_PATH", "s3://bucket/ducklake/")


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.executed.append((sql, self.conn.autocommit))

    def fetchone(self) -> Any:
        return self.conn.fetch.pop(0)


class _Conn:
    def __init__(self, fetch: list[Any] | None = None) -> None:
        self.autocommit = False
        self.executed: list[tuple[str, bool]] = []
        self.fetch = list(fetch or [])
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.closed = True


def _status(*, present: bool, valid: bool, name: str | None = st.INDEX_NAME) -> dict[str, Any]:
    return {"present": present, "valid": valid, "index_name": name if present else None, "idx_scan": 3}


def _script(monkeypatch: pytest.MonkeyPatch, schemas: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(ia.status, "catalog_index_schemas", lambda cur: list(schemas))
    monkeypatch.setattr(ia.status, "catalog_index_status", lambda cur, s: schemas[s].pop(0))


def _connect(monkeypatch: pytest.MonkeyPatch, conn: _Conn) -> None:
    monkeypatch.setattr(_PG, "connect", MagicMock(return_value=conn))


def test_refuses_without_confirm():
    _PG.connect.reset_mock()
    for event in ({}, {"confirm": "ducklake_ops"}, {"confirm": None}):
        with pytest.raises(DuckLakeRuntimeError, match="confirm"):
            ia.action_ensure_catalog_indexes(event, None)
    _PG.connect.assert_not_called()


def test_builds_absent_and_rebuilds_invalid_per_enumerated_schema(monkeypatch):
    conn = _Conn()
    _connect(monkeypatch, conn)
    _script(
        monkeypatch,
        {
            "ducklake_ops": [_status(present=False, valid=False), _status(present=True, valid=True)],
            "ducklake_smoke": [_status(present=True, valid=False), _status(present=True, valid=True)],
            "ducklake_valid": [_status(present=True, valid=True), _status(present=True, valid=True)],
            "ducklake_equiv": [_status(present=True, valid=True, name="their_idx"), _status(present=True, valid=True)],
        },
    )
    monkeypatch.setattr(ia, "_probe_pushdown", lambda cur, dsn: {"idx_scan_before": 1, "idx_scan_after": 2, "engaged": True})

    out = ia.action_ensure_catalog_indexes({"confirm": _CONFIRM}, None)

    assert out["ok"] and out["pushdown_probe"]["engaged"] is True
    assert out["migration_sha256"] == hashlib.sha256(_SQL.read_bytes()).hexdigest()
    assert out["schemas"]["ducklake_ops"] == {
        "existed": False, "valid_before": False, "created": True, "rebuilt": False, "valid_after": True,
    }  # fmt: skip
    assert out["schemas"]["ducklake_smoke"]["rebuilt"] is True and out["schemas"]["ducklake_smoke"]["created"] is False
    assert not out["schemas"]["ducklake_valid"]["created"] and not out["schemas"]["ducklake_equiv"]["created"]

    sql = [s for s, _ in conn.executed]
    assert sql[0] == "SET statement_timeout = '300s'"
    build = _SQL.read_text().format(meta_schema="ducklake_ops")
    assert build in sql and _SQL.read_text().format(meta_schema="ducklake_smoke") in sql
    assert f"DROP INDEX CONCURRENTLY ducklake_smoke.{st.INDEX_NAME}" in sql
    assert sql.index(f"DROP INDEX CONCURRENTLY ducklake_smoke.{st.INDEX_NAME}") < sql.index(
        _SQL.read_text().format(meta_schema="ducklake_smoke")
    )
    assert not any("ducklake_valid" in q or "ducklake_equiv" in q for q in sql)
    assert all(autocommit for _, autocommit in conn.executed) and conn.closed


def test_invalid_after_build_raises(monkeypatch):
    _connect(monkeypatch, _Conn())
    _script(monkeypatch, {"ducklake_ops": [_status(present=False, valid=False), _status(present=True, valid=False)]})
    with pytest.raises(DuckLakeRuntimeError, match="not valid after the build"):
        ia.action_ensure_catalog_indexes({"confirm": _CONFIRM}, None)


def test_errors_never_carry_host(monkeypatch):
    monkeypatch.setattr(_PG, "connect", MagicMock(side_effect=OperationalError(f"could not connect to {_HOST}", "08006")))
    with pytest.raises(DuckLakeRuntimeError) as exc:
        ia.action_ensure_catalog_indexes({"confirm": _CONFIRM}, None)
    assert "OperationalError" in str(exc.value) and "08006" in str(exc.value) and _HOST not in str(exc.value)
    with pytest.raises(DuckLakeRuntimeError) as exc2:
        ia.ensure_index_for("ducklake_ops")
    assert _HOST not in str(exc2.value) and exc2.value.__cause__ is None


def test_ensure_index_for_builds_for_one_schema(monkeypatch):
    conn = _Conn()
    _connect(monkeypatch, conn)
    _script(monkeypatch, {"ducklake_ops": [_status(present=False, valid=False), _status(present=True, valid=True)]})
    out = ia.ensure_index_for("ducklake_ops")
    assert out["created"] and out["valid_after"]
    with pytest.raises(DuckLakeRuntimeError, match="invalid SQL identifier"):
        ia.ensure_index_for("bad;name")


def _probe_env(monkeypatch: pytest.MonkeyPatch, scans: list[int], opened: list[Any]) -> _Conn:
    conn = _Conn(fetch=[(7, "ops_things"), ("rec_id",)])
    it = iter(scans)
    monkeypatch.setattr(ia, "_idx_scan", lambda cur: next(it))
    con = MagicMock()
    monkeypatch.setattr(ia.rt, "open_connection", lambda **kw: (opened.append(kw), con)[1])
    opened.append(con)
    return conn


def test_pushdown_probe_reports_engagement(monkeypatch):
    for scans, expected in (([5, 6], True), ([5, 5, 5, 8], True), ([5] * 40, False)):
        opened: list[Any] = []
        conn = _probe_env(monkeypatch, scans, opened)
        cur = _Cursor(conn)
        out = ia._probe_pushdown(cur, {"host": _HOST})
        assert out["engaged"] is expected and out["idx_scan_before"] == 5
        assert opened[0].close.called  # the DuckDB connection closes before the counter poll
        query = opened[0].execute.call_args.args[0]
        assert query.startswith("SELECT count(*)") and '"rec_id" = ?' in query
    # a probe error keeps only the class name, never the message (the ATTACH string embeds the conninfo)
    monkeypatch.setattr(ia.rt, "open_connection", MagicMock(side_effect=RuntimeError(f"attach failed host={_HOST}")))
    monkeypatch.setattr(ia, "_idx_scan", lambda cur: 1)
    out = ia._probe_pushdown(_Cursor(_Conn(fetch=[(7, "ops_things"), ("rec_id",)])), {"host": _HOST})
    assert out == {"engaged": None, "error_class": "RuntimeError"} and _HOST not in str(out)


def test_probe_without_a_data_path_reports_the_error_class(monkeypatch):
    monkeypatch.setattr(ia._shared, "DATA_PATH", None)
    monkeypatch.setattr(ia, "_idx_scan", lambda cur: 1)
    out = ia._probe_pushdown(_Cursor(_Conn(fetch=[(7, "ops_things"), ("rec_id",)])), {"host": _HOST})
    assert out == {"engaged": None, "error_class": "DuckLakeRuntimeError"}


def test_probe_column_match_is_case_insensitive():
    # DuckLake's catalog stores column types in its own lowercase spelling ("varchar"), not DuckDB's "VARCHAR".
    assert "lower(column_type) = 'varchar'" in ia._PROBE_COLUMN_SQL


@pytest.mark.parametrize("fetch", [[None], [(7, "ops_things"), None]])
def test_probe_names_a_missing_target(monkeypatch, fetch):
    monkeypatch.setattr(ia, "_idx_scan", lambda cur: 1)
    out = ia._probe_pushdown(_Cursor(_Conn(fetch=fetch)), {"host": _HOST})
    assert out == {"engaged": None, "error_class": "ProbeTargetMissing"}


def test_probe_error_never_fails_the_verb(monkeypatch):
    _connect(monkeypatch, _Conn())
    _script(monkeypatch, {"ducklake_ops": [_status(present=True, valid=True), _status(present=True, valid=True)]})
    monkeypatch.setattr(ia, "_probe_pushdown", lambda cur, dsn: {"engaged": None, "error_class": "RuntimeError"})
    out = ia.action_ensure_catalog_indexes({"confirm": _CONFIRM}, None)
    assert out["ok"] and out["pushdown_probe"]["engaged"] is None


def test_idx_scan_reads_the_ops_index(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ia.status, "catalog_index_status", lambda cur, s: (seen.append(s), {"idx_scan": None})[1])
    assert ia._idx_scan(object()) == 0 and seen == ["ducklake_ops"]


def test_sql_file_statement_shape():
    text = _SQL.read_text()
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--")).strip()
    assert body.count(";") == 1 and body.endswith(";")
    assert re.fullmatch(
        r"CREATE INDEX CONCURRENTLY ducklake_file_column_stats_table_id_column_id_idx "
        r"ON \{meta_schema\}\.ducklake_file_column_stats \(table_id, column_id\);",
        body,
    )
    assert "IF NOT EXISTS" not in body


def test_manifest_ships_sql_asset():
    manifest = yaml.safe_load((_REPO / "src/lambdas/ducklake_maintenance/manifest.yaml").read_text())
    assert "src/lambdas/ducklake_maintenance/catalog_stats_index.sql" in manifest["assets"]
    smoke = yaml.safe_load((_REPO / "src/lambdas/ducklake_maintenance_smoke/manifest.yaml").read_text())
    assert "src/lambdas/ducklake_maintenance/index_actions.py" not in smoke["includes"]


def test_contract_declares_verb():
    contract = yaml.safe_load((_REPO / "docs/contracts/ducklake_maintenance.yaml").read_text())
    verb = contract["verbs"]["ensure_catalog_indexes"]
    assert (
        _CONFIRM in verb["payload_schema_ref"]
        and "Decision 214, amending Decision 88 invariant v" in verb["payload_schema_ref"]
    )
