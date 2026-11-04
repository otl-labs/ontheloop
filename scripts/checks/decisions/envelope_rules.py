"""Decision-envelope rules over parsed inputs (PLAN-decision-serves-kind-grammar, Decision 243 clause 8).

Pure functions, no file reads: the one rule set that validate_decision_entry_conformance (the gate) and
scripts.roadmap.plan_decision_drafts (the planning-time report) both call. A FAIL rule returns bare issue text; a WARN
rule returns text that names the lawful remedy (Decision 177: envelopes are append-only). Contracts supply every
vocabulary (kind values, routing tokens, north-star ids) through the arguments.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts.decisions_md import PLACEHOLDER_DATE, PLACEHOLDER_NUMBER

NEVER_NUMBERED_DECISION_TEXT = "Never a numbered Decision"
_SERVES_ID_RE = re.compile(r"^NS\.\d+$")
_FOLDED = "folded scalar: put the draft in a literal (|) block"


def required_field_issues(envelope: dict, required_fields: list[str]) -> list[str]:
    missing = [f for f in required_fields if f not in ("significance", "kind") and not envelope.get(f)]
    return [f"envelope is missing required field(s): {missing}"] if missing else []


def significance_issues(envelope: dict, routing_tokens: dict[str, str]) -> list[str]:
    """Validate the envelope's `significance` claim: present, one of the contract's routing tokens, carrying a
    non-empty one-line justification, and never one of the routing rows whose own text says "Never a numbered
    Decision"."""
    significance = envelope.get("significance")
    if not isinstance(significance, dict) or not significance.get("value"):
        return ["significance element is missing (required: value naming a routing token, plus a justification)"]
    value = str(significance["value"])
    justification = str(significance.get("justification") or "").strip()
    issues: list[str] = []
    if value not in routing_tokens:
        issues.append(f"significance.value {value!r} does not name a decision-entry.yaml significance.routing token")
        return issues
    if NEVER_NUMBERED_DECISION_TEXT in routing_tokens[value]:
        issues.append(
            f"significance.value {value!r} routes to a row whose own contract text reads "
            f"{NEVER_NUMBERED_DECISION_TEXT!r} -- never a numbered Decision"
        )
    if not justification:
        issues.append("significance.justification is missing or empty")
    return issues


def kind_issues(envelope: dict, kind_values: list[str]) -> list[str]:
    kind = envelope.get("kind")
    if not isinstance(kind, dict) or not kind.get("value"):
        return ["kind element is missing (required: value from docs/contracts/decision-kind.yaml, plus a justification)"]
    issues: list[str] = []
    if kind["value"] not in kind_values:
        issues.append(f"kind.value {kind['value']!r} is not one of docs/contracts/decision-kind.yaml values {kind_values}")
    if not str(kind.get("justification") or "").strip():
        issues.append("kind.justification is missing or empty")
    return issues


def serves_issues(envelope: dict, north_star_ids: set[str]) -> list[str]:
    """serves is optional; when present: a non-empty, duplicate-free list of NS.<n> ids that each resolve."""
    if "serves" not in envelope:
        return []
    serves = envelope["serves"]
    if not isinstance(serves, list) or not serves:
        return ["serves must be a non-empty list of north-star ids"]
    issues: list[str] = []
    if len(set(map(str, serves))) != len(serves):
        issues.append(f"serves lists a duplicate id: {serves}")
    for item in serves:
        if not isinstance(item, str) or not _SERVES_ID_RE.fullmatch(item):
            issues.append(f"serves id {item!r} does not match ^NS\\.\\d+$")
        elif item not in north_star_ids:
            issues.append(f"serves id {item!r} does not resolve to an entry in docs/contracts/north-star.yaml")
    return issues


def unknown_key_issues(envelope: dict, allowed_keys: list[str]) -> list[str]:
    unknown = sorted(str(k) for k in envelope if k not in allowed_keys)
    return [f"envelope carries key(s) outside required_fields and optional_fields: {unknown}"] if unknown else []


def number_issues(number: Any, baseline_max: int | None, draft: bool = False) -> list[str]:
    """An unfilled placeholder fails unless the input is a draft; a new committed number must exceed the baseline
    maximum (baseline_max None skips that rule: drafts are judged by the caller's concrete-number WARN)."""
    if number == PLACEHOLDER_NUMBER:
        return [] if draft else [f"number is the unfilled placeholder {PLACEHOLDER_NUMBER}: fill MAX + 1 (Decision 217 cl.5)"]
    if baseline_max is not None and isinstance(number, int) and number <= baseline_max:
        return [f"number {number} is not above the baseline maximum {baseline_max} (assign MAX + 1, Decision 217 cl.5)"]
    return []


def dangling_serves_warnings(number: int, envelope: dict, north_star_ids: set[str]) -> list[str]:
    serves = envelope.get("serves")
    if not isinstance(serves, list):
        return []
    return [
        f"Decision {number} serves {i!r}, which no longer resolves in docs/contracts/north-star.yaml; keep the id there "
        "as retired (envelopes are append-only, Decision 177), never edit the envelope"
        for i in serves
        if isinstance(i, str) and i not in north_star_ids
    ]


def carried_by_mechanism_warnings(carried_by: dict[str, list[int]], kinds: dict[int, str]) -> list[str]:
    """A mechanism entry carries a north star: WARN. `kinds` maps a Decision number to its kind.value; an entry
    without kind is absent from it and reads as direction."""
    return [
        f"Decision {n} is kind mechanism but is listed in {ns} carried_by; drop it from carried_by or supersede the "
        "entry, never edit its envelope (Decision 177)"
        for ns, numbers in sorted(carried_by.items())
        for n in numbers
        if kinds.get(n) == "mechanism"
    ]


def _opener_is_inline(block: str, pos: int) -> bool:
    line_end = block.find("\n", pos)
    rest = block[pos + len("```yaml") : line_end if line_end != -1 else len(block)]
    return (pos > 0 and block[pos - 1] != "\n") or ":" in rest


def _shape_detail(data: dict) -> str:
    if data.get("number") is not None and (isinstance(data["number"], bool) or not isinstance(data["number"], int)):
        return "field number must be an integer"
    for field in ("amends", "supersedes"):
        value = data.get(field)
        if value is not None and not (isinstance(value, list) and all(isinstance(n, int) for n in value)):
            return f"field {field} must be a list of integers"
    for field in ("significance", "kind"):
        if data.get(field) is not None and not isinstance(data[field], dict):
            return f"field {field} must be a mapping {{value, justification}}"
    if data.get("serves") is not None and not (
        isinstance(data["serves"], list) and all(isinstance(s, str) for s in data["serves"])
    ):
        return "field serves must be a list of strings"
    return "the envelope shape is malformed"


def malformed_envelope_detail(block: str, placeholder: bool = False) -> str:
    """Diagnostics only: name why extract_entry_envelope returned None, never decide that it did (Decision 134)."""
    pos = block.find("```yaml")
    if pos != -1 and _opener_is_inline(block, pos):
        return _FOLDED
    lines = block.split("\n")
    i = 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i] != "```yaml":
        return "no bare ```yaml fence immediately after the heading"
    end = next((j for j in range(i + 1, len(lines)) if lines[j] == "```"), None)
    if end is None:
        return "the ```yaml fence is never closed"
    text = "\n".join(lines[i + 1 : end])
    if placeholder:
        text = text.replace(PLACEHOLDER_NUMBER, "0").replace(PLACEHOLDER_DATE, "0")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return f"the envelope is not valid YAML ({str(exc).splitlines()[0]})"
    if not isinstance(data, dict):
        return "the envelope is not a YAML mapping"
    return _shape_detail(data)
