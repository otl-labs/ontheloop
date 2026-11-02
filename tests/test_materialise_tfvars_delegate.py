"""Wiring and behaviour tests for the materialise-tfvars consolidation (rec-3978/rec-3977).

TestDelegateBehaviour drives .github/actions/materialise-tfvars/materialise_tfvars.sh under the
literal GitHub composite argv (Decision 162 R2 -- the exact invocation GitHub uses for a composite
`shell: bash` step body, `bash --noprofile --norc -e -o pipefail <file>`) with a stubbed `aws` on
PATH, never touching real AWS. TestActionWiring / TestDriftWiring / TestBaselineRetired
assert the three consuming surfaces (action.yml, terraform-drift.yml, the baseline) are wired
correctly. TestContractRepoint loads the LIVE docs/contracts/terraform-cc-web-operations.yaml
(not a tmp_path synthetic) and asserts the recovery-procedure repoint landed.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
ACTION_DIR = ROOT / ".github" / "actions" / "materialise-tfvars"
DELEGATE_SH = ACTION_DIR / "materialise_tfvars.sh"
ACTION_YML = ACTION_DIR / "action.yml"
DRIFT_WORKFLOW = ROOT / ".github" / "workflows" / "terraform-drift.yml"
BASELINE_YAML = ROOT / "config" / "composite_action_body_baseline.yaml"
CONTRACT_YAML = ROOT / "docs" / "contracts" / "terraform-cc-web-operations.yaml"

# The exact argv GitHub uses for a composite-action `shell: bash` step body -- load-bearing so the
# harness reproduces the same inherited errexit a real composite step body runs under.
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

DEST_REL = Path("terraform/personal/terraform.personal.tfvars")

# Non-numeric, non-email-shaped placeholders throughout (constraint: a 12-digit account_id
# fixture trips this plan's own Decision 101 leak guard, and an email-address-shaped value trips
# its other clause the same way -- masking only needs a non-empty value, not a realistic one).
_FIXTURE_LINES = (
    'account_id = "acct-placeholder-nonnumeric"',
    'owner_email = "owner-placeholder-nonemail-value"',
    'platform_dev_external_id = "dev-ext-id-placeholder"',
    'platform_admin_external_id = "admin-ext-id-placeholder"',
    'alerts_email = "alerts-placeholder-nonemail-value"',
)
FIXTURE_TFVARS = "\n".join(_FIXTURE_LINES) + "\n"

_AWS_STUB = """\
#!/usr/bin/env bash
if [ "$1 $2" != "ssm get-parameter" ]; then echo "unexpected aws call: $1 $2" >&2; exit 99; fi
if [ -n "${AWS_STUB_STDOUT_FILE:-}" ] && [ -f "$AWS_STUB_STDOUT_FILE" ]; then
  cat "$AWS_STUB_STDOUT_FILE"
fi
if [ -n "${AWS_STUB_STDERR_FILE:-}" ] && [ -f "$AWS_STUB_STDERR_FILE" ]; then
  cat "$AWS_STUB_STDERR_FILE" >&2
fi
exit "${AWS_STUB_EXIT_CODE:-0}"
"""

_MV_LOG_STUB = """\
#!/usr/bin/env bash
if [ -n "${MV_LOG:-}" ]; then
  printf '%s\\n' "$1" >> "$MV_LOG"
fi
exec /usr/bin/mv "$1" "$2"
"""

_MV_FAIL_STUB = """\
#!/usr/bin/env bash
echo "stub mv: simulated write failure" >&2
exit 1
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _make_repo_root(tmp_path: Path) -> Path:
    # Plain helper, not a pytest fixture -- module-level `@pytest.fixture` decorator lines show
    # as wholesale `+`-prefixed additions in `git diff`, and the retained leading `+` collides
    # with this plan's own VP step 11 Decision-101 email-shaped-literal regex (its local-part
    # class admits `+`), a false positive independent of any fixture VALUE chosen above.
    (tmp_path / "terraform" / "personal").mkdir(parents=True)
    return tmp_path


def _make_stub_bin(tmp_path: Path) -> Path:
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    _write_executable(stub_dir / "aws", _AWS_STUB)
    return stub_dir


def _run_delegate(
    repo_root: Path, stub_bin: Path, *, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    env.pop("GITHUB_ACTIONS", None)
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return subprocess.run(
        [*GITHUB_COMPOSITE_BASH, str(DELEGATE_SH)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _configure_success(repo_root: Path, tmp_path: Path) -> dict[str, str]:
    stdout_file = tmp_path / "aws-stdout.txt"
    stdout_file.write_text(FIXTURE_TFVARS, encoding="utf-8")
    return {"AWS_STUB_STDOUT_FILE": str(stdout_file), "AWS_STUB_EXIT_CODE": "0"}


class TestDelegateBehaviour:
    def test_install_terraform_unset(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        env = _configure_success(repo_root, tmp_path)
        env["INSTALL_TERRAFORM"] = None  # ensure unset, not merely absent from overrides
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 0, result.stderr
        dest = repo_root / DEST_REL
        assert dest.read_text(encoding="utf-8") == FIXTURE_TFVARS

    def test_silent_on_both_streams(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        env = _configure_success(repo_root, tmp_path)
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_mode_600(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        env = _configure_success(repo_root, tmp_path)
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 0, result.stderr
        dest = repo_root / DEST_REL
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600

    def test_first_equals_split(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        stdout_file = tmp_path / "aws-stdout.txt"
        stdout_file.write_text(
            'account_id = "acct-placeholder-nonnumeric"\n'
            'owner_email = "owner-placeholder-nonemail-value"\n'
            'platform_dev_external_id = "dev-id-with=equals-sign"\n'
            'platform_admin_external_id = "admin-ext-id-placeholder"\n'
            'alerts_email = "alerts-placeholder-nonemail-value"\n',
            encoding="utf-8",
        )
        env = {"AWS_STUB_STDOUT_FILE": str(stdout_file), "AWS_STUB_EXIT_CODE": "0", "GITHUB_ACTIONS": "true"}
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 0, result.stderr
        assert "::add-mask::dev-id-with=equals-sign" in result.stdout

    def test_fail_closed_on_empty(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        stdout_file = tmp_path / "aws-stdout.txt"
        stdout_file.write_text("", encoding="utf-8")
        env = {"AWS_STUB_STDOUT_FILE": str(stdout_file), "AWS_STUB_EXIT_CODE": "0"}
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 1
        assert "EMPTY_SECRET" in result.stderr
        assert not (repo_root / DEST_REL).exists()

    def test_masks_under_github_actions(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        env = _configure_success(repo_root, tmp_path)
        env["GITHUB_ACTIONS"] = "true"
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("::add-mask::") == 3
        assert "::add-mask::acct-placeholder-nonnumeric" in result.stdout
        assert "::add-mask::dev-ext-id-placeholder" in result.stdout
        assert "::add-mask::admin-ext-id-placeholder" in result.stdout
        # owner_email / alerts_email are NOT in the protected-key set -- never masked.
        assert "owner-placeholder-nonemail-value" not in result.stdout
        assert "alerts-placeholder-nonemail-value" not in result.stdout

    def test_preexisting_file_preserved(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)
        dest = repo_root / DEST_REL
        dest.write_bytes(b"PRE-EXISTING-BYTES-UNCHANGED")
        stderr_file = tmp_path / "aws-stderr.txt"
        stderr_file.write_text("AccessDeniedException: stub fetch failure\n", encoding="utf-8")
        env = {"AWS_STUB_STDERR_FILE": str(stderr_file), "AWS_STUB_EXIT_CODE": "1"}
        result = _run_delegate(repo_root, stub_bin, env_overrides=env)
        assert result.returncode == 1
        assert "FETCH_FAILED" in result.stderr
        assert dest.read_bytes() == b"PRE-EXISTING-BYTES-UNCHANGED"

    def test_distinct_failure_messages(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        stub_bin = _make_stub_bin(tmp_path)

        # Mode 1: fetch failed.
        stderr_file = tmp_path / "aws-stderr.txt"
        stderr_file.write_text("stub fetch error\n", encoding="utf-8")
        fetch_result = _run_delegate(
            repo_root, stub_bin, env_overrides={"AWS_STUB_STDERR_FILE": str(stderr_file), "AWS_STUB_EXIT_CODE": "1"}
        )
        assert fetch_result.returncode == 1

        # Mode 2: empty secret.
        empty_stdout = tmp_path / "aws-stdout-empty.txt"
        empty_stdout.write_text("", encoding="utf-8")
        empty_result = _run_delegate(
            repo_root, stub_bin, env_overrides={"AWS_STUB_STDOUT_FILE": str(empty_stdout), "AWS_STUB_EXIT_CODE": "0"}
        )
        assert empty_result.returncode == 1

        # Mode 3: write failed (stub `mv` always fails; fetch itself succeeds).
        write_fail_bin = tmp_path / "stub-bin-write-fail"
        write_fail_bin.mkdir()
        _write_executable(write_fail_bin / "aws", _AWS_STUB)
        _write_executable(write_fail_bin / "mv", _MV_FAIL_STUB)
        env = dict(os.environ)
        env["PATH"] = f"{write_fail_bin}:{env.get('PATH', '')}"
        env.pop("GITHUB_ACTIONS", None)
        env.update(_configure_success(repo_root, tmp_path))
        write_result = subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(DELEGATE_SH)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert write_result.returncode == 1
        assert "WRITE_FAILED" in write_result.stderr

        messages = {fetch_result.stderr, empty_result.stderr, write_result.stderr}
        assert len(messages) == 3, messages
        assert "FETCH_FAILED" in fetch_result.stderr
        assert "EMPTY_SECRET" in empty_result.stderr
        assert "WRITE_FAILED" in write_result.stderr

    def test_temp_file_beside_destination(self, tmp_path: Path) -> None:
        repo_root = _make_repo_root(tmp_path)
        mv_log = tmp_path / "mv.log"
        mv_stub_bin = tmp_path / "stub-bin-mv-log"
        mv_stub_bin.mkdir()
        _write_executable(mv_stub_bin / "aws", _AWS_STUB)
        _write_executable(mv_stub_bin / "mv", _MV_LOG_STUB)

        env = dict(os.environ)
        env["PATH"] = f"{mv_stub_bin}:{env.get('PATH', '')}"
        env["MV_LOG"] = str(mv_log)
        env.pop("GITHUB_ACTIONS", None)
        env.update(_configure_success(repo_root, tmp_path))

        result = subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(DELEGATE_SH)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        logged_tmp_path = mv_log.read_text(encoding="utf-8").strip()
        assert logged_tmp_path, "mv stub never logged a source path"
        logged_tmp = (repo_root / logged_tmp_path).resolve()
        dest = repo_root / DEST_REL
        assert logged_tmp.parent == dest.parent
        assert logged_tmp.name.startswith(dest.name)
        assert logged_tmp.name != dest.name


class TestActionWiring:
    def test_action_wiring(self) -> None:
        from scripts.checks.ci_guards.validate_composite_action_shell_bodies import _delegated_script, _inline_shell_steps

        manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
        steps = _inline_shell_steps(manifest)
        assert len(steps) == 1
        assert _delegated_script(steps[0][1]) == "materialise_tfvars.sh"

        working_dir_input = manifest["inputs"]["working-directory"]
        assert working_dir_input["required"] is False
        assert working_dir_input["default"] == "."
        runs_steps = manifest["runs"]["steps"]
        assert len(runs_steps) == 1
        assert runs_steps[0]["working-directory"] == "${{ inputs.working-directory }}"


class TestDriftWiring:
    def test_drift_wiring(self) -> None:
        job = yaml.safe_load(DRIFT_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["drift-detect"]
        uses_steps = [s for s in job["steps"] if isinstance(s, dict) and "materialise-tfvars" in str(s.get("uses", ""))]
        assert len(uses_steps) == 1
        cond = str(uses_steps[0].get("if", ""))
        assert "oidc_drift.outcome" in cond and "success" in cond

        from scripts.checks.ci_guards import _workflow_shell_bodies as w

        pattern_keys = (
            "materialise-tfvars-from-secrets-manager",
            "mask-secret-sourced-tfvars-values-in-logs",
            "assert-tfvars-file-is-non-empty-fail-closed",
        )
        live = w.scan_workflow_bodies()
        live_matches = [
            k for k in live if k.startswith(".github/workflows/terraform-drift.yml::") and k.split("::")[-1] in pattern_keys
        ]
        assert not live_matches, live_matches


class TestBaselineRetired:
    def test_baseline_retired(self) -> None:
        baseline = yaml.safe_load(BASELINE_YAML.read_text(encoding="utf-8"))
        r3 = baseline.get("r3") or {}
        r3_workflows = baseline.get("r3_workflows") or {}

        stale_r3 = [k for k in r3 if k.startswith(".github/actions/materialise-tfvars::")]
        assert not stale_r3, stale_r3

        stale_workflow_keys = (
            "materialise-tfvars-from-secrets-manager",
            "mask-secret-sourced-tfvars-values-in-logs",
            "assert-tfvars-file-is-non-empty-fail-closed",
        )
        stale_r3_workflows = [
            k
            for k in r3_workflows
            if k.startswith(".github/workflows/terraform-drift.yml::") and k.split("::")[-1] in stale_workflow_keys
        ]
        assert not stale_r3_workflows, stale_r3_workflows

        from scripts.checks.ci_guards.validate_composite_action_shell_bodies import _load_baseline, scan_repository

        live = scan_repository()
        live_r3 = [k for k in live["r3_bodies"] if k.startswith(".github/actions/materialise-tfvars::")]
        baseline_r3 = [k for k in _load_baseline()["r3"] if k.startswith(".github/actions/materialise-tfvars::")]
        assert not live_r3 and not baseline_r3, (live_r3, baseline_r3)


class TestContractRepoint:
    def test_contract_repoint(self) -> None:
        contract = yaml.safe_load(CONTRACT_YAML.read_text(encoding="utf-8"))
        delegate_path = ".github/actions/materialise-tfvars/materialise_tfvars.sh"
        assert delegate_path in (contract.get("referenced_repo_paths") or [])
        recovery_procedure = str(contract["tfvars_and_remote_state_recovery"]["recovery_procedure"])
        assert "materialise_tfvars.sh" in recovery_procedure
