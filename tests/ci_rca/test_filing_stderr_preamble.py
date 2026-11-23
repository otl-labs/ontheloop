"""rec-5492: CI RCA filing finds the FILED marker behind CLI 2.1.323's stderr trust warning."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "ci" / "claude_p_retry.sh"
WARNING = "Warning: workspace has not been trusted; project settings (.claude/settings.json) are ignored"

_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 1,
    "duration_api_ms": 1,
    "num_turns": 1,
    "result": "Filed.\nFILED: rec-5491 environment",
    "stop_reason": "end_turn",
    "session_id": "s",
    "total_cost_usd": 0,
    "usage": {},
    "modelUsage": {},
    "permission_denials": [],
    "terminal_reason": "completed",
    "uuid": "u",
    "result_index": 0,
}


def test_ci_rca_filing_finds_marker_behind_cli_stderr_warning(tmp_path: Path) -> None:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub.write_text(f"#!/usr/bin/env bash\necho '{WARNING}' >&2\nprintf '%s\\n' '{json.dumps(_ENVELOPE)}'\n", encoding="utf-8")
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    out = tmp_path / "ci-rca-output.json"
    run = subprocess.run(
        ["bash", str(WRAPPER), str(out), "--", "--output-format", "json"],
        env=env,
        input="prompt",
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0
    assert WARNING in run.stderr

    def filing(*args: str) -> str:
        r = subprocess.run([sys.executable, "-m", "scripts.ci_rca.filing", *args], capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 0
        return r.stdout.strip()

    assert filing("--all", str(out)) == "rec-5491,environment"
    assert filing(str(out)) == "rec-5491"
