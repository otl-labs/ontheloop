"""Facade contracts for the ducklake_runtime / ducklake_writer split (PLAN-sloc-ducklake-layer).

Proves the three invariants the decomposition depends on, in one place:
  1. Re-export completeness -- every census-enumerated public + private symbol is a real attribute
     at src.common.ducklake_runtime.* and every accessed handler symbol at
     src.lambdas.ducklake_writer.handler.*.
  2. Interception preservation -- patching each migrated target (src.common.ducklake_tables.CATALOG_ALIAS;
     the 7 smoke_actions private helpers) fires through the moved body; patching the OLD (pre-split)
     location is a proven no-op, confirming the migration was necessary.
  3. Dispatch integrity -- _ACTIONS maps the same action names to callables and
     _CONNECTIONLESS_ACTIONS is unchanged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import src.lambdas.ducklake_writer.handler as h
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_writer import smoke_actions

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# 1. Re-export completeness
# ---------------------------------------------------------------------------

# Mirrors VP step 2's hermetic checklist verbatim (45 non-test import symbols + 17 patched symbols +
# the 12 private internals read by test_ducklake_runtime.py + PINNED_DUCKDB_VERSION via __getattr__),
# plus OCC_MAX_BACKOFF_S (read directly by test_ducklake_runtime.py::test_occ_backoff_sleeps).
_RUNTIME_FACADE_REQUIRED = [
    "open_connection",
    "fetch_dsn",
    "_parse_dsn_document",
    "libpq_conninfo",
    "assert_duckdb_version",
    "get_warm_connection",
    "reset_warm_connection",
    "is_dead_connection_error",
    "_probe_connection_alive",
    "_warm_connection",
    "write_scd2",
    "file_scd2",
    "mint_write_identity",
    "is_occ_collision",
    "_occ_backoff",
    "_safe_rollback",
    "_emit_write_metrics",
    "bootstrap_entity_counter",
    "ensure_entity_counters_table",
    "_allocate_entity_id",
    "_advance_entity_counter",
    "create_scd2_tables",
    "reconcile_table_columns",
    "create_control_table",
    "resolve_control_spec",
    "control_table_names",
    "is_control_table",
    "ControlTableSpec",
    "read_current",
    "read_history",
    "named_read",
    "emit_metric",
    "make_metric_sink",
    "CATALOG_ALIAS",
    "SMOKE_DATA_PATH",
    "SMOKE_HISTORY_TABLE",
    "SMOKE_CURRENT_TABLE",
    "META_SCHEMA",
    "SMOKE_META_SCHEMA",
    "LAMBDA_EXTENSION_DIRECTORY",
    "DSN_PARAMETER_NAME",
    "ENTITY_COUNTERS_TABLE",
    "BAKED_EXTENSIONS",
    "CHURN_WRITERS",
    "CHURN_WRITES_PER_WRITER",
    "COMMIT_LATENCY_BUDGET_MS",
    "OCC_COLLISION_RATE_BUDGET",
    "OCC_MAX_BACKOFF_S",
    "CLOUDWATCH_NAMESPACE",
    "OCCRetryExhaustedError",
    "VersionMismatchError",
    "DuckLakeRuntimeError",
    "WriteIdentity",
    "WriteResult",
    "ReferentialError",
    "SchemaGateError",
    "StatusTransitionError",
    "ducklake_spike",
    "ops_table_names",
    "describe_write_verbs",
    "describe_named_reads",
    "resolve_table_spec",
    "schema_gate",
    "NAMED_READS",
    "NAMED_READS_VERSION",
    "PINNED_DUCKDB_VERSION",
]

_HANDLER_FACADE_REQUIRED = [
    "handler",
    "_ACTIONS",
    "_CONNECTIONLESS_ACTIONS",
    "_parse_event",
    "_response",
    "_open_writer_connection",
    "_warm_writer_connection",
    "_require_ops_table",
    "WriterActionError",
    "action_write_ops",
    "action_file_ops",
    "action_update_ops",
    "action_create_ops_tables",
    "action_describe",
    "action_attach_check",
    "action_create_tables",
    "action_write",
    "action_idempotency_probe",
    "action_partition_probe",
    "action_inlining_probe",
    "action_loudfail_probe",
    "action_connect_probe",
    "action_reset_warm_connection",
    "action_churn",
    "action_churn_single",
    "_churn_one_writer",
    "_churn_one_single_write",
    "_frozen_creds",
    "_concurrency_probe",
    "_count_files",
    "_partition_layout",
    "_count_inlined_rows",
    "_AlwaysCollidingConnection",
    "_p95",
]


@pytest.mark.parametrize("symbol", _RUNTIME_FACADE_REQUIRED)
def test_ducklake_runtime_facade_reexports_symbol(symbol: str) -> None:
    """Every census-enumerated symbol resolves as a real attribute of src.common.ducklake_runtime."""
    assert hasattr(rt, symbol), f"src.common.ducklake_runtime is missing re-export: {symbol!r}"


@pytest.mark.parametrize("symbol", _HANDLER_FACADE_REQUIRED)
def test_ducklake_writer_handler_facade_reexports_symbol(symbol: str) -> None:
    """Every accessed handler symbol resolves as a real attribute of src.lambdas.ducklake_writer.handler."""
    assert hasattr(h, symbol), f"src.lambdas.ducklake_writer.handler is missing re-export: {symbol!r}"


def test_facade_drops_the_caller_sql_helpers() -> None:
    """The caller-SQL helpers are gone from the facade and the reads module (named as strings, never called)."""
    import src.common.ducklake_reads as reads

    for name in ("query_current", "assert_read_only_sql"):
        assert not hasattr(rt, name)
        assert not hasattr(reads, name)


def test_ducklake_spike_submodule_resolves_on_facade() -> None:
    """rt.ducklake_spike stays resolvable (module-scope import, not a re-export line)."""
    assert hasattr(rt.ducklake_spike, "_require_duckdb")


def test_pinned_duckdb_version_resolves_via_getattr_shim() -> None:
    """PINNED_DUCKDB_VERSION resolves lazily via __getattr__, matching the version SSOT loader."""
    from src.common.ducklake_version import pinned_duckdb_version

    assert rt.PINNED_DUCKDB_VERSION == pinned_duckdb_version()


# ---------------------------------------------------------------------------
# 2. Interception preservation
# ---------------------------------------------------------------------------


class _RecordingCon:
    """Minimal connection double: records every executed SQL string; no rows ever exist."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> "_RecordingCon":
        self.executed.append(sql)
        return self

    def fetchall(self) -> list:
        return []

    def close(self) -> None:
        pass


def test_catalog_alias_patch_on_ducklake_tables_intercepts_reconcile_table_columns() -> None:
    """patch("src.common.ducklake_tables.CATALOG_ALIAS") fires through reconcile_table_columns.

    reconcile_table_columns moved to ducklake_tables and imports CATALOG_ALIAS directly from
    ducklake_scd2_schema (never from the facade) -- the migrated patch target must be the module
    the function actually lives in.
    """
    con = _RecordingCon()
    with patch("src.common.ducklake_tables.CATALOG_ALIAS", "sentinel_catalog"):
        rt.reconcile_table_columns(con, table="ops_recommendations")
    assert any("sentinel_catalog" in sql for sql in con.executed)


def test_catalog_alias_patch_on_ducklake_runtime_facade_is_a_noop() -> None:
    """The OLD (pre-split) patch location -- src.common.ducklake_runtime.CATALOG_ALIAS -- no longer
    intercepts reconcile_table_columns: proves the migration in test_ops_data_portal.py was necessary,
    not cosmetic."""
    con = _RecordingCon()
    with patch("src.common.ducklake_runtime.CATALOG_ALIAS", "sentinel_catalog"):
        rt.reconcile_table_columns(con, table="ops_recommendations")
    assert not any("sentinel_catalog" in sql for sql in con.executed)
    assert any("ops_catalog" in sql for sql in con.executed)  # real CATALOG_ALIAS value still used


def test_frozen_creds_patch_on_smoke_actions_intercepts_action_churn_single_setup(monkeypatch) -> None:
    """patch smoke_actions._frozen_creds fires through action_churn_single's setup path."""
    sentinel_creds = ("SENTINEL_AK", "SENTINEL_SK", None, "eu-west-2")  # pragma: allowlist secret
    captured: dict[str, Any] = {}
    monkeypatch.setattr(rt, "fetch_dsn", lambda: {"host": "h"})
    monkeypatch.setattr(smoke_actions, "_frozen_creds", lambda: sentinel_creds)
    monkeypatch.setattr(rt, "open_connection", lambda **kw: captured.update(kw) or _RecordingCon())
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    out = smoke_actions.action_churn_single({"setup": True}, None)
    assert out == {"ok": True, "setup": True}
    assert captured["_creds"] == sentinel_creds


def test_churn_one_writer_patch_on_smoke_actions_intercepts_action_churn(monkeypatch) -> None:
    """patch smoke_actions._churn_one_writer fires through action_churn's ThreadPoolExecutor.map call."""
    sentinel_result = {
        "latency_ms": 5.0,
        "collided": False,
        "connect_ms": 1.0,
        "commit_ms": 2.0,
        "occ_retries": 0,
        "wall_ms": 5.0,
        "cpu_ms": 4.0,
    }
    monkeypatch.setattr(rt, "fetch_dsn", lambda: {"host": "h"})
    monkeypatch.setattr(smoke_actions, "_frozen_creds", lambda: ("ak", "sk", None, "eu-west-2"))
    monkeypatch.setattr(rt, "open_connection", lambda **kw: _RecordingCon())
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    monkeypatch.setattr(smoke_actions, "_churn_one_writer", lambda i, dsn, creds: dict(sentinel_result))
    out = smoke_actions.action_churn({"writers": 2}, _RecordingCon())
    assert out["collision_rate"] == 0.0
    assert out["breakdown"]["p95_commit_ms"] == 2.0  # from sentinel_result, not the real implementation


def test_churn_one_single_write_patch_on_smoke_actions_intercepts_action_churn_single(monkeypatch) -> None:
    """patch smoke_actions._churn_one_single_write fires through action_churn_single's normal path."""
    seen: list[int] = []
    monkeypatch.setattr(rt, "fetch_dsn", lambda: {"host": "h"})
    monkeypatch.setattr(smoke_actions, "_frozen_creds", lambda: ("ak", "sk", None, "eu-west-2"))
    monkeypatch.setattr(
        smoke_actions,
        "_churn_one_single_write",
        lambda i, dsn, creds: seen.append(i) or {"latency_ms": 9.0, "collided": True},
    )
    out = smoke_actions.action_churn_single({"writer_id": 7}, None)
    assert seen == [7]
    assert out["collided"] is True
    assert out["latency_ms"] == 9.0


def test_concurrency_probe_patch_on_smoke_actions_intercepts_action_inlining_probe(monkeypatch) -> None:
    """patch smoke_actions._concurrency_probe fires through action_inlining_probe."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: rt.WriteResult("01U", "rec-1", 0, 1.0, None, None))
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    monkeypatch.setattr(smoke_actions, "_count_files", lambda c, t: 3)
    monkeypatch.setattr(smoke_actions, "_count_inlined_rows", lambda c, t: 0)
    monkeypatch.setattr(smoke_actions, "_concurrency_probe", lambda w: "SENTINEL")
    out = smoke_actions.action_inlining_probe({}, _RecordingCon())
    assert out["occ_conflicts_handled"] == "SENTINEL"


def test_partition_layout_patch_on_smoke_actions_intercepts_action_partition_probe(monkeypatch) -> None:
    """patch smoke_actions._partition_layout fires through action_partition_probe."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: None)

    def _fake_layout(con, catalog_alias, table):
        if "history" in table:
            return {
                "total": 42,
                "path_prefixes": {
                    1: "year=2026/month=1/day=24",
                    2: "year=2026/month=2/day=24",
                    3: "year=2027/month=1/day=24",
                },
                "value_tuples": {1: ("2026", "1", "24"), 2: ("2026", "2", "24"), 3: ("2027", "1", "24")},
            }
        return {
            "total": 99,
            "path_prefixes": {1: "bucket=0", 2: "bucket=1"},
            "value_tuples": {1: ("0",), 2: ("1",)},
        }

    monkeypatch.setattr(smoke_actions, "_partition_layout", _fake_layout)
    out = smoke_actions.action_partition_probe({}, _RecordingCon())
    assert out["history_total"] == 42
    assert out["current_total"] == 99
    assert out["history_calendar_days"] == 3
    assert out["history_partition_tuples"] == 3


def test_count_inlined_rows_patch_on_smoke_actions_intercepts_action_inlining_probe(monkeypatch) -> None:
    """patch smoke_actions._count_inlined_rows fires through action_inlining_probe."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: rt.WriteResult("01U", "rec-1", 0, 1.0, None, None))
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    monkeypatch.setattr(smoke_actions, "_count_files", lambda c, t: 1)
    monkeypatch.setattr(smoke_actions, "_count_inlined_rows", lambda c, t: 13)
    monkeypatch.setattr(smoke_actions, "_concurrency_probe", lambda w: True)
    out = smoke_actions.action_inlining_probe({}, _RecordingCon())
    assert out["inlined_rows"] == 13


def test_open_writer_connection_stays_patched_on_handler(monkeypatch) -> None:
    """_open_writer_connection stays defined+patched on handler.py (it did not move)."""
    con = _RecordingCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    r = h.handler({"action": "create_tables"})
    assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# 3. Dispatch integrity
# ---------------------------------------------------------------------------

_EXPECTED_ACTIONS = {
    "attach_check",
    "create_tables",
    "write",
    "write_ops",
    "file_ops",
    "update_ops",
    "create_ops_tables",
    "idempotency_probe",
    "partition_probe",
    "inlining_probe",
    "inlining",
    "loudfail_probe",
    "churn",
    "churn_single",
    "connect_probe",
    "reset_warm_connection",
    "describe",
}

_EXPECTED_CONNECTIONLESS_ACTIONS = {"churn", "churn_single", "connect_probe", "reset_warm_connection", "describe"}

# Actions that moved to smoke_actions.py -- _ACTIONS must dispatch to the SAME function object.
_SMOKE_ACTION_NAMES = {
    "attach_check": "action_attach_check",
    "create_tables": "action_create_tables",
    "write": "action_write",
    "idempotency_probe": "action_idempotency_probe",
    "partition_probe": "action_partition_probe",
    "inlining_probe": "action_inlining_probe",
    "inlining": "action_inlining_probe",
    "loudfail_probe": "action_loudfail_probe",
    "churn": "action_churn",
    "churn_single": "action_churn_single",
    "connect_probe": "action_connect_probe",
    "reset_warm_connection": "action_reset_warm_connection",
}

# Production actions that stay defined in handler.py.
_HANDLER_ACTION_NAMES = {
    "write_ops": "action_write_ops",
    "file_ops": "action_file_ops",
    "update_ops": "action_update_ops",
    "create_ops_tables": "action_create_ops_tables",
    "describe": "action_describe",
}


def test_actions_dispatch_table_names_unchanged() -> None:
    """_ACTIONS maps exactly the same action names as before the split."""
    assert set(h._ACTIONS) == _EXPECTED_ACTIONS


def test_connectionless_actions_unchanged() -> None:
    """_CONNECTIONLESS_ACTIONS is byte-for-byte unchanged by the split."""
    assert h._CONNECTIONLESS_ACTIONS == _EXPECTED_CONNECTIONLESS_ACTIONS


@pytest.mark.parametrize(("action_name", "smoke_fn_name"), sorted(_SMOKE_ACTION_NAMES.items()))
def test_actions_dispatch_to_smoke_actions_module(action_name: str, smoke_fn_name: str) -> None:
    """Every smoke/probe/churn action in _ACTIONS is the identical function object from smoke_actions."""
    assert h._ACTIONS[action_name] is getattr(smoke_actions, smoke_fn_name)


@pytest.mark.parametrize(("action_name", "handler_fn_name"), sorted(_HANDLER_ACTION_NAMES.items()))
def test_actions_dispatch_to_handler_module(action_name: str, handler_fn_name: str) -> None:
    """Every production ops action in _ACTIONS is the identical function object defined in handler.py."""
    assert h._ACTIONS[action_name] is getattr(h, handler_fn_name)
