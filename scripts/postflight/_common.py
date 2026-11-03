"""Shared primitives for the scripts/postflight package.

SOLE source of shared constants, the subprocess/git helpers, and the find_plan_file /
clear_checkpoint re-imports used by every session_postflight domain module and by the facade
itself. Every body -- facade-resident and moved -- resolves these as _common.<name> at call time
so one patch target (scripts.postflight._common.<name>) intercepts everywhere. No dependency on
scripts.session.postflight (no import cycle).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = sys.executable
_SSO_PROFILE = "agent_platform"
MAX_COMMIT_RETRIES = 3
CI_POLL_INTERVAL_SECONDS = 30
CI_POLL_TIMEOUT_SECONDS = 600  # 10 minutes (CI takes ~4min; allow for startup + buffer)
DEFAULT_MAX_AGE_DAYS = 90
LOGS_DIR = ROOT / "logs"
ARCHIVE_DIR = LOGS_DIR / "archive"
_PRUNE_SKIP_NAMES = frozenset(
    {
        "transcripts",
        "debug",
        "archive",
    }
)

logger = logging.getLogger(__name__)

from scripts.execution_state import clear_checkpoint  # noqa: E402, F401
from scripts.roadmap.find_plan import find_plan_file  # noqa: E402, F401


def _run(cmd: list[str], cwd: Path | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or ROOT,
    )


def _current_branch() -> str:
    result = _run(["git", "branch", "--show-current"])
    return result.stdout.strip() if result.returncode == 0 else "unknown"
