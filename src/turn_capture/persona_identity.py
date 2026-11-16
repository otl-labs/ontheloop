"""Persona identity of a sub-agent run (rules PI-1..PI-3; PI-4 is record_turn's; the envelope's v11 line is their text).

PI-1: the persona is the first Skill call within the sub-agent's open bound whose normalised name is a verdict persona's skill
and whose definition record is readable. PI-2: the hash is the SHA-256 of that record's text minus the header line, its blank
line and the appended ARGUMENTS trailer. PI-3: an absent or unreadable record keeps the sub-agent key and is counted.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Literal

from src.turn_capture.streams import Stream

PLUGIN_PREFIX = "ontheloop:"
PERSONA_SKILLS = {"code-review": "code-review", "decision-scout": "decision-scout", "plan-critique": "plan-critique"}
_HEADER = "Base directory for this skill: "


def skill_name(raw: str) -> str:
    name = raw.strip().lower().removeprefix("/")
    return name.removeprefix(PLUGIN_PREFIX)


def _text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
        text = content[0].get("text")
        return text if isinstance(text, str) else None
    return None


def definition_hash(stream: Stream, use: object, bound: int) -> str | Literal["absent", "unreadable"]:
    found = []
    for rec in stream.recs:
        if rec.kind != "companion" or rec.line > bound or rec.data.get("isMeta") is not True:
            continue
        if rec.data.get("sourceToolUseID") != use.tool_id:  # type: ignore[attr-defined]
            continue
        text = _text((rec.data.get("message") or {}).get("content"))
        if text is not None and text.startswith(_HEADER):
            found.append(text)
    if not found:
        return "absent"
    head, sep, body = found[0].partition("\n")
    if len(found) > 1 or not sep or not body.startswith("\n"):
        return "unreadable"
    body = body[1:]
    spec = use.block_body.get("input")  # type: ignore[attr-defined]
    args = spec.get("args") if isinstance(spec, dict) else None
    if isinstance(args, str) and args:
        trailer = "\n\nARGUMENTS: " + args
        if not body.endswith(trailer):
            return "unreadable"
        body = body[: -len(trailer)]
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def persona_of(stream: Stream, bound: int | None, diag: Counter[str]) -> tuple[str, str] | None:
    if bound is None:
        return None
    uses = sorted((u for u in stream.tool_uses.values() if u.rec.line <= bound), key=lambda u: (u.rec.line, u.block))
    for use in uses:
        spec = use.block_body.get("input")
        raw = spec.get("skill") if isinstance(spec, dict) else None
        if use.block_body.get("name") != "Skill" or not isinstance(raw, str) or skill_name(raw) not in PERSONA_SKILLS:
            continue
        digest = definition_hash(stream, use, bound)
        if digest in ("absent", "unreadable"):
            diag["persona_definition_" + digest] += 1
            continue
        diag["persona_run"] += 1
        return PERSONA_SKILLS[skill_name(raw)], digest
    return None
