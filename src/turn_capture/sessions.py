"""Pure builders for telemetry_sessions and telemetry_agents rows (root and every sub-agent session).

Session rows are the generation marker (Decision 207 R3): the open row is emitted once a closed turn holding an
assistant record exists (never earlier, so its content cannot depend on when a finalize happened); resume and
compact rows wait for their stream's open row; the root stream also gets one close row per finalization (rules
C-1..C-4). Every agents column comes from TRANSCRIPT RECORDS only; a sub-agent's meta.json is never read for row
content.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.turn_capture.provenance import Facts, harness_session_key, open_provenance, resume_provenance
from src.turn_capture.refs import role_ref, session_marker_ref
from src.turn_capture.scrub import scrub_text
from src.turn_capture.streams import (
    ROOT,
    ParsedTree,
    Rec,
    Spawn,
    Stream,
    Turn,
    emitting_streams,
    is_api_error,
    notice_status,
)
from src.turn_capture.transcripts import Ctx, text_of
from src.turn_capture.witness import interrupted

AGENT_TYPE = "claude-code-subagent"
WORKFLOWS = ("orient", "plan", "implement", "develop-executor", "audit", "overseer")
WORKFLOW_SKILLS = {
    "orient": "orient",
    "planning": "plan",
    "implement": "implement",
    "overseer": "overseer",
    "audit-prompt": "audit",
    "plan-critique": "plan-critique",
    "decision-scout": "decision-scout",
    "code-review": "code-review",
    "executor-rca": "executor-rca",
}
SESSION_COLUMNS = (
    "event_kind",
    "event_timestamp",
    "session_started_at",
    "source_ordinal",
    "external_ref",
    "entity_ref",
    "parent_session_ref",
    "producer",
    "producer_version",
    "parser_version",
    "workflow",
    "outcome",
    "execution_attempt",
    "branch",
    "rec_ids",
    "plan_slug",
    "failure_reason",
    "failure_phase",
    "files_changed",
    "lines_added",
    "lines_removed",
    "steps_total",
    "scope_drift_files",
    "pr_url",
    "ci_outcome",
    "model_primary",
    "coverage_before",
    "coverage_after",
    "harness_session_key",
    "checkout_sha",
    "checkout_base_sha",
    "checkout_dirty",
)
AGENT_COLUMNS = (
    "event_kind",
    "event_timestamp",
    "session_started_at",
    "source_ordinal",
    "external_ref",
    "entity_ref",
    "session_ref",
    "observation_ref",
    "producer",
    "producer_version",
    "parser_version",
    "agent_type",
    "agent_name",
    "model",
    "provider",
    "version",
    "trigger",
    "outcome",
    "findings_count",
    "recs_created",
    "queue_entries_written",
    "error",
    "lambda_request_id",
    "workflow_run_id",
)
_ERROR_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
_COMMAND_NAME = re.compile(r"<command-name>/?([A-Za-z][A-Za-z0-9_-]*)</command-name>")


def _first_model(stream: Stream, bound: int | None = None) -> str | None:
    for rec in stream.recs:
        if rec.kind == "assistant" and not is_api_error(rec.data) and (bound is None or rec.line <= bound):
            model = rec.data["message"].get("model")
            return model if isinstance(model, str) else None
    return None


def _skill_workflow(stream: Stream, bound: int) -> str | None:
    for use in stream.tool_uses.values():
        if use.rec.line > bound:
            continue
        spec = use.block_body.get("input")
        skill = spec.get("skill") if isinstance(spec, dict) else None
        if use.block_body.get("name") == "Skill" and isinstance(skill, str):
            name = WORKFLOW_SKILLS.get(skill.lower().removeprefix("/"))
            if name is not None:
                return name
    return None


def _workflow(stream: Stream, bound: int, root: bool, diag: Counter[str]) -> str:
    """Rules WA-1..WA-3: the root's first-prompt command tag, else the first workflow Skill call within the open bound."""
    first = next((r for r in stream.recs if r.kind == "prompt"), None) if root else None
    match = _COMMAND_NAME.search(text_of(first.data["message"]["content"])) if first is not None else None
    name = match.group(1).lower() if match else ""
    if name in WORKFLOWS:
        return name
    skilled = _skill_workflow(stream, bound)
    if skilled is not None:
        diag["workflow_from_skill"] += 1
        return skilled
    return "claude_code" if root else "subagent"


def _open_bound(stream: Stream) -> int | None:
    """Rule WT-9: the first closing event of the first closed assistant-bearing turn, else that turn's last record line."""
    for turn in stream.turns:
        if turn.closed and turn.last_assistant is not None:
            return turn.closes[0].line if turn.closes else max(r.line for r in turn.recs)
    return None


def stream_due(stream: Stream) -> bool:
    """Rule D-1: a stream's rows exist only once its open row is due."""
    return _open_bound(stream) is not None


def _close_outcome(turn: Turn, diag: Counter[str]) -> dict[str, str]:
    """Rule C-3: cancelled when the last turn was interrupted, failed on an API-error record, else success."""
    if interrupted(turn):
        return {"outcome": "cancelled"}
    last = turn.last_assistant
    if last is not None and is_api_error(last.data):
        kind = last.data.get("error")
        reason = {"failure_reason": kind} if isinstance(kind, str) and _ERROR_KIND.fullmatch(kind) else {}
        if kind is not None and not reason:
            diag["failure_reason_dropped"] += 1
        return {"outcome": "failed", **reason}
    return {"outcome": "success"}


def _branch(stream: Stream, bound: int | None) -> str | None:
    for rec in stream.recs:
        branch = rec.data.get("gitBranch")
        if (bound is None or rec.line <= bound) and isinstance(branch, str) and branch:
            return branch
    return None


def _row(
    ctx: Ctx, rec: Rec | None, stream: Stream, ext: str, kind: str, when: Any, ordinal: int, parent: str | None, **cols: Any
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
    row.update(
        event_kind=kind,
        event_timestamp=when,
        session_started_at=ctx.started,
        source_ordinal=ordinal,
        external_ref=ext,
        entity_ref=stream.session_ref,
        parent_session_ref=parent,
        producer=ctx.producer,
        producer_version=ctx.producer_version,
        parser_version=ctx.parser_version,
    )
    row.update(cols)
    return {k: v for k, v in row.items() if v is not None}


def _provenance(facts: Facts) -> dict[str, Any]:
    return dict(zip(("checkout_sha", "checkout_base_sha", "checkout_dirty"), facts, strict=True))


def _segment_closes(ctx: Ctx, stream: Stream, diag: Counter[str]) -> list[dict[str, Any]]:
    """Rules SC-1/SC-2: one close per root resume boundary for the segment before it, derived from the tree."""
    rows: list[dict[str, Any]] = []
    start = -1
    for boundary in stream.resumes:
        effective = stream.resume_at[boundary.line]
        turns = [t for t in stream.turns if start < t.first.line < effective]
        anchor = next(
            (r for r in reversed(stream.recs) if r.line < effective and r.data.get("uuid") and r.ts is not None), None
        )
        if anchor is None or not any(t.last_assistant is not None for t in turns):
            diag["segment_close_skipped"] += 1
        else:
            ext = role_ref(anchor.data["uuid"], 0, "close")
            rows.append(
                _row(ctx, anchor, stream, ext, "close", anchor.ts, anchor.line, None, **_close_outcome(turns[-1], diag))
            )
            if boundary.ts is not None and anchor.ts is not None and boundary.ts < anchor.ts:
                diag["resume_before_close"] += 1
        start = effective - 1
    return rows


def build_sessions(parsed: ParsedTree, ctx: Ctx, diag: Counter[str] | None = None) -> list[dict[str, Any]]:
    diag = Counter() if diag is None else diag
    parents = {sp.agent_id: parsed.streams[sp.parent].session_ref for sp in parsed.spawns}
    rows: list[dict[str, Any]] = []
    for stream in emitting_streams(parsed):
        bound = _open_bound(stream)
        if bound is None:
            continue
        pin = next((r for r in stream.recs if r.ts is not None), None)
        child = stream.key in parents
        root = stream.key == ROOT
        rows.append(
            _row(
                ctx,
                None,
                stream,
                session_marker_ref(stream.session_ref),
                "open",
                ctx.started,
                0 if child or pin is None else pin.line,
                parents.get(stream.key),
                workflow=_workflow(stream, bound, root, diag),
                execution_attempt=1,
                branch=_branch(stream, bound),
                model_primary=_first_model(stream, bound),
                harness_session_key=harness_session_key(stream, bound, diag),
                **_provenance(open_provenance(stream, root, diag)),
            )
        )
        for n, rec in enumerate(stream.resumes):
            if rec.line not in stream.resume_ends:
                continue
            rows.append(
                _row(
                    ctx,
                    rec,
                    stream,
                    role_ref(rec.uuid, 0, "resume"),
                    "resume",
                    rec.ts,
                    rec.line,
                    parents.get(stream.key),
                    execution_attempt=n + 2,
                    **_provenance(resume_provenance(stream, rec, root, diag)),
                )
            )
        for rec in stream.compacts:
            rows.append(
                _row(ctx, rec, stream, role_ref(rec.uuid, 0, "compact"), "compact", rec.ts, rec.line, parents.get(stream.key))
            )
        if root:
            rows += _segment_closes(ctx, stream, diag)
        if parsed.final and root:
            anchor = next((r for r in reversed(stream.recs) if r.data.get("uuid") and r.ts is not None), None)
            if anchor is not None:
                ext = role_ref(anchor.data["uuid"], 0, "close")
                rows.append(
                    _row(
                        ctx,
                        anchor,
                        stream,
                        ext,
                        "close",
                        anchor.ts,
                        anchor.line,
                        None,
                        **_close_outcome(stream.turns[-1], diag),
                    )
                )
    return rows


def _agent_row(ctx: Ctx, spawn: Spawn, child: Stream, ext: str, kind: str, rec: Rec, **cols: Any) -> dict[str, Any]:
    use = spawn.tool_use
    spec = use.block_body.get("input")
    spec = spec if isinstance(spec, dict) else {}
    name = spec.get("subagent_type")
    version = use.rec.data.get("version")
    row: dict[str, Any] = dict.fromkeys(AGENT_COLUMNS)
    row.update(
        event_kind=kind,
        event_timestamp=rec.ts,
        session_started_at=ctx.started,
        source_ordinal=rec.line,
        external_ref=ext,
        entity_ref=use.tool_id,
        session_ref=child.session_ref,
        observation_ref=use.tool_id,
        producer=ctx.producer,
        producer_version=ctx.producer_version,
        parser_version=ctx.parser_version,
        agent_type=AGENT_TYPE,
        agent_name=name if isinstance(name, str) else None,
        model=_first_model(child),
        version=version if isinstance(version, str) else None,
        trigger="subagent_background" if spec.get("run_in_background") is True else "subagent_sync",
    )
    row.update(cols)
    return {k: v for k, v in row.items() if v is not None}


def _notice_close(notice: Rec, diag: Counter[str]) -> dict[str, str]:
    """Rule BN-2: a background run's close outcome follows its notice status."""
    status = notice_status(notice)
    if status == "completed":
        return {"outcome": "success"}
    if status == "timeout":
        return {"outcome": "timeout"}
    token = "absent" if status is None else status
    diag["notice_status_" + token if token in ("absent", "malformed") else "notice_status:" + token] += 1
    return {"outcome": "failed", "error": "notice_status:" + token}


def build_agents(parsed: ParsedTree, ctx: Ctx, diag: Counter[str] | None = None) -> list[dict[str, Any]]:
    diag = Counter() if diag is None else diag
    rows: list[dict[str, Any]] = []
    for spawn in parsed.spawns:
        child = parsed.streams.get(spawn.agent_id)
        if child is None or not (spawn.complete or parsed.final):
            continue
        use = spawn.tool_use
        rows.append(_agent_row(ctx, spawn, child, role_ref(use.rec.uuid, use.block, "agent_open"), "open", use.rec))
        if spawn.close_result is None:
            for notice in spawn.notices:
                ext = role_ref(notice.uuid, 0, "agent_close")
                rows.append(_agent_row(ctx, spawn, child, ext, "close", notice, **_notice_close(notice, diag)))
            continue
        res = spawn.close_result
        info = res.use_result
        failed = res.body.get("is_error") is True or (isinstance(info, dict) and info.get("interrupted") is True)
        error = None
        if res.body.get("is_error") is True:
            error = scrub_text(text_of(res.body.get("content"))).text[:2000]
        closing = res.rec
        ext = role_ref(closing.uuid, spawn.close_block, "agent_close")
        rows.append(
            _agent_row(ctx, spawn, child, ext, "close", closing, outcome="failed" if failed else "success", error=error)
        )
    return rows
