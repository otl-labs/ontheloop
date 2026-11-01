"""The one emitter helper for repo-CLI outcome lines (the status grammar in src/turn_capture/markers.py).

A recording CLI mode calls emit_status(source, result, reason) so the turn-capture parser records a skip or a failure
as a row instead of leaving it as transcript text only. The line goes to stderr so a mode's stdout contract is unchanged.
"""

from __future__ import annotations

import sys


def emit_status(source: str, result: str, reason: str | None = None) -> None:
    """Print one whole status line to stderr; never raises, so a bad argument cannot change a mode's exit code."""
    try:
        from src.turn_capture.markers import format_status_marker  # noqa: PLC0415

        line = format_status_marker(source, result, reason)
    except Exception as exc:
        print(f"status line not emitted for {source!r}: {exc}", file=sys.stderr, flush=True)
        return
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        return
