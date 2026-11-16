"""Hook process_events for tool calls a blocking hook denied in a cloud session (rules HD-1..HD-4, rec-4959).

Claude Code 2.1.322 on the web writes no hook attachment for a blocking PreToolUse hook; the block survives only in the
denied tool_result. HD-1: one error process_event under each tool call whose close is blocked and whose result record
carries permissionDecision.source == 'hook'. HD-2: the name is hook:<stem> from the last HOOK-BLOCK v1 line (rule HB-1 of
the four blocking hooks), else the first legacy 'BLOCKED by .claude/hooks/<stem>.py: ' text, else hook:<event>.<tool>,
else hook:unknown; the result text is never stored. HD-3: no row when an exit-2 hook record already preceded the result
(_hook_row names that block). HD-4: reads only the result record, its tool_use and stream.pre_tool_blocks (R7).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from src.turn_capture.markers import SIGNATURE_PATTERN, hook_block_stems
from src.turn_capture.refs import role_ref
from src.turn_capture.streams import Stream, Turn
from src.turn_capture.transcripts import Ctx, canonical_json, clean, text_of

LEGACY_BLOCK_RE = re.compile(rf"BLOCKED by \.claude/hooks/({SIGNATURE_PATTERN})\.py: ")
_NAME_RE = re.compile(f"hook:{SIGNATURE_PATTERN}")

RowFn = Callable[..., dict[str, Any]]


def _name(text: str, decision: dict[str, Any], tool: Any, diag: Counter[str]) -> tuple[str, int | None]:
    stems = hook_block_stems(text)
    if stems:
        diag["hook_denial_marker"] += 1
        if len(set(stems)) > 1:
            diag["hook_denial_markers_disagree"] += 1
        return "hook:" + stems[-1], 1
    legacy = LEGACY_BLOCK_RE.search(text)
    if legacy:
        diag["hook_denial_legacy"] += 1
        return "hook:" + legacy.group(1), None
    code = decision.get("reasonCode")
    event = code[len("hook:") :] if isinstance(code, str) and code.startswith("hook:") else None
    name = f"hook:{event}.{tool}"
    if event and isinstance(tool, str) and _NAME_RE.fullmatch(name):
        diag["hook_denial_fallback"] += 1
        return name, None
    diag["hook_denial_unknown"] += 1
    return "hook:unknown", None


def build_hook_denials(ctx: Ctx, stream: Stream, turn: Turn, diag: Counter[str], make_row: RowFn) -> list[dict[str, Any]]:
    from src.turn_capture.observations import outcome_of  # noqa: PLC0415 - observations imports this module

    rows: list[dict[str, Any]] = []
    for use in turn.tool_uses:
        res = use.result
        if res is None:
            continue
        decision = res.rec.data.get("permissionDecision")
        if not isinstance(decision, dict) or decision.get("source") != "hook":
            continue
        if outcome_of(res, stream) != "blocked":
            continue
        if any(line < res.rec.line for line in stream.pre_tool_blocks.get(use.tool_id, [])):
            continue
        name, marker_version = _name(clean(text_of(res.body.get("content"))), decision, use.block_body.get("name"), diag)
        code = decision.get("reasonCode")
        event = code[len("hook:") :] if isinstance(code, str) and code.startswith("hook:") else None
        ref = role_ref(res.rec.uuid, res.block, "process_event")
        meta = canonical_json({"hook_event": event, "marker_version": marker_version, "tool_use_id": use.tool_id})
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
                name=name,
                severity="error",
                metadata=meta,
            )
        )
    return rows
