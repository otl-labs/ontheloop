"""CONCERN: scripts/ducklake_smoke/lambda_ops_gates.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: ops_read_your_write,
ops_churn_regate (delegates to lambda_ec_gates.lambda_churn), and catalog_restore_drill.
connect_probe and lambda_append_only (also owned by lambda_ops_gates.py) have no direct unit test
in the monolith -- connect_probe is exercised only via the facade-interception test in
test_facade.py; lambda_append_only has no unit test at all (V3-only coverage).
"""

from __future__ import annotations

import pytest

import scripts.ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import core, lambda_ec_gates, lambda_ops_gates
from tests.fixtures.ducklake_smoke_fakes import _Resp


def test_ops_read_your_write_ok(monkeypatch, capsys):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    state = {"status": "open"}
    written = {}

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action == "write_ops":
            written.update(payload["record"])
            return _Resp(200, {"ok": True})
        if action == "update_ops":
            if payload["record"]["id"].startswith("test-absent"):
                return _Resp(409, {"error_type": "referential"})
            state["status"] = payload["record"]["status"]
            return _Resp(200, {"ok": True})
        if action == "read_ops_current":
            return _Resp(200, {"row_count": 1, "rows": [{"status": state["status"]}]})
        return _Resp(200, {})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    smoke.ops_read_your_write()
    assert "OPS_RYW OK" in capsys.readouterr().out
    # The probe row persists (writer has no delete verb); it must carry the DQ NOT-NULL columns
    # so it does not red the ops_recommendations data-quality checks while it lingers.
    for col in ("automatable", "file", "context", "acceptance"):
        assert written.get(col) is not None, f"probe missing DQ-required column {col!r}"


def test_ops_read_your_write_supersedes_probe_on_success(monkeypatch, capsys):
    """rec-2114: on success, ops_read_your_write supersedes its own test-ryw- probe via a final
    update_ops call (status=superseded) so no open probe lingers to fail the automatable
    not_null DQ check."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    state = {"status": "open"}
    update_statuses = []

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action == "write_ops":
            return _Resp(200, {"ok": True})
        if action == "update_ops":
            if payload["record"]["id"].startswith("test-absent"):
                return _Resp(409, {"error_type": "referential"})
            state["status"] = payload["record"]["status"]
            update_statuses.append(payload["record"]["status"])
            return _Resp(200, {"ok": True})
        if action == "read_ops_current":
            return _Resp(200, {"row_count": 1, "rows": [{"status": state["status"]}]})
        return _Resp(200, {})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    smoke.ops_read_your_write()
    assert update_statuses[-1] == "superseded"
    assert "superseded=true" in capsys.readouterr().out


def test_ops_read_your_write_absent_not_409_fails(monkeypatch):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    state = {"status": "open"}

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action == "read_ops_current":
            return _Resp(200, {"row_count": 1, "rows": [{"status": state["status"]}]})
        if action == "update_ops" and not payload["record"]["id"].startswith("test-absent"):
            state["status"] = "closed"
            return _Resp(200, {"ok": True})
        if action == "update_ops":
            return _Resp(200, {"ok": True})  # absent update wrongly succeeds -> boundary broken
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    with pytest.raises(smoke.SmokeTestFailure, match="expected 409"):
        smoke.ops_read_your_write()


def test_ops_churn_regate_delegates(monkeypatch, capsys):
    monkeypatch.setattr(lambda_ec_gates, "lambda_churn", lambda profile=None, region="eu-west-2": None)
    smoke.ops_churn_regate()
    assert "OPS_CHURN_REGATE OK" in capsys.readouterr().out


def test_catalog_restore_drill_ok(monkeypatch, capsys):
    # T2.19: catalog_restore_drill now INVOKES the maintenance restore_drill action over 443 (the
    # pg_dump/pg_restore runs inside AWS -- no Neon 5432 from CC-web). Mock the URL + the invoke.
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, p, **kw: _Resp(200, {"ok": True, "restored": True, "probe_id": "drill-probe", "pg_version": "16"}),
    )
    smoke.catalog_restore_drill()
    assert "CATALOG_RESTORE_DRILL OK" in capsys.readouterr().out


def test_catalog_restore_drill_probe_lost_fails(monkeypatch):
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200, {"ok": True, "restored": False}))
    with pytest.raises(smoke.SmokeTestFailure, match="did not restore"):
        smoke.catalog_restore_drill()


class _Calls(list):
    """The row-rules table's (action, record) calls; calls on a dormant table land in .dormant as (table, action)."""

    def __init__(self) -> None:
        super().__init__()
        self.dormant: list[tuple[str, str]] = []


def _dormant_400(table, verb):
    return _Resp(400, {"ok": False, "error_type": "action", "error": f"{table!r} is dormant (write_boundary=dormant)"})


def _row_rules_invoke(monkeypatch, *, answer, stored=0, dormant=_dormant_400):
    """Wire the gate to a scripted writer/reader; returns the row-rules table's (action, record) calls."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    calls = _Calls()

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        table = payload.get("table")
        if table in lambda_ops_gates.DORMANT_TABLES:
            if (table, action) not in lambda_ops_gates.DORMANT_REFUSAL_CASES:
                raise AssertionError(f"unexpected call on a dormant table: {table} {action}")
            calls.dormant.append((table, action))
            return dormant(table, action)
        calls.append((action, payload.get("record", {})))
        if action == "write_ops":
            return answer(payload["record"])
        if action == "read_ops_current":
            return _Resp(200, {"row_count": stored, "rows": []})
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    return calls


def _expected_422(record):
    for rule, overrides, column in lambda_ops_gates.ROW_RULES_CASES:
        if all(record.get(k) == v for k, v in overrides.items()):
            return _Resp(422, {"ok": False, "error_type": "row_rule", "rule": rule, "column": column})
    raise AssertionError(f"record matches no case: {record}")


def test_lambda_row_rules_ok(monkeypatch, capsys):
    calls = _row_rules_invoke(monkeypatch, answer=_expected_422)
    smoke.lambda_row_rules()
    out = capsys.readouterr().out
    assert f"LAMBDA_ROW_RULES OK {len(lambda_ops_gates.ROW_RULES_CASES)} cases" in out
    assert f"{len(lambda_ops_gates.DORMANT_REFUSAL_CASES)} dormant (table, verb) cases refused 400" in out
    assert [a for a, _ in calls].count("write_ops") == len(lambda_ops_gates.ROW_RULES_CASES)
    assert calls[-1][0] == "read_ops_current" and "update_ops" not in [a for a, _ in calls]
    assert sorted(calls.dormant) == sorted(lambda_ops_gates.DORMANT_REFUSAL_CASES)  # each pair sent exactly once


_DORMANT_FAILURES = {
    "schema_gate": lambda t, v: _Resp(422, {"ok": False, "error_type": "schema_gate", "error": "dormant"}),
    "runtime": lambda t, v: _Resp(500, {"ok": False, "error_type": "runtime", "error": "dormant"}),
    "referential": lambda t, v: _Resp(409, {"ok": False, "error_type": "referential", "error": "dormant"}),
    "accepted": lambda t, v: _Resp(200, {"ok": True}),
    "400_not_naming_dormant": lambda t, v: _Resp(400, {"ok": False, "error": "unknown or missing ops table"}),
}


def test_dormant_refusal_cases_answer_400(monkeypatch):
    calls = _row_rules_invoke(monkeypatch, answer=_expected_422)
    assert lambda_ops_gates.lambda_dormant_refusal("https://writer") == len(lambda_ops_gates.DORMANT_REFUSAL_CASES) == 6
    assert sorted(calls.dormant) == sorted(lambda_ops_gates.DORMANT_REFUSAL_CASES)
    for name, answer in _DORMANT_FAILURES.items():
        _row_rules_invoke(monkeypatch, answer=_expected_422, dormant=answer)
        with pytest.raises(smoke.SmokeTestFailure, match="instead of 400 naming dormant"):
            lambda_ops_gates.lambda_dormant_refusal("https://writer")
        _row_rules_invoke(monkeypatch, answer=_expected_422, dormant=answer)
        with pytest.raises(smoke.SmokeTestFailure, match="LAMBDA_ROW_RULES FAIL"):
            smoke.lambda_row_rules()


def test_lambda_row_rules_fails_on_accepted_write(monkeypatch):
    first_case = lambda_ops_gates.ROW_RULES_CASES[0]
    calls = _row_rules_invoke(monkeypatch, answer=lambda record: _Resp(200, {"ok": True}), stored=1)
    with pytest.raises(smoke.SmokeTestFailure, match=f"{first_case[0]} case answered 200"):
        smoke.lambda_row_rules()
    actions = [a for a, _ in calls]
    assert actions.index("update_ops") > actions.index("write_ops")  # the repair follows the unexpected 200
    repair = next(r for a, r in calls if a == "update_ops")
    clean = lambda_ops_gates.ROW_RULES_BASE
    assert repair["status"] == "superseded" and "failed --lambda-row-rules gate" in repair["resolution"]
    assert all(repair[k] == v for k, v in clean.items() if k != "status")


def test_lambda_row_rules_fails_on_wrong_rule_or_column(monkeypatch):
    rule, overrides, column = lambda_ops_gates.ROW_RULES_CASES[0]

    def wrong_column(record):
        return _Resp(422, {"ok": False, "error_type": "row_rule", "rule": rule, "column": "title"})

    _row_rules_invoke(monkeypatch, answer=wrong_column)
    with pytest.raises(smoke.SmokeTestFailure, match="expected accepted_values/effort"):
        smoke.lambda_row_rules()

    def schema_gate(record):
        return _Resp(422, {"ok": False, "error_type": "schema_gate"})

    _row_rules_invoke(monkeypatch, answer=schema_gate)
    with pytest.raises(smoke.SmokeTestFailure, match="instead of 422 row_rule"):
        smoke.lambda_row_rules()


def test_lambda_row_rules_fails_when_a_row_is_read_back(monkeypatch):
    calls = _row_rules_invoke(monkeypatch, answer=_expected_422, stored=1)
    with pytest.raises(smoke.SmokeTestFailure, match="left a row in current"):
        smoke.lambda_row_rules()
    assert any(a == "update_ops" for a, _ in calls)
