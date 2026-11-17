#!/usr/bin/env python3
"""PreToolUse hook: refuse a session command that calls the ops writer directly -- a tripwire, never a control.

Exit 2 with the portal pointer on a match; every other path (other tools, malformed input, lexer errors, any exception,
a failed import) exits 0 with no output. It never prints to stdout and never returns a permission decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

_TOOLS = ("Bash", "Monitor")


def main() -> int:
    try:
        from scripts.session.rec_writer_match import classify

        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") not in _TOOLS:
            return 0
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return 0
        hit = classify(command)
    except Exception:
        return 0
    if hit is None:
        return 0
    sys.stderr.write(
        f"rec-writer-tripwire: refused. This command calls the ops writer directly ({hit.shape}: {hit.token}).\n"
        "Recs and decisions are written only through the portal: bin/venv-python -m scripts.ops_data_portal "
        "(file_rec, update_rec, sync), which runs the checks the writer does not.\n"
        "No approval clears this refusal. If the portal lacks what you need, file a rec; if this match is wrong, "
        "tell the operator and file a rec naming the command.\n"
        "This hook is a tripwire, not a control (docs/contracts/ducklake_writer.yaml audit_invariants).\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
