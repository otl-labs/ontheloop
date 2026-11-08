"""Tests for validate_masked_errexit_steps() -- masked-errexit inline-body guard
(PLAN-pr-conflict-signal-exit-status).

Every real-tree fixture below is extracted programmatically from the live workflow files (never
hand-retyped), except the two reconcile wake bodies frozen from the base tree (the live steps became delegates),
so it stays byte-for-byte identical to what the guard actually scans -- a
paraphrased fixture can pass while the real tree still fails (or vice versa), which is exactly the
round-1 gap this plan's own critique measured against a per-line detector.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks.ci_guards.validate_masked_errexit_steps import (
    _find_matching_paren,
    _join_backslash_continuations,
    _mask_nested_regions,
    _unguarded_assignments,
    scanned_workflow_files,
    validate_masked_errexit_steps,
)

_MODULE = "scripts.checks.ci_guards.validate_masked_errexit_steps"
_ROOT = Path(__file__).resolve().parents[3]
_CI_RCA_WORKFLOW = _ROOT / ".github" / "workflows" / "ci-rca.yml"
_RECONCILE_WORKFLOW = _ROOT / ".github" / "workflows" / "reconcile.yml"
_TF_APPLY_SANDBOX_WORKFLOW = _ROOT / ".github" / "workflows" / "terraform-apply-sandbox.yml"

# Frozen byte for byte from reconcile.yml at the base of PLAN-environment-gates-fail-closed: the live wake steps are now
# one-line apply_wake delegates (size relief), so the guard's discrimination on these real-tree shapes is pinned here.
_FROZEN_APPLY_RECONCILE_WAKE_BODY = r"""PR_NUM=$(gh api "repos/${REPO}/commits/${RED_SHA}/pulls" --jq '.[0].number' 2>/dev/null || echo "")
if [ -z "$PR_NUM" ] || [ "$PR_NUM" = "null" ]; then
  echo "No originating PR resolved for ${RED_SHA}; skipping wake comment."
  exit 0
fi
# A routed outcome now ALWAYS carries a fresh_reason: post-rec-2847 routed=true is only
# emitted on the needs_fresh_plan branch of the `resolved` aggregator. The pre-rec-2847
# "routed with no fresh re-plan" message is therefore unreachable and is not restated here.
if [ "$FRESH_REASON" = "guard_routed" ]; then
  WHY="the saved plan.bin for ${RED_SHA} was guard-BLOCKed (so it was never applied and could not report staleness), and Reconcile re-planned fresh in this same run"
else
  WHY="the saved plan.bin for ${RED_SHA} was stale (STALE_PLAN_FRESH_REPLAN), and Reconcile re-planned fresh in this same run"
fi
if [ "$ROUTED" = "true" ]; then
  # The tf-gated-apply approval is requested BEFORE gated-apply-reconcile starts, so at
  # approval time the reviewer can see nothing of the fresh plan from inside that job --
  # the only evidence lives in THIS run's apply-reconcile step logs. Name them, or the
  # approval is uninformed by construction.
  MSG="Reconcile: ${WHY}, then routed the FRESH plan to the tf-gated-apply job in reconcile.yml (out-of-budget IAM / trust / destroy). Pending reviewer approval in GitHub Actions -- the convergence record keeps its pending_gated marker until the gated apply completes. BEFORE APPROVING, review the fresh plan in this run's apply-reconcile job -- steps \`Fresh re-plan (STALE_PLAN_FRESH_REPLAN fallthrough)\` and \`Deterministic apply guard (fail-closed) -- fresh re-plan\` at ${RUN_URL} -- because the gated job itself shows nothing of it at approval time."
elif [ "$REVIEW_OUTCOME" = "starved" ]; then
  MSG="Reconcile's subagent reviewer was STARVED for ${RED_SHA} (max-turns/no-verdict after retry); apply was blocked and the convergence record was NOT touched. Re-dispatch Reconcile once the reviewer is healthy."
elif [ "$APPLY_OUTCOME" = "success" ]; then
  if [ -n "$FRESH_REASON" ]; then
    MSG="Reconcile: ${WHY}, re-guarded it, and converged GREEN; convergence record updated. The fresh plan guard-PASSed, so it was applied here behind the digest-fed subagent review rather than the tf-gated-apply Environment (Decision 158 route (i))."
  else
    MSG="Reconcile converged GREEN for ${RED_SHA}; convergence record updated."
  fi
else
  MSG="Reconcile did NOT converge (RED) for ${RED_SHA}. Inspect this run's logs directly (ci-rca does not watch reconcile.yml)."
fi
gh pr comment "$PR_NUM" --repo "$REPO" --body "$MSG" || true
"""  # noqa: E501

_FROZEN_GATED_APPLY_RECONCILE_WAKE_BODY = r"""PR_NUM=$(gh api "repos/${REPO}/commits/${RED_SHA}/pulls" --jq '.[0].number' 2>/dev/null || echo "")
if [ -z "$PR_NUM" ] || [ "$PR_NUM" = "null" ]; then
  exit 0
fi
if [ "$APPLY_OUTCOME" = "success" ]; then
  MSG="Reconcile's gated apply converged GREEN for ${RED_SHA} (IAM/trust/destroy change applied after reviewer approval); convergence record updated."
else
  MSG="Reconcile's gated apply did NOT converge (RED) for ${RED_SHA}. Inspect this run's logs directly."
fi
gh pr comment "$PR_NUM" --repo "$REPO" --body "$MSG" || true
"""  # noqa: E501
_CI_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"


def _run_with_single_file(tmp_path: Path, content: str) -> list[str]:
    workflow = tmp_path / "solo.yml"
    workflow.write_text(content, encoding="utf-8")
    with (
        patch(f"{_MODULE}.scanned_workflow_files", return_value=[workflow]),
        patch(f"{_MODULE}._common.ROOT", tmp_path),
    ):
        failed: list[str] = []
        validate_masked_errexit_steps(failed)
    return failed


def _step_run(workflow_path: Path, job_id: str, *, step_id: str | None = None, step_name: str | None = None) -> str:
    """A step's `run:` body, byte-for-byte, resolved by id or name from a REAL workflow file."""
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for step in data["jobs"][job_id]["steps"]:
        if step_id is not None and step.get("id") == step_id:
            return step["run"]
        if step_name is not None and step.get("name") == step_name:
            return step["run"]
    raise AssertionError(f"step not found in {workflow_path}::{job_id} (id={step_id!r} name={step_name!r})")


# --- The discrimination pair (VP6). POST-fix is the real, committed ci-rca.yml `evidence` step
# body. PRE-fix is mechanically DERIVED by undoing this plan's own one-line change (never
# hand-retyped, so a whitespace slip cannot silently fabricate a fixture the real guard would
# never actually see) -- the anchor assertion below fails loudly if the step's shape ever moves. ---

_POST_FIX_EVIDENCE_BODY = _step_run(_CI_RCA_WORKFLOW, "rca", step_id="evidence")
_PRE_FIX_ANCHOR = "done | sort -u | paste -sd ',' -) || CATEGORIES=\"\"\n"
_PRE_FIX_UNDONE = "done | sort -u | paste -sd ',' -)\n"
assert _PRE_FIX_ANCHOR in _POST_FIX_EVIDENCE_BODY, (
    "fixture derivation anchor not found -- ci-rca.yml's evidence step changed shape; update this anchor alongside it"
)
_PRE_FIX_EVIDENCE_BODY = _POST_FIX_EVIDENCE_BODY.replace(_PRE_FIX_ANCHOR, _PRE_FIX_UNDONE, 1)
assert _PRE_FIX_EVIDENCE_BODY != _POST_FIX_EVIDENCE_BODY


class TestPositionalGuardDiscrimination:
    """The pair that earns the guard's zero population: fires on the byte-literal PRE-fix
    ci-rca.yml CATEGORIES block, stays silent on the byte-literal POST-fix block. A per-line
    detector fires on BOTH (the guarding `|| CATEGORIES=""` lands three lines below the
    assignment); a paren-balanced joiner that accepts any `||` inside the substitution fires on
    NEITHER (satisfied by the inner `|| echo "unknown"` inside the for-loop). Only the positional
    rule discriminates correctly, while the body's four OTHER assignments (BUNDLE_SHA/S3_URI/
    PATH/LOCAL) stay recognised as already-guarded in both versions."""

    def test_pre_fix_categories_block_fires(self) -> None:
        joined = _join_backslash_continuations(_PRE_FIX_EVIDENCE_BODY)
        assert _unguarded_assignments(joined) == ["CATEGORIES"]

    def test_post_fix_categories_block_is_silent(self) -> None:
        joined = _join_backslash_continuations(_POST_FIX_EVIDENCE_BODY)
        assert _unguarded_assignments(joined) == []


class TestSimpleSingleLineViolator:
    """The easy case: a bare, single-line unguarded assignment with no surrounding structure."""

    def test_bare_unguarded_assignment_fires(self) -> None:
        body = 'X=$(false)\necho "$X"\n'
        assert _unguarded_assignments(body) == ["X"]

    def test_single_line_trailing_guard_is_silent(self) -> None:
        body = 'X=$(false || echo "fallback")\necho "$X"\n'
        assert _unguarded_assignments(body) == []

    def test_after_close_paren_guard_is_silent(self) -> None:
        body = 'X=$(false) || X=""\necho "$X"\n'
        assert _unguarded_assignments(body) == []


class TestRealNearMissesStaySilent:
    """Negatives drawn byte-for-byte from the real tree (the two reconcile wake bodies are frozen fixtures, because the
    live steps became one-line apply_wake delegates) -- the discrimination this plan's zero
    population is earned by: every one of these passes for a SUBSTANTIVE reason (|| true,
    || echo "", && break || sleep 3, a single-line trailing || echo ""), not by narrowing."""

    def test_artifact_id_backslash_continued_guard_is_silent(self) -> None:
        body = _step_run(
            _CI_RCA_WORKFLOW,
            "rca",
            step_name="Fetch merged PR's selection-manifest (Decision 135 escape-attribution)",
        )
        assert "ARTIFACT_ID" in body
        joined = _join_backslash_continuations(body)
        assert _unguarded_assignments(joined) == []

    def test_reconcile_apply_reconcile_wake_comment_is_silent(self) -> None:
        body = _FROZEN_APPLY_RECONCILE_WAKE_BODY
        assert "|| true" in body
        joined = _join_backslash_continuations(body)
        assert _unguarded_assignments(joined) == []
        live = _step_run(
            _RECONCILE_WORKFLOW,
            "apply-reconcile",
            step_name="Best-effort wake comment on the red commit's originating PR",
        )
        assert live.strip() == "python3 -m scripts.ci.apply_wake --job apply-reconcile"

    def test_reconcile_gated_apply_reconcile_wake_comment_is_silent(self) -> None:
        joined = _join_backslash_continuations(_FROZEN_GATED_APPLY_RECONCILE_WAKE_BODY)
        assert _unguarded_assignments(joined) == []
        live = _step_run(
            _RECONCILE_WORKFLOW,
            "gated-apply-reconcile",
            step_name="Best-effort wake comment on the red commit's originating PR",
        )
        assert live.strip() == "python3 -m scripts.ci.apply_wake --job gated-apply-reconcile"

    def test_terraform_apply_sandbox_artifact_sha_is_silent(self) -> None:
        body = _step_run(_TF_APPLY_SANDBOX_WORKFLOW, "apply-sandbox", step_id="artifact_sha")
        assert '|| echo ""' in body
        joined = _join_backslash_continuations(body)
        assert _unguarded_assignments(joined) == []

    def test_terraform_gated_apply_drift_guard_capture_is_silent(self) -> None:
        body = _step_run(_TF_APPLY_SANDBOX_WORKFLOW, "gated-apply", step_id="drift_guard")
        assert "|| RC=$?" in body
        joined = _join_backslash_continuations(body)
        assert _unguarded_assignments(joined) == []

    def test_signal_green_and_break_or_sleep_has_no_assignment_to_flag(self) -> None:
        body = _step_run(_CI_WORKFLOW, "signal-green", step_name="Signal green CI (wake a watching session)")
        assert "&& break || sleep" in body
        joined = _join_backslash_continuations(body)
        assert _unguarded_assignments(joined) == []


class TestContinueOnErrorGate:
    """A body identical to a real violator is out of scope entirely when its step does not
    declare continue-on-error: true -- a loud step reds the build on its own; this guard exists
    only for the SILENT-green class."""

    def test_loud_step_with_no_continue_on_error_is_never_examined(self, tmp_path: Path) -> None:
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "loud.yml").write_text(
            "on: push\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: |\n"
            "          X=$(false)\n"
            '          echo "$X"\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_masked_errexit_steps(failed)
        assert failed == []


class TestSetPlusEClearsCandidacy:
    """A step whose body clears errexit with its own `set +e` is never a candidate, regardless
    of what its assignments look like -- matches every scripts/ci/*.sh delegate's own defence."""

    def test_body_with_set_plus_e_is_never_examined(self, tmp_path: Path) -> None:
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "cleared.yml").write_text(
            "on: push\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - continue-on-error: true\n"
            "        run: |\n"
            "          set +e\n"
            "          X=$(false)\n"
            '          echo "$X"\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_masked_errexit_steps(failed)
        assert failed == []


class TestRealTreeIsClean:
    """The guard's own headline claim, exercised end-to-end against the live tree."""

    def test_validate_masked_errexit_steps_passes_against_the_real_tree(self) -> None:
        failed: list[str] = []
        validate_masked_errexit_steps(failed)
        assert failed == []


class TestScannedFileSetWiring:
    """validate_masked_errexit_steps() must iterate EXACTLY what scanned_workflow_files()
    reports, never a private glob of its own -- closes the gap VP7's independently-computed-glob
    equality assertion checks for. Two same-shaped violators are planted; only the one named by
    the mocked file list may surface, proving the mock -- not an internal glob -- is authoritative.
    """

    def test_validate_leg_honours_a_monkeypatched_file_set(self, tmp_path: Path) -> None:
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        violating_body = (
            "on: push\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - continue-on-error: true\n"
            "        run: |\n"
            "          X=$(false)\n"
        )
        solo = workflows_dir / "solo.yml"
        solo.write_text(violating_body, encoding="utf-8")
        decoy = workflows_dir / "decoy.yml"
        decoy.write_text(violating_body, encoding="utf-8")

        with (
            patch(f"{_MODULE}.scanned_workflow_files", return_value=[solo]),
            patch(f"{_MODULE}._common.ROOT", tmp_path),
        ):
            failed: list[str] = []
            validate_masked_errexit_steps(failed)

        assert len(failed) == 1
        assert "solo.yml" in failed[0]
        assert "decoy.yml" not in failed[0]

    def test_scanned_workflow_files_matches_the_real_directory_listing(self) -> None:
        got = {p.name for p in scanned_workflow_files()}
        want = {p.name for p in (_ROOT / ".github" / "workflows").glob("*.y*ml")}
        assert got == want


class TestFindMatchingParen:
    """The low-level paren-depth scanner the outer-substitution boundary relies on."""

    def test_unclosed_paren_returns_none(self) -> None:
        assert _find_matching_paren("X=$(echo hello", 2) is None


class TestMaskNestedRegionsParenHandling:
    """_mask_nested_regions()'s paren-tracking branches -- do/done nesting is covered by the
    discrimination pair above; these cover a bare subshell `(...)` at both stack depths."""

    def test_bare_top_level_paren_is_masked(self) -> None:
        assert _mask_nested_regions("(echo hi) | sort") == "          | sort"

    def test_paren_nested_inside_a_do_block_is_masked_by_the_outer_block(self) -> None:
        masked = _mask_nested_regions("for f in x; do (echo hi); done | sort")
        assert "(echo hi)" not in masked
        assert "| sort" in masked

    def test_stray_done_with_no_matching_do_survives_unmasked(self) -> None:
        assert _mask_nested_regions("echo done || true") == "echo done || true"


class TestUnguardedAssignmentsUnclosedSubstitution:
    """An unterminated `$(...)` (malformed shell) must be skipped, never crash the guard --
    there is no valid positional judgement to make about a substitution with no closing paren."""

    def test_unclosed_substitution_is_skipped_not_crashed(self) -> None:
        assert _unguarded_assignments("X=$(echo hello\necho done\n") == []


class TestMalformedWorkflowInputsSkipGracefully:
    """Every YAML/structure edge case the main scan loop defends against -- a malformed or
    atypically-shaped workflow file is skipped, never crashes the guard."""

    def test_invalid_yaml_is_skipped(self, tmp_path: Path) -> None:
        assert _run_with_single_file(tmp_path, "jobs: [this is: not valid: yaml") == []

    def test_non_dict_top_level_is_skipped(self, tmp_path: Path) -> None:
        assert _run_with_single_file(tmp_path, "- just\n- a\n- list\n") == []

    def test_missing_jobs_key_is_skipped(self, tmp_path: Path) -> None:
        assert _run_with_single_file(tmp_path, "on: push\n") == []

    def test_non_dict_job_entry_is_skipped(self, tmp_path: Path) -> None:
        assert _run_with_single_file(tmp_path, 'on: push\njobs:\n  j: "not a mapping"\n') == []

    def test_missing_steps_key_is_skipped(self, tmp_path: Path) -> None:
        assert _run_with_single_file(tmp_path, "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n") == []

    def test_ungoverned_shell_step_is_skipped(self, tmp_path: Path) -> None:
        content = (
            "on: push\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: windows-latest\n"
            "    steps:\n"
            "      - continue-on-error: true\n"
            "        shell: pwsh\n"
            "        run: |\n          $X = (Get-Content foo)\n"
        )
        assert _run_with_single_file(tmp_path, content) == []
