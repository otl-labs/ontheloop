"""Envelope + core handler dispatch + error-mapping concern for
src/lambdas/ducklake_writer/handler.py (T2.17, 100% coverage, mocked runtime).

Split from the former tests/test_ducklake_writer_handler.py monolith (rec-2709 Wave 8).
Functions copied VERBATIM.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest

import src.lambdas.ducklake_writer.handler as h
from src.common import ducklake_runtime as rt
from src.common.ducklake_version import pinned_duckdb_version
from src.lambdas.ducklake_writer import smoke_actions
from tests.fixtures.ducklake_writer_handler import FakeCon, _result

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _parse_event / _response
# ---------------------------------------------------------------------------


def test_parse_event_body_string():
    assert h._parse_event({"body": json.dumps({"action": "write"})}) == {"action": "write"}


def test_parse_event_body_dict():
    assert h._parse_event({"body": {"action": "x"}}) == {"action": "x"}


def test_parse_event_body_empty_string():
    assert h._parse_event({"body": ""}) == {}


def test_parse_event_direct_dict():
    assert h._parse_event({"action": "y"}) == {"action": "y"}


def test_parse_event_non_dict():
    assert h._parse_event("nonsense") == {}


def test_response_envelope():
    r = h._response(200, {"ok": True})
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True
    assert r["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# handler dispatch + error mapping
# ---------------------------------------------------------------------------


def test_handler_unknown_action():
    r = h.handler({"action": "nope"})
    assert r["statusCode"] == 400
    assert "unknown action" in json.loads(r["body"])["error"]


def test_handler_attach_check(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(
        rt.ducklake_spike, "_require_duckdb", lambda: types.SimpleNamespace(__version__=pinned_duckdb_version())
    )
    r = h.handler({"action": "attach_check"})
    body = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert body["version"] == pinned_duckdb_version()
    assert body["source"] == "layer"
    # D2 warm reuse: the single-statement path keeps the connection open for the next invocation.
    assert con.closed is False
    assert body["connect_reused"] is False  # first (cold) acquisition in this container


def test_handler_create_tables(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    called = {}
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: called.update(force=force_recreate))
    r = h.handler({"action": "create_tables", "force_recreate_tables": True})
    assert json.loads(r["body"])["force_recreate"] is True
    assert called["force"] is True


def test_handler_write(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result(ulid="01W", rec_id=rec["rec_id"]))
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "write", "record": {"rec_id": "rec-9", "payload": "p"}})
    body = json.loads(r["body"])
    assert body["ulid"] == "01W"
    assert body["rec_id"] == "rec-9"


def test_handler_write_with_force_recreate(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    flags = {}
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: flags.update(f=force_recreate))
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result())
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    h.handler({"action": "write", "record": {"rec_id": "r"}, "force_recreate_tables": True})
    assert flags["f"] is True


def test_handler_idempotency_probe(monkeypatch):
    con = FakeCon(
        fetchone_map={"FROM ops_catalog.ducklake_smoke_history": (1,), "FROM ops_catalog.ducklake_smoke_current": (1,)}
    )
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "mint_write_identity", lambda: rt.WriteIdentity("01ID", datetime(2026, 1, 1, tzinfo=timezone.utc)))
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result(ulid="01ID"))
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "idempotency_probe"})
    body = json.loads(r["body"])
    assert body["ulid_reused"] is True
    assert body["history_rows"] == 1
    assert body["current_rows"] == 1


def test_handler_partition_probe_measures_calendar_day_tuples(monkeypatch):
    """rec-4068: the probe response carries MEASURED calendar-day tuple/layout fields, no constants."""
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "mint_write_identity", lambda: rt.WriteIdentity("01ID", datetime(2026, 1, 1, tzinfo=timezone.utc)))
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result())

    def _fake_layout(con, catalog_alias, table):
        if "history" in table:
            return {
                "total": 4,
                "path_prefixes": {
                    1: "year=2026/month=1/day=24",
                    2: "year=2026/month=2/day=24",
                    3: "year=2027/month=1/day=24",
                    4: "year=2026/month=1/day=24",
                },
                "value_tuples": {
                    1: ("2026", "1", "24"),
                    2: ("2026", "2", "24"),
                    3: ("2027", "1", "24"),
                    4: ("2026", "1", "24"),
                },
            }
        return {
            "total": 8,
            "path_prefixes": {5: "bucket=0", 6: "bucket=1", 7: "bucket=0", 8: "bucket=0"},
            "value_tuples": {5: ("0",), 6: ("1",), 7: ("0",), 8: ("0",)},
        }

    monkeypatch.setattr(smoke_actions, "_partition_layout", _fake_layout)
    r = h.handler({"action": "partition_probe"})
    body = json.loads(r["body"])
    assert body["history_calendar_days"] == 3
    assert body["history_partition_tuples"] == 3
    assert body["history_total"] == 4
    assert body["current_total"] == 8
    assert body["history_files_in_probed_day"] < body["history_total"]
    assert body["current_files_in_probed_bucket"] < body["current_total"]


def test_handler_inlining_probe(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result())
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    monkeypatch.setattr(smoke_actions, "_count_files", lambda c, t: 2)
    monkeypatch.setattr(smoke_actions, "_count_inlined_rows", lambda c, t: 0)
    monkeypatch.setattr(smoke_actions, "_concurrency_probe", lambda w: True)
    r = h.handler({"action": "inlining_probe"})
    body = json.loads(r["body"])
    assert body["inlined_rows"] == 0
    assert body["s3_parquet"] == 2
    assert body["occ_conflicts_handled"] is True


def test_handler_loudfail_probe(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    r = h.handler({"action": "loudfail_probe"})
    body = json.loads(r["body"])
    assert body["schema_reject"] == "raised"
    assert body["occ_exhaust"] == "raised"
    assert body["silent_drop"] is False


def test_loudfail_probe_reports_not_raised_when_gates_broken(monkeypatch):
    """Degenerate case: if the loud-fail mechanisms did NOT raise, the probe reports not_raised.

    This is the failure signal the live VP step 17 catches -- it must be observable, not masked.
    """
    con = FakeCon()
    monkeypatch.setattr(rt, "create_scd2_tables", lambda c, force_recreate=False: None)
    monkeypatch.setattr(rt, "schema_gate", lambda rec, sem=None: None)  # broken: does not raise
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: _result())  # broken: does not raise
    out = h.action_loudfail_probe({}, con)
    assert out["schema_reject"] == "not_raised"
    assert out["occ_exhaust"] == "not_raised"


def test_handler_schema_gate_error_maps_422(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)

    def _raise(c, rec, **kw):
        raise rt.SchemaGateError("bad field")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "write", "record": {"rec_id": "r"}})
    assert r["statusCode"] == 422
    assert json.loads(r["body"])["error_type"] == "schema_gate"


def test_handler_occ_exhausted_maps_503(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)

    def _raise(c, rec, **kw):
        raise rt.OCCRetryExhaustedError("exhausted")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "write", "record": {"rec_id": "r"}})
    assert r["statusCode"] == 503
    assert json.loads(r["body"])["error_type"] == "occ_exhausted"


def test_handler_version_mismatch_maps_500(monkeypatch):
    def _raise():
        raise rt.VersionMismatchError("mismatch")

    monkeypatch.setattr(h, "_open_writer_connection", _raise)
    r = h.handler({"action": "attach_check"})
    assert r["statusCode"] == 500
    assert json.loads(r["body"])["error_type"] == "version_mismatch"


def test_handler_runtime_error_maps_500(monkeypatch):
    def _raise():
        raise rt.DuckLakeRuntimeError("boom")

    monkeypatch.setattr(h, "_open_writer_connection", _raise)
    r = h.handler({"action": "attach_check"})
    assert r["statusCode"] == 500
    assert json.loads(r["body"])["error_type"] == "runtime"


def test_handler_status_transition_error_maps_422(monkeypatch):
    """A resolved-rec reactivation (StatusTransitionError) maps to 422 error_type=status_transition."""
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)

    def _raise(c, rec, **kw):
        raise rt.StatusTransitionError("illegal status transition 'closed' -> 'open'")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "update_ops", "table": "ops_recommendations", "record": {"id": "rec-1", "status": "open"}})
    assert r["statusCode"] == 422
    assert json.loads(r["body"])["error_type"] == "status_transition"


def test_row_rule_violation_maps_to_422(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_writer_connection", lambda: con)

    def _raise(c, rec, **kw):
        raise rt.RowRuleViolationError("ops_recommendations", "min_length", "title", "2026-09-01")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "write", "record": {"rec_id": "r"}})
    body = json.loads(r["body"])
    assert r["statusCode"] == 422
    assert body["ok"] is False and body["error_type"] == "row_rule"
    assert (body["table"], body["rule"], body["column"], body["exclude_before"]) == (
        "ops_recommendations",
        "min_length",
        "title",
        "2026-09-01",
    )
    assert "row rule min_length violated" in body["error"]


def test_handler_unmapped_exception_is_typed_500(monkeypatch, capsys):
    def _raise():
        raise KeyError("pass" + "word=hunter2 s3://bucket/key")

    monkeypatch.setattr(h, "_open_writer_connection", _raise)
    r = h.handler({"action": "attach_check"}, types.SimpleNamespace(aws_request_id="req-9"))
    body = json.loads(r["body"])
    assert r["statusCode"] == 500
    assert (body["error_type"], body["error_class"], body["action"], body["request_id"]) == (
        "unmapped",
        "KeyError",
        "attach_check",
        "req-9",
    )
    assert "hunter2" not in r["body"] and "s3://" not in r["body"]
    out = capsys.readouterr().out
    assert '"ducklake_unmapped_error"' in out and "hunter2" not in out
    assert [json.loads(x)["status"] for x in out.splitlines() if '"ducklake_invocation"' in x] == [500]


def test_handler_dead_connection_after_reopen_is_503(monkeypatch):
    monkeypatch.setattr(h, "_open_writer_connection", lambda: FakeCon())
    monkeypatch.setattr(
        rt, "write_scd2", lambda c, rec, **kw: (_ for _ in ()).throw(Exception("server closed the connection"))
    )
    monkeypatch.setattr(rt, "make_metric_sink", lambda **kw: lambda n, v: None)
    r = h.handler({"action": "write", "record": {"rec_id": "r"}})
    body = json.loads(r["body"])
    assert r["statusCode"] == 503
    assert (body["error_type"], body["reopened"]) == ("catalog_unavailable", True)
    assert "server closed" not in r["body"]


def test_handler_unparseable_body_is_400(capsys):
    r = h.handler({"body": "{not json"})
    assert r["statusCode"] == 400 and json.loads(r["body"])["error_type"] == "bad_request"
    line = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"ducklake_invocation"' in x][0]
    assert (line["action"], line["status"]) == ("unknown", 400)


def test_handler_non_object_body_is_400():
    for raw in ("[1]", '"text"', "[" * 100000):
        r = h.handler({"body": raw})
        assert r["statusCode"] == 400 and json.loads(r["body"])["error_type"] == "bad_request"
