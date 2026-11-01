"""Session provenance (rules PV-1..PV-4). Pure: reads only the stream's own records, never git, the environment or a clock.

The checkout facts reach the tree through the SessionStart pin hook's recorded stderr line; the harness session key
through the remote_session_change attachment. Every miss is counted in the pass diagnostics (rule DG-1).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from src.turn_capture.streams import Rec, Stream
from src.turn_capture.transcript import PIN_SCRIPT_STEM, hook_script

SHA_PATTERN = r"^([0-9a-f]{40}|[0-9a-f]{64})$"
SESSION_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_SESSION_KEY = re.compile(SESSION_KEY_PATTERN)
_SHA = SHA_PATTERN[1:-1].replace("(", "(?:", 1)
_PIN_LINE = re.compile(
    rf"\[{PIN_SCRIPT_STEM}\] capture pin checkout=({_SHA}|unknown) base=({_SHA}|unknown) dirty=(true|false|unknown)"
)
LATE_PIN_EXEMPT = ("SessionStart:resume", "SessionStart:compact")
Facts = tuple[str | None, str | None, bool | None]
NO_FACTS: Facts = (None, None, None)


def _is_pin(rec: Rec) -> bool:
    att = rec.data["attachment"]
    stem, _ = hook_script(att.get("command"))
    return att.get("hookEvent") == "SessionStart" and stem == PIN_SCRIPT_STEM


def pin_facts(att: dict[str, Any]) -> Facts:
    """Rule PV-1: (checkout_sha, checkout_base_sha, checkout_dirty) from a pin attachment's stderr, None for unknown."""
    stderr = att.get("stderr")
    for line in stderr.splitlines() if isinstance(stderr, str) else []:
        match = _PIN_LINE.fullmatch(line.strip())
        if match:
            sha, base, dirty = match.groups()
            return (
                None if sha == "unknown" else sha,
                None if base == "unknown" else base,
                {"true": True, "false": False}.get(dirty),
            )
    return NO_FACTS


def _last_pin(recs: list[Rec], root: bool, diag: Counter[str]) -> Facts:
    pins = [r for r in recs if r.kind == "hook" and _is_pin(r)]
    facts = pin_facts(pins[-1].data["attachment"]) if pins else NO_FACTS
    if root:
        if facts[0] is None:
            diag["pin_without_checkout"] += 1
        elif facts[1] is None:
            diag["checkout_base_unknown"] += 1
    return facts


def open_provenance(stream: Stream, root: bool, diag: Counter[str]) -> Facts:
    """Rule PV-2: the LAST pin attachment before the stream's first prompt record (sub-agents carry none, uncounted)."""
    first = next((r.line for r in stream.recs if r.kind == "prompt"), None)
    before = [r for r in stream.hooks if first is None or r.line < first]
    if root and first is not None and not any(_is_pin(r) for r in before):
        late = (r for r in stream.hooks if r.line > first and _is_pin(r))
        if any(not str(r.data["attachment"].get("hookName")).startswith(LATE_PIN_EXEMPT) for r in late):
            diag["pin_after_first_prompt"] += 1
    return _last_pin(before, root, diag)


def resume_provenance(stream: Stream, resume: Rec, root: bool, diag: Counter[str]) -> Facts:
    """Rule PV-3: the LAST pin attachment inside the resume's hook group (it ends where streams._walk's reset rule says)."""
    end = stream.resume_ends.get(resume.line)
    return _last_pin([r for r in stream.hooks if r.line >= resume.line and (end is None or r.line < end)], root, diag)


def _last_segment(url: Any) -> str:
    try:
        return urlsplit(url).path.rsplit("/", 1)[-1] if isinstance(url, str) else ""
    except ValueError:
        return ""


def harness_session_key(stream: Stream, bound: int | None, diag: Counter[str]) -> str | None:
    """Rule PV-4: the url's last path segment of the FIRST remote_session_change attachment at or before *bound*."""
    for rec in stream.recs:
        if bound is not None and rec.line > bound:
            break
        att = rec.data.get("attachment")
        if rec.kind != "attach" or not isinstance(att, dict) or att.get("type") != "remote_session_change":
            continue
        url = att.get("url")
        segment = _last_segment(url)
        if _SESSION_KEY.fullmatch(segment):
            return segment
        diag["harness_session_key_unparsed"] += 1
        return None
    diag["harness_session_key_absent"] += 1
    return None
