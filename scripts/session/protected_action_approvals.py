"""Pure approval reading for the protected-action gate -- no I/O (rec-4518, docs/contracts/protected-actions.yaml).

Approval sources from Claude Code transcript records, whole-line grammar matching, the one-shot credit walk and the
deny message. Records are the parsed JSONL objects; git state and command classification arrive as callables so this
module never touches the filesystem, git or the shell.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from scripts.session.protected_action_match import Hit

_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_-]*)([^<>]*?)(/?)>")
_ATTR_RE = re.compile(r'([A-Za-z_-]+)="([^"]*)"')
_APPROVE_RE = re.compile(r"^approve (\S+) (\S+?)(?: ([0-9a-f]{7,40}))?$", re.IGNORECASE)
_REWRITE_NOTE = (
    "the gate cannot name the secret: rewrite the command as an aws call naming each secret (--secret-id or --name), "
    "or as a declared entry point (docs/contracts/protected-actions.yaml amendment_log migration_story)"
)
_CARD_RE = re.compile(r'^Chose "([^"]*)"')
_RELAY_TAGS = frozenset({"relay", "coordinator-relay", "cited"})
_BASH_TOOLS = frozenset({"Bash", "Monitor"})


@dataclass(frozen=True)
class Approve:
    cls: str
    target: str
    commit: str | None
    pos: int


@dataclass
class Denial:
    hit: Hit
    line: str
    notes: list[str] = field(default_factory=list)


def _is_source(rec: Mapping[str, Any]) -> bool:
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return (
        rec.get("type") == "user"
        and isinstance(content, str)
        and bool(rec.get("promptId"))
        and not rec.get("sourceToolUseID")
        and not rec.get("turnCompanion")
        and not rec.get("isMeta")
        and not rec.get("isCompactSummary")
        and not rec.get("isSidechain")
    )


def sources(records: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, str]]:
    """(record index, text, origin kind) of each typed-turn source, companions excluded."""
    out: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    latest: str | None = None
    for idx, rec in enumerate(records):
        if not _is_source(rec):
            continue
        pid = str(rec["promptId"])
        if pid != latest:
            if pid in seen:
                continue
            seen.add(pid)
            latest = pid
        origin = rec.get("origin")
        kind = origin.get("kind", "") if isinstance(origin, dict) else ""
        out.append((idx, rec["message"]["content"], kind))
    return out


def _thread_messages(text: str, ts: str | None) -> tuple[list[str], str | None]:
    """Principal messages inside the thread *ts* (the first thread seen when None), parse before unescape."""
    stack: list[tuple[str, dict[str, str]]] = []
    found: list[str] = []
    start = 0
    first_ts = ts
    for m in _TAG_RE.finditer(text):
        closing, name, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if name == "message" and stack and stack[-1][0] == "message":
                _, a = stack[-1]
                names = {n for n, _ in stack}
                thread = next((x for n, x in reversed(stack) if n == "thread"), None)
                if (
                    a.get("from") == "human"
                    and a.get("trust") == "principal"
                    and thread is not None
                    and thread.get("ts") == first_ts
                    and not names & _RELAY_TAGS
                ):
                    found.append(html.unescape(text[start : m.start()]))
            while stack and stack[-1][0] != name:
                stack.pop()
            if stack:
                stack.pop()
            continue
        if selfclose:
            continue
        attr = {k: html.unescape(v) for k, v in _ATTR_RE.findall(attrs)}
        stack.append((name, attr))
        if name == "thread" and first_ts is None:
            first_ts = attr.get("ts")
        start = m.end()
    return found, first_ts


def _lines(text: str) -> list[str]:
    card = _CARD_RE.match(text)
    if card:
        text = card.group(1)
    return [" ".join(item.split()) for line in text.splitlines() for item in line.split(",") if item.strip()]


def approvals(records: Sequence[Mapping[str, Any]]) -> list[Approve]:
    """Every approve item from an allowed source, in transcript order."""
    srcs = sources(records)
    thread_mode = any(text.startswith("<wake ") for _, text, _ in srcs)
    found: list[Approve] = []
    ts: str | None = None
    for idx, text, kind in srcs:
        if thread_mode:
            if not (text.startswith("<wake ") and kind == "human"):
                continue
            messages, ts = _thread_messages(text, ts)
        else:
            if text.startswith("<") or (kind and kind != "human"):
                continue
            messages = [text]
        for message in messages:
            for item in _lines(message):
                m = _APPROVE_RE.match(item)
                if m:
                    found.append(Approve(m.group(1).lower(), m.group(2), (m.group(3) or "").lower() or None, idx))
    return found


def _same_target(hit: Hit, a: Approve) -> bool:
    return a.target == hit.target if hit.exact else a.target.lower() == hit.target.lower()


def _session_ok(hit: Hit, items: list[Approve]) -> bool:
    if hit.target in ("any", "unknown"):
        return False
    return any(a.commit is None and a.cls == hit.cls and _same_target(hit, a) for a in items)


def _bash_commands(rec: Mapping[str, Any], current_id: str | None) -> list[str]:
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if rec.get("type") != "assistant" or rec.get("isSidechain") or not isinstance(content, list):
        return []
    return [
        b["input"]["command"]
        for b in content
        if isinstance(b, dict)
        and b.get("type") == "tool_use"
        and b.get("name") in _BASH_TOOLS
        and b.get("id") != current_id
        and isinstance(b.get("input"), dict)
        and isinstance(b["input"].get("command"), str)
    ]


def _unused_credits(
    records: Sequence[Mapping[str, Any]],
    items: list[Approve],
    hit: Hit,
    current_id: str | None,
    classify_prev: Callable[[str, str | None], list[tuple[str, str]]],
    input_cwd: str,
) -> list[str]:
    unused: list[str] = []
    by_pos: dict[int, list[Approve]] = {}
    for a in items:
        if a.commit and a.cls == hit.cls and a.target.lower() == hit.target.lower():
            by_pos.setdefault(a.pos, []).append(a)
    for idx, rec in enumerate(records):
        unused.extend(a.commit for a in by_pos.get(idx, []) if a.commit)
        for command in _bash_commands(rec, current_id):
            cwd = rec.get("cwd") if isinstance(rec.get("cwd"), str) else input_cwd
            if (hit.cls, hit.target) in classify_prev(command, cwd):
                unused = []
    return unused


def evaluate(
    hits: Sequence[Hit],
    records: Sequence[Mapping[str, Any]],
    *,
    agent_id: bool,
    tool_use_id: str | None,
    input_cwd: str,
    classify_prev: Callable[[str, str | None], list[tuple[str, str]]],
    git_state: Callable[[str], tuple[str, str]],
) -> list[Denial]:
    """The hits with no valid approval. git_state(root_dir) returns (HEAD, porcelain status) or raises."""
    items = approvals(records)
    denials: list[Denial] = []
    for hit in hits:
        if hit.approval != "one_shot_at_commit":
            if _session_ok(hit, items):
                continue
            if hit.target in ("any", "unknown"):
                denials.append(Denial(hit, "", [_REWRITE_NOTE]))
            else:
                denials.append(Denial(hit, f"approve {hit.cls} {hit.target}"))
            continue
        denial = _one_shot(hit, records, items, agent_id, tool_use_id, input_cwd, classify_prev, git_state)
        if denial:
            denials.append(denial)
    return denials


def _one_shot(
    hit: Hit,
    records: Sequence[Mapping[str, Any]],
    items: list[Approve],
    agent_id: bool,
    tool_use_id: str | None,
    input_cwd: str,
    classify_prev: Callable[[str, str | None], list[tuple[str, str]]],
    git_state: Callable[[str], tuple[str, str]],
) -> Denial | None:
    if agent_id:
        return Denial(hit, "", ["a subagent's call is never covered: run it in the main session"])
    if hit.target == "unknown" or hit.from_cwd or hit.root_dir is None:
        note = "the root is not a literal -chdir=terraform/<root> (or cd, pushd, env -C): rewrite the command with one"
        return Denial(hit, "", [note])
    try:
        head, status = git_state(hit.root_dir)
    except Exception:  # noqa: BLE001 - any git failure denies
        return Denial(hit, "", ["git could not read the repository state: check the root path, then retry"])
    line = f"approve {hit.cls} {hit.target} {head[:12]}"
    notes = ["commit every change first (the repository has uncommitted or untracked files)"] if status.strip() else []
    unused = _unused_credits(records, items, hit, tool_use_id, classify_prev, input_cwd)
    if not status.strip() and any(head.lower().startswith(c.lower()) for c in unused):
        return None
    return Denial(hit, line, notes)


def deny_message(denials: Sequence[Denial], on_deny_hint: str = "agent_platform", ambient_only_note: bool = False) -> str:
    """One stderr message covering every unapproved hit."""
    parts = ["protected-action-gate: refused."]
    for d in denials:
        h = d.hit
        parts.append(f"- class {h.cls}, target {h.target}; binding control: {' '.join(h.binding_control.split())}")
        parts.append(f"  approve line: {d.line}" if d.line else "  approve line: none until the command is rewritten")
        parts.extend(f"  note: {n}" for n in d.notes)
    parts.append("The operator writes the approve line in this thread, beside the exact command; run it only after that.")
    if all(d.hit.ambient_only for d in denials):
        parts.append(f"Self-serve fix: re-run with AWS_PROFILE={on_deny_hint}.")
    if all(d.hit.default_only for d in denials):
        parts.append(f"Self-serve fix: re-run with -var aws_profile={on_deny_hint}.")
    parts.append(
        'If the classifier then refuses ("Denied by auto mode classifier"), only the operator\'s own message in this thread '
        "naming the exact command clears it (docs/contracts/protected-actions.yaml procedure, CLASSIFIER VERDICT)."
    )
    return "\n".join(parts)
