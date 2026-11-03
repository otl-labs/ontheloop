"""CONCERN: scripts/ducklake_smoke/lambda_ec_gates.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: lambda_attach/ingress/
idempotency/partition/inlining/loudfail (with the _patch_gate helper), the lambda_churn fan-out
cohort (with _churn_single_body + _patch_fanout_churn), lambda_churn_incontainer, and --
CORRECTED vs the plan's prose (see tests/ducklake_neon_smoke_test's w7_build_file2.py manifest
note; lambda_ec_gates.py, not lambda_ops_gates.py, is where these are actually defined, per the
facade's own import block) -- lambda_reader and lambda_warm_reuse/lambda_warm_reuse_writer (with
the _warm_reuse_invoker helper).
"""

from __future__ import annotations

import json

import pytest

import scripts.ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import core
from src.common.ducklake_version import pinned_duckdb_version
from tests.fixtures.ducklake_smoke_fakes import _Resp


def _patch_gate(monkeypatch, payload, status=200):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(status, payload))


def test_lambda_attach_ok(monkeypatch, capsys):
    pin = pinned_duckdb_version()
    _patch_gate(monkeypatch, {"version": pin, "source": "layer", "connect_ms": 12.0, "commit_ms": 3.0})
    smoke.lambda_attach()
    assert f"LAMBDA_ATTACH OK version={pin} source=layer" in capsys.readouterr().out


def test_lambda_attach_wrong_version_fails(monkeypatch):
    _patch_gate(monkeypatch, {"version": "1.5.2", "source": "layer", "connect_ms": 1, "commit_ms": 1})
    with pytest.raises(smoke.SmokeTestFailure, match="LAMBDA_ATTACH FAIL"):
        smoke.lambda_attach()


def test_lambda_ingress_ok(monkeypatch, capsys):
    monkeypatch.setattr(core, "_function_url", lambda role: "https://w")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200 if kw.get("sign", True) else 403))
    smoke.lambda_ingress()
    assert "INGRESS OK unsigned=403 signed=200" in capsys.readouterr().out


def test_lambda_ingress_fails_when_unsigned_allowed(monkeypatch):
    monkeypatch.setattr(core, "_function_url", lambda role: "https://w")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200))  # unsigned also 200
    with pytest.raises(smoke.SmokeTestFailure, match="INGRESS FAIL"):
        smoke.lambda_ingress()


def test_lambda_idempotency_ok(monkeypatch, capsys):
    _patch_gate(monkeypatch, {"ulid_reused": True, "history_rows": 1, "current_rows": 1})
    smoke.lambda_idempotency()
    assert "IDEMPOTENCY OK ulid_reused=true history_rows=1 current_rows=1" in capsys.readouterr().out


def test_lambda_idempotency_fails(monkeypatch):
    _patch_gate(monkeypatch, {"ulid_reused": True, "history_rows": 2, "current_rows": 1})
    with pytest.raises(smoke.SmokeTestFailure, match="IDEMPOTENCY FAIL"):
        smoke.lambda_idempotency()


def test_lambda_partition_ok(monkeypatch, capsys):
    _patch_gate(
        monkeypatch,
        {
            "history_calendar_days": 3,
            "history_partition_tuples": 3,
            "history_files_in_probed_day": 1,
            "history_total": 3,
            "current_files_in_probed_bucket": 1,
            "current_total": 4,
        },
    )
    smoke.lambda_partition()
    assert "PARTITION OK history_partition_tuples=3" in capsys.readouterr().out


def test_lambda_partition_fails(monkeypatch):
    _patch_gate(
        monkeypatch,
        {
            "history_calendar_days": 3,
            "history_partition_tuples": 3,
            "history_files_in_probed_day": 3,
            "history_total": 3,
            "current_files_in_probed_bucket": 4,
            "current_total": 4,
        },
    )
    with pytest.raises(smoke.SmokeTestFailure, match="PARTITION FAIL"):
        smoke.lambda_partition()


def test_lambda_partition_fails_on_collapsed_calendar_days(monkeypatch):
    """Discriminating case: a deployed day-of-month spelling would collapse the three probed days
    into one partition tuple (history_partition_tuples < history_calendar_days would-be-3), which
    the gate must reject rather than pass on a merely-nonempty history."""
    _patch_gate(
        monkeypatch,
        {
            "history_calendar_days": 1,
            "history_partition_tuples": 1,
            "history_files_in_probed_day": 1,
            "history_total": 1,
            "current_files_in_probed_bucket": 1,
            "current_total": 4,
        },
    )
    with pytest.raises(smoke.SmokeTestFailure, match="PARTITION FAIL"):
        smoke.lambda_partition()


def test_lambda_inlining_ok(monkeypatch, capsys):
    _patch_gate(monkeypatch, {"inlined_rows": 0, "s3_parquet": 2, "occ_conflicts_handled": True})
    smoke.lambda_inlining()
    assert "INLINING OK inlined_rows=0 s3_parquet=2" in capsys.readouterr().out


def test_lambda_inlining_fails(monkeypatch):
    _patch_gate(monkeypatch, {"inlined_rows": 5, "s3_parquet": 0, "occ_conflicts_handled": True})
    with pytest.raises(smoke.SmokeTestFailure, match="INLINING FAIL"):
        smoke.lambda_inlining()


def test_lambda_loudfail_ok(monkeypatch, capsys):
    _patch_gate(monkeypatch, {"schema_reject": "raised", "occ_exhaust": "raised", "silent_drop": False})
    smoke.lambda_loudfail()
    assert "LOUDFAIL OK schema_reject=raised occ_exhaust=raised silent_drop=false" in capsys.readouterr().out


def test_lambda_loudfail_fails(monkeypatch):
    _patch_gate(monkeypatch, {"schema_reject": "not_raised", "occ_exhaust": "raised", "silent_drop": False})
    with pytest.raises(smoke.SmokeTestFailure, match="LOUDFAIL FAIL"):
        smoke.lambda_loudfail()


def _churn_single_body(**kw) -> dict:
    """Default per-invocation churn_single response body for fan-out lambda_churn tests."""
    base = {
        "ok": True,
        "latency_ms": 500.0,
        "collided": False,
        "connect_ms": 120.0,
        "commit_ms": 380.0,
        "cpu_ms": 200.0,
        "occ_retries": 0,
        "wall_ms": 500.0,
    }
    base.update(kw)
    return base


def _patch_fanout_churn(monkeypatch, per_invocation_body=None):
    """Patch _function_url + _sigv4_invoke for the fan-out lambda_churn (including pre-warm phase).

    Handles three payload types: attach_check (pre-warm), churn_single setup=True, churn_single normal.
    """
    monkeypatch.setattr(core, "_function_url", lambda role: "https://writer")
    body = per_invocation_body or _churn_single_body()

    def fake_invoke(url, payload, **kw):
        if payload.get("action") == "attach_check":
            return _Resp(200, {"version": pinned_duckdb_version(), "source": "layer", "connect_ms": 12.0, "commit_ms": 3.0})
        if payload.get("setup"):
            return _Resp(200, {"ok": True, "setup": True})
        return _Resp(200, body)

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)


def test_lambda_churn_fanout_ok(monkeypatch, capsys):
    _patch_fanout_churn(monkeypatch)
    smoke.lambda_churn()
    out = capsys.readouterr().out
    assert "CHURN OK collision_rate=0.0 p95_commit_ms=500.0 endpoint=direct" in out
    assert "within_budget=True" in out
    assert "wall_cpu_ratio=2.5" in out


def test_lambda_churn_fanout_n_concurrent_calls(monkeypatch):
    """CHURN_WRITERS pre-warm + 1 setup + CHURN_WRITERS fan-out calls are issued in order."""
    call_payloads: list[dict] = []
    monkeypatch.setattr(core, "_function_url", lambda role: "https://writer")

    def fake_invoke(url, payload, **kw):
        call_payloads.append(dict(payload))
        if payload.get("action") == "attach_check":
            return _Resp(200, {"version": pinned_duckdb_version(), "source": "layer", "connect_ms": 12.0, "commit_ms": 3.0})
        if payload.get("setup"):
            return _Resp(200, {"ok": True, "setup": True})
        return _Resp(200, _churn_single_body())

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    smoke.lambda_churn()
    prewarm_calls = [p for p in call_payloads if p.get("action") == "attach_check"]
    setup_calls = [p for p in call_payloads if p.get("setup")]
    normal_calls = [p for p in call_payloads if not p.get("setup") and p.get("action") != "attach_check"]
    assert len(prewarm_calls) == smoke.CHURN_WRITERS
    assert len(setup_calls) == 1
    assert len(normal_calls) == smoke.CHURN_WRITERS
    assert all(p.get("action") == "churn_single" for p in normal_calls)
    assert sorted(p.get("writer_id") for p in normal_calls) == list(range(smoke.CHURN_WRITERS))


def test_lambda_churn_fanout_aggregation_breakdown(monkeypatch, capsys):
    """Aggregated breakdown fields are computed correctly from N per-invocation bodies."""
    _patch_fanout_churn(monkeypatch, _churn_single_body(connect_ms=120.0, cpu_ms=200.0, wall_ms=500.0))
    smoke.lambda_churn()
    out = capsys.readouterr().out
    assert "p95_connect_ms=120.0" in out
    assert "wall_cpu_ratio=2.5" in out
    assert "total_occ_retries=0" in out


def test_lambda_churn_fanout_collision_rate_loudfail(monkeypatch):
    """Collision rate over budget triggers SmokeTestFailure."""
    monkeypatch.setattr(core, "_function_url", lambda role: "https://writer")
    idx = [0]

    def fake_invoke(url, payload, **kw):
        if payload.get("action") == "attach_check":
            return _Resp(200, {"version": pinned_duckdb_version(), "source": "layer", "connect_ms": 12.0, "commit_ms": 3.0})
        if payload.get("setup"):
            return _Resp(200, {"ok": True, "setup": True})
        collided = idx[0] < (smoke.CHURN_WRITERS // 2 + 1)
        idx[0] += 1
        return _Resp(200, _churn_single_body(collided=collided))

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    with pytest.raises(smoke.SmokeTestFailure, match="CHURN FAIL"):
        smoke.lambda_churn()


def test_lambda_churn_fanout_latency_loudfail(monkeypatch):
    """p95 wall latency over budget triggers SmokeTestFailure."""
    _patch_fanout_churn(
        monkeypatch,
        _churn_single_body(latency_ms=smoke.COMMIT_LATENCY_BUDGET_MS + 1.0, wall_ms=smoke.COMMIT_LATENCY_BUDGET_MS + 1.0),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="CHURN FAIL"):
        smoke.lambda_churn()


def test_churn_constants_imported_from_runtime():
    from src.common import ducklake_runtime

    assert smoke.OCC_COLLISION_RATE_BUDGET == ducklake_runtime.OCC_COLLISION_RATE_BUDGET
    assert smoke.COMMIT_LATENCY_BUDGET_MS == ducklake_runtime.COMMIT_LATENCY_BUDGET_MS
    assert smoke.CHURN_WRITERS == ducklake_runtime.CHURN_WRITERS
    assert smoke.CHURN_WRITES_PER_WRITER == ducklake_runtime.CHURN_WRITES_PER_WRITER


def test_lambda_churn_incontainer_prints_breakdown(monkeypatch, capsys):
    """lambda_churn_incontainer prints diagnostic breakdown and does NOT raise on over-budget."""
    _patch_gate(
        monkeypatch,
        {
            "collision_rate": 0.0,
            "p95_commit_ms": 8780.0,
            "within_budget": False,
            "endpoint": "direct",
            "breakdown": {
                "wall_cpu_ratio": 10.35,
                "p95_connect_ms": 900.0,
                "p95_cpu_ms": 850.0,
                "total_occ_retries": 0,
            },
        },
    )
    smoke.lambda_churn_incontainer()  # must NOT raise
    out = capsys.readouterr().out
    assert "CHURN_INCONTAINER" in out
    assert "wall_cpu_ratio=10.35" in out
    assert "within_budget=False" in out


def test_lambda_churn_incontainer_budget_pass_still_no_raise(monkeypatch, capsys):
    """lambda_churn_incontainer does not raise even when within_budget=True (always diagnostic)."""
    _patch_gate(monkeypatch, {"collision_rate": 0.0, "p95_commit_ms": 500.0, "within_budget": True, "breakdown": {}})
    smoke.lambda_churn_incontainer()
    assert "CHURN_INCONTAINER" in capsys.readouterr().out


_TABLES = ["telemetry_agents", "telemetry_observations", "telemetry_sessions", "telemetry_transcripts"]


def _json_resp(status, payload):
    return _Resp(status, payload, text=json.dumps(payload))


def _dq_answer(payload, *, skew=None, drop=None, refuse_table=True, echo=False, rows=False, count=0):
    """The reader's dq_check answers: a counts-only 200 per kind, and the two typed 400 refusals."""
    from src.common import ducklake_dq_checks as dq  # noqa: PLC0415

    kind, table, column = payload["check"], payload["table"], payload["params"].get("column")
    if table == "telemetry_sessions":
        return (
            _json_resp(200, {"ok": True, "violation_count": 0})
            if not refuse_table
            else _json_resp(400, {"ok": False, "error_type": "dq_check_input", "check": kind, "param": "table"})
        )
    if column == "no_such_column_marker":
        reason = f"unknown {column}" if echo else "must name a column"
        return _json_resp(
            400, {"ok": False, "error_type": "dq_check_input", "check": kind, "param": "column", "reason": reason}
        )
    if kind == drop:
        return _json_resp(400, {"ok": False, "error_type": "dq_check_input", "check": kind, "param": "check"})
    body = {"ok": True, "check": kind, "table": table, "violation_count": count, "check_version": dq.check_version(kind)}
    if kind == skew:
        body["check_version"] = "0" * 16
    if rows:
        body["rows"] = []
    return _json_resp(200, body)


def _reader_fake(
    *,
    dq=None,
    query_ops=None,
    serves_events=False,
    describe_events=False,
    health=None,
):
    """The shared reader: ops reads, the write boundary and dq_check, with every event verb answered as an unregistered
    verb (Decision 236 cl.2). serves_events=True is a reader that still answers the event verbs (the verification-verb
    checks' own fake); describe_events and health plant the shared reader's regressions."""
    from src.common.ducklake_event_read_verbs import EVENT_READS  # noqa: PLC0415

    def unregistered(verb):
        return _json_resp(500, {"ok": False, "error_type": "runtime", "error": f"unknown read verb {verb!r}"})

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action == "read_current":
            return _json_resp(200, {"row_count": 3})
        if action == "write_probe":
            return _json_resp(200, {"write_denied": True})
        if action == "describe":
            verbs = {"open_recs_tally": {"agent_facing": True}}
            if describe_events or serves_events:
                verbs["sessions_window"] = {"kind": "event_journal", "agent_facing": True}
            return _json_resp(200, {"verbs": verbs})
        if action == "dq_check":
            return _dq_answer(payload, **(dq or {}))
        if action == "query_ops":
            return query_ops or _json_resp(
                400, {"ok": False, "error": "unknown action 'query_ops'", "actions": ["describe", "dq_check"]}
            )
        if payload["verb"] == "telemetry_health_count" and health is not None:
            return health
        if payload["verb"] in EVENT_READS and not serves_events:
            return unregistered(payload["verb"])
        if payload["verb"] == "telemetry_health_count":
            rows = [{"table_name": t, "record_count": 0, "meta_schema": "ducklake_smoke"} for t in _TABLES]
            return _json_resp(200, {"rows": rows, "row_count": len(rows)})
        params = payload["params"]
        if "MARKER" in params.get("session_id", "") or params.get("max_rows") == 0:
            return _json_resp(400, {"ok": False, "error_type": "named_read_input", "error": "session_id is not canonical"})
        if payload["verb"] in ("telemetry_orphan_counts", "telemetry_root_sessions"):
            return _json_resp(200, {"ok": True, "rows": [], "row_count": 0})
        return _json_resp(200, {"rows": [], "row_count": 0})

    return fake_invoke


def _patch_reader(monkeypatch, **kwargs):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", _reader_fake(**kwargs))


def test_lambda_reader_ok(monkeypatch, capsys):
    _patch_reader(monkeypatch)
    smoke.lambda_reader()
    out = capsys.readouterr().out
    assert "READER OK rows=3 write_denied=true" in out and "SHARED_READER_NO_TELEMETRY_OK" in out
    assert "QUERY_OPS_RETIRED" in out and "DQ_CHECK_OK kinds=12" in out


def test_telemetry_read_checks_pass_on_a_consistent_reader(monkeypatch, capsys):
    _patch_reader(monkeypatch)
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    lambda_ec_gates._telemetry_read_checks(profile=None, region="eu-west-2")
    assert capsys.readouterr().out.strip().endswith("SHARED_READER_NO_TELEMETRY_OK")


def _failing(monkeypatch, match, **kwargs):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    _patch_reader(monkeypatch, **kwargs)
    with pytest.raises(core.SmokeTestFailure, match=match):
        lambda_ec_gates._telemetry_read_checks(profile=None, region="eu-west-2")


def test_reader_gate_fails_when_query_ops_still_answers(monkeypatch, capsys):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    _patch_reader(monkeypatch)
    lambda_ec_gates.retired_actions_gate()
    assert capsys.readouterr().out.strip() == "QUERY_OPS_RETIRED"
    for answer in (
        _json_resp(200, {"ok": True, "rows": []}),
        _json_resp(500, {"error_type": "unmapped"}),
        _json_resp(400, {"ok": False, "actions": ["describe", "dq_check", "query_ops"]}),
        _json_resp(400, {"ok": False, "actions": ["describe"]}),
        _json_resp(400, {"ok": False}),
    ):
        _patch_reader(monkeypatch, query_ops=answer)
        with pytest.raises(core.SmokeTestFailure, match="QUERY_OPS_RETIRED FAIL"):
            lambda_ec_gates.retired_actions_gate()


def test_telemetry_read_checks_fail_on_each_other_regression(monkeypatch):
    _failing(monkeypatch, "describe lists event_journal verbs", describe_events=True)
    _failing(monkeypatch, "must answer 500 runtime", health=_json_resp(200, {"rows": [], "row_count": 0}))
    _failing(monkeypatch, "must answer 500 runtime", health=_json_resp(504, {"error_type": "read_deadline_exceeded"}))
    _failing(monkeypatch, "must answer 500 runtime", health=_json_resp(500, {"error_type": "unmapped"}))
    _failing(monkeypatch, "must answer 500 runtime", health=_Resp(500, {}, text="not json"))


def test_shared_reader_checks_refuse_event_verbs(monkeypatch, capsys):
    seen: list[str] = []
    base = _reader_fake()

    def recording(url, payload, **kw):
        if payload["action"] == "named_read":
            seen.append(payload["verb"])
        return base(url, payload, **kw)

    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", recording)
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    lambda_ec_gates._telemetry_read_checks(profile=None, region="eu-west-2")
    assert seen == ["telemetry_health_count"]
    assert capsys.readouterr().out.strip() == "SHARED_READER_NO_TELEMETRY_OK"
    _failing(monkeypatch, "SHARED_READER_NO_TELEMETRY FAIL", health=_json_resp(200, {"rows": [{"x": 1}], "row_count": 1}))


def test_verification_verb_checks_fail_when_the_tree_verb_misbehaves(monkeypatch):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    base = _reader_fake(serves_events=True)

    def fail(reason, detail):
        raise core.SmokeTestFailure(f"{reason}: {detail}")

    def checks(handler):
        def call(payload):
            return handler("https://telemetry-reader", payload) or base("https://telemetry-reader", payload)

        lambda_ec_gates._verification_verb_checks(call, fail)

    def tree_has_rows(url, payload, **kw):
        params = payload.get("params") or {}
        if payload.get("verb") == "session_tree_rows" and params.get("max_rows") == 5 and "MARKER" not in params["session_id"]:
            return _json_resp(200, {"ok": True, "rows": [{}], "row_count": 1})
        return None

    def zero_rows_accepted(url, payload, **kw):
        if payload.get("verb") == "session_tree_rows" and (payload.get("params") or {}).get("max_rows") == 0:
            return _json_resp(200, {"ok": True, "rows": [], "row_count": 0})
        return None

    checks(lambda url, payload: None)
    for handler, match in ((tree_has_rows, "did not answer zero rows"), (zero_rows_accepted, "max_rows of 0")):
        with pytest.raises(core.SmokeTestFailure, match=match):
            checks(handler)


def _warm_reuse_invoker():
    """Fake _sigv4_invoke: 1st attach_check is cold (reused=False), subsequent are warm (reused=True)."""
    state = {"attach_calls": 0}

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action == "reset_warm_connection":
            state["attach_calls"] = 0
            return _Resp(200, {"ok": True, "reset": True})
        if action == "attach_check":
            state["attach_calls"] += 1
            cold = state["attach_calls"] == 1
            return _Resp(200, {"ok": True, "connect_ms": 80.0 if cold else 0.0, "connect_reused": not cold})
        if action == "write":
            return _Resp(200, {"ok": True, "occ_retries": 0})
        return _Resp(200, {"ok": True})

    return fake_invoke


def test_lambda_warm_reuse_ok(monkeypatch, capsys):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", _warm_reuse_invoker())
    smoke.lambda_warm_reuse(json_output=True)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["role"] == "reader"
    assert result["warm_reuse_observed"] is True
    assert result["warm_connect_ms"] == 0.0
    assert result["reconnect_ok"] is True


def test_lambda_warm_reuse_writer_ok(monkeypatch, capsys):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", _warm_reuse_invoker())
    smoke.lambda_warm_reuse_writer(json_output=True)
    result = json.loads(capsys.readouterr().out)
    assert result["role"] == "writer"
    assert result["warm_reuse_observed"] is True
    assert result["write_ok"] is True


def test_lambda_warm_reuse_fails_when_no_reuse(monkeypatch):
    """If reuse is never observed (connect_reused stays False), the gate loud-fails."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")

    def never_reused(url, payload, **kw):
        if payload["action"] == "attach_check":
            return _Resp(200, {"ok": True, "connect_ms": 80.0, "connect_reused": False})
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(core, "_sigv4_invoke", never_reused)
    with pytest.raises(smoke.SmokeTestFailure, match="warm reuse not observed"):
        smoke.lambda_warm_reuse()


def test_lambda_reader_boundary_broken_fails(monkeypatch):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    consistent = _reader_fake()

    def fake_invoke(url, payload, **kw):
        if payload["action"] == "write_probe":
            return _json_resp(200, {"write_denied": False})  # boundary broken
        return consistent(url, payload, **kw)

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    with pytest.raises(smoke.SmokeTestFailure, match="READER FAIL"):
        smoke.lambda_reader()


def test_dq_check_gate_calls_every_kind_counts_only(monkeypatch, capsys):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415
    from src.common import ducklake_dq_checks as dq  # noqa: PLC0415

    seen: list[str] = []
    base = _reader_fake(dq={"count": 7})

    def recording(url, payload, **kw):
        if payload["action"] == "dq_check":
            seen.append(payload["check"])
        return base(url, payload, **kw)

    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", recording)
    lambda_ec_gates.dq_check_gate()
    out = capsys.readouterr().out
    assert set(seen) == set(dq.DQ_KINDS) and out.strip() == f"DQ_CHECK_OK kinds={len(dq.DQ_KINDS)}"
    assert "7" not in out


@pytest.mark.parametrize(
    ("dq_kwargs", "match"),
    [
        ({"skew": "pattern"}, "pattern answered a check_version"),
        ({"drop": "unique"}, "unique did not answer 200"),
        ({"rows": True}, "answered row data"),
        ({"count": -1}, "did not answer 200"),
        ({"refuse_table": False}, "telemetry table"),
        ({"echo": True}, "without echoing"),
    ],
)
def test_dq_check_gate_fails_on_each_regression(monkeypatch, dq_kwargs, match):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    _patch_reader(monkeypatch, dq=dq_kwargs)
    with pytest.raises(core.SmokeTestFailure, match=match):
        lambda_ec_gates.dq_check_gate()


def test_dq_check_gate_fails_when_its_requests_miss_a_kind(monkeypatch):
    from scripts.ducklake_smoke import lambda_ec_gates  # noqa: PLC0415

    _patch_reader(monkeypatch)
    monkeypatch.delitem(lambda_ec_gates._DQ_GATE_REQUESTS, "pattern")
    with pytest.raises(core.SmokeTestFailure, match="does not cover exactly"):
        lambda_ec_gates.dq_check_gate()
