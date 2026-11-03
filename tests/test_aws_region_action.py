"""Behaviour and wiring tests for the aws-region composite action (rec-4067, Decision 202 clause 4).

TestResolveBehaviour drives .github/actions/aws-region/resolve.sh under the literal GitHub
composite argv (Decision 162 R2 -- the exact invocation GitHub uses for a composite `shell: bash`
step body, `bash --noprofile --norc -e -o pipefail <file>`) in a tmp tree laid out like the repo
(tmp/.github/actions/aws-region/resolve.sh, tmp/config/aws-region), with tmp GITHUB_ENV and
GITHUB_OUTPUT files standing in for the real GitHub-provided ones. TestActionWiring asserts
action.yml is a thin anchored delegate (Decision 162 R1) whose output binds
steps.resolve.outputs.aws_region. TestBuildDucklakeFallback asserts the build-ducklake-artifacts
region input dropped its literal default in favour of the AWS_REGION the sign-in now exports.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
ACTION_DIR = ROOT / ".github" / "actions" / "aws-region"
RESOLVE_SH = ACTION_DIR / "resolve.sh"
ACTION_YML = ACTION_DIR / "action.yml"
BUILD_DUCKLAKE_YML = ROOT / ".github" / "actions" / "build-ducklake-artifacts" / "action.yml"

# The exact argv GitHub uses for a composite-action `shell: bash` step body -- load-bearing so the
# harness reproduces the same inherited errexit a real composite step body runs under.
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")


def _make_repo_tree(tmp_path: Path, region_contents: str | None) -> Path:
    """Lay out tmp_path like the real repo: tmp/.github/actions/aws-region/resolve.sh, tmp/config/aws-region."""
    action_dir = tmp_path / ".github" / "actions" / "aws-region"
    action_dir.mkdir(parents=True)
    shutil.copy(RESOLVE_SH, action_dir / "resolve.sh")
    (action_dir / "resolve.sh").chmod(0o755)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    if region_contents is not None:
        (config_dir / "aws-region").write_text(region_contents, encoding="utf-8")
    return action_dir


def _run_resolve(
    tmp_path: Path, region_contents: str | None, *, write_missing: bool = True
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    action_dir = _make_repo_tree(tmp_path, region_contents if write_missing else None)
    github_env = tmp_path / "github_env.txt"
    github_output = tmp_path / "github_output.txt"
    github_env.write_text("", encoding="utf-8")
    github_output.write_text("", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "GITHUB_ACTION_PATH": str(action_dir),
        "GITHUB_ENV": str(github_env),
        "GITHUB_OUTPUT": str(github_output),
    }
    result = subprocess.run(
        [*GITHUB_COMPOSITE_BASH, str(action_dir / "resolve.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, github_env, github_output


class TestResolveBehaviour:
    def test_valid_value_exports_env_and_output(self, tmp_path: Path) -> None:
        result, github_env, github_output = _run_resolve(tmp_path, "eu-west-2\n")
        assert result.returncode == 0, result.stderr
        assert "REGION_OK eu-west-2" in result.stdout
        env_text = github_env.read_text(encoding="utf-8")
        assert "AWS_REGION=eu-west-2" in env_text
        assert "AWS_DEFAULT_REGION=eu-west-2" in env_text
        assert "aws_region=eu-west-2" in github_output.read_text(encoding="utf-8")

    def test_missing_file_fails_closed(self, tmp_path: Path) -> None:
        action_dir = _make_repo_tree(tmp_path, None)
        github_env = tmp_path / "github_env.txt"
        github_output = tmp_path / "github_output.txt"
        github_env.write_text("", encoding="utf-8")
        github_output.write_text("", encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin",
            "GITHUB_ACTION_PATH": str(action_dir),
            "GITHUB_ENV": str(github_env),
            "GITHUB_OUTPUT": str(github_output),
        }
        result = subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(action_dir / "resolve.sh")],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 1
        assert "REGION_MISSING" in result.stderr
        assert github_env.read_text(encoding="utf-8") == ""
        assert github_output.read_text(encoding="utf-8") == ""

    def test_whitespace_only_fails_closed(self, tmp_path: Path) -> None:
        result, github_env, github_output = _run_resolve(tmp_path, "   \n\t\n")
        assert result.returncode == 1
        assert "REGION_MISSING" in result.stderr
        assert github_env.read_text(encoding="utf-8") == ""
        assert github_output.read_text(encoding="utf-8") == ""

    def test_invalid_value_fails_closed(self, tmp_path: Path) -> None:
        result, github_env, github_output = _run_resolve(tmp_path, "not-a-region\n")
        assert result.returncode == 1
        assert "REGION_INVALID" in result.stderr
        assert github_env.read_text(encoding="utf-8") == ""
        assert github_output.read_text(encoding="utf-8") == ""

    def test_gov_region_accepted(self, tmp_path: Path) -> None:
        result, _, github_output = _run_resolve(tmp_path, "us-gov-west-1\n")
        assert result.returncode == 0, result.stderr
        assert "aws_region=us-gov-west-1" in github_output.read_text(encoding="utf-8")


class TestActionWiring:
    def test_action_wiring(self) -> None:
        from scripts.checks.ci_guards.validate_composite_action_shell_bodies import _delegated_script, _inline_shell_steps

        manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
        steps = _inline_shell_steps(manifest)
        assert len(steps) == 1
        assert _delegated_script(steps[0][1]) == "resolve.sh"
        assert manifest["outputs"]["aws_region"]["value"] == "${{ steps.resolve.outputs.aws_region }}"
        assert manifest["runs"]["steps"][0]["id"] == "resolve"
        assert "config/aws-region" in manifest["description"]
        assert "rec-4067" in manifest["description"]


class TestBuildDucklakeFallback:
    def test_build_ducklake_region_falls_back_to_aws_region(self) -> None:
        manifest = yaml.safe_load(BUILD_DUCKLAKE_YML.read_text(encoding="utf-8"))
        region_input = manifest["inputs"]["region"]
        assert "default" not in region_input
        step = manifest["runs"]["steps"][0]
        assert step["env"]["REGION"] == "${{ inputs.region || env.AWS_REGION }}"
