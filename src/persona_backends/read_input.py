"""The read_input tool: line-range and literal-search reads of one named blob of the run's materialised input."""

from __future__ import annotations

import json
from typing import Any

READ_CAP_BYTES = 65536
SEARCH_CAP = 200


def tool_spec(blob_names: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_input",
            "description": "Read one named input blob by line range, or search it for a literal substring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blob": {"type": "string", "enum": list(blob_names)},
                    "mode": {"type": "string", "enum": ["lines", "search"]},
                    "start": {"type": "integer", "minimum": 1},
                    "end": {"type": "integer", "minimum": 1},
                    "query": {"type": "string"},
                },
                "required": ["blob", "mode"],
            },
        },
    }


def blob_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def _int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be an integer of at least 1")
    return value


def _lines(lines: list[str], arguments: dict[str, Any]) -> list[str]:
    total = len(lines)
    start = _int(arguments, "start", 1)
    end = min(_int(arguments, "end", max(total, 1)), total)
    out: list[str] = []
    used = 0
    for number in range(start, end + 1):
        row = f"{number}: {lines[number - 1]}"
        used += len(row.encode("utf-8")) + 1
        if used > READ_CAP_BYTES:
            out.append(f"[truncated: next line {number} of {total}]")
            break
        out.append(row)
    return out


def _search(lines: list[str], arguments: dict[str, Any]) -> list[str]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    hits = [f"{number}: {line}" for number, line in enumerate(lines, 1) if query in line]
    out = hits[:SEARCH_CAP]
    if len(hits) > SEARCH_CAP:
        out.append(f"[{len(hits) - SEARCH_CAP} more matches]")
    return out


def serve(arguments_json: str, blobs: dict[str, Any]) -> str:
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return f"error: arguments are not valid JSON ({exc.msg})"
    if not isinstance(arguments, dict):
        return "error: arguments must be a JSON object"
    name = arguments.get("blob")
    if not isinstance(name, str) or name not in blobs:
        return f"error: blob {name!r} is not declared"
    mode = arguments.get("mode")
    lines = blob_text(blobs[name]).split("\n")
    try:
        if mode == "lines":
            body = _lines(lines, arguments)
        elif mode == "search":
            body = _search(lines, arguments)
        else:
            return "error: mode must be lines or search"
    except ValueError as exc:
        return f"error: {exc}"
    return "\n".join([*body, f"[{name}: {len(lines)} lines]"])
