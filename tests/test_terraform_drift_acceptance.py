"""Regression guard: every workflow-embedded ops portal --acceptance value must pass
lint_acceptance_command, and terraform-drift.yml must preserve alarm-only structural invariants.

Prevents recurrence of the prose-acceptance defect (T2.24 root cause) for any workflow that
files a rec via scripts.ops_data_portal --file-rec --acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.checks._scaffolding import _TRANSIENT_INIT_SIGNATURES
from scripts.executor.acceptance_lint import lint_acceptance_command

WORKFLOWS_DIR = Path(".github/workflows")
DRIFT_WORKFLOW = WORKFLOWS_DIR / "terraform-drift.yml"
APPLY_SANDBOX_WORKFLOW = WORKFLOWS_DIR / "terraform-apply-sandbox.yml"
INIT_RETRY_ACTION = Path(".github/actions/terraform-init-retry/action.yml")

_PORTAL_PATTERN = re.compile(r"scripts\.ops_data_portal|\.venv/bin/python\s+-m\s+scripts\.ops_data_portal")
_ACCEPTANCE_DOUBLE = re.compile(r'--acceptance\s+"([^"]+)"')
_ACCEPTANCE_SINGLE = re.compile(r"--acceptance\s+'([^']+)'")


def _extract_acceptance_values(run_block: str) -> list[str]:
    """Return all --acceptance values from a run block that also invokes ops_data_portal."""
    if not _PORTAL_PATTERN.search(run_block):
        return []
    return _ACCEPTANCE_DOUBLE.findall(run_block) + _ACCEPTANCE_SINGLE.findall(run_block)


def _collect_acceptance_values() -> list[tuple[str, str]]:
    """Collect (workflow_filename, acceptance_value) pairs from all workflow files."""
    results: list[tuple[str, str]] = []
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        content = wf_path.read_text(encoding="utf-8")
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs") or {}
        for _job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                run = step.get("run", "")
                if not isinstance(run, str):
                    continue
                for val in _extract_acceptance_values(run):
                    results.append((wf_path.name, val))
    return results


_ACCEPTANCE_CASES = _collect_acceptance_values()


@pytest.mark.parametrize("workflow_name,acceptance_value", _ACCEPTANCE_CASES)
def test_portal_acceptance_passes_lint(workflow_name: str, acceptance_value: str) -> None:
    """Every ops portal --acceptance argument embedded in a workflow must pass lint_acceptance_command."""
    ok, msg = lint_acceptance_command(acceptance_value)
    assert ok, f"{workflow_name}: --acceptance value fails lint_acceptance_command: {acceptance_value!r} -> {msg}"


def _triggers(workflow: Path) -> dict[str, object]:
    """The workflow's trigger mapping.

    PyYAML resolves the bare key `on` to the boolean True (YAML 1.1), so the lookup has to try
    both spellings rather than assuming the string key survives the parse.
    """
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), f"{workflow.name}: expected a trigger mapping, got {on!r}"
    return on


def test_drift_workflow_cron_is_restored() -> None:
    """Decision 178 clause 4 discharged: the hourly cron is RESTORED now that the personal module's
    retired resources are gone from tfstate, so a drift tick no longer sees the deliberate
    state-vs-code delta. Pins the exact cron so a tick cannot silently move off the :17 slot."""
    on = _triggers(DRIFT_WORKFLOW)
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "17 * * * *"}], f"drift cron must be '17 * * * *', got {on['schedule']!r}"


def test_apply_sandbox_workflow_triggers_are_restored() -> None:
    """Decision 178 clause 4: the push and pull_request triggers are RESTORED -- the push path is the
    routine vehicle for a guard-routed destroy reaching gated-apply (Decision 183 later widened that
    reach to a routed workflow_dispatch as well, so a dispatch no longer skips gated-apply).
    Both restored triggers filter on terraform/personal/** -- a workflows-only PR fires neither."""
    on = _triggers(APPLY_SANDBOX_WORKFLOW)
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}
    push = on["push"]
    assert isinstance(push, dict), f"expected a push trigger mapping, got {push!r}"
    assert push.get("branches") == ["main"], f"push trigger must filter branches: [main], got {push!r}"
    assert push.get("paths") == ["terraform/personal/**"], f"push trigger must filter paths, got {push!r}"
    pull_request = on["pull_request"]
    assert isinstance(pull_request, dict), f"expected a pull_request trigger mapping, got {pull_request!r}"
    assert pull_request.get("paths") == ["terraform/personal/**"], (
        f"pull_request trigger must filter paths, got {pull_request!r}"
    )


def test_drift_workflow_has_no_terraform_apply() -> None:
    """terraform-drift.yml must not contain a terraform apply invocation (alarm-only invariant).

    Comments (lines starting with #) are excluded so documentation mentions of the invariant
    do not trip this guard.
    """
    lines = DRIFT_WORKFLOW.read_text(encoding="utf-8").splitlines()
    non_comment_lines = [ln for ln in lines if not ln.lstrip().startswith("#")]
    non_comment_content = "\n".join(non_comment_lines)
    assert not re.search(r"terraform\s+apply\b", non_comment_content), (
        "ALARM-ONLY violated: terraform apply found in non-comment lines of terraform-drift.yml"
    )


def test_drift_workflow_has_detailed_exitcode() -> None:
    """terraform-drift.yml must use -detailed-exitcode to distinguish drift from no-change."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    assert "-detailed-exitcode" in content, "terraform-drift.yml is missing -detailed-exitcode; drift detection would break"


def test_drift_workflow_has_lock_skip_branch() -> None:
    """terraform-drift.yml must have the lock-acquisition-failure skip branch."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    assert "Error acquiring the state lock" in content, (
        "terraform-drift.yml is missing the lock-acquisition-failure skip branch"
    )


def test_drift_workflow_has_pending_gated_branch() -> None:
    """DEP-11 (T2.47): terraform-drift.yml must recognise a pending_gated marker on the current
    convergence record and label the delta as pending-gated (PENDING_GATED_DRIFT), not merely
    out-of-band drift."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    assert "pending_gated" in content, "terraform-drift.yml is missing the pending_gated recognition branch"
    assert "PENDING_GATED_DRIFT" in content, "terraform-drift.yml is missing the PENDING_GATED_DRIFT marker"


def test_drift_workflow_names_the_pending_pr_via_authenticated_lookup() -> None:
    """The pending_gated branch must resolve the pending PR via an authenticated gh api
    commits/.../pulls lookup (CARRY-3: GH_TOKEN wired for the read-only lookup)."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    assert "GH_TOKEN" in content, "terraform-drift.yml is missing GH_TOKEN for the pending PR gh api lookup"
    assert re.search(r"commits/\$\{[^}]*\}/pulls", content), (
        "terraform-drift.yml is missing the commits/.../pulls PR-resolution lookup"
    )


def test_drift_workflow_permissions_include_pull_requests_read() -> None:
    """CARRY-3: the drift-detect job's permissions must be widened to allow the read-only PR
    lookup -- contents: read alone does not cover commits/.../pulls."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)
    job = doc["jobs"]["drift-detect"]
    assert job["permissions"].get("pull-requests") == "read", (
        "drift-detect job permissions must declare pull-requests: read (CARRY-3)"
    )


def test_drift_workflow_pending_gated_checked_before_out_of_band_flip() -> None:
    """The pending_gated branch must be evaluated BEFORE the out-of-band red-flip narrative, so an
    explained (routed-pending) delta never falls through to the out-of-band path."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    pending_gated_check_idx = content.index("PENDING_GATED_PRESENT=$(")
    out_of_band_narrative_idx = content.index("out-of-band infra drift detected by scheduled terraform plan")
    assert pending_gated_check_idx < out_of_band_narrative_idx, (
        "pending_gated detection must run before the out-of-band red-flip narrative"
    )


def test_drift_workflow_pending_gated_branch_never_red_flips() -> None:
    """CARRY-2: the pending_gated branch must never set status to red -- it stays green with only
    the marker/rec-filing side effects; only the (separate, marker-absent) out-of-band branch
    below it is allowed to flip status red."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    start = content.index("PENDING_GATED_PRESENT=$(")
    end = content.index("PENDING_GATED_DRIFT tf_drift rec filed via ops portal.")
    branch_src = content[start:end]
    assert "= 'red'" not in branch_src, "pending_gated branch must not assign a red-status literal"
    assert "existing['status']" not in branch_src, "pending_gated branch must not touch the status field at all"


def test_drift_workflow_stamps_drift_flagged_at_for_idempotency() -> None:
    """The pending_gated branch must stamp drift_flagged_at, and check it, so a later tick with
    the same still-pending marker skips re-filing (one signal per episode, mirroring the existing
    red/unknown dedup invariant)."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    assert "drift_flagged_at" in content, "terraform-drift.yml is missing the drift_flagged_at idempotency stamp"
    assert "ALREADY_FLAGGED" in content, "terraform-drift.yml is missing the already-flagged idempotency check"


def test_drift_workflow_out_of_band_branch_still_gated_behind_marker_absence() -> None:
    """The pre-existing out-of-band red-flip must remain reachable only when no pending_gated
    marker is present -- the pending_gated branch's `if` must close (via `exit 0` + `fi`) strictly
    before the absent-marker fallthrough comment that precedes the out-of-band red-flip."""
    content = DRIFT_WORKFLOW.read_text(encoding="utf-8")
    start = content.index('if [ "$PENDING_GATED_PRESENT" = "true" ]; then')
    end = content.index("# Absent marker: existing out-of-band red-flip, unchanged.")
    assert start < end, "the pending_gated branch must appear before the absent-marker fallthrough"
    branch_and_gap = content[start:end]
    assert "exit 0" in branch_and_gap, "the pending_gated branch must exit 0 (never fall through) once handled"
    assert branch_and_gap.rstrip().endswith("fi"), (
        "the absent-marker fallthrough comment must appear only after the pending_gated branch's `fi` closes"
    )


def test_drift_workflow_init_retry_signature_parity() -> None:
    """The drift-detect job must take its init retry from the terraform-init-retry composite, which
    carries every _TRANSIENT_INIT_SIGNATURES entry (pinned by
    test_terraform_init_retry_action_signature_parity), and must not carry an inline init loop.

    Equivalent invariant to the earlier inline grep -qE parity check: the signatures now live only in
    the composite, so the drift side asserts consumption rather than a second copy of the list.
    """
    doc = yaml.safe_load(DRIFT_WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["drift-detect"]["steps"]
    consumers = [s for s in steps if s.get("uses") == "./.github/actions/terraform-init-retry"]
    assert len(consumers) == 1, f"expected exactly one terraform-init-retry step, found {len(consumers)}"
    lines = DRIFT_WORKFLOW.read_text(encoding="utf-8").splitlines()
    inline = [ln for ln in lines if not ln.lstrip().startswith("#") and "terraform init" in ln]
    assert not inline, f"terraform-drift.yml still carries an inline terraform init: {inline}"


def test_terraform_init_retry_action_signature_parity() -> None:
    """.github/actions/terraform-init-retry/action.yml must carry every
    _TRANSIENT_INIT_SIGNATURES entry in its executable grep -qE line, anchored on the
    non-comment line so the enumerating comment above it cannot satisfy the guard.

    This consumer had NO parity test prior to PLAN-ci-provider-mirror-terraform-init-hardening;
    this test adds that coverage.
    """
    lines = INIT_RETRY_ACTION.read_text(encoding="utf-8").splitlines()
    grep_lines = [ln for ln in lines if not ln.lstrip().startswith("#") and "grep -qE" in ln]
    assert len(grep_lines) == 1, f"expected exactly one non-comment grep -qE line, found {len(grep_lines)}"
    retry_line = grep_lines[0]
    missing = [sig for sig in _TRANSIENT_INIT_SIGNATURES if sig not in retry_line]
    assert not missing, (
        f".github/actions/terraform-init-retry/action.yml init-retry grep -qE line is missing signatures: {missing}"
    )
