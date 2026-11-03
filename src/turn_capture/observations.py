"""Pure builders for telemetry_observations rows (refs only, never ids).

Turn open/close, model_call (one per response, owned by its last record), tool_call open/close (a tool_use
with no in-span result gets ONE synthetic interrupted close anchored on the tool_use record itself) and one
process_event per hook run (twins collapse, rule HG-1). Tool errors are the tool_call close outcome, never process_events.
Rows of a turn are built only once the turn is closed; a row outside any turn is built at once. A tool_call close
carries output_capture and the raw output identity (output_bytes/sha256/lines); a model_call carries
reasoning_visibility and the provider-reported reasoning_tokens.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from src.turn_capture import witness
from src.turn_capture.gate_events import build_gate_events
from src.turn_capture.markers import SIGNATURE_PATTERN
from src.turn_capture.refs import model_call_ref, role_ref, turn_entity_ref
from src.turn_capture.scrub import scrub_text
from src.turn_capture.streams import (
    HARNESS_DENIAL_PREFIXES,
    HARNESS_INTERRUPT_SENTINELS,
    TOOL_DENIAL_OUTCOMES,
    ParsedTree,
    Rec,
    Result,
    Stream,
    ToolUse,
    Turn,
    emitting_streams,
)
from src.turn_capture.transcript import ROOT, hook_script
from src.turn_capture.transcripts import Ctx, Sidecars, canonical_json, line_count, persisted_output, text_of
from src.turn_capture.usage import usage_columns

OBSERVATION_COLUMNS = (
    "event_kind",
    "event_timestamp",
    "session_started_at",
    "source_ordinal",
    "external_ref",
    "entity_ref",
    "session_ref",
    "parent_observation_ref",
    "producer",
    "producer_version",
    "parser_version",
    "observation_type",
    "name",
    "sequence",
    "outcome",
    "severity",
    "model",
    "tokens_input",
    "tokens_output",
    "tokens_cache_read",
    "tokens_cache_creation",
    "cost_usd_reported",
    "attempt",
    "provider",
    "persona_backend",
    "billing_shape",
    "acceptance_passed",
    "exit_code",
    "time_lost_seconds",
    "output_capture",
    "output_bytes",
    "output_sha256",
    "output_lines",
    "reasoning_visibility",
    "reasoning_tokens",
    "usage_final",
    "usage_source",
    "requested_model",
    "reasoning_effort",
    "cache_creation_1h_tokens",
    "cache_creation_5m_tokens",
    "rec_id",
    "metadata",
)
_SIGNATURE = re.compile(SIGNATURE_PATTERN)
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def _row(
    ctx: Ctx, stream: Stream, rec: Rec, ext: str, ent: str, otype: str, kind: str, parent: str | None, **cols: Any
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(OBSERVATION_COLUMNS)
    row.update(
        event_kind=kind,
        event_timestamp=rec.ts,
        session_started_at=ctx.started,
        source_ordinal=rec.line,
        external_ref=ext,
        entity_ref=ent,
        session_ref=stream.session_ref,
        parent_observation_ref=parent,
        producer=ctx.producer,
        producer_version=ctx.producer_version,
        parser_version=ctx.parser_version,
        observation_type=otype,
    )
    row.update(cols)
    return {k: v for k, v in row.items() if v is not None}


def _meta(**fields: Any) -> str:
    return canonical_json(fields)


def _model(rec: Rec) -> str | None:
    model = rec.data["message"].get("model")
    return model if isinstance(model, str) else None


def outcome_of(res: Result, stream: Stream, diag: Counter[str] | None = None) -> str:
    """blocked, then interrupted, then error, else success (rule K-4)."""
    is_error = res.body.get("is_error") is True
    text = text_of(res.body.get("content"))
    kind = res.rec.data.get("toolDenialKind")
    denial = TOOL_DENIAL_OUTCOMES.get(kind) if isinstance(kind, str) else None
    if kind is not None and denial is None and diag is not None:
        diag["unknown_tool_denial_kind"] += 1
    hook_block = any(line < res.rec.line for line in stream.pre_tool_blocks.get(res.tool_use_id, []))
    if denial == "blocked" or hook_block or (is_error and text.startswith(HARNESS_DENIAL_PREFIXES)):
        return "blocked"
    info = res.use_result
    interrupted = isinstance(info, dict) and info.get("interrupted") is True
    if denial == "interrupted" or interrupted or (is_error and text in HARNESS_INTERRUPT_SENTINELS):
        return "interrupted"
    return "error" if is_error else "success"


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def _result_meta(res: Result) -> str:
    info = res.use_result if isinstance(res.use_result, dict) else {}
    persisted = persisted_output(res)
    decision = res.rec.data.get("permissionDecision")
    return _meta(
        tool_denial_kind=_identifier(res.rec.data.get("toolDenialKind")),
        permission_decision_source=_identifier(decision.get("source") if isinstance(decision, dict) else None),
        is_error=res.body.get("is_error") is True,
        interrupted=info.get("interrupted") if isinstance(info.get("interrupted"), bool) else None,
        persisted_output=persisted.name if persisted is not None else None,
        persisted_output_size=info.get("persistedOutputSize") if type(info.get("persistedOutputSize")) is int else None,
        agent_id=info.get("agentId") if isinstance(info.get("agentId"), str) else None,
        is_async=info.get("isAsync") if isinstance(info.get("isAsync"), bool) else None,
        status=info.get("status") if isinstance(info.get("status"), str) else None,
    )


def _identity(capture: str, data: bytes) -> dict[str, Any]:
    return {
        "output_capture": capture,
        "output_bytes": len(data),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "output_lines": line_count(data.count(b"\n"), len(data), data.endswith(b"\n")),
    }


def _output_cols(ctx: Ctx, res: Result, sidecars: Sidecars) -> dict[str, Any]:
    """output_capture and the RAW output identity: the sidecar bytes when persisted, else the model-visible text."""
    persisted = persisted_output(res)
    if persisted is None:
        return _identity("not_persisted", text_of(res.body.get("content")).encode("utf-8", "replace"))
    out = sidecars.output(persisted.name, ctx.full_output_cap_bytes)
    if out is None:
        return {"output_capture": "unavailable"}
    digest = out.digest
    return {
        "output_capture": "captured" if out.text is not None else "omitted_oversize",
        "output_bytes": digest.size,
        "output_sha256": digest.sha256,
        "output_lines": digest.lines,
    }


def _reasoning(resp: Any, tokens: int | None) -> str:
    """full > summarized > redacted > omitted > none; this producer never sees raw reasoning, so never full."""
    blocks = [b for r in resp.recs for b in r.data["message"]["content"] if isinstance(b, dict)]
    thinking = [b for b in blocks if b.get("type") == "thinking"]
    if any(isinstance(b.get("thinking"), str) and b["thinking"] != "" for b in thinking):
        return "summarized"
    if any(b.get("type") == "redacted_thinking" for b in blocks):
        return "redacted"
    return "omitted" if thinking or (tokens is not None and tokens > 0) else "none"


def _tool_rows(
    ctx: Ctx, stream: Stream, tref: str, use: ToolUse, sidecars: Sidecars, diag: Counter[str]
) -> list[dict[str, Any]]:
    rec, block = use.rec, use.block_body
    caller = block.get("caller")
    tid = use.tool_id
    rows = [
        _row(
            ctx,
            stream,
            rec,
            role_ref(rec.uuid, use.block, "tool_call_open"),
            tid,
            "tool_call",
            "open",
            tref,
            name=block.get("name") if isinstance(block.get("name"), str) else None,
            sequence=use.ordinal,
            model=_model(rec),
            metadata=_meta(
                caller_type=caller.get("type") if isinstance(caller, dict) and isinstance(caller.get("type"), str) else None,
                attribution_mcp_server=rec.data.get("attributionMcpServer")
                if isinstance(rec.data.get("attributionMcpServer"), str)
                else None,
            ),
        )
    ]
    name = block.get("name") if isinstance(block.get("name"), str) else None
    if use.result is not None:
        res = use.result
        rows.append(
            _row(
                ctx,
                stream,
                res.rec,
                role_ref(res.rec.uuid, res.block, "tool_call_close"),
                tid,
                "tool_call",
                "close",
                tref,
                name=name,
                sequence=use.ordinal,
                outcome=outcome_of(res, stream, diag),
                metadata=_result_meta(res),
                **_output_cols(ctx, res, sidecars),
            )
        )
    else:
        rows.append(
            _row(
                ctx,
                stream,
                rec,
                role_ref(rec.uuid, use.block, "tool_call_close"),
                tid,
                "tool_call",
                "close",
                tref,
                name=name,
                sequence=use.ordinal,
                outcome="interrupted",
                output_capture="no_result",
                metadata=_meta(synthetic=True),
            )
        )
    return rows


def _turn_open_row(ctx: Ctx, stream: Stream, turn: Turn, tref: str) -> dict[str, Any]:
    first = turn.first
    return _row(
        ctx,
        stream,
        first,
        role_ref(first.uuid, 0, "turn_open"),
        tref,
        "turn",
        "open",
        None,
        sequence=turn.ordinal,
        metadata=_meta(
            is_meta=first.data.get("isMeta") is True,
            prompt_source=first.data.get("promptSource") if isinstance(first.data.get("promptSource"), str) else None,
            turn_origin=first.data.get("turnOrigin") if isinstance(first.data.get("turnOrigin"), str) else None,
        ),
    )


def _boundary_anchor(turn: Turn) -> Rec | None:
    """Rule WT-5: the turn's last record carrying a uuid and a parseable timestamp."""
    return next((r for r in reversed(turn.recs) if isinstance(r.data.get("uuid"), str) and r.data["uuid"] and r.ts), None)


def _turn_close_rows(ctx: Ctx, stream: Stream, turn: Turn, tref: str) -> list[dict[str, Any]]:
    """Rules WT-3..WT-6: a close per closing event, plus a boundary close when no event ends the last response."""
    windows: list[tuple[Rec, int | None]] = [(event, event.line) for event in turn.closes]
    last = turn.last_assistant
    if last is not None and not any(event.line >= last.line for event in turn.closes):
        anchor = _boundary_anchor(turn)
        if anchor is not None:
            windows.append((anchor, None))
    rows = []
    for rec, bound in windows:
        responses, tools = witness.counts(turn, bound)
        rows.append(
            _row(
                ctx,
                stream,
                rec,
                role_ref(rec.uuid, 0, "turn_close"),
                tref,
                "turn",
                "close",
                None,
                sequence=turn.ordinal,
                outcome=witness.turn_outcome(turn, bound),
                metadata=_meta(response_count=responses, tool_call_count=tools),
            )
        )
    return rows


def _turn_rows(
    ctx: Ctx, stream: Stream, turn: Turn, diag: Counter[str], sidecars: Sidecars, spawn_usage: dict[str, Any] | None
) -> list[dict[str, Any]]:
    tref = turn_entity_ref(stream.session_ref, turn.prompt_id)
    rows = [_turn_open_row(ctx, stream, turn, tref), *_turn_close_rows(ctx, stream, turn, tref)]
    for resp in turn.responses:
        owner, message = resp.owner, resp.owner.data["message"]
        last_of_first = spawn_usage if turn is stream.turns[0] and resp is turn.responses[-1] else None
        cols = usage_columns(resp, last_of_first, diag, subagent=stream.key != ROOT)
        rows.append(
            _row(
                ctx,
                stream,
                owner,
                model_call_ref(resp.message_id),
                resp.message_id,
                "model_call",
                "point",
                tref,
                name="agent_turn",
                model=_model(owner),
                billing_shape=ctx.billing_shape,
                **cols,
                reasoning_visibility=_reasoning(resp, cols["reasoning_tokens"]),
                metadata=_meta(
                    request_id=owner.data.get("requestId") if isinstance(owner.data.get("requestId"), str) else None,
                    stop_reason=message.get("stop_reason") if isinstance(message.get("stop_reason"), str) else None,
                    block_count=sum(len(r.data["message"]["content"]) for r in resp.recs),
                ),
            )
        )
    for use in turn.tool_uses:
        rows += _tool_rows(ctx, stream, tref, use, sidecars, diag)
    for res in turn.orphan_results:
        rows.append(
            _row(
                ctx,
                stream,
                res.rec,
                role_ref(res.rec.uuid, res.block, "tool_call_close"),
                res.tool_use_id,
                "tool_call",
                "close",
                tref,
                outcome=outcome_of(res, stream, diag),
                metadata=_result_meta(res),
                **_output_cols(ctx, res, sidecars),
            )
        )
    return rows


def hook_name(att: dict[str, Any]) -> str:
    """Rules H-1..H-3: the script stem, else hookName with ':' as '.', else hook:unknown; each must fit the class."""
    stem, _ = hook_script(att.get("command"))
    if stem is not None and _SIGNATURE.fullmatch(stem):
        return "hook:" + stem
    hook = att.get("hookName")
    name = hook.replace(":", ".") if isinstance(hook, str) else ""
    return "hook:" + (name if _SIGNATURE.fullmatch(name) else "unknown")


def _hook_row(ctx: Ctx, stream: Stream, rec: Rec) -> dict[str, Any]:
    att = rec.data["attachment"]
    ext = role_ref(rec.uuid, 0, "process_event")
    code = att.get("exitCode")
    use_id = att.get("toolUseID") if isinstance(att.get("toolUseID"), str) else None
    if use_id in stream.tool_uses:
        parent = use_id
    else:
        parent = turn_entity_ref(stream.session_ref, rec.turn.prompt_id) if rec.turn is not None else None
    severity = "error" if code == 2 else "info" if code in (0, None) else "warning"
    command, stderr = att.get("command"), att.get("stderr")
    return _row(
        ctx,
        stream,
        rec,
        ext,
        ext,
        "process_event",
        "point",
        parent,
        name=hook_name(att),
        severity=severity,
        metadata=_meta(
            hook_event=att.get("hookEvent") if isinstance(att.get("hookEvent"), str) else None,
            hook_name=att.get("hookName") if isinstance(att.get("hookName"), str) else None,
            attachment_type=att.get("type"),
            exit_code=code if type(code) is int else None,
            duration_ms=att.get("durationMs") if type(att.get("durationMs")) is int else None,
            tool_use_id=use_id,
            command=scrub_text(command).text if isinstance(command, str) else None,
            stderr_head=scrub_text(stderr).text[:512] if isinstance(stderr, str) else None,
        ),
    )


def _sync_spawn_usage(parsed: ParsedTree) -> dict[str, dict[str, Any]]:
    """Rule US-3 inputs: each sync spawn's result usage by child stream key (background spawns never contribute)."""
    out: dict[str, dict[str, Any]] = {}
    for spawn in parsed.spawns:
        info = spawn.close_result.use_result if spawn.close_result is not None else None
        usage = info.get("usage") if isinstance(info, dict) else None
        if isinstance(usage, dict):
            out[spawn.agent_id] = usage
    return out


def build_observations(parsed: ParsedTree, ctx: Ctx, diag: Counter[str], sidecars: Sidecars) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    spawn_usage = _sync_spawn_usage(parsed)
    for stream in emitting_streams(parsed):
        for turn in stream.turns:
            tref = turn_entity_ref(stream.session_ref, turn.prompt_id)
            if turn.closed:
                rows += _turn_rows(ctx, stream, turn, diag, sidecars, spawn_usage.get(stream.key))
                rows += build_gate_events(ctx, stream, turn, tref, sidecars, diag, _row)
            else:
                rows.append(_turn_open_row(ctx, stream, turn, tref))
        rows += [_hook_row(ctx, stream, rec) for rec in stream.hooks if rec.turn is None or rec.turn.closed]
    return rows
