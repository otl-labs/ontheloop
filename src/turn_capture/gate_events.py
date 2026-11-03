"""Gate, pre-commit and status process_events from structured markers in a Bash tool_call's full output (G-1..G-6, ST-2..ST-4).

Only markers markers.parse_markers recognises count, never free text; a marker carries identifiers only, so the row's
name is the only stored trace of the output.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from src.turn_capture.markers import GATE_MARKER_VERSION, STATUS_MARKER_VERSION, count_overlong_status_lines, parse_markers
from src.turn_capture.refs import gate_event_ref
from src.turn_capture.streams import Stream, Turn
from src.turn_capture.transcripts import Ctx, Sidecars, canonical_json, clean, persisted_output, text_of

GATE_COMMAND_TOKENS = ("scripts.validate", "scripts/validate.py", "pre-commit", "pre_commit", "git commit")
STATUS_COMMAND_TOKENS = ("-m scripts.", "scripts/")
GATE_TAIL_LINES = 200
_STATUS_SEVERITY = {"skipped": "warning", "failed": "error"}

RowFn = Callable[..., dict[str, Any]]


def _scanned_text(ctx: Ctx, result: Any, sidecars: Sidecars) -> str | None:
    persisted = persisted_output(result)
    if persisted is None:
        return clean(text_of(result.body.get("content")))
    out = sidecars.output(persisted.name, ctx.full_output_cap_bytes)
    return None if out is None else out.text


def _line_total(text: str) -> int:
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def build_gate_events(
    ctx: Ctx, stream: Stream, turn: Turn, tref: str, sidecars: Sidecars, diag: Counter[str], make_row: RowFn
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for use in turn.tool_uses:
        command = use.block_body.get("input", {}).get("command") if isinstance(use.block_body.get("input"), dict) else None
        if use.result is None or use.block_body.get("name") != "Bash" or not isinstance(command, str):
            continue
        gated = any(token in command for token in GATE_COMMAND_TOKENS)
        statused = any(token in command for token in STATUS_COMMAND_TOKENS)
        if not (gated or statused):
            continue
        text = _scanned_text(ctx, use.result, sidecars)
        if text is None:
            diag["gate_scan_unavailable"] += 1
            continue
        floor = _line_total(text) - GATE_TAIL_LINES
        if statused:
            tail = "\n".join(text.split("\n")[max(floor, 0) :])
            diag["status_source_too_long"] += count_overlong_status_lines(tail)
        seen: set[tuple[str, str]] = set()
        res = use.result
        for marker in parse_markers(text):
            key = (marker.source, marker.signature)
            if key in seen:
                continue
            if marker.source == "status":
                if not statused or marker.line < floor or marker.result not in _STATUS_SEVERITY:
                    continue
            elif not gated or (marker.source == "gate" and marker.line < floor):
                continue
            seen.add(key)
            ref = gate_event_ref(res.rec.uuid, res.block, len(seen))
            rows.append(
                make_row(
                    ctx,
                    stream,
                    res.rec,
                    ref,
                    ref,
                    "process_event",
                    "point",
                    use.tool_id,
                    name=f"{marker.source}:{marker.signature}",
                    severity=_STATUS_SEVERITY.get(marker.result or "", "error") if marker.source == "status" else "error",
                    metadata=_metadata(use.tool_id, marker.source),
                )
            )
    return rows


def _metadata(tool_use_id: str, source: str) -> str:
    version = STATUS_MARKER_VERSION if source == "status" else GATE_MARKER_VERSION
    return canonical_json({"marker_version": version, "tool_use_id": tool_use_id})
