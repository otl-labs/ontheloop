"""Real-DuckLake proof of the owner step's additive leg (Decision 222; PLAN-telemetry-column-additions).

pytest.mark.integration: never gates CI (rec-4071), a local proof. On a file-backed DuckLake catalog aliased ops_catalog, the
telemetry tables are created from the projection with the ten new columns removed and given rows; create_telemetry_tables with
the real projection then adds the columns. The old rows must survive and read NULL in them, the partitions must be unchanged,
the writer's verify must pass, and a row carrying the new columns must append, read back and re-send idempotently. The
catalog-wide GC calls then run, because GC reaches these tables on every later deploy. The fixture FAILS when the extension
cannot load (a skip is a FAIL). If DuckLake refuses or re-partitions on ADD COLUMN, Decision 222's reversal condition
ducklake-alter-breaks-table fires and the design returns to /plan.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from src.telemetry.append import EventTableSpec, execute_prepared, prepare_batch  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(seconds=30)
NEW_COLUMNS = {
    "telemetry_sessions": ("harness_session_key", "checkout_sha", "checkout_base_sha", "checkout_dirty"),
    "telemetry_observations": (
        "usage_final",
        "usage_source",
        "requested_model",
        "reasoning_effort",
        "cache_creation_1h_tokens",
        "cache_creation_5m_tokens",
    ),
}
ADDED = sorted(f"{t}.{c}" for t, cols in NEW_COLUMNS.items() for c in cols)


def _reduced() -> dict[str, Any]:
    sem = copy.deepcopy(SEM)
    for table, cols in NEW_COLUMNS.items():
        for col in cols:
            del sem["ops_tables"][table]["columns"][col]
    return sem


@pytest.fixture
def con(tmp_path: Path, _allow_network_for_integration: None) -> Any:
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL ducklake; LOAD ducklake")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"ducklake extension could not load -- a skip is a FAIL (rec-4071): {exc}")
    connection.execute("SET ducklake_default_data_inlining_row_limit=0")
    connection.execute("SET TimeZone='UTC'")
    connection.execute(f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS ops_catalog (DATA_PATH '{tmp_path / 'data'}')")
    yield connection
    connection.close()


def _event(ref: str, **over: Any) -> dict[str, Any]:
    row = {
        "event_kind": "point",
        "event_timestamp": T0 + timedelta(seconds=5),
        "session_started_at": T0,
        "external_ref": ref,
        "entity_ref": ref,
        "session_ref": "sess",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 3,
    }
    return {**row, **over}


def _session(ref: str, **over: Any) -> dict[str, Any]:
    return {
        k: v
        for k, v in _event(ref, event_kind="open", workflow="implement", execution_attempt=1, **over).items()
        if k != "session_ref"
    }


def _model_call(ref: str, **over: Any) -> dict[str, Any]:
    return _event(
        ref,
        observation_type="model_call",
        tokens_input=1,
        tokens_output=2,
        tokens_cache_read=0,
        tokens_cache_creation=5,
        billing_shape="metered_marginal",
        reasoning_visibility="full",
        reasoning_tokens=1,
        **over,
    )


NEW_SESSION = {
    "harness_session_key": "session_01AbC",
    "checkout_sha": "a" * 40,
    "checkout_base_sha": "b" * 40,
    "checkout_dirty": False,
}
NEW_MODEL_CALL = {
    "usage_final": True,
    "usage_source": "record",
    "requested_model": "claude-opus-5-5",
    "reasoning_effort": "xhigh",
    "cache_creation_1h_tokens": 3,
    "cache_creation_5m_tokens": 2,
}


def _append(con: Any, sem: dict[str, Any], table: str, row: dict[str, Any]) -> int:
    spec = EventTableSpec.from_projection(table, sem["ops_tables"][table])
    prepared = prepare_batch(spec, [row], tenant_id=TENANT, project_id=PROJECT, now=NOW)
    return run_telemetry_txn(con, lambda c: execute_prepared(c, prepared, catalog="ops_catalog")).result.inserted


def _read(con: Any, table: str, columns: tuple[str, ...], event_id_like: str) -> list[tuple[Any, ...]]:
    return con.execute(
        f"SELECT {', '.join(columns)} FROM ops_catalog.{table} WHERE external_ref LIKE ? ORDER BY external_ref",
        [event_id_like],
    ).fetchall()


def _gc(con: Any) -> None:
    ts = (datetime.now(timezone.utc) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S+00")
    con.execute("CALL ducklake_flush_inlined_data('ops_catalog')").fetchall()
    con.execute(f"SELECT count(*) FROM ducklake_expire_snapshots('ops_catalog', older_than=TIMESTAMPTZ '{ts}')").fetchall()
    con.execute(
        "SELECT count(*) FROM ducklake_cleanup_old_files('ops_catalog', dry_run=False, cleanup_all=False, "
        f"older_than=TIMESTAMPTZ '{ts}')"
    ).fetchall()
    con.execute(
        f"SELECT count(*) FROM ducklake_delete_orphaned_files('ops_catalog', dry_run=False, older_than=TIMESTAMPTZ '{ts}')"
    ).fetchall()


def test_additive_leg_keeps_rows_and_partitions_and_the_new_columns_round_trip(con: Any) -> None:
    reduced = _reduced()
    created = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=reduced)).result
    assert len(created.created) == 9 and created.added == ()
    assert _append(con, reduced, "telemetry_sessions", _session("old-s#0")) == 1
    assert _append(con, reduced, "telemetry_observations", _model_call("old-o#0")) == 1

    result = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=SEM)).result
    assert list(result.added) == ADDED and result.created == ()
    assert prov.inspect_partitions(con, prov.existing_tables(con), SEM) == dict.fromkeys(prov.target_tables(SEM), True)
    run_telemetry_txn(con, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM))

    def assert_state() -> None:
        old_s = _read(con, "telemetry_sessions", NEW_COLUMNS["telemetry_sessions"], "old-s#0")
        old_o = _read(con, "telemetry_observations", NEW_COLUMNS["telemetry_observations"], "old-o#0")
        assert old_s == [(None,) * 4] and old_o == [(None,) * 6]
        new_s = _read(con, "telemetry_sessions", NEW_COLUMNS["telemetry_sessions"], "new-s#0")
        new_o = _read(con, "telemetry_observations", NEW_COLUMNS["telemetry_observations"], "new-o#0")
        assert new_s == [tuple(NEW_SESSION[c] for c in NEW_COLUMNS["telemetry_sessions"])]
        assert new_o == [tuple(NEW_MODEL_CALL[c] for c in NEW_COLUMNS["telemetry_observations"])]
        assert prov.inspect_partitions(con, prov.existing_tables(con), SEM) == dict.fromkeys(prov.target_tables(SEM), True)

    session, call = _session("new-s#0", **NEW_SESSION), _model_call("new-o#0", **NEW_MODEL_CALL)
    assert _append(con, SEM, "telemetry_sessions", session) == 1
    assert _append(con, SEM, "telemetry_observations", call) == 1
    assert _append(con, SEM, "telemetry_sessions", session) == 0
    assert _append(con, SEM, "telemetry_observations", call) == 0
    assert_state()

    again = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=SEM)).result
    assert again.added == () and again.created == ()

    _gc(con)
    assert_state()
    assert _append(con, SEM, "telemetry_sessions", session) == 0
