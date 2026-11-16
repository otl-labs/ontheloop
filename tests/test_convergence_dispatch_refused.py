"""dispatch-refused input of the write-convergence-record action (Decision 205 cl.1)."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import yaml

ACTION_DIR = Path(".github/actions/write-convergence-record")
ACTION = ACTION_DIR / "action.yml"
BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")
AWS_STUB = """#!/usr/bin/env bash
store="$STUB_DIR/object"
if [ "$2" = "cp" ] || [ "$1" = "s3" ]; then
  if [ "$3" = "-" ]; then cat > "$store"; echo "WRITE" >> "$STUB_DIR/calls"; exit 0; fi
  if [ "$4" = "-" ]; then cat "$store" 2>/dev/null; exit 0; fi
fi
exit 0
"""


def _steps() -> list[dict[str, Any]]:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]


def test_input_declared_with_false_default() -> None:
    inputs = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]
    assert inputs["dispatch-refused"]["default"] == "false"
    assert "refused" in inputs


def test_writing_step_is_if_gated_on_not_true() -> None:
    assert _steps()[0]["if"] == "inputs.dispatch-refused != 'true'"


def test_dispatch_refused_step_is_gated_and_touches_no_aws() -> None:
    step = _steps()[1]
    assert step["id"] == "dispatch_refused"
    assert step["if"] == "inputs.dispatch-refused == 'true'"
    assert step["shell"] == "bash"
    assert "aws" not in step["run"]
    assert "Dispatch refused by the persona dispatch gate (Decision 205 cl.1)" in step["run"]


def _drive(tmp_path: Path, apply_outcome: str, **extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    body = _steps()[0]["run"]
    script = tmp_path / "body.sh"
    script.write_text(body, encoding="utf-8")
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    aws = stub_dir / "aws"
    aws.write_text(AWS_STUB, encoding="utf-8")
    aws.chmod(aws.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(stub_dir),
        "GITHUB_ACTION_PATH": str(ACTION_DIR.resolve()),
        "REFUSED": "false",
        "STILL_ACTIONABLE": "true",
        "ROUTED": "false",
        "PRE_APPLY_FAILURE": "false",
        "FAILED_STEP": "",
        "REVIEW_OUTCOME": "",
        "REVIEW_STEP_OUTCOME": "",
        "APPLY_OUTCOME": apply_outcome,
        "SHA": "abc",
        "PLAN_SHA_MODE": "event-gated",
        "WORKDIR": str(tmp_path),
        "CONVERGENCE_BUCKET": "b",
        "CONVERGENCE_KEY": "k",
        "RUN_ID": "1",
        "SERVER_URL": "https://example.invalid",
        "REPO": "o/r",
        "EVENT_NAME": "workflow_dispatch",
    }
    env.update(extra)
    res = subprocess.run([*BASH, str(script)], env=env, capture_output=True, text=True, check=False)
    obj = stub_dir / "object"
    return res, (json.loads(obj.read_text()) if obj.exists() else {})


def test_dispatch_refused_false_keeps_red_latch_on_skipped_apply(tmp_path: Path) -> None:
    res, record = _drive(tmp_path, "skipped")
    assert res.returncode == 0, res.stderr
    assert record["status"] == "red"


def test_dispatch_refused_false_green_on_success(tmp_path: Path) -> None:
    res, record = _drive(tmp_path, "success")
    assert res.returncode == 0, res.stderr
    assert record["status"] == "green"


def test_body_zero_does_not_mention_dispatch_refused() -> None:
    assert not re.search(r"dispatch.refused|DISPATCH_REFUSED", _steps()[0]["run"], re.I)
