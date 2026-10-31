"""rec-4639 acceptance: no Terraform value reaches the merge-time plan reviewer.

The digest (names-only, per-type public literals) is checked against the sentinel fixture, and
review.sh is driven end to end under GitHub's exact composite-step argv with the REAL digest module
and review_verdict.py against a stub claude that records everything it is given. The stub's record
directory is baked into the stub because review.sh runs it under env -i.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.ci.terraform_plan_digest import build_digest, load_literals

ROOT = Path(__file__).parent.parent
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
FIXTURES = Path(__file__).parent / "fixtures" / "subagent_plan_review"
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

_STUB_CLAUDE = """\
#!/usr/bin/env bash
R="__RECORD__"
printf '%s\\0' "$@" > "$R/argv"
pwd -P > "$R/cwd"
ls -A > "$R/ls"
env > "$R/env"
cat > "$R/stdin"
echo "invocation" >> "$R/calls"
echo '{"type":"result","subtype":"success","is_error":false,"result":"PROCEED","num_turns":1,"permission_denials":[]}'
"""


def _plan_literals() -> frozenset[str]:
    return load_literals([str(FIXTURES / "source.tf.txt")])


def _fixture_plan() -> dict:
    import json  # noqa: PLC0415

    return json.loads((FIXTURES / "plan.json").read_text(encoding="utf-8"))


def test_digest_over_fixture_has_no_sentinel_but_keeps_the_public_literals() -> None:
    digest = build_digest(_fixture_plan(), _plan_literals())
    assert "SENTINEL-" not in digest
    assert "+[<non-literal>, s3:GetObject]" in digest
    assert "principal=events.amazonaws.com" in digest


@pytest.fixture()
def run_review(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record"
    record.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(_STUB_CLAUDE.replace("__RECORD__", str(record)), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    workdir = tmp_path / "work"
    workdir.mkdir()
    shutil.copy(FIXTURES / "plan.json", workdir / "plan.json")
    shutil.copy(FIXTURES / "source.tf.txt", workdir / "main.tf")
    (workdir / "terraform.personal.tfvars").write_text('db_name = "SENTINEL-TFVARS"\n', encoding="utf-8")
    (workdir / "plan.bin").write_bytes(b"SENTINEL-PLANBIN")

    def run(context: str = "the sandbox auto-apply pipeline") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env.update(
            CLAUDE_CODE_OAUTH_TOKEN="stub-token",
            CONTEXT_DESCRIPTION=context,
            WORKSPACE_ROOT=str(ROOT),
            GITHUB_OUTPUT=str(tmp_path / "github_output"),
            AWS_PROFILE="SENTINEL-PROFILE",
            AWS_REGION="SENTINEL-REGION",
            CLAUDE_P_RETRY_BACKOFF_BASE="0",
        )
        env.pop("GITHUB_STEP_SUMMARY", None)
        return subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(REVIEW_SH)], cwd=workdir, env=env, capture_output=True, text=True, timeout=120
        )

    run.record = record  # type: ignore[attr-defined]
    run.workdir = workdir  # type: ignore[attr-defined]
    run.output = tmp_path / "github_output"  # type: ignore[attr-defined]
    return run


def test_end_to_end_reviewer_gets_no_value_no_tools_empty_cwd_curated_env(run_review) -> None:
    result = run_review()
    record: Path = run_review.record
    assert "outcome=proceed" in run_review.output.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr

    argv = (record / "argv").read_bytes().split(b"\0")[:-1]
    args = [a.decode() for a in argv]
    stdin = (record / "stdin").read_text(encoding="utf-8")
    env = (record / "env").read_text(encoding="utf-8")
    transcript = (run_review.workdir / "review.json").read_text(encoding="utf-8")
    assert "s3:GetObject" in stdin
    for blob in (stdin, "\n".join(args), env, transcript):
        assert "SENTINEL" not in blob

    tools_at = args.index("--tools")
    assert args[tools_at + 1] == ""
    assert "--strict-mcp-config" in args
    assert args[-1] != "" and tools_at + 1 != len(args) - 1
    assert args[-1].startswith("Review the terraform plan digest below for ")
    for forbidden in ("--allowedTools", "Read", "--bare"):
        assert forbidden not in args
    assert "plan.json" not in args[-1]

    cwd = Path((record / "cwd").read_text(encoding="utf-8").strip())
    assert (record / "ls").read_text(encoding="utf-8") == ""
    assert not cwd.is_relative_to(ROOT) and not cwd.is_relative_to(run_review.workdir)
    assert not cwd.exists()

    keys = {line.split("=", 1)[0] for line in env.splitlines() if "=" in line}
    assert not [k for k in keys if k.startswith("AWS_")]
    assert {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN"} <= keys
    allowed = {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_P_RETRY_BACKOFF_BASE", "TMPDIR", "LANG"}
    assert keys <= allowed | {"PWD", "SHLVL", "_", "OLDPWD"}
    assert "[SUBAGENT-REVIEW] stats num_turns=1 permission_denials=0" in result.stderr


def test_deferred_document_reaches_the_reviewer_rebuilt_without_values(run_review) -> None:
    result = run_review()
    assert result.returncode == 0, result.stderr
    record: Path = run_review.record
    stdin = (record / "stdin").read_text(encoding="utf-8")
    argv = (record / "argv").read_bytes().decode()
    env = (record / "env").read_text(encoding="utf-8")
    transcript = (run_review.workdir / "review.json").read_text(encoding="utf-8")
    assert "rebuilt from deferred read data.aws_iam_policy_document.deferred_reviewer" in stdin
    assert "Allow +[kinesis:PutRecord]" in stdin
    for blob in (stdin, argv, env, transcript):
        assert "SENTINEL" not in blob


@pytest.mark.parametrize("context", ["bad`id`", "bad $(id)", "bad ${HOME}", "", "x" * 301, "line\nbreak"])
def test_context_description_outside_the_allowed_charset_starves_before_any_call(run_review, context: str) -> None:
    result = run_review(context)
    assert result.returncode != 0
    assert run_review.output.read_text(encoding="utf-8").strip().splitlines()[-1] == "outcome=starved"
    assert not (run_review.record / "calls").exists()
