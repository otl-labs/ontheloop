"""The one usage mapping for a LiteLLM response (rec-4028 plan 3a; Decision 199).

USAGE_TABLE mirrors docs/contracts/inference-provider.yaml usage_mapping.rows key for key (a test holds them equal);
this package is stdlib only and cannot read the contract. map_usage interprets the table with a small restricted parser,
never eval or exec, and raises ValueError on a row form it does not know when first used, never at import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

USAGE_TABLE: dict[str, dict[str, Any]] = {
    "deepseek": {
        "tokens_input": "prompt_cache_miss_tokens",
        "tokens_cache_read": "prompt_cache_hit_tokens",
        "tokens_cache_creation": 0,
        "tokens_output": "completion_tokens",
        "cache_creation_split": "none",
        "reasoning_tokens": "completion_tokens_details.reasoning_tokens",
        "guaranteed": ["prompt_cache_miss_tokens", "prompt_cache_hit_tokens", "completion_tokens"],
        "response_id": "provider",
    },
    "anthropic": {
        "tokens_input": "prompt_tokens - cache_read_input_tokens - cache_creation_input_tokens",
        "tokens_cache_read": "cache_read_input_tokens",
        "tokens_cache_creation": "cache_creation_input_tokens",
        "tokens_output": "completion_tokens",
        "cache_creation_split": (
            "prompt_tokens_details.cache_creation_token_details.ephemeral_1h_input_tokens and ephemeral_5m_input_tokens"
        ),
        "reasoning_tokens": "none",
        "guaranteed": ["prompt_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "completion_tokens"],
        "response_id": "not_recorded",
    },
    "default": {
        "tokens_input": "prompt_tokens - prompt_tokens_details.cached_tokens",
        "tokens_cache_read": "prompt_tokens_details.cached_tokens",
        "tokens_cache_creation": 0,
        "tokens_output": "completion_tokens",
        "cache_creation_split": "none",
        "reasoning_tokens": "none",
        "guaranteed": ["prompt_tokens", "completion_tokens"],
        "response_id": "not_recorded",
    },
}

_CLASSES = ("tokens_input", "tokens_cache_read", "tokens_cache_creation", "tokens_output")
_PATH = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*$")
_SPLIT = re.compile(r"^(?P<path>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*) and (?P<other>[a-z_][a-z0-9_]*)$")
_MISSING = object()


@dataclass(frozen=True)
class UsageColumns:
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_creation: int
    cache_creation_1h_tokens: int | None
    cache_creation_5m_tokens: int | None
    reasoning_tokens: int | None
    usage_absent: tuple[str, ...]
    response_id: str


def row_for(prefix: str) -> dict[str, Any]:
    return USAGE_TABLE.get(prefix, USAGE_TABLE["default"])


def _bad(field: str, form: Any) -> ValueError:
    return ValueError(f"usage_mapping row field {field} has an unknown form: {form!r}")


def _terms(field: str, form: Any) -> list[str] | int:
    if isinstance(form, int) and not isinstance(form, bool):
        if form == 0:
            return 0
        raise _bad(field, form)
    if not isinstance(form, str):
        raise _bad(field, form)
    terms = form.split(" - ")
    if not all(_PATH.match(term) for term in terms):
        raise _bad(field, form)
    return terms


def _dig(usage: dict[str, Any], path: str) -> Any:
    node: Any = usage
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node if isinstance(node, int) and not isinstance(node, bool) else _MISSING


def _all_zero(usage: dict[str, Any]) -> bool:
    return all(_dig(usage, key) == 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens"))


def _class_value(field: str, row: dict[str, Any], usage: dict[str, Any]) -> tuple[int, bool]:
    terms = _terms(field, row[field])
    if terms == 0:
        return 0, False
    values = [_dig(usage, term) for term in terms]  # type: ignore[union-attr]
    guaranteed = set(row["guaranteed"])
    if any(v is _MISSING and t in guaranteed for v, t in zip(values, terms, strict=True)):  # type: ignore[arg-type]
        return 0, True
    numbers = [0 if v is _MISSING else v for v in values]
    result = numbers[0] - sum(numbers[1:])
    return (0, True) if result < 0 else (result, False)


def _reasoning(row: dict[str, Any], usage: dict[str, Any]) -> int | None:
    form = row["reasoning_tokens"]
    if form == "none":
        return None
    if not isinstance(form, str) or not _PATH.match(form):
        raise _bad("reasoning_tokens", form)
    value = _dig(usage, form)
    return None if value is _MISSING else value


def _split(row: dict[str, Any], usage: dict[str, Any], creation: int) -> tuple[int | None, int | None]:
    form = row["cache_creation_split"]
    if form == "none":
        return None, None
    match = _SPLIT.match(form) if isinstance(form, str) else None
    if match is None:
        raise _bad("cache_creation_split", form)
    path = match["path"]
    parent = path.rsplit(".", 1)[0] if "." in path else ""
    other = f"{parent}.{match['other']}" if parent else match["other"]
    out: list[int | None] = []
    for source in (path, other):
        value = _dig(usage, source)
        out.append(None if value is _MISSING or value > creation else value)
    return out[0], out[1]


def map_usage(prefix: str, usage: dict[str, Any] | None) -> UsageColumns:
    row = row_for(prefix)
    response_id = row["response_id"]
    if response_id not in ("provider", "not_recorded"):
        raise _bad("response_id", response_id)
    if usage is None or _all_zero(usage):
        absent = tuple(sorted(name for name in _CLASSES if _terms(name, row[name]) != 0))
        return UsageColumns(0, 0, 0, 0, None, None, None, absent, response_id)
    absent_names: list[str] = []
    values: dict[str, int] = {}
    for name in _CLASSES:
        values[name], named = _class_value(name, row, usage)
        if named:
            absent_names.append(name)
    one_hour, five_min = _split(row, usage, values["tokens_cache_creation"])
    return UsageColumns(
        tokens_input=values["tokens_input"],
        tokens_output=values["tokens_output"],
        tokens_cache_read=values["tokens_cache_read"],
        tokens_cache_creation=values["tokens_cache_creation"],
        cache_creation_1h_tokens=one_hour,
        cache_creation_5m_tokens=five_min,
        reasoning_tokens=_reasoning(row, usage),
        usage_absent=tuple(sorted(absent_names)),
        response_id=response_id,
    )
