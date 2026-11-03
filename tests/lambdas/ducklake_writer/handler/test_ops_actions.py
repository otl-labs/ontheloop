"""T2.19 production ops verbs (write_ops/update_ops/create_ops_tables/file_ops) + connect_probe
concern for src/lambdas/ducklake_writer/handler.py (T2.17, 100% coverage, mocked runtime).

Split from the former tests/test_ducklake_writer_handler.py monolith (rec-2709 Wave 8).
Functions copied VERBATIM. The connect_probe tests do a lazy first-party
`import src.common.ducklake_connect_probe as p` and file_ops tests a lazy first-party
`from src.common.ducklake_scd2_schema import WriteResult` -- NEITHER is a heavy dep, so this
module carries NO heavy-dep marker (preserves the monolith's no-marker state for this concern).
"""

from __future__ import annotations

import json

import pytest

import src.lambdas.ducklake_writer.handler as h
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_writer_handler import FakeCon, _result

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# T2.19 production ops actions: write_ops / update_ops / create_ops_tables
# ---------------------------------------------------------------------------


def test_action_write_ops_dispatches_to_runtime(monkeypatch):
    captured = {}

    def _write(con, record, *, table, **kw):  # noqa: ARG001
        captured["table"] = table
        captured["record"] = record
        return _result(ulid="01W", rec_id="rec-9")

    monkeypatch.setattr(rt, "write_scd2", _write)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    out = h.action_write_ops({"table": "ops_recommendations", "record": {"id": "rec-9", "status": "open"}}, FakeCon())
    assert out["ok"] is True
    assert out["table"] == "ops_recommendations"
    assert out["key"] == "rec-9"
    assert captured["table"] == "ops_recommendations"


def test_action_update_ops_sets_require_exists(monkeypatch):
    captured = {}

    def _write(con, record, *, table, require_exists=False, **kw):  # noqa: ARG001
        captured["require_exists"] = require_exists
        return _result(rec_id=record["id"])

    monkeypatch.setattr(rt, "write_scd2", _write)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    out = h.action_update_ops({"table": "ops_recommendations", "record": {"id": "rec-1", "status": "closed"}}, FakeCon())
    assert out["ok"] is True
    assert captured["require_exists"] is True


def test_action_create_ops_tables(monkeypatch):
    calls = {}

    def _create(con, *, table, force_recreate):  # noqa: ARG001
        calls.update(table=table, force=force_recreate)

    monkeypatch.setattr(rt, "create_scd2_tables", _create)
    out = h.action_create_ops_tables(
        {"table": "ops_decisions", "force_recreate_tables": True, "confirm_force_recreate": "ops_decisions"},
        FakeCon(),
    )
    assert out["ok"] is True
    assert calls == {"table": "ops_decisions", "force": True}
    assert out["tables"] == ["ops_decisions_history", "ops_decisions_current"]


def test_action_create_ops_tables_force_requires_confirm(monkeypatch):
    """Destructive-action guard (Decision 84): force_recreate without confirm loud-fails."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda con, *, table, force_recreate: None)
    with pytest.raises(h.WriterActionError, match="confirm_force_recreate"):
        h.action_create_ops_tables({"table": "ops_decisions", "force_recreate_tables": True}, FakeCon())


def test_action_create_ops_tables_plain_create_needs_no_confirm(monkeypatch):
    calls = {}

    def _create(con, *, table, force_recreate):  # noqa: ARG001
        calls.update(table=table, force=force_recreate)

    monkeypatch.setattr(rt, "create_scd2_tables", _create)
    out = h.action_create_ops_tables({"table": "ops_priority_queue"}, FakeCon())
    assert out["ok"] is True
    assert calls == {"table": "ops_priority_queue", "force": False}


def test_action_file_ops(monkeypatch):
    captured = {}

    def _file(con, record, *, table, identity, metric_sink):  # noqa: ARG001
        captured.update(record=dict(record), table=table, identity=identity)
        from datetime import datetime, timezone

        from src.common.ducklake_scd2_schema import WriteResult

        ts = datetime(2026, 6, 11, tzinfo=timezone.utc)
        return WriteResult(
            ulid=identity.ulid if identity else "01AUTO",
            rec_id="rec-2171",
            occ_retries=0,
            commit_ms=12.0,
            created_timestamp=ts,
            last_updated_timestamp=ts,
        )

    monkeypatch.setattr(rt, "file_scd2", _file)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    out = h.action_file_ops(
        {
            "table": "ops_recommendations",
            "record": {"title": "t", "status": "open"},
            "idempotency_ulid": "01JXQ4N9V8TEST0000000000",
        },
        FakeCon(),
    )
    assert out["ok"] is True
    assert out["key"] == "rec-2171"
    assert captured["table"] == "ops_recommendations"
    assert captured["identity"].ulid == "01JXQ4N9V8TEST0000000000"


def test_action_file_ops_rejects_bad_idempotency_ulid(monkeypatch):
    monkeypatch.setattr(rt, "file_scd2", lambda *a, **k: None)
    with pytest.raises(h.WriterActionError, match="idempotency_ulid"):
        h.action_file_ops({"table": "ops_recommendations", "record": {}, "idempotency_ulid": "no spaces!"}, FakeCon())


def test_action_file_ops_without_idempotency_mints_identity(monkeypatch):
    captured = {}

    def _file(con, record, *, table, identity, metric_sink):  # noqa: ARG001
        captured["identity"] = identity
        from datetime import datetime, timezone

        from src.common.ducklake_scd2_schema import WriteResult

        ts = datetime(2026, 6, 11, tzinfo=timezone.utc)
        return WriteResult("01X", "rec-1", 0, 1.0, ts, ts)

    monkeypatch.setattr(rt, "file_scd2", _file)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    out = h.action_file_ops({"table": "ops_recommendations", "record": {"title": "t"}}, FakeCon())
    assert out["ok"] is True
    assert captured["identity"] is None


def test_require_ops_table_rejects_unknown():
    with pytest.raises(h.WriterActionError, match="unknown or missing ops table"):
        h._require_ops_table("not_a_table", direction="write")


def test_require_ops_table_rejects_non_string():
    with pytest.raises(h.WriterActionError):
        h._require_ops_table(None, direction="write")


def test_require_ops_table_rejects_control_class_table():
    """T2.26: the control-class refusal is class-specific, checked before the unknown-table case."""
    with pytest.raises(h.WriterActionError, match="control-class table"):
        h._require_ops_table("ops_entity_counters", direction="write")


def test_handler_write_ops_unknown_table_returns_400(monkeypatch):
    monkeypatch.setattr(h, "_open_writer_connection", lambda: FakeCon())
    resp = h.handler({"action": "write_ops", "table": "bogus", "record": {}})
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error_type"] == "action"


def test_handler_update_ops_referential_returns_409(monkeypatch):
    monkeypatch.setattr(h, "_open_writer_connection", lambda: FakeCon())

    def _raise(*a, **k):
        raise rt.ReferentialError("absent rec-x")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    resp = h.handler({"action": "update_ops", "table": "ops_recommendations", "record": {"id": "rec-x", "status": "closed"}})
    assert resp["statusCode"] == 409
    assert json.loads(resp["body"])["error_type"] == "referential"


# ---------------------------------------------------------------------------
# connect_probe: runs WITHOUT a pre-opened connection
# ---------------------------------------------------------------------------


def test_handler_reopens_on_dead_connection_and_retries(monkeypatch):
    """A dead-session error on the single-statement write path reopens ONCE and retries (D2).

    Production write actions are replay-safe under retry (file_ops via the client ULID; update/write_ops
    MERGE-idempotent), and a connection death aborts the catalog txn, so the retry commits exactly once.
    """
    opens: list[int] = []
    monkeypatch.setattr(h, "_open_writer_connection", lambda: opens.append(1) or FakeCon())
    calls = {"n": 0}

    def flaky_write_scd2(con, rec, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("could not connect to server: connection refused")  # dead session
        return _result()

    monkeypatch.setattr(rt, "write_scd2", flaky_write_scd2)
    r = h.handler({"action": "write_ops", "table": "ops_recommendations", "record": {"id": "rec-1"}})
    body = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert body["ok"] is True
    assert len(opens) == 2  # cold open + one reopen


def test_handler_reset_warm_connection(monkeypatch):
    """reset_warm_connection is connectionless: it drops the warm cache without opening a connection (D2 VP)."""

    def _should_not_open():
        raise AssertionError("reset_warm_connection must not open a connection")

    monkeypatch.setattr(h, "_open_writer_connection", _should_not_open)
    reset_called = {"n": 0}
    monkeypatch.setattr(rt, "reset_warm_connection", lambda: reset_called.__setitem__("n", reset_called["n"] + 1))
    r = h.handler({"action": "reset_warm_connection"})
    body = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert body == {"ok": True, "reset": True}
    assert reset_called["n"] == 1


def test_handler_connect_probe_runs_without_connection(monkeypatch):
    """connect_probe action returns the structured probe result even when _open_writer_connection would hang.

    Proves the probe is dispatched via _CONNECTIONLESS_ACTIONS ahead of _open_writer_connection.
    """
    hang_called = {"called": False}

    def _hanging_open():
        hang_called["called"] = True
        raise RuntimeError("hang: should not be called for connect_probe")

    monkeypatch.setattr(h, "_open_writer_connection", _hanging_open)
    fake_result = {
        "phase_reached": "tcp",
        "failed_phase": "auth",
        "dns_ms": 5.0,
        "tcp_ms": 8.0,
        "auth_ms": 12.0,
        "attach_ms": None,
        "ok": False,
        "error": "AUTH: authentication failed",
    }
    import src.common.ducklake_connect_probe as p

    monkeypatch.setattr(p, "probe_connection", lambda dsn, **kw: fake_result)
    _fake_dsn = {"host": "h", "dbname": "d", "username": "u", "password": "p"}  # pragma: allowlist secret
    monkeypatch.setattr(rt, "fetch_dsn", lambda: _fake_dsn)

    r = h.handler({"action": "connect_probe"})
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["failed_phase"] == "auth"
    assert body["ok"] is False
    assert hang_called["called"] is False, "_open_writer_connection must NOT be called for connect_probe"


def test_handler_connect_probe_success(monkeypatch):
    """connect_probe returns ok=True and phase_reached=attach on a successful probe."""
    monkeypatch.setattr(h, "_open_writer_connection", lambda: (_ for _ in ()).throw(RuntimeError("should not be called")))
    success_result = {
        "phase_reached": "attach",
        "failed_phase": None,
        "dns_ms": 2.0,
        "tcp_ms": 5.0,
        "auth_ms": 50.0,
        "attach_ms": 1200.0,
        "ok": True,
        "error": None,
    }
    import src.common.ducklake_connect_probe as p

    monkeypatch.setattr(p, "probe_connection", lambda dsn, **kw: success_result)
    _fake_dsn = {"host": "h", "dbname": "d", "username": "u", "password": "p"}  # pragma: allowlist secret
    monkeypatch.setattr(rt, "fetch_dsn", lambda: _fake_dsn)

    r = h.handler({"action": "connect_probe"})
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["phase_reached"] == "attach"
    assert body["failed_phase"] is None


def test_connect_probe_in_connectionless_actions():
    """connect_probe must be in _CONNECTIONLESS_ACTIONS so it bypasses _open_writer_connection."""
    assert "connect_probe" in h._CONNECTIONLESS_ACTIONS


def test_action_create_ops_tables_caller_keyspace_skips_counter(monkeypatch):
    """ops_decisions has a caller-owned keyspace (DECISIONS.md numbering): no counter is seeded."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda con, *, table, force_recreate: None)
    called = []
    monkeypatch.setattr(rt, "bootstrap_entity_counter", lambda con, spec: called.append(spec.table))
    out = h.action_create_ops_tables({"table": "ops_decisions"}, FakeCon())
    assert out["ok"] is True
    assert out["counter_seed"] is None
    assert called == []


def test_action_create_ops_tables_writer_keyspace_seeds_counter(monkeypatch):
    """ops_recommendations has a writer-owned keyspace: provisioning seeds/repairs its counter."""
    monkeypatch.setattr(rt, "create_scd2_tables", lambda con, *, table, force_recreate: None)
    seeded = []
    monkeypatch.setattr(rt, "bootstrap_entity_counter", lambda con, spec: (seeded.append(spec.table), 2178)[1])
    out = h.action_create_ops_tables({"table": "ops_recommendations"}, FakeCon())
    assert out["ok"] is True
    assert out["counter_seed"] == 2178
    assert seeded == ["ops_recommendations"]


def test_action_create_ops_tables_control_class_table(monkeypatch):
    """A control-class table (T2.26) branches before _require_ops_table's generic refusal --
    this verb is its only legitimate provisioning path: one CREATE + partition, no counter seed."""
    calls = {}

    def _create_control(con, *, table, force_recreate):  # noqa: ARG001
        calls.update(table=table, force=force_recreate)

    monkeypatch.setattr(rt, "create_control_table", _create_control)
    out = h.action_create_ops_tables(
        {"table": "ops_entity_counters", "force_recreate_tables": True, "confirm_force_recreate": "ops_entity_counters"},
        FakeCon(),
    )
    assert out["ok"] is True
    assert calls == {"table": "ops_entity_counters", "force": True}
    assert out["tables"] == ["ops_entity_counters"]
    assert out["counter_seed"] is None


def test_action_file_ops_rejects_caller_keyspace_table(monkeypatch):
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    with pytest.raises(rt.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        h.action_file_ops({"table": "ops_decisions", "record": {"title": "t", "status": "open"}}, FakeCon())


def test_action_write_ops_append_only_table(monkeypatch):
    """write_ops on ops_smoke_events (append_only) routes through write_scd2 without error."""
    captured = {}

    def _write(con, record, *, table, **kw):  # noqa: ARG001
        captured["table"] = table
        captured["record"] = record
        return _result(ulid="01AO", rec_id="test-ao-event-1")

    monkeypatch.setattr(rt, "write_scd2", _write)
    monkeypatch.setattr(rt, "make_metric_sink", lambda: None)
    out = h.action_write_ops(
        {"table": "ops_smoke_events", "record": {"event_id": "test-ao-event-1", "event_type": "smoke"}},
        FakeCon(),
    )
    assert out["ok"] is True
    assert out["table"] == "ops_smoke_events"
    assert captured["table"] == "ops_smoke_events"


_REFUSED_TABLES = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_transcripts",
    "telemetry_agents",
    "ops_tenants",
    "ops_projects",
)


def test_generic_verbs_refuse_telemetry_and_registration_tables(monkeypatch):
    """write_ops, update_ops, file_ops and create_ops_tables each refuse all six tables with the directed
    error naming the write_boundary, before any runtime call (so no generic path can write or create them)."""
    called = []
    for name in ("write_scd2", "file_scd2", "create_scd2_tables"):
        monkeypatch.setattr(rt, name, lambda *a, _n=name, **k: called.append(_n))
    for table in _REFUSED_TABLES:
        boundary = "registration" if table.startswith("ops_") else "telemetry_append"
        for action in (h.action_write_ops, h.action_update_ops, h.action_file_ops, h.action_create_ops_tables):
            with pytest.raises(
                h.WriterActionError, match=rf"written only by the telemetry verbs \(write_boundary={boundary}\)"
            ):
                action({"table": table, "record": {}}, FakeCon())
    assert called == []


_DORMANT_TABLES = ("ops_priority_queue", "ops_execution_plans")


def test_dormant_tables_refuse_generic_writes_but_provision(monkeypatch):
    """write_ops, file_ops and update_ops answer a directed 400 naming dormant before any runtime call; create_ops_tables
    still provisions each dormant table (catalog reinit, restore and the DQ reads of the empty tables)."""
    called = []
    for name in ("write_scd2", "file_scd2"):
        monkeypatch.setattr(rt, name, lambda *a, _n=name, **k: called.append(_n))
    provisioned = []
    monkeypatch.setattr(rt, "create_scd2_tables", lambda con, *, table, force_recreate: provisioned.append(table))
    for table in _DORMANT_TABLES:
        for action in (h.action_write_ops, h.action_update_ops, h.action_file_ops):
            with pytest.raises(h.WriterActionError, match=r"dormant \(write_boundary=dormant\)"):
                action({"table": table, "record": {}}, FakeCon())
        assert h.action_create_ops_tables({"table": table}, FakeCon())["ok"] is True
    assert called == []
    assert provisioned == list(_DORMANT_TABLES)


def test_generic_verbs_still_refuse_unknown_and_non_string_tables():
    for table in ("nope", None, ["ops_projects"]):
        with pytest.raises(h.WriterActionError, match="unknown or missing ops table"):
            h.action_write_ops({"table": table, "record": {}}, FakeCon())
