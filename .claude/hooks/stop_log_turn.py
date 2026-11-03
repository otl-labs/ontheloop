#!/usr/bin/env python3
"""Stop, StopFailure and UserPromptSubmit capture shim (rules HK-3 and WT-1).

Prints the witness line (rule WT-1) as its first action, on every path, then exits 0 at once on any error. For Stop and
StopFailure (stop_hook_active included) it starts the capture runner fully detached (own session, /dev/null descriptors)
when the switch and the guards say yes (rule SW-1); for UserPromptSubmit it starts it with the payload's prompt_id without
reading the switch (the runner's own check decides, before any state is touched). Never runs a pass in the hook process;
prints the witness line only. The switch is checked before any import beyond the stdlib and yaml. RUNNER_MODULE is a
constant: no environment variable redirects it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_MODULE = "scripts.telemetry_capture"
WITNESS_LINE = "[stop_log_turn] turn witness"
LOCK_WAIT_S = "120"
PROMPT_EVENT = "UserPromptSubmit"
_PROMPT_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def _hook_fields(payload: object) -> tuple[str, str, str] | None:
    if not isinstance(payload, dict):
        return None
    fields = (payload.get("hook_event_name"), payload.get("transcript_path"), payload.get("session_id"))
    if not all(isinstance(value, str) and value for value in fields):
        return None
    return fields  # type: ignore[return-value]


def _witness() -> None:
    try:
        sys.stderr.write(WITNESS_LINE + "\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001, S110 -- a closed stderr never changes the exit code
        pass


def main(
    stdin_text: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    detach: Callable[[Sequence[str], Mapping[str, str]], object] | None = None,
) -> int:
    _witness()
    try:
        payload = json.loads(sys.stdin.read() if stdin_text is None else stdin_text)
        fields = _hook_fields(payload)
        if fields is None:
            return 0
        event, transcript, session_id = fields
        args = ["--event", event, "--transcript", transcript, "--session-id", session_id, "--lock-wait", LOCK_WAIT_S]
        environ = os.environ if env is None else env
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        if event == PROMPT_EVENT:
            prompt_id = payload.get("prompt_id")
            if not (isinstance(prompt_id, str) and _PROMPT_ID.fullmatch(prompt_id)):
                return 0
            args += ["--prompt-id", prompt_id]
        else:
            from scripts.telemetry_capture import switch  # noqa: PLC0415

            if not switch.capture_allowed(environ, repo_root):
                return 0
        if detach is not None:
            detach(args, environ)
        else:
            from scripts.telemetry_capture import detach_runner  # noqa: PLC0415

            detach_runner(RUNNER_MODULE, args, repo_root, env=None if env is None else env)
    except Exception:  # noqa: BLE001, S110 -- the hook exits 0 silently on every path
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
