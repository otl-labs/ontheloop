"""rec-5494: CI claude -p agents never load project settings and declare their own MCP and read-verb grants."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "ci" / "claude_p_retry.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci-rca.yml"

_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_ARGV"
N=$(cat "$STUB_COUNT" 2>/dev/null || echo 0)
N=$((N + 1))
echo "$N" > "$STUB_COUNT"
if [ "${STUB_TRANSIENT_FIRST:-}" = 1 ] && [ "$N" -eq 1 ]; then
    echo "API Error: 500 Internal server error"
    exit 1
fi
echo ok
"""


def _run(tmp_path: Path, extra: list[str], transient_first: bool = False) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    argv = tmp_path / "argv"
    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        STUB_ARGV=str(argv),
        STUB_COUNT=str(tmp_path / "count"),
        CLAUDE_P_RETRY_BACKOFF_BASE="0",
        STUB_TRANSIENT_FIRST="1" if transient_first else "0",
    )
    result = subprocess.run(
        ["bash", str(WRAPPER), str(tmp_path / "out.json"), "--", "--output-format", "json", *extra],
        input="prompt",
        env=env,
        capture_output=True,
        text=True,
    )
    lines = argv.read_text(encoding="utf-8").splitlines() if argv.exists() else []
    return result, lines


def test_wrapper_never_loads_project_settings(tmp_path: Path) -> None:
    result, lines = _run(tmp_path, [], transient_first=True)
    assert result.returncode == 0
    assert len(lines) == 2
    assert all("--setting-sources user" in ln for ln in lines)


@pytest.mark.parametrize("flag", ["--setting-sources", "--setting-sources=project", "--settings", "--settings=x"])
def test_wrapper_refuses_settings_overrides(tmp_path: Path, flag: str) -> None:
    result, lines = _run(tmp_path, [flag])
    assert result.returncode == 1
    assert "CI never loads project settings" in result.stderr
    assert lines == []


def _agent_step_run() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for job in steps.values():
        for step in job.get("steps", []):
            if step.get("name") == "Run ci-rca agent":
                return step["run"]
    raise AssertionError("Run ci-rca agent step not found")


def test_ci_rca_step_declares_its_own_mcp_and_read_verbs() -> None:
    run = _agent_step_run()
    assert re.search(r'--mcp-config "\$\{GITHUB_WORKSPACE\}/\.mcp\.json"', run)
    assert "--strict-mcp-config" in run
    assert "--add-dir" not in run
    assert "--setting-sources" not in run and "--settings" not in run
    allowed = re.search(r'--allowedTools "([^"]+)"', run)
    assert allowed
    mcp = [e for e in allowed.group(1).split(",") if e.startswith("mcp__")]
    assert mcp
    assert all(re.fullmatch(r"mcp__ducklake-reads__\w+", e) for e in mcp)
