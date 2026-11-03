"""Tests for src/lambdas/ducklake_maintenance_smoke/handler.py (T2.18 c9 follow-on, 100% coverage,
mocked).

Dispatch table is EXACTLY {merge, gc, breaker_probe, hot_merge}; no production-destructive or
operational verb is reachable. Action logic mirrors the admin handler's former smoke-action tests
(moved here as part of the T2.18 c9 split -- see tests/lambdas/ducklake_maintenance/handler/).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance_smoke.handler as h
from src.common import ducklake_telemetry_provision as prov
from src.common.ducklake_maintenance import DuckLakeMaintenanceError
from src.common.ducklake_runtime import DuckLakeRuntimeError, VersionMismatchError
from src.lambdas.ducklake_maintenance_smoke import telemetry_actions as ta
from tests.fixtures.ducklake_maintenance_handler import FakeCon, _response_body
from tests.fixtures.terraform_hcl_blocks import find_resource_block

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Blast-radius invariant: dispatch is EXACTLY the 5 smoke actions
# ---------------------------------------------------------------------------


def test_dispatch_table_is_exactly_the_five_smoke_actions():
    assert set(h._ACTIONS) == {"merge", "gc", "hot_merge", "breaker_probe", "create_telemetry_tables"}


def test_dispatch_has_no_production_destructive_verb():
    destructive = {"catalog_reinit", "merge_ops", "reconcile_columns", "restore_drill", "clone_catalog", "catalog_stats"}
    assert destructive.isdisjoint(h._ACTIONS)


def test_handler_destructive_payload_returns_400_unknown_action():
    r = h.handler({"action": "catalog_reinit"})
    assert r["statusCode"] == 400
    body = _response_body(r)
    assert "unknown action" in body["error"]
    assert set(body["actions"]) == {"merge", "gc", "hot_merge", "breaker_probe", "create_telemetry_tables"}


def test_open_connection_takes_no_event_argument():
    """Env-pinning invariant: _open_connection() is niladic -- structurally unable to consult the
    event for data_path/meta_schema, so a crafted payload cannot redirect this Lambda at the prod
    catalog (only the env-pinned h.DATA_PATH/h.META_SCHEMA module constants are ever used).
    """
    import inspect

    assert inspect.signature(h._open_connection).parameters == {}


def test_open_connection_builds_connection_from_fetched_dsn():
    """Exercises _open_connection's real body: fetch_dsn's result is threaded into
    open_connection() with the env-pinned DATA_PATH/META_SCHEMA/EXTENSION_DIRECTORY constants."""
    fake_con = object()
    with patch.object(h.rt, "fetch_dsn", return_value={"host": "x"}) as mock_fetch:
        with patch.object(h.rt, "open_connection", return_value=fake_con) as mock_open_conn:
            result = h._open_connection()
    assert result is fake_con
    mock_fetch.assert_called_once_with(h.DSN_PARAMETER_NAME)
    mock_open_conn.assert_called_once_with(
        dsn={"host": "x"},
        data_path=h.DATA_PATH,
        meta_schema=h.META_SCHEMA,
        extension_directory=h.EXTENSION_DIRECTORY,
    )


def test_open_connection_reads_the_secret_named_by_env(monkeypatch):
    import importlib

    monkeypatch.delenv("DUCKLAKE_DSN_PARAMETER", raising=False)
    assert importlib.reload(h).DSN_PARAMETER_NAME == h.rt.DSN_PARAMETER_NAME
    monkeypatch.setenv("DUCKLAKE_DSN_PARAMETER", "/secret/agent-platform-ducklake-smoke-maintainer-dsn")
    try:
        reloaded = importlib.reload(h)
        assert reloaded.DSN_PARAMETER_NAME == "/secret/agent-platform-ducklake-smoke-maintainer-dsn"
        with patch.object(reloaded.rt, "fetch_dsn", return_value={"host": "x"}) as mock_fetch:
            with patch.object(reloaded.rt, "open_connection", return_value=object()):
                reloaded._open_connection()
        mock_fetch.assert_called_once_with("/secret/agent-platform-ducklake-smoke-maintainer-dsn")
    finally:
        monkeypatch.delenv("DUCKLAKE_DSN_PARAMETER", raising=False)
        importlib.reload(h)


def test_handler_ignores_data_path_and_meta_schema_in_event():
    con = FakeCon(fetchall=[], fetchone_map={"ducklake_list_files": (0,), "count(*)": (0,)})
    with patch.object(h, "_open_connection", return_value=con) as mock_open:
        r = h.handler({"action": "merge", "data_path": "s3://prod-bucket/ducklake/", "meta_schema": "ducklake_ops"})
    assert r["statusCode"] == 200
    mock_open.assert_called_once_with()


# ---------------------------------------------------------------------------
# _parse_event / _response
# ---------------------------------------------------------------------------


def test_parse_event_body_string():
    assert h._parse_event({"body": json.dumps({"action": "merge"})}) == {"action": "merge"}


def test_parse_event_body_dict():
    assert h._parse_event({"body": {"action": "gc"}}) == {"action": "gc"}


def test_parse_event_body_empty_string():
    assert h._parse_event({"body": ""}) == {}


def test_parse_event_direct_dict():
    assert h._parse_event({"action": "merge"}) == {"action": "merge"}


def test_parse_event_non_dict():
    assert h._parse_event("nonsense") == {}


def test_response_envelope():
    r = h._response(200, {"ok": True})
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True
    assert r["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# action_merge
# ---------------------------------------------------------------------------


def _merge_con() -> FakeCon:
    return FakeCon(fetchall=[], fetchone_map={"ducklake_list_files": (4,), "count(*)": (4,)})


def test_action_merge_ok():
    con = _merge_con()
    result = h.action_merge({}, con)
    assert result["ok"] is True
    assert result["action"] == "merge"
    assert "files_before" in result
    assert "files_after_merge" in result
    assert "elapsed_ms" in result


def test_action_merge_force_recreate_calls_create_tables():
    con = _merge_con()
    with patch.object(h.rt, "create_scd2_tables") as mock_create:
        h.action_merge({"force_recreate_tables": True}, con)
        mock_create.assert_called_once_with(con, force_recreate=True)


def test_action_merge_no_force_skips_create_tables():
    con = _merge_con()
    with patch.object(h.rt, "create_scd2_tables") as mock_create:
        h.action_merge({}, con)
        mock_create.assert_not_called()


def test_action_merge_emits_metrics():
    con = _merge_con()
    with patch.object(h, "_emit_maintenance_metric") as mock_emit:
        h.action_merge({}, con)
        metric_names = [call.args[0] for call in mock_emit.call_args_list]
        assert "MergeDurationMs" in metric_names
        assert "FilesBeforeMerge" in metric_names
        assert "FilesAfterMerge" in metric_names


# ---------------------------------------------------------------------------
# action_gc
# ---------------------------------------------------------------------------


def _gc_con() -> FakeCon:
    return FakeCon(
        fetchall=[],
        fetchone_map={
            "ducklake_list_files": (5,),
            "ducklake_cleanup_old_files": (1,),
            "ducklake_delete_orphaned_files": (0,),
            "ducklake_expire_snapshots": (2,),
            "count(*)": (5,),
        },
    )


def test_action_gc_ok():
    con = _gc_con()
    with patch.object(h.maint, "run_gc") as mock_gc:
        mock_gc.return_value = {
            "ok": True,
            "action": "gc",
            "tables": ["t1"],
            "files_before": 5,
            "files_after": 4,
            "snapshots_expired": 2,
            "files_cleaned": 1,
            "orphans_deleted": 0,
            "guard_stats": {"g4_bounded": False},
        }
        result = h.action_gc({}, con)
    assert result["ok"] is True
    assert result["action"] == "gc"


def test_action_gc_force_recreate_calls_create_tables():
    con = _gc_con()
    with patch.object(h.rt, "create_scd2_tables") as mock_create:
        with patch.object(h.rt, "write_scd2") as mock_write:
            with patch.object(h.maint, "run_gc") as mock_gc:
                mock_gc.return_value = {
                    "ok": True,
                    "action": "gc",
                    "tables": [],
                    "files_before": 0,
                    "files_after": 0,
                    "snapshots_expired": 0,
                    "files_cleaned": 0,
                    "orphans_deleted": 0,
                    "guard_stats": {},
                }
                h.action_gc({"force_recreate_tables": True}, con)
    mock_create.assert_called_once_with(con, force_recreate=True)
    mock_write.assert_called_once()


def test_action_gc_seeds_catalog_before_gc():
    """rec-3802: force_recreate_tables=True must seed one row into the smoke pair BEFORE run_gc
    runs, so G3's post-pass live-set check is exercised non-vacuously. The plain gc verb (no
    force_recreate_tables) must issue no write at all -- the seed is confined to the fixture
    branch, never reaching the real weekly cadence."""
    manager = MagicMock()
    con = _gc_con()
    with patch.object(h.rt, "create_scd2_tables") as mock_create:
        with patch.object(h.rt, "write_scd2") as mock_write:
            with patch.object(h.maint, "run_gc") as mock_gc:
                manager.attach_mock(mock_create, "create_scd2_tables")
                manager.attach_mock(mock_write, "write_scd2")
                manager.attach_mock(mock_gc, "run_gc")
                mock_gc.return_value = {
                    "ok": True,
                    "action": "gc",
                    "tables": [],
                    "files_before": 1,
                    "files_after": 1,
                    "snapshots_expired": 0,
                    "files_cleaned": 0,
                    "orphans_deleted": 0,
                    "guard_stats": {},
                }
                h.action_gc({"force_recreate_tables": True}, con)
    mock_write.assert_called_once()
    call_order = [c[0] for c in manager.mock_calls]
    assert call_order.index("write_scd2") < call_order.index("run_gc"), "seed must happen before run_gc"

    con_plain = _gc_con()
    with patch.object(h.rt, "write_scd2") as mock_write_plain:
        with patch.object(h.maint, "run_gc") as mock_gc_plain:
            mock_gc_plain.return_value = {
                "ok": True,
                "action": "gc",
                "tables": [],
                "files_before": 0,
                "files_after": 0,
                "snapshots_expired": 0,
                "files_cleaned": 0,
                "orphans_deleted": 0,
                "guard_stats": {},
            }
            h.action_gc({}, con_plain)
    mock_write_plain.assert_not_called()


def test_action_gc_emits_metrics():
    con = _gc_con()
    with patch.object(h.maint, "run_gc") as mock_gc:
        mock_gc.return_value = {
            "ok": True,
            "action": "gc",
            "tables": [],
            "files_before": 3,
            "files_after": 2,
            "snapshots_expired": 1,
            "files_cleaned": 1,
            "orphans_deleted": 0,
            "guard_stats": {},
        }
        with patch.object(h, "_emit_maintenance_metric") as mock_emit:
            h.action_gc({}, con)
            metric_names = [call.args[0] for call in mock_emit.call_args_list]
            assert "GcDurationMs" in metric_names
            assert "FilesBeforeGc" in metric_names
            assert "FilesAfterGc" in metric_names
            assert "MaintenanceBreakerTrip" in metric_names


# ---------------------------------------------------------------------------
# tests for action_breaker_probe
# ---------------------------------------------------------------------------


def test_breaker_probe_forces_g1_trip():
    """The probe's whole purpose is a guaranteed trip -- it must raise regardless of the smoke
    catalog's real contents (an empty catalog must not silently no-op it)."""
    con = FakeCon()
    with pytest.raises(DuckLakeMaintenanceError, match="G1 reachability"):
        h.action_breaker_probe({}, con)


def test_breaker_probe_non_forcing_input_does_not_trip():
    """Red case for the forcing mechanism itself: gcops.assert_reachability with disjoint sets and
    no force_conflict must NOT raise -- proving the NAMED FORCING CONSTANT (not an unconditional
    raise) is what trips the probe, so the two directions are actually distinguishable."""
    h.gcops.assert_reachability({"a"}, {"b"})  # no raise


def test_action_breaker_probe_does_not_emit_metric_directly():
    """H1 fix: action_breaker_probe must NOT emit MaintenanceBreakerTrip itself.
    The handler's outer DuckLakeMaintenanceError catch is the single emit point.
    """
    con = FakeCon()
    with patch.object(h, "_emit_maintenance_metric") as mock_emit:
        with pytest.raises(DuckLakeMaintenanceError):
            h.action_breaker_probe({}, con)
    assert mock_emit.call_count == 0, "action_breaker_probe must not emit metrics -- handler outer catch does it"


def test_handler_breaker_probe_emits_metric_exactly_once():
    """H1 fix: full handler path for breaker_probe should emit MaintenanceBreakerTrip exactly once."""
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        with patch.object(h, "_emit_maintenance_metric") as mock_emit:
            r = h.handler({"action": "breaker_probe"})
    assert r["statusCode"] == 500
    trip_emits = [c for c in mock_emit.call_args_list if c.args[0] == "MaintenanceBreakerTrip"]
    assert len(trip_emits) == 1, f"MaintenanceBreakerTrip must be emitted exactly once, got {len(trip_emits)}"


# ---------------------------------------------------------------------------
# Smoke metrics stay off the production breaker alarm
# ---------------------------------------------------------------------------

_TF_DIR = Path(__file__).resolve().parents[1] / "terraform" / "personal"
_DIMENSIONS_RE = re.compile(r"dimensions\s*=\s*\{([^}]*)\}")
_DIMENSION_PAIR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _alarm_dimensions(tf_file: str, resource_name: str) -> dict[str, str] | None:
    block = find_resource_block((_TF_DIR / tf_file).read_text(encoding="utf-8"), "aws_cloudwatch_metric_alarm", resource_name)
    match = _DIMENSIONS_RE.search(block)
    return dict(_DIMENSION_PAIR_RE.findall(match.group(1))) if match else None


def test_emit_maintenance_metric_carries_smoke_dimension():
    with patch.object(h.rt, "emit_metric") as mock_emit:
        h._emit_maintenance_metric("MaintenanceBreakerTrip", 1.0)
    mock_emit.assert_called_once_with(
        "MaintenanceBreakerTrip",
        1.0,
        namespace=h.maint.MAINTENANCE_CLOUDWATCH_NAMESPACE,
        profile=None,
        dimensions={"Catalog": "smoke"},
    )


def test_smoke_alarm_selects_the_handler_dimension():
    assert _alarm_dimensions("ducklake_maintenance_smoke.tf", "ducklake_maintenance_smoke_breaker") == h.METRIC_DIMENSIONS


def test_production_breaker_alarm_declares_no_dimensions():
    """CloudWatch matches an alarm's dimension set exactly: with none declared, the production alarm
    sees only the admin function's undimensioned datum, never the smoke Catalog=smoke one that the
    per-deploy breaker_probe gate trips on purpose."""
    assert _alarm_dimensions("ducklake_maintenance.tf", "ducklake_maintenance_breaker") is None


def test_smoke_breaker_trip_reason_is_logged(capsys):
    with patch.object(h, "_open_connection", return_value=MagicMock()):
        with patch.object(h, "_emit_maintenance_metric"):
            h.handler({"action": "breaker_probe"})
    out = capsys.readouterr().out
    assert "MAINTENANCE_SMOKE_FAILURE status=500" in out
    assert "G1 reachability" in out


def test_smoke_response_logs_only_server_errors(capsys):
    h._response(200, {"ok": True})
    h._response(400, {"ok": False})
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# action_hot_merge
# ---------------------------------------------------------------------------


def _hot_merge_con() -> FakeCon:
    return FakeCon(fetchall=[], fetchone_map={"ducklake_list_files": (3,), "count(*)": (3,)})


def test_action_hot_merge_ok():
    con = _hot_merge_con()
    with patch.object(h.maint, "run_hot_merge") as mock_hot:
        mock_hot.return_value = {"ok": True, "action": "hot_merge", "tables": ["t1"], "files_before": 3, "files_after": 2}
        result = h.action_hot_merge({}, con)
    assert result["ok"] is True
    assert result["action"] == "hot_merge"
    assert "elapsed_ms" in result


def test_action_hot_merge_no_destructive_dispatch():
    """action_hot_merge must not call run_gc or any destructive function."""
    con = _hot_merge_con()
    with patch.object(h.maint, "run_hot_merge") as mock_hot:
        mock_hot.return_value = {"ok": True, "action": "hot_merge", "tables": [], "files_before": 0, "files_after": 0}
        with patch.object(h.maint, "run_gc") as mock_gc:
            h.action_hot_merge({}, con)
    mock_gc.assert_not_called()


def test_action_hot_merge_emits_metrics():
    con = _hot_merge_con()
    with patch.object(h.maint, "run_hot_merge") as mock_hot:
        mock_hot.return_value = {"ok": True, "action": "hot_merge", "tables": [], "files_before": 3, "files_after": 2}
        with patch.object(h, "_emit_maintenance_metric") as mock_emit:
            h.action_hot_merge({}, con)
    metric_names = [c.args[0] for c in mock_emit.call_args_list]
    assert "HotMergeDurationMs" in metric_names
    assert "FilesBeforeHotMerge" in metric_names
    assert "FilesAfterHotMerge" in metric_names


def test_action_hot_merge_force_recreate():
    con = _hot_merge_con()
    _ret = {"ok": True, "action": "hot_merge", "tables": [], "files_before": 0, "files_after": 0}
    with patch.object(h.maint, "run_hot_merge", return_value=_ret):
        with patch.object(h.rt, "create_scd2_tables") as mock_create:
            h.action_hot_merge({"force_recreate_tables": True}, con)
    mock_create.assert_called_once()


def test_handler_hot_merge_dispatch():
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        _ret = {"ok": True, "action": "hot_merge", "tables": [], "files_before": 0, "files_after": 0, "elapsed_ms": 1.0}
        good = MagicMock(return_value=_ret)
        with patch.dict(h._ACTIONS, {"hot_merge": good}):
            with patch.object(h, "_emit_maintenance_metric"):
                r = h.handler({"action": "hot_merge"})
    assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# handler dispatch + error mapping
# ---------------------------------------------------------------------------


def test_handler_missing_action():
    r = h.handler({})
    assert r["statusCode"] == 400


def test_handler_connection_closed_on_success():
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        good = MagicMock(return_value={"ok": True, "action": "merge", "tables": [], "files_after_merge": 0, "elapsed_ms": 1.0})
        with patch.dict(h._ACTIONS, {"merge": good}):
            with patch.object(h, "_emit_maintenance_metric"):
                h.handler({"action": "merge"})
        mock_con.close.assert_called_once()


def test_handler_connection_closed_on_error():
    raiser = MagicMock(side_effect=DuckLakeMaintenanceError("trip"))
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        with patch.dict(h._ACTIONS, {"merge": raiser}):
            with patch.object(h, "_emit_maintenance_metric"):
                h.handler({"action": "merge"})
        mock_con.close.assert_called_once()


def test_handler_version_mismatch_maps_to_500():
    raiser = MagicMock(side_effect=VersionMismatchError("bad version"))
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        with patch.dict(h._ACTIONS, {"merge": raiser}):
            r = h.handler({"action": "merge"})
    assert r["statusCode"] == 500
    body = _response_body(r)
    assert body["error_type"] == "version_mismatch"


def test_handler_runtime_error_maps_to_500():
    raiser = MagicMock(side_effect=DuckLakeRuntimeError("runtime fail"))
    with patch.object(h, "_open_connection") as mock_open:
        mock_con = MagicMock()
        mock_open.return_value = mock_con
        with patch.dict(h._ACTIONS, {"merge": raiser}):
            r = h.handler({"action": "merge"})
    assert r["statusCode"] == 500
    body = _response_body(r)
    assert body["error_type"] == "runtime"


# ---------------------------------------------------------------------------
# create_telemetry_tables (Decision 216): owner-login, create-only, smoke-pinned, typed failures
# ---------------------------------------------------------------------------

CLEAN = prov.OwnershipReport(0, True, False, False, True, "ducklake_smoke_owner")
NINE = tuple(f"t{i}" for i in range(9))


class _Rec(FakeCon):
    def __init__(self):
        super().__init__()
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        return super().execute(sql, params)


def _patched(reports=(CLEAN, CLEAN), create=None):
    report = MagicMock(side_effect=list(reports))
    made = create or MagicMock(return_value=prov.CreateResult(NINE, NINE[:2]))
    return (
        patch.object(ta.prov, "ownership_report", report),
        patch.object(ta.prov, "create_telemetry_tables", made),
        report,
        made,
    )


def _run(event=None, con=None, **kw):
    p1, p2, report, made = _patched(**kw)
    con = con or _Rec()
    with p1, p2:
        return ta.action_create_telemetry_tables(event or {"action": "create_telemetry_tables"}, con), con, report, made


def test_create_telemetry_event_keys_are_never_read():
    decoy = {"action": "create_telemetry_tables", "meta_schema": "ducklake_ops", "data_path": "s3://x/", "table": "ops_x"}
    out, con, report, made = _run(decoy)
    assert out["ok"] is True
    assert [c.kwargs["meta_schema"] for c in report.call_args_list] == [h.rt.SMOKE_META_SCHEMA] * 2
    assert "ducklake_ops" not in str(report.call_args_list) + str(made.call_args_list) + "".join(con.sql)


def test_create_telemetry_non_smoke_meta_schema_refuses_before_any_statement(monkeypatch):
    monkeypatch.setenv("DUCKLAKE_META_SCHEMA", "ducklake_ops")
    p1, p2, report, made = _patched()
    con = _Rec()
    with p1, p2, pytest.raises(DuckLakeRuntimeError, match="pinned to the smoke metadata schema"):
        ta.action_create_telemetry_tables({}, con)
    assert con.sql == [] and not report.called and not made.called


def test_create_telemetry_owner_mismatch_refuses_before_any_ddl():
    for dirty in (
        prov.OwnershipReport(1, True, False, False, True, "ducklake_smoke_owner"),
        prov.OwnershipReport(0, False, False, False, False, "ducklake_ops_other"),
        prov.OwnershipReport(0, True, True, False, True, "ducklake_smoke_owner"),
        prov.OwnershipReport(0, True, False, True, True, "ducklake_smoke_owner"),
    ):
        p1, p2, _report, made = _patched(reports=(dirty,))
        con = _Rec()
        with p1, p2, pytest.raises(DuckLakeRuntimeError, match=r"single-owner check failed \(pre-DDL\)"):
            ta.action_create_telemetry_tables({}, con)
        assert not made.called and con.sql == []


def test_create_telemetry_passes_when_the_owner_is_held_through_membership():
    member = prov.OwnershipReport(0, False, False, False, True, "ducklake_smoke_owner")
    p1, p2, _report, made = _patched(reports=(member, member))
    with p1, p2:
        out = ta.action_create_telemetry_tables({}, _Rec())
    assert out["ok"] is True and made.called
    assert out["ownership"]["schema_owner_held"] is True and out["ownership"]["schema_owner_is_current_user"] is False
    not_held = prov.OwnershipReport(0, True, False, False, False, "ducklake_smoke_owner")
    p1, p2, _report, made = _patched(reports=(not_held,))
    with p1, p2, pytest.raises(DuckLakeRuntimeError, match=r"single-owner check failed \(pre-DDL\)"):
        ta.action_create_telemetry_tables({}, _Rec())
    assert not made.called


def test_create_telemetry_single_owner_violation_raises_with_report_fields_only():
    p1, p2, _report, made = _patched(reports=(CLEAN, prov.OwnershipReport(2, True, False, True, True, "ducklake_smoke_owner")))
    with p1, p2, pytest.raises(DuckLakeRuntimeError) as info:
        ta.action_create_telemetry_tables({}, _Rec())
    text = str(info.value)
    assert "post-DDL" in text and "'foreign_owned': 2" in text and "'scoped_create_on_public': True" in text
    assert made.called and "Traceback" not in text


@pytest.mark.parametrize("where", ["create", "ownership"])
def test_create_telemetry_unmapped_txn_error_names_the_class_only(where, capsys):
    sentinel = RuntimeError("connection to sentinel-host.example failed")
    if where == "create":
        p1, p2, _r, _m = _patched(create=MagicMock(side_effect=sentinel))
    else:
        p1, p2, _r, _m = _patched(reports=(sentinel,))
    with p1, p2, pytest.raises(DuckLakeRuntimeError) as info:
        ta.action_create_telemetry_tables({}, _Rec())
    assert "RuntimeError" in str(info.value) and "sentinel-host" not in str(info.value)
    assert "sentinel-host" in capsys.readouterr().err


def test_create_telemetry_ownership_read_failure_names_the_class_only(capsys):
    boom = ValueError("password for sentinel-host.example rejected")
    p1, p2, _report, made = _patched(reports=(boom,))
    with p1, p2, pytest.raises(DuckLakeRuntimeError) as info:
        ta.action_create_telemetry_tables({}, _Rec())
    assert str(info.value) == "create_telemetry_tables failed: ValueError" and not made.called
    assert "sentinel-host" in capsys.readouterr().err


def test_create_telemetry_response_shape():
    out, con, _report, _made = _run()
    assert set(out) == {"ok", "tables", "created", "added", "ownership", "elapsed_ms"}
    assert out["added"] == []
    assert out["tables"] == sorted(NINE) and out["created"] == sorted(NINE[:2])
    assert out["ownership"] == {
        "foreign_owned": 0,
        "schema_owner_is_current_user": True,
        "schema_owner_held": True,
        "schema_owner": "ducklake_smoke_owner",
        "scoped_create_on_schema": False,
        "scoped_create_on_public": False,
    }
    assert con.sql[0] == "BEGIN TRANSACTION" and con.sql[-1] == "COMMIT"


def test_create_telemetry_response_lists_added_columns():
    added = ("telemetry_sessions.checkout_sha", "telemetry_observations.usage_final")
    made = MagicMock(return_value=prov.CreateResult(NINE, (), added))
    out, _con, _report, _made = _run(create=made)
    assert out["added"] == sorted(added) and out["created"] == []


def test_create_telemetry_handler_returns_500_runtime_never_raises():
    p1, p2, _report, _made = _patched(reports=(RuntimeError("sentinel-host.example"),))
    with p1, p2, patch.object(h, "_open_connection", return_value=_Rec()):
        r = h.handler({"action": "create_telemetry_tables"})
    body = _response_body(r)
    assert r["statusCode"] == 500 and body["error_type"] == "runtime" and "sentinel-host" not in r["body"]
    p1, p2, _report, _made = _patched()
    with p1, p2, patch.object(h, "_open_connection", return_value=_Rec()):
        assert h.handler({"action": "create_telemetry_tables"})["statusCode"] == 200
