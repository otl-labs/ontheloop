"""rec-5397: the plan-time high end and the gate allowance compute equal values, so equal formulas never
classify as a false breach (Decision 208 cl.1: the same function gates CI and predicts at plan time)."""

from __future__ import annotations

import json

import pytest

from scripts.checks.deps import module_cost_table as mct
from scripts.checks.deps import selection_budget as sb

_MEASURED_N = 48
_MEASURED_COST = 89.821
_CAP = sb.CEILING_SECONDS - sb.NON_TEST_BUDGET_SECONDS - sb.PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS


def _selection(n: int) -> dict:
    return {
        "selected": [f"tests/t{i}.py" for i in range(n)],
        "manifest": {"full_suite_forced": False, "fallback": False},
    }


def _ok_table(n: int, cost_s: float) -> mct.CostTableRead:
    return mct.CostTableRead("ok", {f"tests/t{i}.py": cost_s / n for i in range(n)}, 1.0)


def test_measured_case_classifies_breadth_waived() -> None:
    _, test_high = sb.predict_test_half(_MEASURED_N, cost_s=_MEASURED_COST)
    verdict = sb.classify(
        non_test_s=sb._PREDICT_NON_TEST_HIGH,
        static_s=sb._PREDICT_NON_TEST_HIGH,
        test_s=test_high,
        replay_s=0.0,
        elapsed=test_high + sb._PREDICT_NON_TEST_HIGH,
        n_selected=_MEASURED_N,
        forced=False,
        derivation_ok=True,
        census=None,
        cost_s=_MEASURED_COST,
    )
    assert verdict.outcome == "breadth_waived"


def test_high_end_equals_allowance_exactly() -> None:
    checked = 0
    costs = [round(60 + 0.137 * i, 3) for i in range(0, 4000, 7)] + [_MEASURED_COST]
    pairs = [(n, c) for n in range(1, 401) for c in costs[::13]] + [(_MEASURED_N, _MEASURED_COST)]
    for n, cost_s in pairs:
        raw = sb.COST_COEFFICIENT * cost_s + sb.PREPASS_FIXED_SECONDS + sb.PREPASS_PER_MODULE_SECONDS * n
        if not sb.TEST_BASE_SECONDS < raw < _CAP:
            continue
        checked += 1
        assert sb.predict_test_half(n, cost_s=cost_s)[1] == sb.cost_based_allowance(cost_s, n), (n, cost_s)
    assert checked > 0


def test_gate_allowance_is_bit_identical() -> None:
    got = sb.cost_based_allowance(_MEASURED_COST, _MEASURED_N)
    assert got == 210.15629999999996
    assert got == sb.COST_COEFFICIENT * _MEASURED_COST + sb.PREPASS_FIXED_SECONDS + sb.PREPASS_PER_MODULE_SECONDS * _MEASURED_N


def test_cli_reports_breadth_waived_for_the_measured_case(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr("scripts.checks.deps.affected_tests.derive_affected_tests", lambda *a, **k: _selection(_MEASURED_N))
    table = _ok_table(_MEASURED_N, _MEASURED_COST)
    monkeypatch.setattr(mct, "read_at_base_ref", lambda root: table)
    monkeypatch.setattr(mct, "selection_cost", lambda selected, read, root: _MEASURED_COST)
    monkeypatch.setattr("scripts.checks._scaffolding.precommit_escalates", lambda paths: False)
    assert sb.main(["--paths", "scripts/validate.py", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["predicted_outcome"] == "breadth_waived"
