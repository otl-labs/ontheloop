"""The hook-side producer runtime for Claude Code turn capture: switch, state, transport, runner, operator CLI and the c8
smoke (PLAN-telemetry-capture-hooks).

Lives under scripts/ because src/turn_capture (stdlib only) and src/telemetry_client (stdlib plus src.telemetry) are fenced
against network and botocore imports. This module stays stdlib only: the hook shims import it before the switch says yes.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def detach_runner(
    module: str, args: Sequence[str], repo_root: Path, env: Mapping[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """Start `python -m <module> run <args>` fully detached: own session, no inherited pipe, nothing waited on."""
    return subprocess.Popen(
        [sys.executable, "-m", module, "run", *args],
        cwd=repo_root,
        env=None if env is None else dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
