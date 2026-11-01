"""review.sh: digest overflow is a distinct STARVED, and a REVISE prints one redacted reason line.

Real scripts under GitHub's literal composite argv; only claude is stubbed (tests/test_subagent_plan_review_values_stripped.py
pattern). The stub's reply and record directory are baked in because review.sh runs it under env -i.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
FIXTURES = Path(__file__).parent / "fixtures" / "subagent_plan_review"
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

_STUB = """\
#!/usr/bin/env bash
R="__RECORD__"
echo "invocation" >> "$R/calls"
n=$(wc -l < "$R/calls")
cat > /dev/null
cat "$R/reply_$n.json" 2> /dev/null || cat "$R/reply_1.json"
"""


def _envelope(result: str, subtype: str = "success") -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": False, "result": result, "num_turns": 1})


@pytest.fixture()
def run_review(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record"
    record.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(_STUB.replace("__RECORD__", str(record)), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    workdir = tmp_path / "work"
    workdir.mkdir()
    shutil.copy(FIXTURES / "plan.json", workdir / "plan.json")
    shutil.copy(FIXTURES / "source.tf.txt", workdir / "main.tf")
    summary = tmp_path / "summary"

    def run(*replies: str) -> subprocess.CompletedProcess[str]:
        for i, reply in enumerate(replies, start=1):
            (record / f"reply_{i}.json").write_text(reply, encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env.update(
            CLAUDE_CODE_OAUTH_TOKEN="stub-token",
            CONTEXT_DESCRIPTION="the sandbox auto-apply pipeline",
            WORKSPACE_ROOT=str(ROOT),
            GITHUB_OUTPUT=str(tmp_path / "github_output"),
            GITHUB_STEP_SUMMARY=str(summary),
            CLAUDE_P_RETRY_BACKOFF_BASE="0",
        )
        return subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(REVIEW_SH)], cwd=workdir, env=env, capture_output=True, text=True, timeout=120
        )

    run.record = record  # type: ignore[attr-defined]
    run.workdir = workdir  # type: ignore[attr-defined]
    run.outcome = lambda: (tmp_path / "github_output").read_text(encoding="utf-8").strip().splitlines()[-1]  # type: ignore[attr-defined]
    run.summary = lambda: summary.read_text(encoding="utf-8") if summary.exists() else ""  # type: ignore[attr-defined]
    return run


def _calls(run_review) -> int:
    path = run_review.record / "calls"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def test_digest_overflow_is_distinct_starved_and_never_calls_the_reviewer(run_review) -> None:
    changes = [
        {
            "address": f"aws_x.r{i:05d}",
            "type": "aws_x",
            "change": {"actions": ["update"], "before": {"a": 1}, "after": {"a": 2}, "after_unknown": {}},
        }
        for i in range(1000)
    ]
    (run_review.workdir / "plan.json").write_text(json.dumps({"resource_changes": changes}), encoding="utf-8")
    result = run_review(_envelope("PROCEED"))
    assert result.returncode == 1
    assert run_review.outcome() == "outcome=starved"
    assert "[SUBAGENT-REVIEW] STARVED: digest overflow:" in result.stderr
    assert "digest overflow" in run_review.summary()
    assert "plan digest build failed" not in result.stderr
    assert _calls(run_review) == 0


def test_other_digest_failure_keeps_the_build_failed_marker(run_review) -> None:
    (run_review.workdir / "plan.json").write_text("not json", encoding="utf-8")
    result = run_review(_envelope("PROCEED"))
    assert result.returncode == 1
    assert run_review.outcome() == "outcome=starved"
    assert "plan digest build failed" in result.stderr
    assert "digest overflow" not in result.stderr
    assert _calls(run_review) == 0


def test_revise_prints_one_redacted_reason_line_and_mirrors_it(run_review) -> None:
    acct = "3" * 12
    arn = f"arn:aws:iam::{acct}:role/x"
    result = run_review(_envelope(f"REVISE too broad\n::error::boom `x`\nrole {arn} acct {acct}"))
    assert result.returncode == 1
    assert run_review.outcome() == "outcome=revise"
    lines = [line for line in result.stderr.splitlines() if "REVISE reason:" in line]
    assert len(lines) == 1 and lines[0].startswith("[SUBAGENT-REVIEW] REVISE reason: too broad")
    assert acct not in result.stderr and acct not in run_review.summary()
    assert "[ARN]" in lines[0] and "[ACCOUNT_ID]" in lines[0]
    summary = run_review.summary()
    assert "## Subagent review REVISE" in summary
    reason_line = next(line for line in summary.splitlines() if "REVISE reason:" in line)
    assert reason_line.startswith("`[SUBAGENT-REVIEW]") and reason_line.endswith("`")
    assert "`" not in reason_line[1:-1]


def test_starved_then_revise_prints_the_retry_transcript_reason(run_review) -> None:
    result = run_review(_envelope("", subtype="error_max_turns"), _envelope("REVISE retry reason here"))
    assert result.returncode == 1
    assert run_review.outcome() == "outcome=revise"
    assert "[SUBAGENT-REVIEW] REVISE reason: retry reason here" in result.stderr


def test_proceed_and_starved_print_no_reason_line(run_review) -> None:
    proceed = run_review(_envelope("PROCEED"))
    assert proceed.returncode == 0 and "REVISE reason" not in proceed.stderr
    starved = run_review(_envelope("", subtype="error_max_turns"))
    assert starved.returncode == 1 and run_review.outcome() == "outcome=starved"
    assert "REVISE reason" not in starved.stderr
