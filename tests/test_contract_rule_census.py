"""Mirror test for scripts/contract_rule_census.py (rec-4158 plan A), duckdb-free: rule compilation to dq_check kinds, the
exempt list and the exit-code mapping from a stubbed DQ harness."""

from __future__ import annotations

import json

import pytest

import scripts.contract_rule_census as census
from scripts.contract_rules import DeclaredRule
from scripts.data_quality_models import CheckResult, RunResult

_EXEMPT = {"class": "cross_row", "reason": "r", "owner": "rec-1"}


def _rule(
    kind: str, params: dict | None = None, *, column: str = "c", gate: str | None = None, exemption=None
) -> DeclaredRule:
    return DeclaredRule("ops_recommendations", column, kind, params or {}, gate, exemption)


def test_census_compiles_rules_to_dq_kinds() -> None:
    cases = [
        (_rule("not_null"), "not_null", {"column": "c"}),
        (_rule("not_null", gate="2026-05-01"), "not_null", {"column": "c", "exclude_before": "2026-05-01"}),
        (_rule("accepted_values", {"values": ["a", 7]}), "accepted_values", {"column": "c", "values": ["a", "7"]}),
        (_rule("min_length", {"value": "10"}), "min_length", {"column": "c", "value": 10}),
        (_rule("pattern", {"value": "^a'b$"}), "pattern", {"column": "c", "value": "^a'b$"}),
        (_rule("array_element_format", {"pattern": "^r$"}), "array_element_format", {"column": "c", "pattern": "^r$"}),
        (_rule("not_before", {"value": "created_timestamp"}), "not_before", {"column": "c", "value": "created_timestamp"}),
    ]
    for rule, kind, params in cases:
        check = census.compile_rule(rule)
        assert (check.kind, check.params, check.test_type) == (kind, params, kind)
        assert not hasattr(check, "sql")
    check = census.compile_rule(_rule("min_length", {"value": 3}, gate="2026-09-01"))
    assert (check.severity, check.enforced, check.exclude_before) == ("error", True, "2026-09-01")
    with pytest.raises(ValueError, match="no census check kind and no exemption"):
        census.compile_rule(_rule("acceptance_lint", {"require_discrimination": True}))


def test_census_dry_run_prints_requests_not_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(census, "declared_rules", lambda table: (_rule("pattern", {"value": "^x$"}),))
    code, lines, rows = census.census(("ops_recommendations",), dry_run=True)
    assert code == 0 and rows == []
    request = json.loads(lines[-1].splitlines()[-1])
    assert request == {
        "action": "dq_check",
        "check": "pattern",
        "table": "ops_recommendations",
        "params": {"column": "c", "value": "^x$"},
    }
    assert not any("SELECT" in line for line in lines)


def test_exempt_rules_are_listed_not_compiled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rules = (_rule("not_null"), _rule("array_element_reference", column="d", exemption=_EXEMPT))
    monkeypatch.setattr(census, "declared_rules", lambda table: rules)
    plan = census.build_plan("ops_recommendations")
    assert [c.test_type for c in plan.checks] == ["not_null"] and plan.declared_non_exempt == 1
    assert [r.kind for r in plan.exempt] == ["array_element_reference"]
    code, lines, rows = census.census(("ops_recommendations",), dry_run=True)
    assert code == 0 and rows == []
    assert lines[0] == "EXEMPT  ops_recommendations.d array_element_reference class=cross_row"
    assert sum("array_element_reference" in line for line in lines) == 1
    assert census.main(["--table", "ops_recommendations", "--dry-run", "--json"]) == 0


def _stub(monkeypatch: pytest.MonkeyPatch, verdicts: list[tuple[str, int]], *, rules=None, drop: int = 0) -> list:
    rules = rules if rules is not None else tuple(_rule("not_null", column=f"c{i}") for i in range(len(verdicts)))
    monkeypatch.setattr(census, "declared_rules", lambda table: rules)

    def fake_run(checks, **_kw):
        results = [CheckResult(check=c, verdict=v, violation_count=n) for c, (v, n) in zip(checks, verdicts)]
        return RunResult(results=results[: len(results) - drop] if drop else results)

    monkeypatch.setattr(census, "run_checks", fake_run)
    return list(rules)


def test_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cases = [
        ([("PASS", 0), ("PASS", 0)], 0),
        ([("PASS", 0), ("FAIL", 3)], 1),
        ([("PASS", 0), ("WARN", 2)], 1),
        ([("UNENFORCED_FAIL", 1)], 1),
        ([("PASS", 0), ("SKIP", 0)], 2),
        ([("PASS", 0), ("UNAVAILABLE", 0)], 2),
        ([("ERROR", 0)], 2),
    ]
    for verdicts, expected in cases:
        _stub(monkeypatch, verdicts)
        assert census.main(["--table", "ops_recommendations"]) == expected, verdicts
        out = capsys.readouterr().out
        assert "SUMMARY compiled=" in out
        assert f"violators={verdicts[-1][1]}" in out


def test_exit_two_on_an_empty_set_or_a_count_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, [], rules=())
    assert census.main(["--table", "ops_recommendations"]) == 2
    _stub(monkeypatch, [("PASS", 0), ("PASS", 0)], drop=1)
    assert census.main(["--table", "ops_recommendations"]) == 2
    monkeypatch.setattr(census, "declared_rules", lambda table: (_rule("acceptance_lint"),))
    assert census.main(["--table", "ops_decisions"]) == 2
    assert "census could not compile" in capsys.readouterr().err


def test_json_output_lists_each_rule(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _stub(monkeypatch, [("PASS", 0)])
    assert census.main(["--table", "ops_recommendations", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"rule": "not_null"' in out and '"violators": 0' in out
