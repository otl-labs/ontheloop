"""Hermetic test of scripts/ci/subagent_review_smoke.sh against a stub claude (rec-4639)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SMOKE_SH = ROOT / "scripts" / "ci" / "subagent_review_smoke.sh"

_STUB = """\
#!/usr/bin/env bash
cat > /dev/null
N=0
[ -f "__COUNTER__" ] && N=$(cat "__COUNTER__")
N=$((N + 1))
echo "$N" > "__COUNTER__"
F="__REPLIES__/$N.json"
[ -f "$F" ] || F=$(ls "__REPLIES__"/*.json | tail -n 1)
cat "$F"
"""

_PY_SHIM = """\
#!/usr/bin/env bash
"__REAL__" "$@"
RC=$?
case "${1:-}" in *terraform_plan_digest.py) echo "SENTINEL-DIGEST" ;; esac
exit $RC
"""

PROCEED = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "PROCEED",
    "num_turns": 1,
    "permission_denials": [],
}
REVISE = dict(PROCEED, result="REVISE - looks wrong")
STARVED = {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "", "num_turns": 5}


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path: Path, replies: list[dict], *, plant_digest: bool = False) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    replies_dir = tmp_path / "replies"
    replies_dir.mkdir()
    for i, reply in enumerate(replies, start=1):
        (replies_dir / f"{i}.json").write_text(json.dumps(reply), encoding="utf-8")
    _exe(bin_dir / "claude", _STUB.replace("__COUNTER__", str(tmp_path / "counter")).replace("__REPLIES__", str(replies_dir)))
    if plant_digest:
        _exe(bin_dir / "python3", _PY_SHIM.replace("__REAL__", sys.executable))
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "stub-token"
    env["CLAUDE_P_RETRY_BACKOFF_BASE"] = "0"
    return subprocess.run(["bash", str(SMOKE_SH)], env=env, capture_output=True, text=True, timeout=120)


def test_green_on_proceed(tmp_path: Path) -> None:
    result = _run(tmp_path, [PROCEED])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "SMOKE outcome=proceed"
    assert lines[1] == "[SUBAGENT-REVIEW] stats num_turns=1 permission_denials=0"
    assert len(lines) == 2
    assert result.stderr == ""


def test_green_on_revise(tmp_path: Path) -> None:
    result = _run(tmp_path, [REVISE])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "SMOKE outcome=revise"


def test_red_on_starved_and_prints_only_the_error_class(tmp_path: Path) -> None:
    result = _run(tmp_path, [STARVED, STARVED])
    assert result.returncode != 0
    assert "SMOKE outcome=starved" in result.stdout
    assert "SMOKE transcript subtype=error_max_turns" in result.stdout
    assert "[SUBAGENT-REVIEW] STARVED" in result.stdout


def test_red_on_sentinel_in_first_transcript(tmp_path: Path) -> None:
    result = _run(tmp_path, [dict(PROCEED, note="SENTINEL-LEAK")])
    assert result.returncode != 0
    assert "SMOKE sentinel leaked into review.json" in result.stdout
    assert "SENTINEL-LEAK" not in result.stdout


def test_red_on_sentinel_in_retry_transcript(tmp_path: Path) -> None:
    result = _run(tmp_path, [STARVED, dict(PROCEED, note="SENTINEL-LEAK")])
    assert result.returncode != 0
    assert "SMOKE sentinel leaked into review_retry.json" in result.stdout


def test_red_on_sentinel_in_digest(tmp_path: Path) -> None:
    result = _run(tmp_path, [PROCEED], plant_digest=True)
    assert result.returncode != 0
    assert "SMOKE sentinel leaked into plan_digest.txt" in result.stdout
    assert "SENTINEL-DIGEST" not in result.stdout


@pytest.mark.parametrize("reply", [PROCEED, REVISE])
def test_stdout_holds_only_smoke_and_stats_lines(tmp_path: Path, reply: dict) -> None:
    for line in _run(tmp_path, [reply]).stdout.splitlines():
        assert line.startswith(("SMOKE ", "[SUBAGENT-REVIEW] "))
