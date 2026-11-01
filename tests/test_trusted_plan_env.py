"""Tests for the trusted plan's environment wrappers (Decision 238 cl.4, PLAN-trusted-plan-cutover PR 2).

Drives trusted_plan_env.sh, quiet_run.sh and materialise_tfvars.sh --stdout under the literal GitHub composite
argv (Decision 162 R2) with a stubbed `aws` on PATH, never touching real Secrets Manager. Fixture values are
non-numeric and non-email-shaped placeholders so they cannot trip the Decision 101 leak guard.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
MATERIALISE = ROOT / ".github" / "actions" / "materialise-tfvars" / "materialise_tfvars.sh"
ENV_SH = ROOT / "scripts" / "ci" / "trusted_plan_env.sh"
QUIET_SH = ROOT / "scripts" / "ci" / "quiet_run.sh"
BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

TFVARS = (
    'account_id = "acct-placeholder-nonnumeric"\n'
    'owner_email = "owner-placeholder-nonemail"\n'
    'platform_dev_external_id = "dev-ext-placeholder"\n'
    'platform_admin_external_id = "admin-ext-placeholder"\n'
    'alerts_email = "alerts-placeholder-nonemail"\n'
)
VALUES = ("acct-placeholder-nonnumeric", "owner-placeholder-nonemail", "dev-ext-placeholder", "admin-ext-placeholder")

_AWS_STUB = """\
#!/usr/bin/env bash
if [ -n "${AWS_STUB_STDOUT_FILE:-}" ] && [ -f "$AWS_STUB_STDOUT_FILE" ]; then cat "$AWS_STUB_STDOUT_FILE"; fi
if [ -n "${AWS_STUB_STDERR_FILE:-}" ] && [ -f "$AWS_STUB_STDERR_FILE" ]; then cat "$AWS_STUB_STDERR_FILE" >&2; fi
exit "${AWS_STUB_EXIT_CODE:-0}"
"""

_PRINT_ENV_CHILD = (
    'echo "OUT acct=$_ACCT dev=$_DEV adm=$_ADM own=$_OWNER"; '
    'echo "TFV acct=${TF_VAR_account_id:-unset} owner=${TF_VAR_owner_email:-unset}"; '
    'echo "child-stderr" >&2'
)


def _setup(tmp_path: Path, tfvars: str = TFVARS, exit_code: int = 0) -> dict[str, str]:
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    aws = stub_bin / "aws"
    aws.write_text(_AWS_STUB, encoding="utf-8")
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "aws-out.txt"
    out.write_text(tfvars, encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["GITHUB_ACTIONS"] = "true"
    env["AWS_STUB_STDOUT_FILE"] = str(out)
    env["AWS_STUB_EXIT_CODE"] = str(exit_code)
    env["RUNNER_TEMP"] = str(tmp_path / "runner-temp")
    (tmp_path / "runner-temp").mkdir(exist_ok=True)
    return env


def _run(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def _files_under(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


class TestMaterialiseStdout:
    def test_masks_on_stderr_tfvars_on_stdout_no_file(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        work = tmp_path / "work"
        (work / "terraform" / "personal").mkdir(parents=True)
        result = _run([*BASH, str(MATERIALISE), "--stdout"], env, work)
        assert result.returncode == 0, result.stderr
        assert result.stdout == TFVARS
        assert "::add-mask::" not in result.stdout
        for value in VALUES[:1] + VALUES[2:]:
            assert f"::add-mask::{value}" in result.stderr
        assert _files_under(work) == set()

    def test_default_mode_unchanged(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        env.pop("GITHUB_ACTIONS")
        work = tmp_path / "work"
        (work / "terraform" / "personal").mkdir(parents=True)
        result = _run([*BASH, str(MATERIALISE)], env, work)
        assert result.returncode == 0
        assert (work / "terraform" / "personal" / "terraform.personal.tfvars").read_text(encoding="utf-8") == TFVARS
        assert result.stdout == "" and result.stderr == ""

    def test_empty_secret_fails_closed(self, tmp_path: Path) -> None:
        env = _setup(tmp_path, tfvars="")
        result = _run([*BASH, str(MATERIALISE), "--stdout"], env, tmp_path)
        assert result.returncode == 1
        assert "EMPTY_SECRET" in result.stderr
        assert result.stdout == ""

    def test_unknown_argument_refused(self, tmp_path: Path) -> None:
        result = _run([*BASH, str(MATERIALISE), "--file"], _setup(tmp_path), tmp_path)
        assert result.returncode == 2
        assert "BAD_ARGUMENT" in result.stderr


class TestTrustedPlanEnv:
    def test_env_reaches_child_masks_on_stderr_only(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        before = _files_under(tmp_path)
        result = _run([*BASH, str(ENV_SH), "--", "bash", "-c", _PRINT_ENV_CHILD], env, work)
        assert result.returncode == 0, result.stderr
        assert "OUT acct=acct-placeholder-nonnumeric dev=dev-ext-placeholder adm=admin-ext-placeholder" in result.stdout
        assert "TFV acct=acct-placeholder-nonnumeric owner=owner-placeholder-nonemail" in result.stdout
        assert "::add-mask::" not in result.stdout
        for value in VALUES:
            assert f"::add-mask::{value}" in result.stderr
        assert _files_under(tmp_path) == before
        assert _files_under(work) == set()

    def test_scrub_only_exports_scrub_vars_and_no_tf_var(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        result = _run([*BASH, str(ENV_SH), "--scrub-only", "--", "bash", "-c", _PRINT_ENV_CHILD], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert (
            "OUT acct=acct-placeholder-nonnumeric dev=dev-ext-placeholder adm=admin-ext-placeholder "
            "own=owner-placeholder-nonemail" in result.stdout
        )
        assert "TFV acct=unset owner=unset" in result.stdout

    def test_malformed_line_fails_closed_without_echo(self, tmp_path: Path) -> None:
        env = _setup(tmp_path, tfvars=TFVARS + "this line has secret-looking-text and no equals\n")
        result = _run([*BASH, str(ENV_SH), "--", "bash", "-c", "echo ran"], env, tmp_path)
        assert result.returncode == 1
        assert "BAD_TFVARS_LINE" in result.stderr
        assert "secret-looking-text" not in result.stderr + result.stdout
        assert "ran" not in result.stdout

    def test_bad_key_fails_closed_without_echo(self, tmp_path: Path) -> None:
        env = _setup(tmp_path, tfvars=TFVARS + 'Bad-Key = "value-should-not-show"\n')
        result = _run([*BASH, str(ENV_SH), "--", "bash", "-c", "echo ran"], env, tmp_path)
        assert result.returncode == 1
        assert "value-should-not-show" not in result.stderr + result.stdout

    def test_usage_without_command(self, tmp_path: Path) -> None:
        result = _run([*BASH, str(ENV_SH), "--"], _setup(tmp_path), tmp_path)
        assert result.returncode == 1


class TestQuietRun:
    def test_only_child_output_to_log_status_kept_nothing_printed(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        log = Path(env["RUNNER_TEMP"]) / "run.log"
        result = _run(
            [*BASH, str(QUIET_SH), str(log), "--", "bash", "-c", "echo out-line; echo err-line >&2; exit 3"], env, tmp_path
        )
        assert result.returncode == 3
        assert result.stdout == "" and result.stderr == ""
        text = log.read_text(encoding="utf-8")
        assert "out-line" in text and "err-line" in text

    def test_stdout_file_gets_only_stdout(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        temp = Path(env["RUNNER_TEMP"])
        result = _run(
            [
                *BASH,
                str(QUIET_SH),
                str(temp / "r.log"),
                "--stdout",
                str(temp / "plan.json"),
                "--",
                "bash",
                "-c",
                "echo json-line; echo err-line >&2",
            ],
            env,
            tmp_path,
        )
        assert result.returncode == 0
        assert (temp / "plan.json").read_text(encoding="utf-8") == "json-line\n"
        assert "err-line" in (temp / "r.log").read_text(encoding="utf-8")
        assert "err-line" not in (temp / "plan.json").read_text(encoding="utf-8")

    def test_log_outside_runner_temp_refused(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        outside = tmp_path / "elsewhere.log"
        result = _run([*BASH, str(QUIET_SH), str(outside), "--", "bash", "-c", "echo hi"], env, tmp_path)
        assert result.returncode == 1
        assert "LOG_OUTSIDE_RUNNER_TEMP" in result.stderr
        assert not outside.exists()

    def test_dotdot_escape_refused(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        sneaky = Path(env["RUNNER_TEMP"]) / ".." / "escape.log"
        result = _run([*BASH, str(QUIET_SH), str(sneaky), "--", "bash", "-c", "echo hi"], env, tmp_path)
        assert result.returncode == 1

    def test_unset_runner_temp_refused(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        env.pop("RUNNER_TEMP")
        result = _run([*BASH, str(QUIET_SH), str(tmp_path / "x.log"), "--", "bash", "-c", "echo hi"], env, tmp_path)
        assert result.returncode == 1


class TestTfvarsGrammar:
    def test_whitespace_only_and_indented_comment_lines_are_skipped(self, tmp_path: Path) -> None:
        env = _setup(tmp_path, tfvars='a = "1"\n\n   \n   # note\nb = 2\n')
        result = _run([*BASH, str(ENV_SH), "--", "bash", "-c", 'echo "$TF_VAR_a $TF_VAR_b"'], env, tmp_path)
        assert (result.returncode, result.stdout.strip()) == (0, "1 2")

    @pytest.mark.parametrize("line", ['t = "v" # inline', 'w = "a\\"b"', "m = [1, 2]", "e ="])
    def test_unsupported_value_shapes_fail_closed_without_echo(self, tmp_path: Path, line: str) -> None:
        env = _setup(tmp_path, tfvars=TFVARS + line + "\n")
        result = _run([*BASH, str(ENV_SH), "--", "bash", "-c", "echo ran"], env, tmp_path)
        assert result.returncode == 1
        assert "ran" not in result.stdout
        assert "inline" not in result.stderr + result.stdout

    def test_every_email_valued_key_is_masked(self, tmp_path: Path) -> None:
        env = _setup(tmp_path)
        result = _run([*BASH, str(ENV_SH), "--", "true"], env, tmp_path)
        assert "::add-mask::alerts-placeholder-nonemail" in result.stderr
        assert "::add-mask::owner-placeholder-nonemail" in result.stderr


def test_materialise_masks_unquoted_values_without_leading_space(tmp_path: Path) -> None:
    env = _setup(tmp_path, tfvars="account_id = 12345\nplatform_dev_external_id =   spaced-id  \n")
    result = _run([*BASH, str(MATERIALISE), "--stdout"], env, tmp_path)
    assert "::add-mask::12345\n" in result.stderr
    assert "::add-mask::spaced-id\n" in result.stderr
