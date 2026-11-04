"""The host-run sidecar, pin rule P-5 and the top-level agent run rows (pure, stdlib only).

A host that runs `claude -p` without the interactive capture hooks (GitHub Actions today) writes <session dir>/host-run.json
before the capture pass. The sidecar names the project, the agent (a source_registry key), the content policy and the host
facts; from it the producer pins the project_ref when the hook pin is absent (rule P-5), writes a root session's top-level
telemetry_agents open/close pair (the contract's run_shapes.top_level) and applies the withheld content policy.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.turn_capture.refs import role_ref
from src.turn_capture.sessions import agent_row_base, first_model, open_bound, root_close_points
from src.turn_capture.streams import ROOT, ParsedTree
from src.turn_capture.transcript import PIN_REF, TranscriptTree, project_ref_pin
from src.turn_capture.transcripts import Ctx

SCHEMA_VERSION = 1
AGENT_KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
CONTENT_POLICIES = ("full", "withheld")
ACTIONS_EVENTS = ("workflow_run", "workflow_dispatch", "push", "schedule")
_KEYS = {"schema_version", "project_ref", "agent", "content_policy", "host"}
_HOST_KEYS = {
    "kind",
    "run_id",
    "run_attempt",
    "job",
    "retry_ordinal",
    "event_name",
    "repository",
    "workflow_path",
    "ref",
    "sha",
    "checkout_sha",
}
_DIGITS = re.compile(r"[0-9]{1,20}")
_ATTEMPT = re.compile(r"[0-9]{1,6}")
_JOB = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]{1,150}")
_REF = re.compile(r"refs/[A-Za-z0-9_./-]{1,240}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True)
class HostRun:
    project_ref: str
    agent: str
    content_policy: str
    run_id: str
    run_attempt: str
    job: str
    retry_ordinal: int
    event_name: str
    repository: str
    workflow_path: str
    ref: str
    sha: str
    checkout_sha: str | None


def _text(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _host_fields(host: object) -> dict[str, Any] | None:
    """The validated host block as HostRun field values, else None (the ci-pass value grammar)."""
    if not isinstance(host, dict) or not {"kind", "run_id", "run_attempt", "job", "retry_ordinal"} <= set(host):
        return None
    if not set(host) <= _HOST_KEYS or host["kind"] != "gha":
        return None
    ordinal = host["retry_ordinal"]
    checkout = host.get("checkout_sha")
    fields: dict[str, Any] = {
        "run_id": _text(host["run_id"], _DIGITS),
        "run_attempt": _text(host["run_attempt"], _ATTEMPT),
        "job": _text(host["job"], _JOB),
        "event_name": host.get("event_name") if host.get("event_name") in ACTIONS_EVENTS else None,
        "repository": _text(host.get("repository"), _REPOSITORY),
        "workflow_path": _text(host.get("workflow_path"), _WORKFLOW),
        "ref": _text(host.get("ref"), _REF),
        "sha": _text(host.get("sha"), _SHA),
    }
    if any(value is None for value in fields.values()):
        return None
    if type(ordinal) is not int or not 1 <= ordinal <= 999:
        return None
    if checkout is not None and _text(checkout, _SHA) is None:
        return None
    return {**fields, "retry_ordinal": ordinal, "checkout_sha": checkout}


def valid_project_ref(value: str) -> bool:
    return PIN_REF.fullmatch(value) is not None


def valid_host_facts(host: object) -> bool:
    return _host_fields(host) is not None


def parse_host_run(data: bytes | None) -> HostRun | None:
    """Strict schema_version 1 shape and the ci-pass value grammar; anything else is None (the caller counts it)."""
    try:
        body = json.loads(data) if data is not None else None
    except ValueError:
        return None
    if not isinstance(body, dict) or set(body) != _KEYS or body["schema_version"] != SCHEMA_VERSION:
        return None
    host = _host_fields(body["host"])
    project_ref, agent = _text(body["project_ref"], PIN_REF), _text(body["agent"], AGENT_KEY)
    policy = body["content_policy"]
    if host is None or project_ref is None or agent is None or policy not in CONTENT_POLICIES:
        return None
    return HostRun(project_ref=project_ref, agent=agent, content_policy=policy, **host)


def render_host_run_ref(host: HostRun) -> str:
    return f"gha:{host.run_id}/{host.run_attempt}/{host.job}/{host.retry_ordinal}"


def resolve_pin(tree: TranscriptTree) -> tuple[str | None, str]:
    """Rule P-5: (project_ref | None, token). The hook pin and the sidecar must agree where both exist."""
    hook = project_ref_pin(tree)
    sidecar = parse_host_run(tree.host_run())
    side = sidecar.project_ref if sidecar is not None else None
    if hook is not None and side is not None and hook != side:
        return None, "pin_conflict"
    pinned = hook or side
    return (pinned, "pinned") if pinned is not None else (None, "no_pin")


# telemetry_agents.outcome is the closed set success|failed|timeout|throttled; an interrupted run (C-3 cancelled) is a failed run.
_AGENT_OUTCOME = {"cancelled": "failed"}


def top_level_agent_rows(parsed: ParsedTree, ctx: Ctx, host: HostRun) -> list[dict[str, Any]]:
    """The root's top-level run rows (run_shapes.top_level): the open row once the root's open row is due (rule D-1), and one
    close row per root session close row, keyed by the closing record like it, so every cut of a growing tree builds the same
    rows (a run that is never resumed has exactly one close)."""
    stream = parsed.streams.get(ROOT)
    bound = open_bound(stream) if stream is not None else None
    if stream is None or bound is None:
        return []
    entity = f"{stream.session_ref}/run"
    cols = {
        "entity_ref": entity,
        "session_ref": stream.session_ref,
        "agent_type": host.agent,
        "model": first_model(stream, bound),
        "trigger": host.event_name,
        "workflow_run_id": host.run_id,
        "host_run_ref": render_host_run_ref(host),
    }
    first = next((r for r in stream.recs if r.ts is not None), None)
    version = next(
        (r.data["version"] for r in stream.recs if r.line <= bound and isinstance(r.data.get("version"), str)), None
    )
    rows = [
        agent_row_base(
            ctx, "open", ctx.started, 0 if first is None else first.line, f"{entity}#0/open", version=version, **cols
        )
    ]
    for anchor, outcome in root_close_points(stream, parsed.final, Counter()):
        ext = role_ref(anchor.data["uuid"], 0, "run_close")
        rows.append(
            agent_row_base(ctx, "close", anchor.ts, anchor.line, ext, version=version, outcome=_AGENT_OUTCOME.get(outcome["outcome"], outcome["outcome"]), **cols)
        )
    return rows
