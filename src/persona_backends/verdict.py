"""Evaluate a contract's verdict_consistency rule against a result value; never raises on any value."""

from __future__ import annotations

from typing import Any


def _elements(value: Any, path: str) -> list[Any]:
    node = value
    for step in path.split("."):
        if not isinstance(node, dict) or step not in node:
            return []
        node = node[step]
    return node if isinstance(node, list) else []


def _holding(cond: dict[str, Any], value: Any) -> str | None:
    path = cond["array"]
    field = cond.get("field")
    for index, element in enumerate(_elements(value, path)):
        if field is None:
            return f"{path}[{index}] is present"
        if isinstance(element, dict) and element.get(field) in cond["in"]:
            return f"{path}[{index}].{field} is {element[field]}"
    return None


def expected(rule: dict[str, Any], value: Any) -> tuple[str, str]:
    for case in rule["cases"]:
        for cond in case["any"]:
            reason = _holding(cond, value)
            if reason is not None:
                return case["verdict"], reason
    return rule["otherwise"], "no case holds"


def verdict_errors(rule: dict[str, Any], value: Any) -> list[str]:
    want, reason = expected(rule, value)
    actual = value.get("verdict") if isinstance(value, dict) else None
    if actual == want:
        return []
    shown = "absent" if actual is None else str(actual)
    message = f"$['verdict']: is {shown}, but the verdict rule gives {want} ({reason}); "
    return [message + "change the verdict, never the findings, to follow the rule"]
