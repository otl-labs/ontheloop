#!/usr/bin/env python3
"""SessionEnd capture hook (PLAN-telemetry-capture-hooks rule HK-4).

The same parse, switch and guards as the Stop shim, then one final pass (session_final true) in process under a single
deadline measured from hook start (hook start + 50 s; the registration's timeout is 60 s). The tree's lock is tried for up
to 5 s inside that deadline; when it is busy, or when less than 20 s remain after record_turn, a final pass is detached
instead (event SessionEnd, --final, its own lock wait up to 600 s). Exit 0 always, nothing printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_MODULE = "scripts.telemetry_capture"
DEADLINE_S = 50.0
LOCK_WAIT_S = 5.0
DETACH_BELOW_S = 20.0
FINAL_LOCK_WAIT_S = 600.0


def _hook_fields(stdin_text: str) -> tuple[str, str] | None:
    payload = json.loads(stdin_text)
    if not isinstance(payload, dict):
        return None
    fields = (payload.get("transcript_path"), payload.get("session_id"))
    if not all(isinstance(value, str) and value for value in fields):
        return None
    return fields  # type: ignore[return-value]


def main(
    stdin_text: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    deadline_s: float = DEADLINE_S,
    lock_wait_s: float = LOCK_WAIT_S,
    detach_below_s: float = DETACH_BELOW_S,
    client_factory: Callable[..., Any] | None = None,
    detach: Callable[[Sequence[str], Mapping[str, str]], object] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    start = clock()
    try:
        fields = _hook_fields(sys.stdin.read() if stdin_text is None else stdin_text)
        if fields is None:
            return 0
        environ = os.environ if env is None else env
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from scripts.telemetry_capture import switch  # noqa: PLC0415

        if not switch.capture_allowed(environ, repo_root):
            return 0
        from scripts.telemetry_capture import detach_runner  # noqa: PLC0415
        from scripts.telemetry_capture.runner import default_client_factory, run_pass  # noqa: PLC0415
        from scripts.telemetry_capture.transport import Budget  # noqa: PLC0415

        transcript, session_id = fields
        result = run_pass(
            Path(transcript),
            session_id,
            "SessionEnd",
            final=True,
            budget=Budget(start + deadline_s, clock),
            env=environ,
            repo_root=repo_root,
            client_factory=client_factory or default_client_factory,
            lock_wait=lock_wait_s,
            detach_below=detach_below_s,
        )
        if result.outcome in ("busy", "short_budget"):
            args = ["--event", "SessionEnd", "--transcript", transcript, "--session-id", session_id]
            args += ["--final", "--lock-wait", str(FINAL_LOCK_WAIT_S)]
            if detach is not None:
                detach(args, environ)
            else:
                detach_runner(RUNNER_MODULE, args, repo_root, env=None if env is None else env)
    except Exception:  # noqa: BLE001, S110 -- the hook exits 0 silently on every path
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
