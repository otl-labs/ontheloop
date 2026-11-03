"""rec-3875: python -m scripts.ops_portal.cli runs the CLI instead of exiting 0 silently."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cli_module_runs_under_python_m() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.ops_portal.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0
    assert "usage" in proc.stdout.lower()
