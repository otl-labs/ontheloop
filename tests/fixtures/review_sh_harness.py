"""Harness that runs review.sh under GitHub's composite argv with shimmed claude, python3, sudo and npm.

The python3 shim stubs terraform_plan_digest.py with a fixed digest and passes review_verdict.py calls (including
--reason) to the real script, so verdict exit codes and the reason line come from the real classifier. The claude
shim replays the configured transcripts, one per invocation. Importable from tests; tests/fixtures is exempt from the
cross-test-import guard.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

_PYTHON3_SHIM = """\
#!/usr/bin/env bash
case "${1:-}" in
  *terraform_plan_digest.py)
    echo "# stub plan digest"
    exit 0
    ;;
esac
exec "__REAL_PYTHON__" "$@"
"""

_CLAUDE_SHIM = """\
#!/usr/bin/env bash
R="__RECORD__"
echo "invocation" >> "$R/calls"
n=$(wc -l < "$R/calls")
cat > /dev/null
cat "$R/reply_$n.json" 2> /dev/null || cat "$R/reply_1.json"
"""

_NETWORK_SHIM = """\
#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "__RECORD__/network"
exit 0
"""


def envelope(result: str, subtype: str = "success", is_error: bool = False) -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": is_error, "result": result, "num_turns": 1})


def _write_shim(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ReviewShHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.record = tmp_path / "record"
        self.record.mkdir()
        self.workdir = tmp_path / "work"
        self.workdir.mkdir()
        (self.workdir / "plan.json").write_text("{}", encoding="utf-8")
        self.github_output = tmp_path / "github_output"
        self.summary_path = tmp_path / "summary"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        record = str(self.record)
        _write_shim(bin_dir / "python3", _PYTHON3_SHIM.replace("__REAL_PYTHON__", sys.executable))
        _write_shim(bin_dir / "claude", _CLAUDE_SHIM.replace("__RECORD__", record))
        _write_shim(bin_dir / "sudo", _NETWORK_SHIM.replace("__RECORD__", record))
        _write_shim(bin_dir / "npm", _NETWORK_SHIM.replace("__RECORD__", record))
        self.env = os.environ.copy()
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env['PATH']}"
        self.env.update(
            CLAUDE_CODE_OAUTH_TOKEN="stub-token",
            CONTEXT_DESCRIPTION="the sandbox auto-apply pipeline",
            WORKSPACE_ROOT=str(ROOT),
            GITHUB_OUTPUT=str(self.github_output),
            GITHUB_STEP_SUMMARY=str(self.summary_path),
            CLAUDE_P_RETRY_BACKOFF_BASE="0",
        )
        self.env.pop("REVISE_ROUTES", None)
        self.result: subprocess.CompletedProcess[str] | None = None

    def run(self, *replies: str, revise_routes: str | None = None) -> ReviewShHarness:
        for i, reply in enumerate(replies, start=1):
            (self.record / f"reply_{i}.json").write_text(reply, encoding="utf-8")
        if revise_routes is not None:
            self.env["REVISE_ROUTES"] = revise_routes
        self.result = subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(REVIEW_SH)],
            cwd=self.workdir,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        return self

    @property
    def rc(self) -> int:
        assert self.result is not None
        return self.result.returncode

    @property
    def stderr(self) -> str:
        assert self.result is not None
        return self.result.stderr

    @property
    def outcome(self) -> str:
        if not self.github_output.exists():
            return ""
        values = [
            line.partition("=")[2]
            for line in self.github_output.read_text(encoding="utf-8").splitlines()
            if line.startswith("outcome=")
        ]
        return values[-1] if values else ""

    @property
    def summary(self) -> str:
        return self.summary_path.read_text(encoding="utf-8") if self.summary_path.exists() else ""

    @property
    def claude_calls(self) -> int:
        path = self.record / "calls"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    @property
    def retry_transcript(self) -> Path:
        return self.workdir / "review_retry.json"
