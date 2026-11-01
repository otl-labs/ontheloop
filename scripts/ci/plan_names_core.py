#!/usr/bin/env python3
"""Root-agnostic names-only core shared by the Terraform plan reporters (Decision 238 cl.4).

Action counting, changed top-level attribute NAMES (schema names, never values), drift rows, changed output
names, and normalise_address(), which strips every [..] instance key and index from an address so a for_each
key or count index (which can be a value) is never printed. Before/after values are only compared, never
returned. Stdlib only.
"""

from __future__ import annotations

from typing import Any


def has_true(node: Any) -> bool:
    if node is True:
        return True
    if isinstance(node, dict):
        return any(has_true(v) for v in node.values())
    if isinstance(node, list):
        return any(has_true(v) for v in node)
    return False


def changed_names(change: dict[str, Any]) -> list[str]:
    before = change.get("before") or {}
    after = change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    unknown = unknown if isinstance(unknown, dict) else {}
    names = set()
    for key in set(before) | set(after) | set(unknown):
        if before.get(key) != after.get(key) or has_true(unknown.get(key)):
            names.add(key)
    return sorted(names)


def action_bucket(actions: list[str], importing: bool) -> str | None:
    """Count bucket of one resource change: import, add, change, destroy, replace, other, or None for no change."""
    if importing:
        return "import"
    if actions in (["no-op"], ["read"], []):
        return None
    if actions == ["create"]:
        return "add"
    if actions == ["update"]:
        return "change"
    if actions == ["delete"]:
        return "destroy"
    if sorted(actions) == ["create", "delete"]:
        return "replace"
    return "other"


def normalise_address(address: str) -> str:
    """Remove every [..] instance key from a resource address, module path included."""
    out: list[str] = []
    depth = 0
    in_str = False
    i, n = 0, len(address)
    while i < n:
        ch = address[i]
        if depth == 0:
            if ch == "[":
                depth = 1
            else:
                out.append(ch)
        elif in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return "".join(out)


def drift_addresses(plan: dict[str, Any], *, normalise: bool = False) -> list[str]:
    raw = [str(d.get("address", "")) for d in plan.get("resource_drift") or []]
    return sorted({normalise_address(a) for a in raw}) if normalise else sorted(raw)


def changed_output_names(plan: dict[str, Any]) -> list[str]:
    names = []
    for name, ch in (plan.get("output_changes") or {}).items():
        actions = (ch or {}).get("actions") or []
        if actions not in (["no-op"], []):
            names.append(str(name))
    return sorted(names)
