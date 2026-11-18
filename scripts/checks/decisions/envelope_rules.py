"""Decision-envelope rules over parsed inputs (PLAN-decision-serves-kind-grammar, Decision 243 clause 8).

Pure functions, no file reads: the one rule set that validate_decision_entry_conformance (the gate) and
scripts.roadmap.plan_decision_drafts (the planning-time report) both call. A FAIL rule returns bare issue text; a WARN
rule returns text that names the lawful remedy (Decision 177: envelopes are append-only). Contracts supply every
vocabulary (kind values, routing tokens, north-star ids) through the arguments.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import yaml

from scripts.decisions_md import PLACEHOLDER_DATE, PLACEHOLDER_NUMBER

NEVER_NUMBERED_DECISION_TEXT = "Never a numbered Decision"
_SERVES_ID_RE = re.compile(r"^NS\.\d+$")
_FOLDED = "folded scalar: put the draft in a literal (|) block"
_DELEGATE_KEY_RE = re.compile(r"^([^#\s]+\.(?:yaml|yml))#([A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*)$")
_PLAN_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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


def split_delegated_key(key: object) -> tuple[str, str] | None:
    """A delegates key "<path>.(yaml|yml)#<dotted>" -> (path, dotted); None when it does not match."""
    match = _DELEGATE_KEY_RE.match(key) if isinstance(key, str) else None
    return (match.group(1), match.group(2)) if match else None


def dotted_value(parsed: Any, dotted: str) -> tuple[bool, Any]:
    """Walk a parsed YAML value down a dotted key: (found, value)."""
    node = parsed
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounds_issue(bounds: object) -> str | None:
    if not isinstance(bounds, dict):
        return "bounds must be a mapping"
    keys = set(bounds)
    if keys == {"min", "max"}:
        ok = _is_number(bounds["min"]) and _is_number(bounds["max"]) and bounds["min"] <= bounds["max"]
        return None if ok else "bounds {min, max} needs numbers with min <= max"
    if keys == {"one_of"}:
        ok = isinstance(bounds["one_of"], list) and bool(bounds["one_of"])
        return None if ok else "bounds {one_of} needs a non-empty list"
    if keys == {"text"}:
        text = bounds["text"]
        ok = isinstance(text, str) and bool(text.strip()) and "\n" not in text.strip()
        return None if ok else "bounds {text} needs a non-empty one-line string"
    return "bounds must be exactly one of {min, max}, {one_of} or {text}"


def delegates_issues(envelope: dict, change_rules: list[str], resolve: Callable[[str], bool]) -> list[str]:
    """delegates is optional; when present: a non-empty list of {key, change_rule, bounds} items, the key resolving."""
    if "delegates" not in envelope:
        return []
    delegates = envelope["delegates"]
    if not isinstance(delegates, list) or not delegates:
        return ["delegates must be a non-empty list of {key, change_rule, bounds}"]
    issues: list[str] = []
    for item in delegates:
        if not isinstance(item, dict) or set(item) != {"key", "change_rule", "bounds"}:
            issues.append(f"delegates item {item!r} must be a mapping with exactly key, change_rule and bounds")
            continue
        key = item["key"]
        if split_delegated_key(key) is None:
            issues.append(f"delegates key {key!r} does not match <path>.(yaml|yml)#<dotted key>")
        elif not resolve(key):
            issues.append(f"delegates key {key!r} does not resolve at head")
        if item["change_rule"] not in change_rules:
            issues.append(f"delegates change_rule {item['change_rule']!r} is not one of {change_rules}")
        bounds_issue = _bounds_issue(item["bounds"])
        if bounds_issue:
            issues.append(f"delegates key {key!r}: {bounds_issue}")
    return issues


def plan_issues(envelope: dict) -> list[str]:
    """plan is optional; when present it is a slug string (FAIL otherwise)."""
    if "plan" not in envelope:
        return []
    slug = envelope["plan"]
    if not isinstance(slug, str) or not _PLAN_SLUG_RE.match(slug):
        return [f"plan {slug!r} must be a slug matching ^[a-z0-9][a-z0-9-]*$"]
    return []


def plan_warnings(envelope: dict, plan_exists: Callable[[str], bool]) -> list[str]:
    """A well-formed plan slug whose docs/plans/PLAN-<slug>.yaml is absent at head is a WARN, never a FAIL."""
    slug = envelope.get("plan")
    if plan_issues(envelope) or slug is None or plan_exists(slug):
        return []
    return [f"plan {slug!r} has no docs/plans/PLAN-{slug}.yaml at head; name an existing plan"]


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
    if data.get("delegates") is not None and not isinstance(data["delegates"], list):
        return "field delegates must be a list of {key, change_rule, bounds}"
    if data.get("plan") is not None and not isinstance(data["plan"], str):
        return "field plan must be a plan slug string"
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
