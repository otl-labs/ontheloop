"""rec-5509: review.sh starves (fails closed) when the claude CLI installer refuses a missing or malformed pin.

Decision 162 R2: review.sh runs under the literal composite-step argv GitHub uses for the subagent-plan-review
action, with WORKSPACE_ROOT pointing at a scratch root that holds the real scripts/ci/install_claude_cli.sh and a bad
config/claude-cli-version, and PATH limited to recording sudo/npm stubs plus /usr/bin and /bin.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
INSTALLER = ROOT / "scripts" / "ci" / "install_claude_cli.sh"
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

_RECORDER = '#!/usr/bin/env bash\nprintf \'%s\\n\' "$(basename "$0") $*" >> "$CALL_LOG"\nexit 0\n'


def _run_review(tmp_path: Path, pin_text: str | None) -> tuple[subprocess.CompletedProcess[str], list[str], list[str], str]:
    workspace = tmp_path / "workspace"
    (workspace / "scripts" / "ci").mkdir(parents=True)
    (workspace / "config").mkdir()
    shutil.copy2(INSTALLER, workspace / "scripts" / "ci" / "install_claude_cli.sh")
    if pin_text is not None:
        (workspace / "config" / "claude-cli-version").write_text(pin_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("sudo", "npm"):
        stub = bin_dir / name
        stub.write_text(_RECORDER, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "plan.json").write_text("{}", encoding="utf-8")
    call_log, github_output, summary = tmp_path / "calls", tmp_path / "github_output", tmp_path / "summary"
    result = subprocess.run(
        [*GITHUB_COMPOSITE_BASH, str(REVIEW_SH)],
        cwd=workdir,
        env={
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "CALL_LOG": str(call_log),
            "WORKSPACE_ROOT": str(workspace),
            "CLAUDE_CODE_OAUTH_TOKEN": "stub-token",
            "CONTEXT_DESCRIPTION": "the sandbox auto-apply pipeline",
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    output_lines = github_output.read_text(encoding="utf-8").splitlines()
    outcomes = [line.split("=", 1)[1] for line in output_lines if line.startswith("outcome=")]
    return result, calls, outcomes, summary.read_text(encoding="utf-8") if summary.exists() else ""


@pytest.mark.parametrize(("pin_text", "cause"), [(None, "pin file missing"), ("latest\n", "pin file malformed")])
def test_bad_pin_starves_review_before_any_install(tmp_path: Path, pin_text: str | None, cause: str) -> None:
    result, calls, outcomes, summary = _run_review(tmp_path, pin_text)
    assert result.returncode == 1
    assert outcomes[-1] == "starved"
    assert calls == []
    assert cause in result.stderr
    assert "[SUBAGENT-REVIEW] STARVED: claude CLI installer (scripts/ci/install_claude_cli.sh) failed" in result.stderr
    assert "Subagent review STARVED" in summary
