"""Source-shape counts over one transcript tree, with its own json walk (independent of the producer's parse).

Counts non-final model calls by stream kind (root or sub-agent) and position (the stream's final call or mid-stream), compares
each sync spawn's toolUseResult.usage with its child's last call, reads each background spawn's completion-notice status and
compares it with the given agent close outcomes, finds sidecars the cursor pinned absent that now exist, and counts the root's
Stop hook summaries. Nothing here reads, returns or prints transcript content: only counts, agent ids and finding names.

Finding vocabulary (stable names the in-session source check and the verification monitor plans consume):
  declared  nonfinal_call (rec-4354), spawn_equal (not a defect), spawn_corrects_nonfinal (rec-4354),
            background_status_mismatch (rec-4358 while it is open)
  alarm     nonfinal_root_call (never yet observed), spawn_usage_mismatch, sidecar_pinned_absent_present (no owner rec yet)
  count     stop_hook_summaries (a denominator only)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from scripts.telemetry_verify.ledger import CLASSES, USAGE_KEYS, find_spawns, last_responses, read_stream
from src.turn_capture.transcript import ROOT, TranscriptTree

DECLARED = {
    "nonfinal_call": "rec-4354",
    "spawn_equal": "the spawn result equals the child's last call",
    "spawn_corrects_nonfinal": "rec-4354",
    "background_status_mismatch": "rec-4358",
}
ALARMS = ("nonfinal_root_call", "spawn_usage_mismatch", "sidecar_pinned_absent_present")
_STATUS = re.compile(r"<status>([^<]*)</status>")
_TASK = re.compile(r"<task-id>([^<]*)</task-id>")


@dataclass(frozen=True)
class ShapeFinding:
    name: str
    agent_id: str | None = None
    kind: str | None = None
    position: str | None = None
    detail: str | None = None

    @property
    def alarm(self) -> bool:
        return self.name in ALARMS


@dataclass
class Shape:
    nonfinal: dict[tuple[str, str], int] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    stop_hook_summaries: int = 0
    stop_hook_count: int = 0
    findings: list[ShapeFinding] = field(default_factory=list)

    def named(self, name: str) -> list[ShapeFinding]:
        return [f for f in self.findings if f.name == name]


def _is_final(record: dict[str, Any]) -> bool:
    return bool(record["message"].get("stop_reason")) or bool(record.get("isAbortedMidStream"))


def _usage(record: dict[str, Any]) -> dict[str, int]:
    usage = record["message"]["usage"]
    return {name: usage[USAGE_KEYS[name]] if isinstance(usage.get(USAGE_KEYS[name]), int) else 0 for name in CLASSES}


def _count_nonfinal(shape: Shape, kind: str, stream: list[dict[str, Any]], agent_id: str | None) -> dict[str, Any] | None:
    """Record the stream's non-final calls; return its last call."""
    calls = last_responses(stream)
    for index, call in enumerate(calls):
        if call["message"].get("stop_reason") or call.get("isAbortedMidStream"):
            continue
        position = "final" if index == len(calls) - 1 else "mid"
        shape.nonfinal[(kind, position)] = shape.nonfinal.get((kind, position), 0) + 1
        name = "nonfinal_root_call" if kind == "root" else "nonfinal_call"
        shape.findings.append(ShapeFinding(name, agent_id, kind, position))
    return calls[-1] if calls else None


def _compare_spawn(shape: Shape, agent_id: str, spawn: dict[str, Any], last: dict[str, Any] | None) -> None:
    usage = spawn.get("usage")
    if not isinstance(usage, dict) or last is None:
        return
    given = {name: usage[USAGE_KEYS[name]] if isinstance(usage.get(USAGE_KEYS[name]), int) else 0 for name in CLASSES}
    child = _usage(last)
    if given == child:
        shape.findings.append(ShapeFinding("spawn_equal", agent_id))
    elif (
        all(given[n] == child[n] for n in CLASSES if n != "output")
        and not _is_final(last)
        and given["output"] > child["output"]
    ):
        shape.findings.append(ShapeFinding("spawn_corrects_nonfinal", agent_id, detail=str(given["output"] - child["output"])))
    else:
        shape.findings.append(ShapeFinding("spawn_usage_mismatch", agent_id))


def _notice_statuses(stream: list[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for record in stream:
        attachment = record.get("attachment")
        prompt = attachment.get("prompt") if isinstance(attachment, dict) else None
        if isinstance(prompt, str) and "<task-notification>" in prompt:
            task, status = _TASK.search(prompt), _STATUS.search(prompt)
            if task and status:
                out[task.group(1)] = status.group(1)
    return out


def check_shape(tree: TranscriptTree, *, closes: Mapping[str, str] | None = None, pinned_absent: Iterable[str] = ()) -> Shape:
    """Shape counts and findings of one tree; *closes* maps an agent id to its stored close outcome, *pinned_absent* lists
    sidecar basenames the cursor pinned absent."""
    shape = Shape()
    root = read_stream(tree, ROOT)
    streams = {agent: read_stream(tree, agent) for agent in tree.agent_ids()}
    _count_nonfinal(shape, "root", root, None)
    lasts = {agent: _count_nonfinal(shape, "sub", stream, agent) for agent, stream in streams.items()}
    owners = [root, *streams.values()]
    for stream in owners:
        for _, agent, is_async in find_spawns(stream):
            if not is_async and agent in lasts:
                result = next(r["toolUseResult"] for r in stream if r.get("toolUseResult", {}).get("agentId") == agent)
                _compare_spawn(shape, agent, result, lasts[agent])
    shape.statuses = _notice_statuses(root)
    for agent, status in shape.statuses.items():
        if status != "completed" and (closes or {}).get(agent) == "success":
            shape.findings.append(ShapeFinding("background_status_mismatch", agent, detail=status))
    for name in pinned_absent:
        if tree.sidecar_size(name) is not None:
            shape.findings.append(ShapeFinding("sidecar_pinned_absent_present", detail=name))
    for record in root:
        if record.get("subtype") == "stop_hook_summary" or record.get("type") == "stop_hook_summary":
            shape.stop_hook_summaries += 1
            count = record.get("hookCount")
            shape.stop_hook_count += count if isinstance(count, int) else 0
    return shape
