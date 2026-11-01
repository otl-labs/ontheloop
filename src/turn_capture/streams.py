"""Record classification, stream membership, turn spans and closure, response grouping, sub-agent completeness.

The result is an immutable-in-practice parsed tree the row builders consume. Pure: no writes, no network.
A turn spans from its first prompt-record to the next TURN BOUNDARY (a prompt-record with a different
promptId, or the first attachment of a SessionStart:resume hook group); it is CLOSED iff a later boundary
exists or the stream's eof closes it (session_final, or a sub-agent's completion evidence).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.turn_capture.hook_groups import RESUME_HOOK, TURNLESS_HOOKS, is_late_session_start, late_resume_start, twin_key
from src.turn_capture.refs import root_session_ref, subagent_session_ref
from src.turn_capture.transcript import ROOT, TranscriptTree, inherited_lines, read_lines, record_time
from src.turn_capture.witness import API_ERROR_FLAG_KEYS, closing_events, frontier, is_api_error, is_witness  # noqa: F401

AGENT_TOOL_NAMES = ("Agent", "Task")
IGNORED_RECORD_TYPES = ("ai-title", "last-prompt", "atis-latch", "mode", "queue-operation", "cost-state", "system")
KNOWN_ATTACHMENT_TYPES = (
    "environment",
    "model",
    "deferred_tools_delta",
    "agent_listing_delta",
    "mcp_instructions_delta",
    "skill_listing",
    "auto_mode",
    "instructions",
    "session_context",
    "context_sections",
    "date",
    "credential_org",
    "remote_session_change",
    "prompt_snapshot",
    "total_tokens_reminder",
    "relevant_memories",
    "command_permissions",
    "deferred_tools_record",
    "nested_memory",
    "task_reminder",
    "queued_command",
    "critical_system_reminder",
    "read_truncation_notice",
)
WITNESS_COUNTERS = ("witness_not_at_rest", "witness_outside_turn")
COMPACT_HOOKS = ("SessionStart:compact", "PreCompact", "PostCompact")
BACKGROUND_COMPLETION_TAG = "<task-notification>"
_NOTICE_STATUS = re.compile(r"<status>(.*?)</status>", re.DOTALL)
_STATUS_TOKEN = re.compile(r"[a-z_]{1,32}")
HARNESS_INTERRUPT_SENTINELS = ("[Request interrupted by user]", "[Request interrupted by user for tool use]")
HARNESS_DENIAL_PREFIXES: tuple[str, ...] = ()
TOOL_DENIAL_OUTCOMES = {"permission-rule": "blocked", "user-rejected": "interrupted"}


@dataclass
class Rec:
    file: str
    line: int
    data: dict[str, Any]
    ts: datetime | None
    kind: str
    turn: Turn | None = None

    @property
    def uuid(self) -> str:
        return self.data["uuid"]


@dataclass
class Result:
    rec: Rec
    block: int
    tool_use_id: str
    body: dict[str, Any]
    disposition: str = "closed"

    @property
    def use_result(self) -> Any:
        return self.rec.data.get("toolUseResult")


@dataclass
class ToolUse:
    rec: Rec
    block: int
    block_body: dict[str, Any]
    ordinal: int
    turn: Turn | None
    result: Result | None = None
    result_any: Result | None = None

    @property
    def tool_id(self) -> str:
        return self.block_body["id"]


@dataclass
class Response:
    message_id: str
    recs: list[Rec]
    turn: Turn

    @property
    def owner(self) -> Rec:
        return self.recs[-1]


@dataclass
class Turn:
    stream: str
    prompt_id: str
    ordinal: int
    first: Rec
    closed: bool = False
    bounded: bool = False
    closes: list[Rec] = field(default_factory=list)
    prompts: list[Rec] = field(default_factory=list)
    companions: list[Rec] = field(default_factory=list)
    recs: list[Rec] = field(default_factory=list)
    last_assistant: Rec | None = None
    tool_uses: list[ToolUse] = field(default_factory=list)
    orphan_results: list[Result] = field(default_factory=list)
    responses: list[Response] = field(default_factory=list)


@dataclass
class Stream:
    key: str
    session_ref: str
    recs: list[Rec]
    turns: list[Turn] = field(default_factory=list)
    hooks: list[Rec] = field(default_factory=list)
    resumes: list[Rec] = field(default_factory=list)
    compacts: list[Rec] = field(default_factory=list)
    tool_uses: dict[str, ToolUse] = field(default_factory=dict)
    result_by_id: dict[str, Result] = field(default_factory=dict)
    pre_tool_blocks: dict[str, list[int]] = field(default_factory=dict)
    resume_ends: dict[int, int] = field(default_factory=dict)
    resume_at: dict[int, int] = field(default_factory=dict)


@dataclass
class Spawn:
    parent: str
    tool_use: ToolUse
    agent_id: str
    complete: bool
    close_rec: Rec | None
    close_block: int
    close_result: Result | None
    notices: list[Rec] = field(default_factory=list)
    completed_at_boundary: bool = False


@dataclass
class ParsedTree:
    session_id: str
    final: bool
    streams: dict[str, Stream]
    spawns: list[Spawn]
    lines_total: dict[str, int]
    diag: Counter[str]


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _content(data: dict[str, Any]) -> Any:
    message = data.get("message")
    return message.get("content") if isinstance(message, dict) else None


def has_tool_result_block(content: Any) -> bool:
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def classify(data: dict[str, Any], diag: Counter[str], inherited: bool = False) -> str:
    if inherited:
        diag["inherited_records"] += 1
        return "inherited"
    kind = data.get("type")
    if kind not in ("user", "assistant", "attachment"):
        if kind not in IGNORED_RECORD_TYPES:
            diag[f"unknown_record_type:{kind}"] += 1
        return "other"
    if not _nonempty(data.get("uuid")):
        diag["records_without_uuid"] += 1
        return "other"
    if record_time(data) is None:
        diag["records_without_timestamp"] += 1
        return "other"
    content = _content(data)
    if kind == "user":
        if has_tool_result_block(content):
            return "tool_result"
        if not isinstance(content, (str, list)):
            diag["unclassified_user_records"] += 1
            return "other"
        if data.get("sourceToolUseID") or data.get("turnCompanion"):
            return "companion"
        if not _nonempty(data.get("promptId")):
            diag["unclassified_user_records"] += 1
            return "other"
        return "prompt"
    if kind == "assistant":
        message = data.get("message")
        if isinstance(message, dict) and _nonempty(message.get("id")) and isinstance(content, list):
            if not is_api_error(data) and message.get("model") == "<synthetic>":
                diag["synthetic_assistant_records"] += 1
                return "other"
            return "assistant"
        diag["unclassified_assistant_records"] += 1
        return "other"
    attachment = data.get("attachment")
    a_type = attachment.get("type") if isinstance(attachment, dict) else None
    if isinstance(a_type, str) and a_type.startswith("hook_"):
        return "hook"
    if a_type not in KNOWN_ATTACHMENT_TYPES:
        diag[f"unknown_attachment_type:{a_type}"] += 1
    return "attach"


def block_base(data: dict[str, Any]) -> int:
    index = data.get("apiBlockIndex")
    return index if type(index) is int else 0


def _open_turn(stream: Stream, cur: Turn | None, rec: Rec, seen: set[str], diag: Counter[str]) -> tuple[Turn | None, str]:
    """Apply a prompt-record: extend the current turn, open a new one, or downgrade a reused promptId to a companion."""
    pid = rec.data["promptId"]
    if pid in seen and (cur is None or pid != cur.prompt_id):
        diag["reused_prompt_ids"] += 1
        return cur, "companion"
    if cur is None or pid != cur.prompt_id:
        if cur is not None:
            cur.closed = True
        cur = Turn(stream.key, pid, len(stream.turns) + 1, rec)
        stream.turns.append(cur)
        seen.add(pid)
    return cur, "prompt"


def _hook_effect(
    stream: Stream, rec: Rec, cur: Turn | None, groups: dict[str, bool], latest: datetime | None, diag: Counter[str]
) -> Turn | None:
    """Record resume/compact hook groups; an on-time resume group's first record is a turn boundary (rule HG-3/HG-4)."""
    name = str(rec.data["attachment"].get("hookName") or "")
    if name.startswith(RESUME_HOOK):
        if not groups["resume"]:
            stream.resumes.append(rec)
            groups["resume"] = True
            if late_resume_start(rec.ts, rec.data["attachment"].get("durationMs"), latest):
                diag["late_resume_group"] += 1
                stream.resume_at[rec.line] = cur.first.line if cur is not None else rec.line
                return cur
            stream.resume_at[rec.line] = rec.line
            if cur is not None:
                cur.closed = True
            return None
    elif name.startswith(COMPACT_HOOKS) and not groups["compact"]:
        stream.compacts.append(rec)
        groups["compact"] = True
    return cur


def _attach_to_turn(rec: Rec, cur: Turn | None) -> None:
    rec.turn = cur
    if cur is None:
        return
    cur.recs.append(rec)
    if rec.kind == "prompt":
        cur.prompts.append(rec)
    elif rec.kind == "companion":
        cur.companions.append(rec)
    elif rec.kind == "assistant":
        cur.last_assistant = rec


def _walk(stream: Stream, closing: bool, diag: Counter[str]) -> None:
    cur: Turn | None = None
    seen: set[str] = set()
    twins: set[tuple[Any, ...] | None] = set()
    latest: datetime | None = None
    prompted = False
    groups = {"resume": False, "compact": False}
    for rec in stream.recs:
        if rec.kind in ("prompt", "assistant"):
            if groups["resume"]:
                stream.resume_ends[stream.resumes[-1].line] = rec.line
            groups = {"resume": False, "compact": False}
            latest = latest if rec.ts is None else rec.ts if latest is None else max(latest, rec.ts)
        if rec.kind == "prompt":
            cur, rec.kind = _open_turn(stream, cur, rec, seen, diag)
            prompted = prompted or rec.kind == "prompt"
        elif rec.kind in ("hook", "hook_twin"):
            att = rec.data["attachment"]
            key = twin_key(att)
            if rec.kind == "hook_twin" or (key is not None and key in twins):
                rec.kind = "hook_twin"
                diag["hook_twin_records"] += 1
                _attach_to_turn(rec, None)
                continue
            twins.add(key)
            cur = _hook_effect(stream, rec, cur, groups, latest, diag)
            stream.hooks.append(rec)
            if str(att.get("hookName") or "").startswith(TURNLESS_HOOKS):
                diag["late_session_start_records"] += 1 if is_late_session_start(str(att["hookName"]), prompted) else 0
                _attach_to_turn(rec, None)
                continue
        _attach_to_turn(rec, cur)
    if cur is not None and closing:
        cur.closed = True


def _register_uses(stream: Stream, rec: Rec, diag: Counter[str]) -> None:
    for i, block in enumerate(rec.data["message"]["content"]):
        if not (isinstance(block, dict) and block.get("type") == "tool_use" and _nonempty(block.get("id"))):
            continue
        if block["id"] in stream.tool_uses:
            diag["duplicate_tool_uses"] += 1
            continue
        turn = rec.turn
        ordinal = (len(turn.tool_uses) + 1) if turn is not None else 0
        tool_use = ToolUse(rec, block_base(rec.data) + i, block, ordinal, turn)
        stream.tool_uses[block["id"]] = tool_use
        if turn is not None:
            turn.tool_uses.append(tool_use)


def _note_pre_tool_block(stream: Stream, rec: Rec) -> None:
    att = rec.data["attachment"]
    use_id = att.get("toolUseID")
    if str(att.get("hookName") or "").startswith("PreToolUse") and att.get("exitCode") == 2 and isinstance(use_id, str):
        stream.pre_tool_blocks.setdefault(use_id, []).append(rec.line)


def _place_result(stream: Stream, res: Result, diag: Counter[str]) -> None:
    rec, use = res.rec, stream.tool_uses.get(res.tool_use_id)
    if use is not None:
        use.result_any = res
    if rec.turn is None:
        res.disposition = "outside"
        diag["results_outside_turn"] += 1
    elif use is not None and use.turn is not rec.turn:
        res.disposition = "late"
        diag["late_tool_results"] += 1
    elif use is not None:
        use.result = res
    else:
        res.disposition = "orphan"
        diag["orphan_tool_results"] += 1
        rec.turn.orphan_results.append(res)


def _register_results(stream: Stream, rec: Rec, diag: Counter[str]) -> None:
    for k, body in enumerate(_content(rec.data)):
        if not (isinstance(body, dict) and body.get("type") == "tool_result" and _nonempty(body.get("tool_use_id"))):
            continue
        tid = body["tool_use_id"]
        if tid in stream.result_by_id:
            diag["duplicate_tool_results"] += 1
            continue
        res = Result(rec, k, tid, body)
        stream.result_by_id[tid] = res
        _place_result(stream, res, diag)


def _index_tools(stream: Stream, diag: Counter[str]) -> None:
    for rec in stream.recs:
        if rec.kind == "assistant":
            _register_uses(stream, rec, diag)
        elif rec.kind == "hook":
            _note_pre_tool_block(stream, rec)
    for rec in stream.recs:
        if rec.kind == "tool_result":
            _register_results(stream, rec, diag)


def _group_responses(stream: Stream, diag: Counter[str]) -> None:
    by_key: dict[tuple[int, str], Response] = {}
    for rec in stream.recs:
        if rec.kind != "assistant":
            continue
        if rec.turn is None:
            diag["pre_turn_assistant_records"] += 1
            continue
        if is_api_error(rec.data):
            continue
        mid = rec.data["message"]["id"]
        key = (rec.turn.ordinal, mid)
        if key in by_key:
            by_key[key].recs.append(rec)
        else:
            by_key[key] = Response(mid, [rec], rec.turn)
            rec.turn.responses.append(by_key[key])


def _close_turns(stream: Stream, diag: Counter[str]) -> None:
    """Rules WT-3/WT-4: each turn's closing events (a turn holding one is closed); an unusable witness is counted."""
    for turn in stream.turns:
        turn.bounded = turn.closed
        turn.closes = closing_events(turn)
        turn.closed = turn.closed or bool(turn.closes)
    for rec in stream.hooks:
        if is_witness(rec):
            if rec.turn is None:
                diag["witness_outside_turn"] += 1
            elif all(rec is not close for close in rec.turn.closes):
                diag["witness_not_at_rest"] += 1


def _assemble(
    key: str, session_ref: str, recs: list[Rec], closing: bool, diag: Counter[str], witnesses: Counter[str] | None = None
) -> Stream:
    stream = Stream(key, session_ref, recs)
    _walk(stream, closing, diag)
    _index_tools(stream, diag)
    _group_responses(stream, diag)
    _close_turns(stream, diag if witnesses is None else witnesses)
    return stream


def _frontier_cut(stream: Stream) -> int | None:
    """Rule WT-7: the line after which a root's open last turn contributes nothing (None when nothing is withheld)."""
    last = stream.turns[-1] if stream.turns else None
    if last is None or last.bounded:
        return None
    cut = frontier(last)
    return cut if stream.recs and cut < stream.recs[-1].line else None


def _build_stream(
    tree: TranscriptTree,
    key: str,
    limit: int | None,
    session_ref: str,
    closing: bool,
    diag: Counter[str],
    totals: dict[str, int],
    frontier: bool = False,
) -> Stream | None:
    data = tree.read(key)
    if data is None:
        return None
    lines = read_lines(data, limit)
    totals[key] = len(lines)
    skip = inherited_lines(lines) if key == ROOT else set()
    recs: list[Rec] = []
    for item in lines:
        if item.data is None:
            diag["malformed_lines"] += 1
            continue
        kind = classify(item.data, diag, item.line in skip)
        if kind != "inherited":
            recs.append(Rec(key, item.line, item.data, record_time(item.data), kind))
    if not frontier:
        return _assemble(key, session_ref, recs, closing, diag)
    probe: Counter[str] = Counter()
    stream = _assemble(key, session_ref, recs, closing, probe)
    cut = _frontier_cut(stream)
    if cut is None:
        diag.update(probe)
        return stream
    diag["frontier_withheld"] += sum(1 for r in recs if r.line > cut)
    for name in WITNESS_COUNTERS:
        diag[name] += probe[name]
    return _assemble(key, session_ref, [r for r in recs if r.line <= cut], closing, diag, Counter())


def _completion_text(rec: Rec) -> str | None:
    if rec.kind == "prompt":
        text = _content(rec.data)
    elif rec.kind == "attach" and rec.data["attachment"].get("type") == "queued_command":
        text = rec.data["attachment"].get("prompt")
    else:
        return None
    return text if isinstance(text, str) else None


def is_background_completion(rec: Rec, agent_id: str) -> bool:
    text = _completion_text(rec)
    return text is not None and text.startswith(BACKGROUND_COMPLETION_TAG) and f"<task-id>{agent_id}</task-id>" in text


def notice_status(rec: Rec) -> str | None:
    """Rule BN-1: the <status> token of a completion notice, "malformed" for anything else in the tag, None without one."""
    text = _completion_text(rec) or ""
    if "<status" not in text:
        return None
    match = _NOTICE_STATUS.search(text)
    return match.group(1) if match and _STATUS_TOKEN.fullmatch(match.group(1)) else "malformed"


def _background_spawn(stream: Stream, use: ToolUse, agent_id: str, diag: Counter[str]) -> Spawn:
    notices = [r for r in stream.recs if r.line > use.rec.line and is_background_completion(r, agent_id)]
    spawn = Spawn(stream.key, use, agent_id, bool(notices), notices[0] if notices else None, 0, None, notices)
    at = stream.resume_at
    boundary = next((r for r in stream.resumes if at[r.line] > use.rec.line), None) if stream.key == ROOT else None
    if boundary is None:
        return spawn
    if not notices or notices[0].line > at[boundary.line]:
        spawn.complete, spawn.completed_at_boundary = True, True
        diag["background_completed_at_resume"] += 1
        if notices:
            diag["notice_after_boundary"] += 1
    return spawn


def _spawns_of(stream: Stream, diag: Counter[str]) -> list[Spawn]:
    out: list[Spawn] = []
    for use in stream.tool_uses.values():
        result = use.result_any
        info = result.use_result if result is not None else None
        if (
            result is None
            or use.block_body.get("name") not in AGENT_TOOL_NAMES
            or not isinstance(info, dict)
            or not _nonempty(info.get("agentId"))
        ):
            continue
        agent_id = info["agentId"]
        if info.get("isAsync"):
            out.append(_background_spawn(stream, use, agent_id, diag))
        else:
            out.append(Spawn(stream.key, use, agent_id, True, result.rec, result.block, result))
    return out


def parse_tree(tree: TranscriptTree, limits: dict[str, int] | None, final: bool, *, frontier: bool = False) -> ParsedTree:
    """Parse the tree at the given per-file line limits (None = every consumable line).

    *frontier* (rule WT-7, honoured only on a non-final parse) withholds the root's open last turn beyond its frontier.
    """
    diag: Counter[str] = Counter()
    totals: dict[str, int] = {}

    def limit_of(key: str) -> int | None:
        return None if limits is None else limits.get(key, 0)

    streams: dict[str, Stream] = {}
    spawns: list[Spawn] = []
    root = _build_stream(
        tree, ROOT, limit_of(ROOT), root_session_ref(tree.session_id), final, diag, totals, frontier and not final
    )
    queue = [root] if root is not None else []
    known = set(tree.agent_ids())
    claimed: set[str] = set()
    while queue:
        stream = queue.pop(0)
        streams[stream.key] = stream
        for spawn in _spawns_of(stream, diag):
            if spawn.agent_id not in known or spawn.agent_id in claimed:
                diag["missing_child_files" if spawn.agent_id not in known else "duplicate_spawns"] += 1
                continue
            spawns.append(spawn)
            claimed.add(spawn.agent_id)
            ref = subagent_session_ref(tree.session_id, spawn.agent_id)
            child = _build_stream(tree, spawn.agent_id, limit_of(spawn.agent_id), ref, final or spawn.complete, diag, totals)
            if child is not None:
                queue.append(child)
    if orphans := known - {s.agent_id for s in spawns}:
        diag["orphan_subagents"] += len(orphans)
    return ParsedTree(tree.session_id, final, streams, spawns, totals, diag)


def emitting_streams(parsed: ParsedTree) -> list[Stream]:
    """The root stream plus every sub-agent stream whose parent holds its completion (or the pass is final)."""
    out = [s for key, s in parsed.streams.items() if key == ROOT]
    out += [
        parsed.streams[sp.agent_id] for sp in parsed.spawns if sp.agent_id in parsed.streams and (sp.complete or parsed.final)
    ]
    return out
