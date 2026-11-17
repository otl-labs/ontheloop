"""Fast-tier budget formula, constants and plan-time reporter (Decision 208, amends Decision 182).

The single home of every budget constant the fast tier asserts on, of the three-term split those
budgets are asserted over, and of the plan-time CLI that reports a candidate scope's selection
breadth before the work is written. scripts/validate.py restates no number from here: it BINDS its
two existing public constant names to FLOOR_TOTAL_SECONDS and CEILING_SECONDS.

The tier asserts THREE quantities rather than one aggregate wall clock:

* NON_TEST_BUDGET_SECONDS on ``elapsed - phase_times[TEST_PHASE_NAME] - escalation_carve_out(...)``
  -- identically ``static_s + replay_s + unattributed_s`` minus the escalation carve-out, so
  nothing the tier spends is left ungoverned. Unwaivable: no bypass, forced-scope, breadth or
  fallback path reaches it.
* a measured-cost test-execution allowance on ``phase_times[TEST_PHASE_NAME]``, capped at the
  DERIVED expression ``CEILING_SECONDS - NON_TEST_BUDGET_SECONDS - PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS``
  so the worst-case ASSERTED total across all three governed terms is exactly the existing derived
  ceiling -- no second ceiling constant.
* PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS, carved out of the non-test half only when the pre-commit
  `--all-files` escalation (docs/contracts/presubmit-tool-pin-escalation.yaml) actually fired on
  this run -- excess above the allowance stays in the unwaivable half, so escalation adds no new
  outcome.

REPLAY_ALLOWANCE_SECONDS is neither a budget asserted here nor a term in the cap: it MIRRORS
validate_vp_replay's own ratified MAX_AGGREGATE_SECONDS ALONE -- the check's green maximum under
its shared-aggregate deadline model. It is recorded because NON_TEST_BUDGET_SECONDS was derived to
DOMINATE it -- a check sanctioned to consume the whole outer budget would make the two jointly
unsatisfiable -- and because the breach diagnostic prints replay_s beside it. It is pinned equal to
its source by a test rather than imported: importing that check's defining module here would trade
this module's stdlib purity (and validate.py's eager import of it) for a coupling the pin already
covers.

Test-half allowance, measured-cost (Decision 208 point 1, amends 182 point 1): the retired
``max(180, 2.0 x min(n_selected, census))`` never distinguished two similar-count selections of
different composition. ``cost_based_allowance`` prices the selection instead, from a per-module
junit cost table (scripts/checks/deps/module_cost_table.py) read at the merge-base with
origin/main -- so a PR cannot raise its own allowance by editing the table it is judged against.
The SAME function gates CI and predicts at plan time (Decision 208 point 1): predict_test_half and
predict_ci_elapsed take the same cost_s and the same COST_COEFFICIENT high end the gate itself
asserts on. test_execution_allowance's legacy formula is KEPT (never removed) as the fallback for
a merge-base table read that is ``absent`` -- the installing PR, and any environment with no
committed table yet.

Declare or split -- the plan-time reading of this CLI's report:
  within_budget    plan the scope as one unit; the measured breadth is already inside the base.
  breadth_waived   KEEP the scope and DECLARE the measured breadth in the plan, so the waiver is
                   expected and reviewed rather than discovered in CI.
  breach           SPLIT the scope into atomic units until the prediction clears -- the measured
                   selection breadth, not the scope-row file count, is the quantity to plan
                   against.
  non_test_breach  neither declare nor split: the non-test half is drifting, which no scope change
                   fixes; open a planning session against the recorded static_s series.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

# The two budgets the fast tier asserts on, the derived ceiling they are bounded by, and the floor
# total they sum to. Re-derived in Decision 208 from the recorded pr-validate population (193
# budget blocks, 2026-09-14..28) plus the enumerated in-tier allowance the non-test half must
# dominate; re-derive by amendment against the recorded static_s / replay_s / phase_count /
# test-green series, never by a silent raise.
NON_TEST_BUDGET_SECONDS = 270.0
TEST_BASE_SECONDS = 180.0

# Legacy fallback rate: KEPT (Decision 208) for test_execution_allowance's un-removed formula,
# used only when the merge-base cost table read is `absent` (the installing PR).
PER_MODULE_SECONDS = 2.0

# Derived guardrail, not a second tier budget (Decision 153): pr-validate's 45-min job timeout
# (.github/workflows/ci.yml) minus the persona image conformance step's 15-min timeout minus a ~5-min diagnostic
# margin = 25 min = 1500 s, on the fast tier's own elapsed clock (job clock additionally includes checkout/pip).
# Re-derive if either timeout-minutes changes.
CEILING_SECONDS = 1500.0

# 450.0 -- written as the partition it IS, so the two halves and their sum can never drift apart.
FLOOR_TOTAL_SECONDS = NON_TEST_BUDGET_SECONDS + TEST_BASE_SECONDS

# Mirror of validate_vp_replay's ratified in-tier allowance (MAX_AGGREGATE_SECONDS alone, under
# the shared-aggregate deadline model). Pinned to its source by test; see the module docstring.
REPLAY_ALLOWANCE_SECONDS = 120.0

# The pre-commit `--all-files` escalation's own governed allowance (Decision 208 point 2): derived
# as ceil10(the recorded escalated precommit_changed max, 108.618) + 10s headroom for
# tracked-file-growth detect-secrets scans = 120.0 -- a different quantity from
# REPLAY_ALLOWANCE_SECONDS (also 120.0, by coincidence). Carved from the non-test half only when
# this run actually escalated (see escalation_carve_out); excess above it stays unwaivable.
PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS = 120.0

# The --pre step name whose phase time the escalation carve-out reads. Its single home: validate.py
# spells no phase name.
PRECOMMIT_PHASE_NAME = "precommit_changed"

# CI-runner noise property (Decision 208): the maximum same-commit primary-session spread
# (max/min) over the recorded fixture's run_id-keyed re-run-attempt groups (>= 2 attempts, minimum
# primary session >= 60s) -- invariant to the cost table's own scale, hence its own named constant.
# Re-derive together with COST_COEFFICIENT on every table regeneration (reversal condition (c)).
NOISE_MARGIN = 1.449

# The zero-false-positive fit of the committed cost table's prices over the recorded test-green
# population: max((single_s - overhead) / cost) across every run whose estimated single-invocation
# test half exceeds TEST_BASE_SECONDS. Re-derived together with COST_COEFFICIENT and NOISE_MARGIN
# on every table regeneration (tests/checks/deps/test_selection_budget_calibration.py owns the
# derivation and its pin).
RECORDED_K_MIN = 1.566

# ceil10(RECORDED_K_MIN x NOISE_MARGIN): a hard duration gate on shared runners is never set at the
# noise floor (a green re-run here is recorded NONDETERMINISTIC, never a rescue) -- the margin,
# not re-runs, absorbs CI-runner noise. Floor: COST_COEFFICIENT >= RECORDED_K_MIN (the
# zero-false-positive fit over the record is the floor, not the target).
COST_COEFFICIENT = 2.3

# The measured collect-only prepass + session overhead (Decision 208): OLS fit of
# (test_s - primary_session_s) on n_selected over the fixture's single-invocation runs.
# tests/checks/deps/test_selection_budget.py::TestPerModuleSeconds owns the derivation.
PREPASS_FIXED_SECONDS = 1.6
PREPASS_PER_MODULE_SECONDS = 0.041

# The two phase names this module reports on. Their single home: validate.py spells neither.
TEST_PHASE_NAME = "pytest_diff"
REPLAY_PHASE_NAME = "validate_vp_replay"

# Measured pytest_diff population (Decision 182): a 98-module floor at 28.317s and the two
# same-head slopes to 263 modules. Used ONLY as the legacy fallback prediction when cost_s is None
# (module scope has no cost table, or the caller never priced the selection).
_PREDICT_FLOOR_MODULES = 98
_PREDICT_FLOOR_SECONDS = 28.317
_PREDICT_SLOPE_LOW = 1.2742
_PREDICT_SLOPE_HIGH = 1.6996

# The measured per-run ratio (single_s - overhead) / cost, over the same recorded test-green
# population RECORDED_K_MIN is drawn from: the median, not the max (a plan-time LOW prediction).
PREDICT_COST_RATIO_LOW = 0.58

# The recorded GREEN (job_conclusion == success) governed non-test half's median / max, added to
# the predicted test half so the CLI reports a whole-run range rather than only its governed test
# term.
_PREDICT_NON_TEST_LOW = 56.369
_PREDICT_NON_TEST_HIGH = 158.014

_DECLARE_OR_SPLIT = {
    "within_budget": "declare-or-split: plan the scope as one unit; the measured breadth is already inside the base.",
    "breadth_waived": (
        "declare-or-split: KEEP the scope and DECLARE the measured breadth in the plan, so the waiver is "
        "expected and reviewed rather than discovered in CI."
    ),
    "breach": (
        "declare-or-split: SPLIT the scope into atomic units until the prediction clears -- the measured "
        "selection breadth, not the scope-row file count, is the quantity to plan against."
    ),
    "non_test_breach": (
        "declare-or-split: neither declare nor split -- the non-test half is drifting, which no scope change "
        "fixes; open a planning session against the recorded static_s series."
    ),
}


@dataclasses.dataclass(frozen=True)
class BudgetVerdict:
    """One run's budget disposition: which arm fired, the limit it was judged against, why it was
    waived (if it was) and the exit code the caller owes."""

    outcome: str
    limit_s: float
    waiver_cause: str | None
    exit_code: int
    allowance_s: float

    @property
    def hard_fail(self) -> bool:
        return self.exit_code != 0


def count_test_modules(root: Path | str | None = None) -> int:
    """Census of test modules on disk (tests/**/test_*.py) -- the sanity floor under the breadth
    allowance, so an over-selecting or corrupted selector cannot inflate its own allowance past
    what the repository could actually run. stdlib rglob; never raises on a missing tree."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    tests_dir = base / "tests"
    if not tests_dir.is_dir():
        return 0
    return sum(1 for _ in tests_dir.rglob("test_*.py"))


def test_execution_allowance(n_selected: int, census: int | None = None) -> float:
    """The LEGACY breadth-derived allowance for the test half -- kept, never removed, as the
    fallback when the merge-base cost table is `absent` (Decision 208).

    Flat TEST_BASE_SECONDS below the crossover, PER_MODULE_SECONDS per selected module above it,
    clamped by the on-disk census and capped at the DERIVED expression
    CEILING_SECONDS - NON_TEST_BUDGET_SECONDS - PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS -- written
    as that expression, never as a literal, so the worst-case asserted total across all three
    governed terms stays exactly CEILING_SECONDS.
    """
    effective = min(n_selected, census) if census is not None else n_selected
    cap = CEILING_SECONDS - NON_TEST_BUDGET_SECONDS - PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS
    return min(max(TEST_BASE_SECONDS, PER_MODULE_SECONDS * max(effective, 0)), cap)


def cost_based_allowance(cost_s: float, n_selected: int, census: int | None = None) -> float:
    """The MEASURED-cost allowance for the test half (Decision 208 point 1): the same function the
    plan-time predictor's high end uses, so a plan-time `breach` is reachable only at the derived
    cap or under a derivation fallback.

    ``cost_s`` is priced by scripts/checks/deps/module_cost_table.py's selection_cost() over the
    selection this run actually made. The measured collect-only prepass/session overhead term
    (PREPASS_FIXED_SECONDS + PREPASS_PER_MODULE_SECONDS per selected module, census-clamped like
    the legacy formula) is added so a wide selection of cheap modules is not under-priced by cost
    alone. Floored at TEST_BASE_SECONDS, capped at the same derived expression
    test_execution_allowance uses.
    """
    effective = min(n_selected, census) if census is not None else n_selected
    cap = CEILING_SECONDS - NON_TEST_BUDGET_SECONDS - PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS
    raw = COST_COEFFICIENT * cost_s + PREPASS_FIXED_SECONDS + PREPASS_PER_MODULE_SECONDS * max(effective, 0)
    return min(max(TEST_BASE_SECONDS, raw), cap)


def escalation_carve_out(phase_times: dict[str, float], *, precommit_escalated: bool) -> float:
    """The amount of the pre-commit phase carved OUT of the unwaivable non-test half (Decision 208
    point 2): ``min(phase_times[PRECOMMIT_PHASE_NAME], PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS)``
    when this run actually escalated, else 0.0. Excess above the allowance stays in the unwaivable
    half by construction (a min(), never a subtract-and-floor), so escalation adds no new outcome."""
    if not precommit_escalated:
        return 0.0
    return min(float(phase_times.get(PRECOMMIT_PHASE_NAME, 0.0)), PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS)


def split_phase_times(phase_times: dict[str, float], elapsed: float) -> tuple[float, float, float, float]:
    """Split one run's recorded phases into (static_s, test_s, replay_s, unattributed_s).

    TEST_PHASE_NAME is the SOLE subtracted phase. REPLAY_PHASE_NAME is broken out for REPORTING
    only -- replay_s stays inside the non-test half it is measured in, and nothing exempts it. The
    unattributed remainder is returned separately and never folded into static_s, so both
    ``static_s + test_s + replay_s + unattributed_s == elapsed`` and ``non_test_s + test_s ==
    elapsed`` hold by construction. The escalation carve-out is applied by the CALLER (validate.py),
    on top of this split's non_test_s -- it is not a fourth return here.
    """
    test_s = float(phase_times.get(TEST_PHASE_NAME, 0.0))
    replay_s = float(phase_times.get(REPLAY_PHASE_NAME, 0.0))
    static_s = float(sum(v for name, v in phase_times.items() if name not in (TEST_PHASE_NAME, REPLAY_PHASE_NAME)))
    unattributed_s = float(elapsed) - float(sum(phase_times.values()))
    return static_s, test_s, replay_s, unattributed_s


def dominant_non_test_phase(phase_times: dict[str, float]) -> str | None:
    """The slowest phase OTHER than the test phase, so an unwaivable non-test alarm is always
    attributable to a named component rather than to an opaque remainder."""
    candidates = {name: seconds for name, seconds in phase_times.items() if name != TEST_PHASE_NAME}
    if not candidates:
        return None
    return max(candidates, key=lambda name: candidates[name])


def predict_ci_elapsed(n_selected: int, *, cost_s: float | None = None) -> tuple[float, float]:
    """Predicted CI elapsed range (low, high) for a selection of ``n_selected`` modules.

    When ``cost_s`` is given (Decision 208), (low, high) uses the SAME cost function the gate
    asserts on: (PREDICT_COST_RATIO_LOW x cost_s + overhead, COST_COEFFICIENT x cost_s + overhead).
    When ``cost_s`` is None, falls back to the legacy floor+slopes prediction. A report, never an
    assertion."""
    test_low, test_high = predict_test_half(n_selected, cost_s=cost_s)
    return test_low + _PREDICT_NON_TEST_LOW, test_high + _PREDICT_NON_TEST_HIGH


def predict_test_half(n_selected: int, *, cost_s: float | None = None) -> tuple[float, float]:
    """The test-half term of predict_ci_elapsed, alone -- the quantity the breadth allowance
    governs, so the CLI can compare like with like.

    ``cost_s`` given: the SAME cost function the gate asserts on (Decision 208) -- the high end,
    COST_COEFFICIENT x cost_s + overhead, IS cost_based_allowance's raw value before the
    base/cap clamp. ``cost_s`` None: the legacy floor+slopes prediction (Decision 182).
    """
    if cost_s is not None:
        overhead = PREPASS_FIXED_SECONDS + PREPASS_PER_MODULE_SECONDS * max(n_selected, 0)
        return (
            PREDICT_COST_RATIO_LOW * cost_s + overhead,
            COST_COEFFICIENT * cost_s + overhead,
        )
    above = max(0, n_selected - _PREDICT_FLOOR_MODULES)
    return (
        _PREDICT_FLOOR_SECONDS + _PREDICT_SLOPE_LOW * above,
        _PREDICT_FLOOR_SECONDS + _PREDICT_SLOPE_HIGH * above,
    )


def budget_extra_keys(
    *,
    n_selected: int,
    static_s: float,
    test_s: float,
    replay_s: float,
    unattributed_s: float,
    phase_count: int,
    waiver_cause: str | None,
    precommit_escalated: bool = False,
    escalation_s: float = 0.0,
    cost_s: float | None = None,
    cost_table_status: str | None = None,
) -> dict[str, Any]:
    """The pure extra-keys dict the caller merges into build_budget_record's returned record at its
    OWN call site -- so the manifest budget block carries the split without scripts/checks/
    _budget_recs.py being edited.

    ``phase_count`` is the PRE-truncation phase count: the recorded phase_times is truncated to the
    10 slowest, so without it static_s could not be re-derived from the same artifact.

    Decision 208 adds five keys: precommit_escalated, escalation_s, cost_s, cost_table_status and
    cost_ratio -- ``(test_s - overhead) / cost_s``, null off the cost path (cost_s is None). The
    overhead term mirrors predict_test_half's cost-path overhead exactly.
    """
    cost_ratio: float | None = None
    if cost_s is not None and cost_s > 0:
        overhead = PREPASS_FIXED_SECONDS + PREPASS_PER_MODULE_SECONDS * max(n_selected, 0)
        cost_ratio = round((test_s - overhead) / cost_s, 3)
    return {
        "n_selected": int(n_selected),
        "static_s": round(float(static_s), 3),
        "test_s": round(float(test_s), 3),
        "replay_s": round(float(replay_s), 3),
        "unattributed_s": round(float(unattributed_s), 3),
        "phase_count": int(phase_count),
        "waiver_cause": waiver_cause,
        "precommit_escalated": bool(precommit_escalated),
        "escalation_s": round(float(escalation_s), 3),
        "cost_s": round(float(cost_s), 3) if cost_s is not None else None,
        "cost_table_status": cost_table_status,
        "cost_ratio": cost_ratio,
    }


def classify(
    *,
    non_test_s: float,
    static_s: float,
    test_s: float,
    replay_s: float,
    elapsed: float,
    n_selected: int,
    forced: bool,
    derivation_ok: bool,
    bypass: bool = False,
    census: int | None = None,
    cost_s: float | None = None,
) -> BudgetVerdict:
    """Dispatch one run to exactly one named outcome, in a PINNED branch order.

    (1) non_test_breach -- unwaivable, evaluated BEFORE any bypass so the escape hatch cannot reach
        it and before the forced waiver so a forced run whose non-test half has drifted no longer
        gets Decision 153 point 1's waiver; (2) bypass; (3) forced_waived; (4)
        forced_ceiling_breach; (5) breach; (6) breadth_waived; (7) within_budget.

    ``derivation_ok`` False (a Decision 55 selection fallback) collapses the allowance to
    TEST_BASE_SECONDS: a degraded selection gets no breadth relief. ``cost_s`` given (Decision 208)
    selects the measured-cost allowance; ``cost_s`` None keeps the legacy breadth-derived one --
    the caller decides by the merge-base cost table's read status, not this function.
    """
    if not derivation_ok:
        allowance = TEST_BASE_SECONDS
    elif cost_s is not None:
        allowance = cost_based_allowance(cost_s, n_selected, census)
    else:
        allowance = test_execution_allowance(n_selected, census)
    if non_test_s > NON_TEST_BUDGET_SECONDS:
        return BudgetVerdict("non_test_breach", NON_TEST_BUDGET_SECONDS, None, 1, allowance)
    if bypass:
        return BudgetVerdict("bypass", allowance, "ignore_budget_flag", 0, allowance)
    if forced and elapsed <= CEILING_SECONDS:
        return BudgetVerdict("forced_waived", CEILING_SECONDS, "full_suite_forced", 0, allowance)
    if forced:
        return BudgetVerdict("forced_ceiling_breach", CEILING_SECONDS, None, 1, allowance)
    if test_s > allowance:
        return BudgetVerdict("breach", allowance, None, 1, allowance)
    if test_s > TEST_BASE_SECONDS:
        return BudgetVerdict("breadth_waived", allowance, "selection_breadth", 0, allowance)
    return BudgetVerdict("within_budget", allowance, None, 0, allowance)


def _report(paths: list[str], root: Path) -> dict[str, Any]:
    """Measure a candidate scope's selection breadth and report the outcome the fast tier would
    reach for it. Imports derive_affected_tests LAZILY so this module's own scope stays
    stdlib-only (module-scope networkx here would break validate.py's --terraform-only path).

    Reads the cost table via the MODULE ATTRIBUTE `module_cost_table.read_at_base_ref` (a lazy
    module import, never `from ... import read_at_base_ref`, so every seam pin binds) and prints
    its status; also prints whether this scope touches a pre-commit global input, via a lazy import
    of scripts.checks._scaffolding.precommit_escalates -- no pre-commit run exists at plan time.
    """
    from scripts.checks.deps import (
        affected_tests,  # noqa: PLC0415
        module_cost_table,  # noqa: PLC0415
    )

    selection = affected_tests.derive_affected_tests([("M", p) for p in paths], repo_root=root)
    n_selected = len(selection["selected"])
    census = count_test_modules(root)

    table_read = module_cost_table.read_at_base_ref(root)
    cost_s: float | None = None
    if table_read.status == "ok":
        cost_s = module_cost_table.selection_cost(selection["selected"], table_read, root)
        allowance = cost_based_allowance(cost_s, n_selected, census)
    else:
        allowance = test_execution_allowance(n_selected, census)

    test_low, test_high = predict_test_half(n_selected, cost_s=cost_s)
    low, high = predict_ci_elapsed(n_selected, cost_s=cost_s)
    verdict = classify(
        non_test_s=_PREDICT_NON_TEST_HIGH,
        static_s=_PREDICT_NON_TEST_HIGH,
        test_s=test_high,
        replay_s=0.0,
        elapsed=high,
        n_selected=n_selected,
        forced=bool(selection["manifest"].get("full_suite_forced", False)),
        derivation_ok=not selection["manifest"].get("fallback", False),
        census=census,
        cost_s=cost_s,
    )

    from scripts.checks import _scaffolding  # noqa: PLC0415

    precommit_note = (
        "touches a pre-commit global input (escalates to --all-files)"
        if (_scaffolding.precommit_escalates(paths))
        else "does not touch a pre-commit global input"
    )

    return {
        "paths": paths,
        "n_selected": n_selected,
        "census": census,
        "allowance_s": allowance,
        "cost_table_status": table_read.status,
        "cost_s": round(cost_s, 3) if cost_s is not None else None,
        "precommit_note": precommit_note,
        "predicted_test_half_s": [round(test_low, 3), round(test_high, 3)],
        "predicted_elapsed_s": [round(low, 3), round(high, 3)],
        "predicted_outcome": verdict.outcome,
        "declare_or_split": _DECLARE_OR_SPLIT[verdict.outcome],
    }


def _paths_from_plan(plan_path: Path) -> list[str]:
    """Read scope[].file rows out of a plan YAML without importing yaml (module scope is
    stdlib-only): the rows are a flat ``- file: <path>`` sequence in this repo's schema."""
    paths: list[str] = []
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- file:"):
            paths.append(stripped.split(":", 1)[1].strip().strip("'\""))
    return paths


def main(argv: list[str] | None = None) -> int:
    """Plan-time reporter. Exits 0 on every path -- it is a report, never a gate."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.checks.deps.selection_budget",
        description="Report a candidate scope's measured selection breadth and the fast-tier outcome it predicts.",
        epilog=(
            "Declare or split -- the plan-time reading of this report:\n"
            "  within_budget    plan the scope as one unit; the measured breadth is already inside the base.\n"
            "  breadth_waived   KEEP the scope and DECLARE the measured breadth in the plan, so the waiver is\n"
            "                   expected and reviewed rather than discovered in CI.\n"
            "  breach           SPLIT the scope into atomic units until the prediction clears -- the measured\n"
            "                   selection breadth, not the scope-row file count, is the quantity to plan\n"
            "                   against.\n"
            "  non_test_breach  neither declare nor split: the non-test half is drifting, which no scope change\n"
            "                   fixes; open a planning session against the recorded static_s series."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--paths", nargs="+", default=[], help="repo-relative paths of the candidate scope")
    parser.add_argument("--plan", default=None, help="a PLAN-*.yaml whose scope[].file rows are the candidate scope")
    parser.add_argument("--json", action="store_true", help="emit the same record as JSON")
    parser.add_argument("--root", default=None, help="repository root (defaults to this file's repo)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[3]
    paths = list(args.paths)
    if args.plan:
        plan_path = Path(args.plan)
        paths += _paths_from_plan(plan_path if plan_path.is_absolute() else root / plan_path)
        paths.append(str(plan_path))
    if not paths:
        parser.print_help()
        return 0

    record = _report(paths, root)
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    low, high = record["predicted_elapsed_s"]
    test_low, test_high = record["predicted_test_half_s"]
    print(f"scope: {len(paths)} path(s) | n_selected: {record['n_selected']} (census {record['census']})")
    print(
        f"test-execution allowance: {record['allowance_s']:.0f}s | non-test budget: {NON_TEST_BUDGET_SECONDS:.0f}s "
        f"| cost table: {record['cost_table_status']}"
    )
    print(f"predicted CI test half: {test_low:.1f}-{test_high:.1f}s | predicted CI elapsed: {low:.1f}-{high:.1f}s")
    print(f"predicted outcome: {record['predicted_outcome']}")
    print(record["precommit_note"])
    print(record["declare_or_split"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
