# complexity-waiver: decision-43
#!/usr/bin/env python3
"""Local CI validation script. Run before every commit.

Runs validation checks that mirror the GitHub Actions CI pipeline.
Default (no flags) runs the full check suite. Use --pre for fast lint/format
checks only during implementation.

Thin CLI (Decision 104, dispatch rewired to per-domain manifests by Decision 169): every check
lives in scripts/checks/<domain>/ and is tagged in the scripts/checks/registry.py check registry,
dispatched via registry.resolve(name) -- never a facade re-export in this file. This file retains
only the argparse surface, the recursion/branch guards, the fast-tier budget assertion, the
non-check scaffolding steps (lint, precommit, mypy, explicit pytest, unit-test invoke_step,
dependency/terraform gates), and re-exports of the shared _common/_scaffolding primitives (back-compat
for `patch("validate.<primitive>")` and `from scripts.validate import <primitive>`).
"""

import argparse
import fnmatch
import os
import shutil  # noqa: F401  (back-compat: patch("validate.shutil.which") test target; global module identity)
import subprocess  # noqa: F401  (back-compat: patch("validate.subprocess.run") test target; global module identity)
import sys
import time
from pathlib import Path as _Path

# Some callers invoke this file as a direct script path (`python scripts/validate.py`,
# e.g. scripts/execute_recommendation.py's [VALIDATE] finalize step) rather than as a
# module (`python -m scripts.validate`); the former does not put the repo root on
# sys.path, so `scripts` would not be importable as a top-level package. Ensure it is,
# before importing scripts.checks.* below.
_REPO_ROOT = _Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.checks import _common, registry, validation_result  # noqa: E402

# Not a facade re-export: the pure (env-only, no-I/O) budget-record builder the fast-tier budget
# assertion attaches to the selection manifest. _budget_recs defines no @register check, and
# _scaffolding below already imports it, so this widens validate.py's import closure by nothing --
# and it stays pyyaml/pydantic-free, so --terraform-only is unaffected.
from scripts.checks._budget_recs import build_budget_record  # noqa: E402

# Facade re-exports: shared primitives (back-compat for `from scripts.validate import ROOT` etc.
# and for `patch("validate.ROOT"/"validate.run"/"validate.PYTHON"/"validate.invoke_step"/
# "validate.get_changed_files")`). Scaffolding below uses the qualified _common.* form so that
# scripts.checks._common is the single interception point for every caller, extracted or not.
from scripts.checks._common import PYTHON, ROOT, get_changed_files, get_status_aware_diff, invoke_step, run  # noqa: F401,E402
from scripts.checks._scaffolding import (  # noqa: F401,E402
    _DQ_FRESHNESS_SECONDS,
    _TERRAFORM_ROOTS,
    _TRANSIENT_CLAUDE_SIGNATURES,
    _TRANSIENT_INIT_SIGNATURES,
    _build_unit_test_cmd,
    _file_budget_breach_rec,
    _file_budget_bypass_rec,
    _mirror_budget_notice_to_summary,
    _terraform_init_with_retry,
    ensure_fresh_dq_results,
    run_coverage_check,
    run_dependency_checks,
    run_lint_checks,
    run_precommit_checks,
    run_pytest_diff,
    run_terraform_checks,
    run_terraform_creds_free,
)

# Module-style imports (Decision 182, extended by Decision 208): scripts/checks/deps/selection_budget.py
# is the SINGLE home of every budget constant this tier asserts on, of the three-term split, and of
# the two phase names it reports on -- this file restates NO budget number, it BINDS its two existing
# public constant names below. module_cost_table.read_at_base_ref is called via the MODULE ATTRIBUTE
# (same binding style) so every seam pin (`monkeypatch.setattr(module_cost_table, "read_at_base_ref",
# ...)`) binds. Module-style rather than `from ... import <name>` so no second CEILING-bearing name
# enters this namespace. Both modules define no check and import only stdlib at module scope, so
# --terraform-only is unaffected; derive_affected_tests stays the function-scope import below.
from scripts.checks.deps import module_cost_table, selection_budget  # noqa: E402

# Bound, never restated: the fast tier's FLOOR total (the two budgets' sum) and the derived
# forced-run ceiling. Both names are kept for their existing readers.
_FAST_TIER_BUDGET_SECONDS = selection_budget.FLOOR_TOTAL_SECONDS
_FORCED_FULL_SUITE_CEILING_SECONDS = selection_budget.CEILING_SECONDS


def _dispatch_check(name: str, failed: list[str]) -> None:
    """Resolve a registered check by name via registry.resolve() and call it.

    registry.resolve(name) imports the check's DEFINING module and does a late-bound getattr at
    call time (Decision 169, amending Decision 104's namespace-dict dispatch) -- so
    `patch("<the check's defining module>.<name>", ...)` continues to intercept, and the resolved
    callable is never cached across dispatches.
    """
    validation_result.dispatch_recording(name, failed, registry.resolve(name))


def _pre_glob_match(path: str, glob: str) -> bool:
    """fnmatch-based match of a single repo-relative path against a single pre_globs pattern.

    fnmatch's `*` already crosses path separators (unlike pathlib/glob.glob's non-recursive
    `*`), so a `**` segment (e.g. "docs/plans/**") matches any depth without special-casing.

    A LEADING `**/` is the one case fnmatch gets wrong for this purpose: it translates to a
    pattern requiring at least one `/`, so "**/*.py" missed repo-ROOT files like setup.py while
    the checks gated on it (validate_cc_limits) scan the root. Retry with the leading `**/`
    stripped so it also matches zero directories -- the recall-safe direction.
    """
    if fnmatch.fnmatch(path, glob):
        return True
    return glob.startswith("**/") and fnmatch.fnmatch(path, glob[3:])


def _should_run_in_pre(pre_globs: tuple[str, ...] | None, changed_paths, derivation_ok: bool) -> bool:
    """Decide whether a --pre gated check should run (VTS-09, dec-55/dec-135/dec-153 fail-closed).

    True (run) when: the check is ungated (pre_globs is None), the diff derivation failed or
    returned something unexpected (derivation_ok is False), the derived changed-path set is
    empty (a no-diff or derivation-blind-spot branch state), or at least one changed path
    matches one of the check's globs. False (skip) ONLY on a successful, non-empty derivation
    with zero matches -- never silently skip on doubt.
    """
    if pre_globs is None or not derivation_ok or not changed_paths:
        return True
    return any(_pre_glob_match(path, glob) for path in changed_paths for glob in pre_globs)


def run_python_checks(failed: list[str]) -> None:
    """Dispatch the ENTIRE full (default) tier by iterating registry.full_sequence() -- every
    check and non-check scaffold step, from lint through the all-files precommit run. This is
    the sole source of full-tier order: main() calls this once and does not hand-dispatch any
    of these steps itself, so registry.py stays the single place that adding/reordering a
    full-tier check touches.
    """

    def _scaffold_lint() -> None:
        run_lint_checks(failed)

    def _scaffold_unit_tests() -> None:
        _common.invoke_step("Unit tests + coverage", _build_unit_test_cmd(), failed)

    def _scaffold_terraform_checks() -> None:
        run_terraform_checks(failed)

    def _scaffold_dependency_health() -> None:
        run_dependency_checks()

    def _scaffold_ensure_fresh_dq() -> None:
        ensure_fresh_dq_results(failed)

    def _scaffold_precommit_all_files() -> None:
        run_precommit_checks(failed, all_files=True)

    scaffold_fns = {
        "lint": _scaffold_lint,
        "unit_tests": _scaffold_unit_tests,
        "terraform_checks": _scaffold_terraform_checks,
        "dependency_health": _scaffold_dependency_health,
        "ensure_fresh_dq": _scaffold_ensure_fresh_dq,
        "precommit_all_files": _scaffold_precommit_all_files,
    }

    for step in registry.full_sequence():
        if step.kind == "check":
            _dispatch_check(step.name, failed)
        else:
            validation_result.run_scaffold(step.name, failed, scaffold_fns[step.name])


def main() -> None:
    # Recursion guard: validate.py spawns pytest, which may collect tests that
    # import/call validate.py again.  _VALIDATE_DEPTH prevents infinite loops.
    depth = int(os.environ.get("_VALIDATE_DEPTH", "0"))
    if depth >= 1:
        print(f"[SKIP] validate.py recursion detected (depth={depth}). Exiting.")
        sys.exit(0)
    os.environ["_VALIDATE_DEPTH"] = str(depth + 1)

    if os.environ.get("PYTEST_CURRENT_TEST"):
        print("[SKIP] validate.py invoked from within a pytest run (PYTEST_CURRENT_TEST set). Exiting.")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Local CI validation. Run before every commit.", allow_abbrev=False)
    parser.add_argument(
        "--pre",
        action="store_true",
        help="Run diff-aware lint/format/mypy/pytest + prompt validation only. Skips terraform and dependencies. "
        "Use for per-step validation during implementation. Subject to a 5-minute wall-clock budget. "
        "Outside GitHub Actions it re-executes in the CI-shaped fast-tier environment.",
    )
    parser.add_argument(
        "--full-env",
        action="store_true",
        help="Run --pre in the current interpreter instead of the CI-shaped environment; the result does not predict PR CI.",
    )
    parser.add_argument(
        "--verifier-coverage",
        "--coverage",
        dest="verifier_coverage",
        action="store_true",
        help="Report scope files lacking verifier coverage (advisory; exits 0 unconditionally). "
        "--coverage is a deprecated alias for --verifier-coverage.",
    )
    parser.add_argument(
        "--terraform-only",
        action="store_true",
        help="Run ONLY the credential-free terraform gate (init -backend=false + validate + fmt -check) "
        "for terraform/ and terraform/personal/. Used by the terraform-validate CI job; no AWS creds needed.",
    )
    parser.add_argument(
        "--ignore-budget",
        action="store_true",
        help="Skip the 5-minute fast-tier budget assertion. Emergency escape hatch only. "
        "Disallowed when CI=true. Bypass is audited via ops_data_portal.",
    )
    parser.add_argument(
        "--ignore-budget-reason",
        default=None,
        metavar="TEXT",
        help="Optional reason for bypassing the budget assertion (captured in the bypass audit rec).",
    )
    parser.add_argument(
        "--update-sloc-budgets",
        action="store_true",
        help="Requires a feature branch (refused on main, like every validate.py invocation). "
        "Regenerate config/sloc_budgets.yaml from the current tree (downward-only ratchet): "
        "lowers shrunk budgets, drops files now <=500. Never raises an existing budget and never "
        "seeds a newly-oversized file (Decision 128) -- decompose it, or register it manually with "
        "a `# raise-approved: dec-NNN` marker.",
    )
    args = parser.parse_args()
    if args.pre and (args.full_env or args.ignore_budget):
        from scripts.checks.deps import fast_tier_env  # noqa: PLC0415

        print(fast_tier_env.FULL_ENV_BANNER)

    full_started_at: str | None = None
    if not any((args.pre, args.verifier_coverage, args.terraform_only, args.update_sloc_budgets)):
        full_started_at = validation_result.utc_now()
        validation_result.clear()

    # CI guard: --ignore-budget is forbidden in CI environments
    if args.ignore_budget and os.environ.get("CI") == "true":
        print("[ERROR] --ignore-budget cannot be used in CI. The escape hatch is for local sessions only.")
        sys.exit(1)

    # Branch guard (skip in CI to allow running from CI environments)
    if os.environ.get("CI") != "true":
        result = _common.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, encoding="utf-8", cwd=_common.ROOT
        )
        if result.stdout.strip() == "main":
            print("\n[ERROR] validate.py refused to run on 'main'.")
            print("Create a feature branch first: git checkout -b agent/{slug}")
            sys.exit(1)

    failed: list[str] = []

    # --verifier-coverage: advisory verifier-coverage report, then exit 0 (--coverage: deprecated alias)
    if args.verifier_coverage:
        if "--coverage" in sys.argv:
            print("[DEPRECATED] --coverage is a deprecated alias; use --verifier-coverage instead.")
        run_coverage_check()
        sys.exit(0)

    # --update-sloc-budgets: regenerate the SLOC budget registry and exit
    if args.update_sloc_budgets:
        # Deferred import (Decision 169): a module-level import here would make this file eagerly
        # import a check-defining module, which the import-closure verification gate forbids.
        from scripts.checks.sloc.sloc_limits import _update_sloc_budgets  # noqa: PLC0415

        _update_sloc_budgets()
        sys.exit(0)

    # --terraform-only: creds-free terraform gate for both roots (CI terraform-validate job)
    if args.terraform_only:
        run_terraform_creds_free(failed)
        print("\n=== Validation Summary (scope: terraform-only) ===")
        if not failed:
            print("All checks passed.")
            sys.exit(0)
        validation_result.print_failure_summary(failed)
        sys.exit(1)

    # --pre: diff-aware lint/format/mypy/affected-set pytest (Decision 135) + prompt validation, with 5-min budget
    if args.pre:
        _t0 = time.monotonic()
        print("Pre mode: diff-aware lint/format/mypy/pytest and prompt validation.")

        changed = _common.get_changed_files()
        diff_manifest = list(changed)
        changed_py = [f for f in changed if f.endswith(".py")]
        # Live, cacheless, strictly-additive affected-set selection (Decision affected-set-
        # selection, amends Decision 73's 2nd amendment): upgrades the edited-set (test files
        # literally in the diff) to a per-run affected-set (tests AFFECTED by the diff), so a
        # source-only PR -- or a test broken by a change it does not itself contain -- is
        # caught pre-merge. Feeds the SAME pytest_diff scaffold below (preferred inline path --
        # touches neither scripts/checks/registry.py nor the frozen pre-sequence scaffold
        # baseline). status_entries is a SEPARATE diff surface from `changed` above (includes
        # deletions + untracked new files, rec-2638) -- get_changed_files()'s own contract for
        # its existing callers (lint/mypy/precommit/coverage) is unchanged. Defensively unioned
        # with `changed` (assumed status "M") so the two independent git reads never silently
        # diverge on which paths are in scope for selection.
        #
        # Deferred (function-scope) import: scripts.checks.deps.affected_tests imports networkx
        # at its own module scope, and this is the ONLY place these two names are used --
        # importing eagerly at validate.py's module scope would break --terraform-only, whose CI
        # job installs only pyyaml+pydantic (see the facade-import block's comment above).
        from scripts.checks.deps.affected_tests import (  # noqa: PLC0415
            derive_affected_tests,
            emit_manifest,
            write_manifest,
        )

        status_entries = _common.get_status_aware_diff()
        _status_paths = {path for _, path in status_entries}
        for f in changed:
            if f not in _status_paths:
                status_entries.append(("M", f))
        _affected_selection = derive_affected_tests(status_entries, repo_root=_common.ROOT)
        changed_tests = _affected_selection["selected"]
        emit_manifest(_affected_selection["manifest"])

        # derive_affected_tests() degrades to the edited-set on any internal error and records it
        # as manifest['fallback']. That is loud inside the derivation but was never surfaced in
        # the summary an operator (or the CI step log) reads, so a persistent derivation bug
        # could hold the gate at pre-Decision-135 recall indefinitely while still exiting 0.
        _selection_fallback = bool(_affected_selection["manifest"].get("fallback", False))
        if _selection_fallback:
            print(
                "\nWARNING: AFFECTED-SET SELECTION DEGRADED -- derivation fell back to the edited-set "
                f"({len(changed_tests)} test file(s)); tests affected by this diff but not IN it are NOT "
                f"gated pre-merge on this run. Reason: {_affected_selection['manifest'].get('fallback_reason', 'unknown')}"
            )

        def _scaffold_lint() -> None:
            run_lint_checks(failed, files=changed)

        _precommit_escalated: dict[str, bool] = {}

        def _scaffold_precommit_changed() -> None:
            # Decision 208: capture the OBSERVED escalation decision run_precommit_checks actually
            # applied, so the budget scaffold reads the applied fact instead of re-evaluating the
            # predicate with a second git show. `is True` below guards against a mocked scaffold's
            # truthy MagicMock ever counting.
            _precommit_escalated["value"] = run_precommit_checks(failed, all_files=False, files=changed)

        def _scaffold_mypy_diff() -> None:
            if changed_py:
                print("\n=== Type check (mypy -- informational) ===")
                mypy_result = _common.run(
                    [_common.PYTHON, "-m", "mypy", "--follow-imports=silent"] + changed_py, cwd=_common.ROOT
                )
                if mypy_result.returncode != 0:
                    print("mypy: type errors found in changed files (informational - not blocking). Fix progressively.")

        def _scaffold_pytest_diff() -> None:
            run_pytest_diff(changed_tests, failed)

        def _scaffold_verifier_coverage_report() -> None:
            run_coverage_check(changed)

        phase_times: dict[str, float] = {}

        def _record_budget_outcome(
            verdict: "selection_budget.BudgetVerdict",
            elapsed: float,
            dominant_phase: str | None,
            extra_keys: dict,
        ) -> None:
            """Attach this run's budget verdict to the selection manifest and re-write it locally.

            The artifact ci.yml's pr-validate job already uploads
            (logs/debug/selection-manifest.json, at `if: always()`) then carries a machine-readable
            budget record alongside `timings` -- so the CI breach population becomes enumerable
            with zero new workflow YAML and zero credential surface. The `within_budget` row is
            what makes it a denominator rather than only an alarm.

            NOT written on every --pre run: only when this trailing scaffold is REACHED. The CI
            hard-reject of --ignore-budget above, or an abort in an earlier phase, leaves the
            manifest with no `budget` key at all -- a distinguishable "run aborted" third state.
            The best-effort S3 copy is uploaded on emit_manifest's first write and so never
            carries this block; see write_manifest's docstring.

            The split's seven extra keys (Decision 182) are merged into build_budget_record's
            returned dict HERE, at this call site: build_budget_record keeps exactly its existing
            six keyword arguments and scripts/checks/_budget_recs.py stays byte-identical, while the
            manifest budget block still carries n_selected / static_s / test_s / replay_s /
            unattributed_s / phase_count / waiver_cause.

            Decision 55 loud-skip: an observability write must never decide a gate's exit code, so
            every failure here (write_manifest's own OSError leg included) is caught and printed.
            """
            try:
                record = build_budget_record(
                    outcome=verdict.outcome,
                    elapsed_s=elapsed,
                    limit_s=verdict.limit_s,
                    dominant_phase=dominant_phase,
                    diff_manifest=diff_manifest,
                    phase_times=phase_times,
                )
                record.update(extra_keys)
                _affected_selection["manifest"]["budget"] = record
                write_manifest(_affected_selection["manifest"])
            except Exception as exc:  # noqa: BLE001 -- Decision 55: never alters the budget verdict
                print(f"Selection manifest: budget-block write failed -- loud skip (Decision 55): {exc!r}")

        def _scaffold_budget_assertion() -> None:
            """Assert the fast tier's THREE budgets (Decision 208, amending Decision 182).

            An absolute NON-TEST half, where Decision 73's anti-drift rationale applies; a
            MEASURED-COST test-execution allowance, priced from a per-module cost table read at
            the merge-base; and a governed PRE-COMMIT ESCALATION allowance, carved out of the
            non-test half only when this run actually escalated. All three are defined, bounded and
            dispatched in scripts/checks/deps/selection_budget.py; this scaffold measures, renders
            and exits. The non-test half is asserted BY SUBTRACTION on the whole remaining half
            (elapsed minus the one subtracted test phase, minus the escalation carve-out), so
            unattributed time and the replay phase are governed at the non-test budget rather than
            only at the ceiling, and a routine escalation no longer inflates the unwaivable half.
            """
            elapsed = time.monotonic() - _t0
            dominant_phase = max(phase_times, key=lambda phase: phase_times[phase]) if phase_times else None
            static_s, test_s, replay_s, unattributed_s = selection_budget.split_phase_times(phase_times, elapsed)
            n_selected = len(changed_tests)

            # Decision 208: the observed fact run_precommit_checks itself applied, never
            # re-evaluated -- `is True` guards against a mocked scaffold's truthy MagicMock, and a
            # phase that never ran (precommit_changed absent from _precommit_escalated) is False,
            # fail-closed per Decision 153 point 2.
            precommit_escalated = _precommit_escalated.get("value") is True
            escalation_s = selection_budget.escalation_carve_out(phase_times, precommit_escalated=precommit_escalated)
            non_test_s = elapsed - test_s - escalation_s

            cost_table_read = module_cost_table.read_at_base_ref(_common.ROOT)
            cost_s: float | None = None
            if cost_table_read.status == "ok":
                cost_s = module_cost_table.selection_cost(changed_tests, cost_table_read, _common.ROOT)

            # Decision 153's fail-closed reads, both unchanged: _empty_manifest defaults
            # full_suite_forced to False, and a derivation fallback collapses the breadth allowance
            # to its base, so a degraded selection still hard-fails and gets no breadth relief.
            # Decision 208 point 1: an `unreadable` merge-base read is the SAME kind of degraded
            # derivation (Decision 55) -- it also collapses the allowance to TEST_BASE_SECONDS,
            # never falling through to the more permissive legacy breadth rule.
            derivation_ok = not _selection_fallback and cost_table_read.status != "unreadable"
            verdict = selection_budget.classify(
                non_test_s=non_test_s,
                static_s=static_s,
                test_s=test_s,
                replay_s=replay_s,
                elapsed=elapsed,
                n_selected=n_selected,
                forced=bool(_affected_selection["manifest"].get("full_suite_forced", False)),
                derivation_ok=derivation_ok,
                bypass=bool(args.ignore_budget),
                census=selection_budget.count_test_modules(_common.ROOT),
                cost_s=cost_s,
            )
            extra_keys = selection_budget.budget_extra_keys(
                n_selected=n_selected,
                static_s=static_s,
                test_s=test_s,
                replay_s=replay_s,
                unattributed_s=unattributed_s,
                phase_count=len(phase_times),
                waiver_cause=verdict.waiver_cause,
                precommit_escalated=precommit_escalated,
                escalation_s=escalation_s,
                cost_s=cost_s,
                cost_table_status=cost_table_read.status,
            )
            predicted_low, predicted_high = selection_budget.predict_ci_elapsed(n_selected, cost_s=cost_s)
            print(
                f"\nSelection breadth: {n_selected} test module(s) selected; test-execution allowance "
                f"{verdict.allowance_s:.0f}s, non-test budget {selection_budget.NON_TEST_BUDGET_SECONDS:.0f}s "
                f"(escalation carve-out {escalation_s:.1f}s of {selection_budget.PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS:.0f}s "
                f"allowance). CI-predicted elapsed {predicted_low:.0f}-{predicted_high:.0f}s. This run's local wall "
                f"clock ({elapsed:.1f}s: non-test {non_test_s:.1f}s, test {test_s:.1f}s) is ADVISORY -- local "
                "hardware is not the CI runner, though the same assertions are applied to it. Cost table: "
                f"{cost_table_read.status}."
            )
            if extra_keys["cost_ratio"] is not None and extra_keys["cost_ratio"] > selection_budget.RECORDED_K_MIN:
                print(
                    f"ADVISORY: this run's cost ratio ({extra_keys['cost_ratio']:.3f}) exceeds the recorded envelope "
                    f"(RECORDED_K_MIN {selection_budget.RECORDED_K_MIN:.3f}) -- feeds the next coefficient derivation."
                )
            if verdict.hard_fail and _selection_fallback:
                print(
                    "\nNOTE: this run's affected-set derivation fell back to the edited-set, so the "
                    "pytest scope it just ran is not the scope this diff should have run -- read the "
                    "phase attribution below as degraded, not as ordinary drift."
                )

            if verdict.outcome == "non_test_breach":
                # Branch 1, evaluated before every waiver: rec-free BY DESIGN (the outcome is
                # deliberately outside _budget_recs._REC_FILING_OUTCOMES, whose breach wording names
                # a limit this run was never judged against), so the reporting obligation is
                # discharged through the OTHER channel -- a titled CI step-summary section, exactly
                # as Decision 153 point 3 requires of a deliberately rec-free arm.
                _mirror_budget_notice_to_summary(
                    "Fast-tier non-test budget breached",
                    f"ERROR: Fast tier exceeded budget (non-test budget: {verdict.limit_s / 60:.1f} min). "
                    f"Non-test half: {non_test_s:.1f}s of {elapsed:.1f}s elapsed -- static {static_s:.1f}s, "
                    f"{selection_budget.REPLAY_PHASE_NAME} {replay_s:.1f}s (its own ratified allowance is "
                    f"{selection_budget.REPLAY_ALLOWANCE_SECONDS:.0f}s), unattributed {unattributed_s:.1f}s, "
                    f"escalation carve-out {escalation_s:.1f}s of "
                    f"{selection_budget.PRECOMMIT_ESCALATION_ALLOWANCE_SECONDS:.0f}s allowance already applied. "
                    f"Dominant non-test phase: {selection_budget.dominant_non_test_phase(phase_times) or 'unknown'}.\n"
                    "This half is UNWAIVABLE: no bypass, forced-scope, breadth or fallback path reaches it. "
                    "Fix the named component, or re-derive the budget by amendment against the recorded "
                    "static_s series -- never by a bypass flag.",
                )
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
                sys.exit(1)
            elif verdict.outcome == "bypass":
                _file_budget_bypass_rec(elapsed, diff_manifest, args.ignore_budget_reason, dominant_phase)
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
                print(f"\nBudget assertion skipped (--ignore-budget). Elapsed: {elapsed / 60:.1f} min.")
            elif verdict.outcome == "forced_waived":
                # Decision 153: a root-conftest change deterministically forces full-suite scope
                # (Decision 135/VTS-03) and is a self-identified, already-diagnosed cause -- not
                # drift. Unchanged, with one stated subordination: the unwaivable non-test arm above
                # outranks it, so a forced run whose NON-TEST half has drifted no longer gets it.
                _mirror_budget_notice_to_summary(
                    "Fast-tier budget waived (forced full-suite scope)",
                    "budget waived: full-suite scope forced by root-conftest change. "
                    f"Elapsed: {elapsed / 60:.1f} min (forced-run ceiling: "
                    f"{_FORCED_FULL_SUITE_CEILING_SECONDS / 60:.0f} min). Dominant phase: "
                    f"{dominant_phase or 'unknown'}. No breach rec filed -- this is a "
                    "deterministic, already-diagnosed full-suite run, not drift.",
                )
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
            elif verdict.outcome == "forced_ceiling_breach":
                _mirror_budget_notice_to_summary(
                    "Fast-tier forced-run ceiling breached",
                    "ERROR: Fast tier exceeded budget (forced-run ceiling: "
                    f"{_FORCED_FULL_SUITE_CEILING_SECONDS / 60:.0f} min) -- the forced full-suite run "
                    f"exceeded the forced-run ceiling. Elapsed: {elapsed / 60:.1f} min. Dominant phase: "
                    f"{dominant_phase or 'unknown'}.\n"
                    "The forced full suite itself is now the problem, not the gate -- "
                    "open a planning session (Decision 153 reversal condition (b)).",
                )
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
                sys.exit(1)
            elif verdict.outcome == "breach":
                _file_budget_breach_rec(elapsed, diff_manifest, dominant_phase)
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
                print(
                    f"\nERROR: Fast tier exceeded budget (test-execution allowance: "
                    f"{verdict.limit_s / 60:.1f} min for {n_selected} selected module(s)). Test half: "
                    f"{test_s:.1f}s of {elapsed:.1f}s elapsed. Dominant phase: {dominant_phase or 'unknown'}.\n"
                    "This tier has grown beyond its design contract. Either:\n"
                    "  1. Move the slow check to the full tier, or\n"
                    "  2. Optimise the check, or\n"
                    "  3. Open a planning session to revise this budget (requires Decision Record)."
                )
                sys.exit(1)
            elif verdict.outcome == "breadth_waived":
                # The SECOND measured cause (Decision 182): a test half above the base, explained by
                # measured selection breadth rather than by drift. Loud and attributable, to the same
                # bar Decision 153 point 1 sets for the forced waiver, and recorded as an explicit
                # waiver_cause in the manifest rather than by overloading a boolean.
                _mirror_budget_notice_to_summary(
                    "Fast-tier test budget waived (selection breadth)",
                    f"budget waived: waiver_cause selection_breadth -- this diff selected {n_selected} test "
                    f"module(s), so the test-execution allowance is {verdict.allowance_s:.0f}s and the measured "
                    f"test half is {test_s:.1f}s. Non-test half: {non_test_s:.1f}s against an unwaived "
                    f"{selection_budget.NON_TEST_BUDGET_SECONDS:.0f}s. No breach rec filed -- this is measured "
                    "breadth, not drift.",
                )
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)
            else:
                _record_budget_outcome(verdict, elapsed, dominant_phase, extra_keys)

        scaffold_fns = {
            "lint": _scaffold_lint,
            "precommit_changed": _scaffold_precommit_changed,
            "mypy_diff": _scaffold_mypy_diff,
            "pytest_diff": _scaffold_pytest_diff,
            "verifier_coverage_report": _scaffold_verifier_coverage_report,
            "budget_assertion": _scaffold_budget_assertion,
        }

        # pre_globs gate derivation (VTS-09): reuse the already-built status-aware diff:
        # fail-closed on any derivation error (dec-135) -- an exception here must never
        # silently skip a gated check, so _gate_derivation_ok flips to False and
        # _should_run_in_pre runs everything regardless of glob match.
        try:
            _gate_changed_paths = {path for _, path in status_entries}
            _gate_derivation_ok = True
        except Exception as exc:
            _gate_changed_paths = set()
            _gate_derivation_ok = False
            print(f"pre_globs: diff derivation failed ({exc}); running all gated checks (fail-closed).")

        for step in registry.pre_sequence():
            step_t0 = time.monotonic()
            if step.kind == "check":
                if _should_run_in_pre(step.pre_globs, _gate_changed_paths, _gate_derivation_ok):
                    _dispatch_check(step.name, failed)
                else:
                    print(f"skipped-by-glob: {step.name}")
            else:
                validation_result.run_scaffold(step.name, failed, scaffold_fns[step.name])
            phase_times[step.name] = time.monotonic() - step_t0

        print("\n=== Validation Summary (scope: pre) ===")
        if not failed:
            print("All checks passed.")
            sys.exit(0)
        else:
            validation_result.print_failure_summary(failed)
            print("\nFix all failures before committing.")
            sys.exit(1)

    scope = "all"

    # Full (default) tier: run_python_checks() dispatches the ENTIRE registry.full_sequence()
    # (every check + scaffold step, lint through the all-files precommit run) -- see its
    # docstring. There is no separate hand-dispatched block here; registry.py is the sole
    # source of full-tier order.
    run_python_checks(failed)

    print(f"\n=== Validation Summary (scope: {scope}) ===")
    if not failed:
        print("All checks passed.")
        validation_result.write_completed_visible(
            started_at=full_started_at or validation_result.utc_now(), exit_code=0, failed_checks=[]
        )
        sys.exit(0)
    else:
        validation_result.print_failure_summary(failed)
        print("\nFix all failures before committing.")
        validation_result.write_completed_visible(
            started_at=full_started_at or validation_result.utc_now(), exit_code=1, failed_checks=failed
        )
        sys.exit(1)


def _entrypoint() -> None:
    # Kept out of main(): many tests drive main() with --pre and every environment guard stripped.
    from scripts.checks.deps import fast_tier_env  # noqa: PLC0415

    fast_tier_env.reexec_pre_if_needed(sys.argv, os.environ)
    main()


if __name__ == "__main__":
    _entrypoint()
