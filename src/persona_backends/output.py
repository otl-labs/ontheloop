"""Check a terminal turn against the contract's output_schema or the failure envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from src.persona_backends import verdict
from src.persona_backends.contract import LoadedContract

_ERROR_CAP = 20


@dataclass(frozen=True)
class Checked:
    value: dict[str, Any] | None
    errors: list[str]


def json_path(path: Any) -> str:
    return "$" + "".join(f"[{part!r}]" if isinstance(part, str) else f"[{part}]" for part in path)


def schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    found = sorted(f"{json_path(err.absolute_path)}: {err.message}" for err in Draft202012Validator(schema).iter_errors(value))
    return found[:_ERROR_CAP]


def _failure_errors(value: dict[str, Any], loaded: LoadedContract) -> list[str]:
    errors: list[str] = []
    if set(value) != {"outcome", "failure", "detail"}:
        errors.append("$: a failure value has exactly the keys outcome, failure and detail")
    if value.get("failure") not in loaded.failure_ids:
        errors.append("$['failure']: not a failure id the contract lists")
    if not isinstance(value.get("detail"), str):
        errors.append("$['detail']: must be a string")
    return errors


def check(content: str, finish_reason: str | None, loaded: LoadedContract) -> Checked:
    if finish_reason == "length":
        return Checked(None, ["$: the reply was cut off at the token limit"])
    if not (content or "").strip():
        return Checked(None, ["$: the reply is blank"])
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return Checked(None, [f"$: not valid JSON ({exc.msg})"])
    if not isinstance(value, dict):
        return Checked(None, ["$: not a JSON object"])
    if value.get("outcome") == "failure":
        errors = _failure_errors(value, loaded)
    else:
        errors = schema_errors(loaded.output_schema, value)
        if not errors and loaded.verdict_consistency is not None:
            errors = verdict.verdict_errors(loaded.verdict_consistency, value)
    return Checked(None if errors else value, errors)


def repair_message(errors: list[str]) -> str:
    lines = ["Your last reply is not a valid result:", *errors]
    lines.append("Reply with one JSON object only: the result, or a failure the contract lists.")
    return "\n".join(lines)


def failure(failure_id: str, detail: str) -> dict[str, Any]:
    return {"outcome": "failure", "failure": failure_id, "detail": detail}
