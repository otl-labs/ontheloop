"""Tests for src/lambdas/ducklake_reader/handler.py (T2.17, 100% coverage, mocked runtime)."""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_version import pinned_duckdb_version
from tests.fixtures.ducklake_reader_fakes import CountCon, FakeCon

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_warm_connection():
    """The reader uses a per-container warm-connection global (D2); reset it around every test."""
    rt.reset_warm_connection()
    yield
    rt.reset_warm_connection()


# ---------------------------------------------------------------------------
# _parse_event / _response
# ---------------------------------------------------------------------------


def test_parse_event_variants():
    assert h._parse_event({"body": json.dumps({"action": "read_current"})}) == {"action": "read_current"}
    assert h._parse_event({"body": {"action": "x"}}) == {"action": "x"}
    assert h._parse_event({"body": ""}) == {}
    assert h._parse_event({"action": "y"}) == {"action": "y"}
    assert h._parse_event(123) == {}


def test_response_envelope():
    r = h._response(200, {"ok": True})
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True


# ---------------------------------------------------------------------------
# _json_safe
# ---------------------------------------------------------------------------


def test_json_safe_coerces_datetimes():
    rows = [{"ulid": "01A", "created_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc), "payload": "p"}]
    out = h._json_safe(rows)
    assert out[0]["created_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert out[0]["payload"] == "p"


# ---------------------------------------------------------------------------
# handler dispatch
# ---------------------------------------------------------------------------


def test_handler_unknown_action():
    r = h.handler({"action": "nope"})
    assert r["statusCode"] == 400


def test_handler_attach_check(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)
    monkeypatch.setattr(
        rt.ducklake_spike, "_require_duckdb", lambda: types.SimpleNamespace(__version__=pinned_duckdb_version())
    )
    r = h.handler({"action": "attach_check"})
    body = json.loads(r["body"])
    assert body["version"] == pinned_duckdb_version()
    assert body["source"] == "layer"
    # D2 warm reuse: the connection is kept open for the next invocation, NOT closed per-request.
    assert con.closed is False
    assert body["connect_reused"] is False  # first (cold) acquisition in this container


def test_handler_read_current(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)
    rows = [
        {
            "ulid": "01A",
            "rec_id": "rec-1",
            "payload": "p",
            "created_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "last_updated_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    ]
    monkeypatch.setattr(rt, "read_current", lambda c, rec_id=None, limit=None: rows)
    r = h.handler({"action": "read_current", "rec_id": "rec-1", "limit": 10})
    body = json.loads(r["body"])
    assert body["row_count"] == 1
    assert body["rows"][0]["rec_id"] == "rec-1"


def test_handler_partition_prune_check(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)
    monkeypatch.setattr(rt, "read_current", lambda c, rec_id=None, limit=None: [{"rec_id": rec_id}])
    r = h.handler({"action": "partition_prune_check", "rec_id": "rec-part-0"})
    body = json.loads(r["body"])
    assert body["rows_returned"] == 1
    assert body["partitions_scanned"] == 1


def test_handler_write_probe_denied(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)

    def _raise(c, rec, **kw):
        raise RuntimeError("AccessDenied: s3:PutObject")

    monkeypatch.setattr(rt, "write_scd2", _raise)
    r = h.handler({"action": "write_probe"})
    body = json.loads(r["body"])
    assert body["write_denied"] is True
    assert body["detail"] == "RuntimeError"


def test_handler_write_probe_boundary_broken(monkeypatch):
    con = FakeCon()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)
    monkeypatch.setattr(rt, "write_scd2", lambda c, rec, **kw: None)  # write SUCCEEDS (boundary broken)
    r = h.handler({"action": "write_probe"})
    assert json.loads(r["body"])["write_denied"] is False


def test_handler_version_mismatch_maps_500(monkeypatch):
    def _raise():
        raise rt.VersionMismatchError("mismatch")

    monkeypatch.setattr(h, "_open_reader_connection", _raise)
    r = h.handler({"action": "attach_check"})
    assert r["statusCode"] == 500
    assert json.loads(r["body"])["error_type"] == "version_mismatch"


def test_handler_runtime_error_maps_500(monkeypatch):
    def _raise():
        raise rt.DuckLakeRuntimeError("boom")

    monkeypatch.setattr(h, "_open_reader_connection", _raise)
    r = h.handler({"action": "read_current"})
    assert r["statusCode"] == 500
    assert json.loads(r["body"])["error_type"] == "runtime"


def test_open_reader_connection(monkeypatch):
    monkeypatch.setattr(rt, "fetch_dsn", lambda: {"host": "h"})
    captured = {}
    monkeypatch.setattr(rt, "open_connection", lambda **kw: captured.update(kw) or "CON")
    out = h._open_reader_connection()
    assert out == "CON"
    assert captured["extension_directory"] == h.EXTENSION_DIRECTORY


# ---------------------------------------------------------------------------
# T2.19 production ops read actions: read_ops_current / read_ops_history
# ---------------------------------------------------------------------------


def test_action_read_ops_current(monkeypatch):
    rows = [{"id": "rec-1", "status": "open", "created_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    monkeypatch.setattr(rt, "read_current", lambda con, *, table, key, key_column, limit: rows)
    out = h.action_read_ops_current({"table": "ops_recommendations", "id": "rec-1"}, FakeCon())
    assert out["ok"] is True
    assert out["row_count"] == 1
    # datetime is coerced to ISO string for the JSON body
    assert isinstance(out["rows"][0]["created_timestamp"], str)


def test_action_read_ops_current_structural_filter(monkeypatch):
    """rec-2170: a {column, value} filter binds the NAMED column, not the merge key."""
    seen = {}

    def _read(con, *, table, key, key_column, limit):  # noqa: ARG001
        seen.update(key=key, key_column=key_column)
        return []

    monkeypatch.setattr(rt, "read_current", _read)
    out = h.action_read_ops_current(
        {"table": "ops_recommendations", "filter": {"column": "status", "value": "open"}}, FakeCon()
    )
    assert out["ok"] is True
    assert seen == {"key": "open", "key_column": "status"}


def test_action_named_read(monkeypatch):
    captured = {}

    def _named(con, *, verb, params, limit=None):  # noqa: ARG001
        captured.update(verb=verb, params=params, limit=limit)
        return [{"id": "rec-9"}]

    monkeypatch.setattr(rt, "named_read", _named)
    out = h.action_named_read({"verb": "rec_by_id", "params": {"id": "rec-9"}}, FakeCon())
    assert out["ok"] is True
    assert out["registry_version"] == rt.NAMED_READS_VERSION
    assert captured == {"verb": "rec_by_id", "params": {"id": "rec-9"}, "limit": None}


def test_action_named_read_passes_limit_through(monkeypatch):
    captured = {}

    def _named(con, *, verb, params, limit=None):  # noqa: ARG001
        captured.update(limit=limit)
        return [{"id": "rec-9"}]

    monkeypatch.setattr(rt, "named_read", _named)
    h.action_named_read({"verb": "open_recs", "params": {}, "limit": 5}, FakeCon())
    assert captured == {"limit": 5}


def test_action_named_read_requires_verb():
    with pytest.raises(rt.DuckLakeRuntimeError, match="non-empty 'verb'"):
        h.action_named_read({"params": {}}, FakeCon())


def test_action_named_read_rejects_non_dict_params():
    with pytest.raises(rt.DuckLakeRuntimeError, match="must be an object"):
        h.action_named_read({"verb": "rec_by_id", "params": ["rec-9"]}, FakeCon())


def test_action_read_ops_history(monkeypatch):
    monkeypatch.setattr(rt, "read_history", lambda con, *, table, key, limit: [{"ulid": "01A"}, {"ulid": "01B"}])
    out = h.action_read_ops_history({"table": "ops_decisions", "limit": 5}, FakeCon())
    assert out["row_count"] == 2


def test_require_ops_table_rejects_unknown():
    with pytest.raises(rt.DuckLakeRuntimeError, match="unknown or missing ops table"):
        h._require_ops_table("nope")


def test_require_ops_table_rejects_control_class_table():
    """A control-class table (read_boundary=none, T2.26) is refused here -- registering it for
    governance never grants application read access. The symmetric writer-side refusal test lives
    separately in tests/lambdas/ducklake_reader/test_control_table_refusal.py."""
    with pytest.raises(rt.DuckLakeRuntimeError, match="control-class table"):
        h._require_ops_table("ops_entity_counters")


def test_handler_read_ops_current_end_to_end(monkeypatch):
    monkeypatch.setattr(h, "_open_reader_connection", lambda: FakeCon())
    monkeypatch.setattr(rt, "read_current", lambda con, *, table, key, key_column, limit: [{"id": "rec-1"}])
    resp = h.handler({"action": "read_ops_current", "table": "ops_recommendations"})
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["row_count"] == 1


# ---------------------------------------------------------------------------
# connect_probe: runs WITHOUT a pre-opened connection
# ---------------------------------------------------------------------------


def test_handler_reopens_on_dead_connection_and_retries(monkeypatch):
    """A dead-session error (Neon scale-to-zero) reopens the warm connection ONCE and retries (D2).

    Reads are idempotent, so the reopen never leaks a 5xx for this expected transient.
    """
    opens: list[int] = []
    monkeypatch.setattr(h, "_open_reader_connection", lambda: opens.append(1) or FakeCon())
    calls = {"n": 0}

    def flaky_read_current(con, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("server closed the connection unexpectedly")  # dead session
        return [{"rec_id": "rec-1"}]

    monkeypatch.setattr(rt, "read_current", flaky_read_current)
    r = h.handler({"action": "read_current", "limit": 5})
    body = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert body["row_count"] == 1
    assert len(opens) == 2  # cold open + one reopen


def test_handler_non_dead_error_does_not_reopen(monkeypatch):
    """A non-connection error is NOT a dead-session signal: it propagates (no reopen), maps to 500."""
    opens: list[int] = []
    monkeypatch.setattr(h, "_open_reader_connection", lambda: opens.append(1) or FakeCon())
    monkeypatch.setattr(rt, "read_current", lambda con, **kw: (_ for _ in ()).throw(rt.DuckLakeRuntimeError("bad query")))
    r = h.handler({"action": "read_current"})
    assert r["statusCode"] == 500
    assert len(opens) == 1  # no reopen for a non-dead error


def test_handler_reset_warm_connection(monkeypatch):
    """reset_warm_connection is connectionless: it drops the warm cache without opening a connection (D2 VP)."""

    def _should_not_open():
        raise AssertionError("reset_warm_connection must not open a connection")

    monkeypatch.setattr(h, "_open_reader_connection", _should_not_open)
    reset_called = {"n": 0}
    monkeypatch.setattr(rt, "reset_warm_connection", lambda: reset_called.__setitem__("n", reset_called["n"] + 1))
    r = h.handler({"action": "reset_warm_connection"})
    body = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert body == {"ok": True, "reset": True}
    assert reset_called["n"] == 1


def test_handler_connect_probe_runs_without_connection(monkeypatch):
    """connect_probe action returns the structured probe result even when _open_reader_connection would hang.

    Proves the probe is dispatched via _CONNECTIONLESS_ACTIONS ahead of _open_reader_connection.
    """
    hang_called = {"called": False}

    def _hanging_open():
        hang_called["called"] = True
        raise RuntimeError("hang: should not be called for connect_probe")

    monkeypatch.setattr(h, "_open_reader_connection", _hanging_open)
    fake_result = {
        "phase_reached": "dns",
        "failed_phase": "tcp",
        "dns_ms": 4.0,
        "tcp_ms": 10001.0,
        "auth_ms": None,
        "attach_ms": None,
        "ok": False,
        "error": "TCP: timed out",
    }
    import src.common.ducklake_connect_probe as p

    monkeypatch.setattr(p, "probe_connection", lambda dsn, **kw: fake_result)
    _fake_dsn = {"host": "h", "dbname": "d", "username": "u", "password": "p"}  # pragma: allowlist secret
    monkeypatch.setattr(rt, "fetch_dsn", lambda: _fake_dsn)

    r = h.handler({"action": "connect_probe"})
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["failed_phase"] == "tcp"
    assert body["ok"] is False
    assert hang_called["called"] is False, "_open_reader_connection must NOT be called for connect_probe"


def test_handler_connect_probe_success(monkeypatch):
    """connect_probe returns ok=True and phase_reached=attach on a successful probe."""
    monkeypatch.setattr(h, "_open_reader_connection", lambda: (_ for _ in ()).throw(RuntimeError("should not be called")))
    success_result = {
        "phase_reached": "attach",
        "failed_phase": None,
        "dns_ms": 1.5,
        "tcp_ms": 4.0,
        "auth_ms": 45.0,
        "attach_ms": 900.0,
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
    """connect_probe must be in _CONNECTIONLESS_ACTIONS so it bypasses _open_reader_connection."""
    assert "connect_probe" in h._CONNECTIONLESS_ACTIONS


# ---------------------------------------------------------------------------
# describe: per-verb parameter schema (CD.10 / CD.15), connectionless
# ---------------------------------------------------------------------------


def test_action_describe_returns_named_reads_schema():
    out = h.action_describe({}, None)
    assert out["ok"] is True
    assert set(out["verbs"]) == set(rt.NAMED_READS) and not set(out["verbs"]) & set(EVENT_READS)
    assert "params" in out["verbs"]["rec_by_id"]


def test_describe_in_connectionless_actions():
    assert "describe" in h._CONNECTIONLESS_ACTIONS
    assert h._ACTIONS["describe"] is h.action_describe


def test_handler_describe_end_to_end():
    """describe dispatches without opening a connection (registered connectionless)."""
    r = h.handler({"action": "describe"})
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert "open_recs" in body["verbs"]


# ---------------------------------------------------------------------------
# Growth-safe per-verb parametrized dispatch: every NAMED_READS verb renders + validates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted(rt.NAMED_READS))
def test_named_read_dispatch_every_registered_verb(verb, monkeypatch):
    """Every NAMED_READS verb dispatches through action_named_read with its declared params bound."""
    entry = rt.NAMED_READS[verb]
    captured = {}

    def _named(con, *, verb, params, limit=None):  # noqa: ARG001
        captured.update(verb=verb, params=params)
        return []

    monkeypatch.setattr(rt, "named_read", _named)
    params = dict.fromkeys(entry.params, "x")
    out = h.action_named_read({"verb": verb, "params": params}, FakeCon())
    assert out["ok"] is True
    assert out["verb"] == verb
    assert captured["params"] == params


def test_action_read_ops_current_rejects_malformed_filter():
    """A filter missing 'value' must loud-fail, never degrade to an unfiltered full-table read."""
    with pytest.raises(rt.DuckLakeRuntimeError, match="BOTH 'column' and 'value'"):
        h.action_read_ops_current({"table": "ops_recommendations", "filter": {"column": "status"}}, FakeCon())
    with pytest.raises(rt.DuckLakeRuntimeError, match="BOTH 'column' and 'value'"):
        h.action_read_ops_current({"table": "ops_recommendations", "filter": "status=open"}, FakeCon())


_REFUSED_READ_TABLES = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_transcripts",
    "telemetry_agents",
    "ops_tenants",
    "ops_projects",
)


def test_generic_reads_refuse_telemetry_and_registration_tables(monkeypatch):
    """read_ops_current and read_ops_history refuse the six tables by their `table` parameter
    with the directed error naming the write_boundary (a 500 under the reader's error map)."""
    monkeypatch.setattr(rt, "read_current", lambda *a, **k: pytest.fail("runtime reached"))
    monkeypatch.setattr(rt, "read_history", lambda *a, **k: pytest.fail("runtime reached"))
    for table in _REFUSED_READ_TABLES:
        boundary = "registration" if table.startswith("ops_") else "telemetry_append"
        for action in (h.action_read_ops_current, h.action_read_ops_history):
            with pytest.raises(rt.DuckLakeRuntimeError, match=rf"write_boundary={boundary}"):
                action({"table": table, "sql": "SELECT 1"}, FakeCon())


def test_named_read_response_carries_read_version(monkeypatch):
    monkeypatch.setattr(rt, "named_read", lambda con, *, verb, params, limit=None: [{"id": "rec-9"}])  # noqa: ARG005
    out = h.action_named_read({"verb": "rec_by_id", "params": {"id": "rec-9"}}, FakeCon())
    assert out["registry_version"] == rt.NAMED_READS_VERSION
    assert out["read_version"] == rt.read_version("rec_by_id")
    assert out["read_version"] != rt.read_version("open_recs")
    assert h.action_describe({}, None)["verbs"]["rec_by_id"]["read_version"] == out["read_version"]


# ---------------------------------------------------------------------------
# one ducklake_invocation line per invocation (Decision 214)
# ---------------------------------------------------------------------------


def _invocation_lines(out: str) -> list[dict]:
    return [json.loads(x) for x in out.splitlines() if x.startswith("{") and '"ducklake_invocation"' in x]


def test_one_invocation_line_per_path(monkeypatch, capsys):
    fixed = {
        "event",
        "function",
        "action",
        "status",
        "connect_ms",
        "connect_reused",
        "reopened",
        "elapsed_ms",
        "response_bytes",
    }
    monkeypatch.setattr(h, "_open_reader_connection", lambda: FakeCon())
    monkeypatch.setattr(rt, "read_current", lambda con, **kw: [])

    def one(event) -> dict:
        capsys.readouterr()
        h.handler(event)
        lines = _invocation_lines(capsys.readouterr().out)
        assert len(lines) == 1 and set(lines[0]) == fixed and lines[0]["function"] == "reader"
        return lines[0]

    ok = one({"action": "read_current", "rec_id": "SECRET-PAYLOAD-VALUE"})
    assert (ok["action"], ok["status"], ok["connect_reused"], ok["reopened"]) == ("read_current", 200, False, False)
    assert one({"action": "read_current"})["connect_reused"] is True  # warm reuse

    bad = one({"action": "SECRET-ACTION-NAME"})
    assert (bad["action"], bad["status"], bad["connect_ms"]) == ("unknown", 400, None)
    capsys.readouterr()
    h.handler({"action": "SECRET-ACTION-NAME"})
    assert "SECRET" not in capsys.readouterr().out

    monkeypatch.setattr(rt, "read_current", lambda con, **kw: (_ for _ in ()).throw(rt.DuckLakeRuntimeError("boom")))
    assert one({"action": "read_current"})["status"] == 500

    monkeypatch.setattr(rt, "reset_warm_connection", lambda: None)
    conless = one({"action": "reset_warm_connection"})
    assert (conless["status"], conless["connect_ms"], conless["connect_reused"]) == (200, None, None)


def test_invocation_line_marks_both_reopen_paths(monkeypatch, capsys):
    monkeypatch.setattr(h, "_open_reader_connection", lambda: FakeCon())
    calls = {"n": 0}

    def flaky(con, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("server closed the connection unexpectedly")
        return []

    monkeypatch.setattr(rt, "read_current", flaky)
    h.handler({"action": "read_current"})
    line = _invocation_lines(capsys.readouterr().out)[0]
    assert line["status"] == 200 and line["reopened"] is True  # mid-statement dead-connection branch

    monkeypatch.setattr(rt, "read_current", lambda con, **kw: [])
    monkeypatch.setattr(
        h,
        "_warm_reader_connection",
        lambda force_reopen=False: (FakeCon(), {"connect_ms": 9.0, "reused": False, "reopened": True}),
    )
    h.handler({"action": "read_current"})
    line = _invocation_lines(capsys.readouterr().out)[0]
    assert line["reopened"] is True and line["connect_ms"] == 9.0  # acquisition-time probe reopen


def test_invocation_line_on_unparseable_body(capsys):
    r = h.handler({"body": "{not json"})
    assert r["statusCode"] == 400 and json.loads(r["body"])["error_type"] == "bad_request"
    line = _invocation_lines(capsys.readouterr().out)[0]
    assert (line["action"], line["status"]) == ("unknown", 400)


@pytest.mark.parametrize("raw", ["[1, 2]", '"text"', "7", "[" * 100000])
def test_non_object_body_is_400(raw):
    r = h.handler({"body": raw})
    assert r["statusCode"] == 400 and json.loads(r["body"])["error_type"] == "bad_request"


_PW = "pass" + "word=hunter2"


def _duckdb_class(name, module="_duckdb"):
    return type(name, (Exception,), {"__module__": module})


def _named_read_raising(monkeypatch, exc):
    monkeypatch.setattr(
        h, "_warm_reader_connection", lambda force_reopen=False: (FakeCon(), {"connect_ms": 1.0, "reused": True})
    )
    monkeypatch.setattr(rt, "named_read", lambda con, **kw: (_ for _ in ()).throw(exc))
    return h.handler({"action": "named_read", "verb": "rec_by_id", "params": {"id": "x"}})


@pytest.mark.parametrize(
    "cls", ["InvalidInputException", "ConversionException", "OutOfRangeException", "TypeMismatchException"]
)
def test_named_read_caller_input_error_is_400_with_its_message(monkeypatch, capsys, cls):
    r = _named_read_raising(monkeypatch, _duckdb_class(cls)("narrow the query\nsecond line " + _PW))
    body = json.loads(r["body"])
    assert r["statusCode"] == 400
    assert (body["error_type"], body["verb"], body["error_class"]) == ("named_read_input", "rec_by_id", cls)
    assert body["error"] == "narrow the query" and "hunter2" not in r["body"]
    out = capsys.readouterr().out
    assert '"ducklake_named_read_input"' in out and "hunter2" not in out


@pytest.mark.parametrize("cls", ["BinderException", "ParserException", "IOException", "HTTPException"])
def test_named_read_other_duckdb_error_is_unmapped_500(monkeypatch, capsys, cls):
    r = _named_read_raising(monkeypatch, _duckdb_class(cls)("s3://bucket/key " + _PW))
    body = json.loads(r["body"])
    assert r["statusCode"] == 500 and (body["error_type"], body["error_class"]) == ("unmapped", cls)
    assert "hunter2" not in r["body"] and "s3://" not in r["body"]
    assert '"ducklake_unmapped_error"' in capsys.readouterr().out


def test_caller_input_class_name_from_another_module_is_unmapped(monkeypatch):
    r = _named_read_raising(monkeypatch, _duckdb_class("InvalidInputException", "elsewhere")("x"))
    assert r["statusCode"] == 500 and json.loads(r["body"])["error_type"] == "unmapped"


def test_dead_connection_after_reopen_is_503(monkeypatch, capsys):
    monkeypatch.setattr(h, "_open_reader_connection", lambda: FakeCon())
    monkeypatch.setattr(
        rt, "read_current", lambda con, **kw: (_ for _ in ()).throw(Exception("server closed the connection unexpectedly"))
    )
    r = h.handler({"action": "read_current"}, types.SimpleNamespace(aws_request_id="req-1"))
    body = json.loads(r["body"])
    assert r["statusCode"] == 503
    assert (body["error_type"], body["reopened"], body["request_id"]) == ("catalog_unavailable", True, "req-1")
    assert "server closed" not in r["body"]
    assert _invocation_lines(capsys.readouterr().out)[0]["status"] == 503


def test_unmapped_error_outside_a_connection_action_is_500(monkeypatch):
    monkeypatch.setattr(rt, "describe_named_reads", lambda: (_ for _ in ()).throw(KeyError("boom")))
    r = h.handler({"action": "describe"})
    assert r["statusCode"] == 500 and json.loads(r["body"])["error_class"] == "KeyError"


def test_real_duckdb_caller_input_errors_are_400(monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    for sql, bind in (("SELECT CAST(? AS INTEGER)", ["abc"]), ("SELECT error('narrow the query')", None)):

        def _run(_con, *, verb, params, limit=None, _sql=sql, _bind=bind):
            return con.execute(_sql, _bind).fetchall() if _bind else con.execute(_sql).fetchall()

        monkeypatch.setattr(
            h, "_warm_reader_connection", lambda force_reopen=False: (FakeCon(), {"connect_ms": 1.0, "reused": True})
        )
        monkeypatch.setattr(rt, "named_read", _run)
        r = h.handler({"action": "named_read", "verb": "rec_by_id", "params": {"id": "x"}})
        assert r["statusCode"] == 400 and json.loads(r["body"])["error_type"] == "named_read_input", sql
    with pytest.raises(duckdb.ParserException):
        con.execute("SELEC 1")
    monkeypatch.setattr(rt, "named_read", lambda c, **kw: con.execute("SELEC 1"))
    r = h.handler({"action": "named_read", "verb": "rec_by_id", "params": {"id": "x"}})
    assert r["statusCode"] == 500 and json.loads(r["body"])["error_class"] == "ParserException"


# ---------------------------------------------------------------------------
# response-size guard (rec-4232) -- the 413 branch from the handler's own coverage home
# ---------------------------------------------------------------------------


class TestResponseSizeGuard:
    def test_oversized_envelope_becomes_413_and_logs_bytes(self, monkeypatch, capsys):
        monkeypatch.setattr(h, "_open_reader_connection", lambda: FakeCon())
        monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 500)
        monkeypatch.setattr(rt, "read_current", lambda con, **kw: [{"payload": "x" * 600}])
        capsys.readouterr()
        r = h.handler({"action": "read_current"})
        body = json.loads(r["body"])
        assert r["statusCode"] == 413 and body["error_type"] == "response_too_large"
        assert body["action"] == "read_current" and body["cap"] == 500 and body["bytes"] > 500
        line = _invocation_lines(capsys.readouterr().out)[0]
        assert line["status"] == 413 and line["response_bytes"] == body["bytes"]

    def test_unknown_action_name_is_never_echoed(self, monkeypatch):
        monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 10)
        r = h._guard_response_size(h._response(200, {"pad": "x" * 100}), {"action": "SECRET-ACTION"}, {})
        assert r["statusCode"] == 413 and "SECRET" not in r["body"]

    def test_in_cap_response_passes_through_with_measured_bytes(self):
        meta: dict = {}
        resp = h._response(200, {"ok": True})
        assert h._guard_response_size(resp, {"action": "describe"}, meta) is resp
        assert meta["response_bytes"] == len(json.dumps(resp).encode("utf-8"))


def test_size_guard_failure_is_an_unmapped_500(monkeypatch):
    monkeypatch.setattr(h, "_guard_response_size", lambda *a, **k: (_ for _ in ()).throw(KeyError("guard")))
    r = h.handler({"action": "describe"})
    assert r["statusCode"] == 500 and json.loads(r["body"])["error_class"] == "KeyError"


def test_dq_check_dispatch_through_the_handler(monkeypatch):
    monkeypatch.setattr(h, "_open_reader_connection", lambda: CountCon(2))
    call = {"action": "dq_check", "check": "row_count_min", "table": "ops_decisions", "params": {"min": 1}}
    r = h.handler({"body": json.dumps(call)})
    assert r["statusCode"] == 200 and json.loads(r["body"])["violation_count"] == 2
    bad = h.handler({"body": json.dumps({**call, "params": {}})})
    assert bad["statusCode"] == 400 and json.loads(bad["body"])["error_type"] == "dq_check_input"
    for exc, kind in (
        (type("InvalidInputException", (Exception,), {"__module__": "_duckdb"})("x"), "dq"),
        (ValueError("x"), "raw"),
    ):
        monkeypatch.setattr(h.dq, "run_check", lambda con, check, e=exc: (_ for _ in ()).throw(e))
        with pytest.raises(h.dq.DqCheckInputError if kind == "dq" else ValueError):
            h.action_dq_check(call, CountCon())
