"""Structural contract for terraform-validate's provider-mirror consumption
(PLAN-ci-provider-mirror-terraform-init-hardening, rec-2836).

Asserts the terraform-validate job in .github/workflows/ci.yml carries: id-token:write,
event-conditional OIDC role selection, continue-on-error on the creds step, a fail-open gating
chain with a visible reason on every degradation branch, and the fail-closed TF_CLI_CONFIG_FILE
consistency invariant. Also asserts config/terraform/cc-web.tfrc's filesystem_mirror include and
direct exclude patterns stay symmetric -- an asymmetry would exclude kislerdm from direct with
nothing serving it (see terraform/CLAUDE.md).

Each assertion here is meaningful against the PRE-change tree too: the id-token:write assertion
fails on a job carrying only `contents: read`; the version-gate assertion fails if the activation
condition greps for directory non-emptiness instead of the locked-version filename; the symmetry
assertion fails if the tfrc's include/exclude prefixes ever diverge.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CI_WORKFLOW = Path(".github/workflows/ci.yml")
CC_WEB_TFRC = Path("config/terraform/cc-web.tfrc")
GATE_SCRIPT = Path("scripts/ci/provider_mirror_gate.sh")


def _load_terraform_validate_job() -> dict:
    doc = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["terraform-validate"]


def _find_step(job: dict, step_id: str) -> dict:
    for step in job["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id={step_id!r} found in terraform-validate job")


class TestIdTokenAndPermissions:
    def test_id_token_write_present(self) -> None:
        """id-token:write is REQUIRED for the OIDC mint -- without it every mint fails and the
        whole provider-mirror fix silently no-ops."""
        job = _load_terraform_validate_job()
        assert job["permissions"].get("id-token") == "write", (
            "terraform-validate job permissions must declare id-token: write, or every OIDC mint "
            "in the gate chain fails and the mirror consumption silently no-ops"
        )

    def test_contents_read_retained(self) -> None:
        job = _load_terraform_validate_job()
        assert job["permissions"].get("contents") == "read"


class TestEventConditionalRoleSelection:
    def test_creds_step_selects_role_by_event_name(self) -> None:
        """The OIDC role-to-assume must branch on github.event_name so a pull_request run assumes
        the read-only PR role and a push run assumes the already-trusted branch role."""
        job = _load_terraform_validate_job()
        step = _find_step(job, "mirror_oidc")
        role_expr = step["with"]["role-to-assume"]
        assert "github.event_name == 'pull_request'" in role_expr, (
            "role-to-assume must branch on github.event_name == 'pull_request'"
        )
        assert "agent-platform-github-ci-pr" in role_expr
        assert "agent-platform-github-ci-branch" in role_expr

    def test_creds_step_continue_on_error(self) -> None:
        """The creds step must carry continue-on-error so an OIDC/AWS outage, a fork PR, or an
        unmerged IAM grant degrades to gate 1/3 rather than failing this required check."""
        job = _load_terraform_validate_job()
        step = _find_step(job, "mirror_oidc")
        assert step.get("continue-on-error") is True, (
            "the OIDC creds step lacks continue-on-error -- an OIDC failure would fail the "
            "required check outright instead of degrading fail-open"
        )


class TestGateChainFailOpen:
    def _gate_script(self) -> str:
        return GATE_SCRIPT.read_text(encoding="utf-8")

    def test_gate_step_delegates_to_the_script(self) -> None:
        job = _load_terraform_validate_job()
        step = _find_step(job, "mirror_gate")
        assert step["run"].strip() == "bash scripts/ci/provider_mirror_gate.sh"

    def test_gate_chain_never_declares_its_own_if(self) -> None:
        """The gate-chain step must run unconditionally (no top-level `if:`) so it always has the
        chance to report a reason -- gating happens INSIDE the script, not via step skipping."""
        job = _load_terraform_validate_job()
        step = _find_step(job, "mirror_gate")
        assert "if" not in step, "mirror_gate step must not be skipped by a job-level if: condition"

    def test_gate_chain_checks_oidc_outcome_first(self) -> None:
        script = self._gate_script()
        step = _find_step(_load_terraform_validate_job(), "mirror_gate")
        assert step["env"]["OIDC_FAILED"] == "${{ steps.mirror_oidc.outcome != 'success' }}"
        assert '"${OIDC_FAILED:-}" != "false"' in script, "gate 1/3 must check the OIDC outcome first"

    def test_gate_chain_checks_non_empty_sync(self) -> None:
        script = self._gate_script()
        assert "aws s3 sync" in script
        assert 'ls -A "$MIRROR_DIR"' in script, "gate 2/3 must check the synced tree is non-empty"

    def test_gate_chain_keys_activation_on_exact_locked_version_not_directory_emptiness(self) -> None:
        """CRITICAL (plan-critique wedge-path finding): the activation gate must key on the exact
        kislerdm/neon version parsed from the lockfile, never on mere directory non-emptiness --
        a version-incomplete mirror with the tfrc exported would leave init with NO direct fallback
        (provider_installation has no method-chaining)."""
        script = self._gate_script()
        assert ".terraform.lock.hcl" in script, "gate 3/3 must parse terraform/personal/.terraform.lock.hcl"
        assert "kislerdm" in script and "neon" in script
        assert re.search(r"terraform-provider-neon_\$\{?NEON_VERSION\}?_", script), (
            "gate 3/3 must check for the exact-version package filename, not directory non-emptiness"
        )
        assert re.search(r"terraform-provider-github_\$\{?GITHUB_VERSION\}?_", script)
        assert "integrations/github" in script

    def test_every_degradation_branch_prints_a_reason(self) -> None:
        script = self._gate_script()
        for expected in ("gate 1/3", "gate 2/3", "gate 3/3"):
            assert expected in script, f"missing a labeled reason for {expected}"

    def test_reason_printed_to_stdout_and_step_summary(self) -> None:
        script = self._gate_script()
        assert "$GITHUB_STEP_SUMMARY" in script, "degradation reason must be written to $GITHUB_STEP_SUMMARY"
        assert 'echo "provider-mirror: DEGRADED' in script

    def test_success_path_reports_active(self) -> None:
        script = self._gate_script()
        assert "provider-mirror: ACTIVE" in script, "the success path must print a visible ACTIVE marker"

    def test_success_path_exports_tf_cli_config_file(self) -> None:
        script = self._gate_script()
        assert "TF_CLI_CONFIG_FILE" in script and "$GITHUB_ENV" in script

    def test_gate_chain_never_hard_fails(self) -> None:
        """The gate-chain script must not itself exit non-zero on a degradation branch -- `set -e`
        would turn a normal fail-open branch into a job failure."""
        script = self._gate_script()
        assert "set -uo pipefail" in script, "gate chain must not run under `set -e` (fail-open, not fail-hard)"


class TestFailClosedInvariant:
    def _invariant_step(self) -> dict:
        job = _load_terraform_validate_job()
        for step in job["steps"]:
            if step.get("name", "").startswith("Provider-mirror fail-closed"):
                return step
        raise AssertionError("no fail-closed consistency invariant step found")

    def test_invariant_gated_on_all_gates_succeeding(self) -> None:
        step = self._invariant_step()
        assert step.get("if") == "steps.mirror_gate.outputs.active == 'true'", (
            "the fail-closed invariant must fire ONLY when all three gates already succeeded"
        )

    def test_invariant_exits_nonzero_when_tf_cli_config_file_unset(self) -> None:
        step = self._invariant_step()
        script = step["run"]
        assert "TF_CLI_CONFIG_FILE" in script
        assert "exit 1" in script, "the invariant must fail loudly (exit 1) on inconsistency"
        assert "set -euo pipefail" in script, "the invariant step must run fail-closed (set -e)"


class TestTfrcSymmetry:
    def test_filesystem_mirror_include_matches_direct_exclude(self) -> None:
        """An asymmetry between the mirror's include list and direct's exclude list would leave
        kislerdm excluded from direct with nothing in filesystem_mirror serving it (or vice
        versa) -- a state that reds terraform init regardless of mirror content."""
        content = CC_WEB_TFRC.read_text(encoding="utf-8")
        include_match = re.search(r"filesystem_mirror\s*\{[^}]*include\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
        exclude_match = re.search(r"direct\s*\{[^}]*exclude\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
        assert include_match and exclude_match, "cc-web.tfrc must declare both filesystem_mirror.include and direct.exclude"

        def _parse_list(raw: str) -> set[str]:
            return {tok.strip().strip('"') for tok in raw.split(",") if tok.strip()}

        include_set = _parse_list(include_match.group(1))
        exclude_set = _parse_list(exclude_match.group(1))
        assert include_set == exclude_set, (
            f"filesystem_mirror.include {include_set} must equal direct.exclude {exclude_set} "
            "(an asymmetry excludes a provider from direct with nothing serving it)"
        )
        assert include_set, "include/exclude sets must be non-empty"

    def test_mirror_serves_terraform_github_provider(self) -> None:
        content = CC_WEB_TFRC.read_text(encoding="utf-8")
        include_match = re.search(r"filesystem_mirror\s*\{[^}]*include\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
        assert include_match and "registry.terraform.io/integrations/*" in include_match.group(1)
        assert "integrations/github" in content
