"""Run record types for the litellm persona backend: plain dicts, written atomically to one events file.

No telemetry row shape lives here; the producer plan (rec-4028) maps this file (Decision 221 cl.7).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def input_sha256(materialised_input: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(materialised_input).encode("utf-8")).hexdigest()


def tools_sha256(tools: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(tools).encode("utf-8")).hexdigest()


def header(
    contract_path: str,
    persona: str,
    contract_version: int,
    prompt_sha256: str,
    input_hash: str,
    started_at_ms: int,
    tools_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "backend": "litellm",
        "contract_path": contract_path,
        "persona": persona,
        "contract_version": contract_version,
        "rendered_prompt_sha256": prompt_sha256,
        "input_sha256": input_hash,
        "started_at_ms": started_at_ms,
        "tools_sha256": tools_sha256,
    }


def attempt_dict(attempt: Any) -> dict[str, Any]:
    return {
        "role": attempt.role,
        "model": attempt.model,
        "attempt": attempt.attempt,
        "outcome": attempt.outcome,
        "latency_ms": attempt.latency_ms,
        "provider": attempt.provider,
        "billing_shape": attempt.billing_shape,
        "started_at_ms": attempt.started_at_ms,
    }


def turn_record(
    *,
    turn: int,
    role: str,
    attempts: list[dict[str, Any]],
    attempts_known: bool,
    probe_turn: int,
    record: dict[str, Any] | None,
    failed_over: bool,
    finish_reason: str | None,
    tool_calls: list[str],
    tool_result_bytes: list[int],
    kind: str,
    revision: int,
    message: dict[str, Any] | None,
    started_at_ms: int,
    ended_at_ms: int,
    probe_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "turn": turn,
        "role": role,
        "attempts": attempts,
        "attempts_known": attempts_known,
        "probe_turn": probe_turn,
        "record": record,
        "failed_over": failed_over,
        "finish_reason": finish_reason,
        "tool_calls": tool_calls,
        "tool_result_bytes": tool_result_bytes,
        "kind": kind,
        "revision": revision,
        "message": message,
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "probe_record": probe_record,
    }


def end_record(
    outcome: str,
    failure: str | None,
    turns_used: int,
    revisions_used: int,
    ended_at_ms: int,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "outcome": outcome,
        "failure": failure,
        "turns_used": turns_used,
        "revisions_used": revisions_used,
        "ended_at_ms": ended_at_ms,
    }
    if error is not None:
        record["error"] = error
    return record


def write_atomic(path: Path, head: dict[str, Any], turns: list[dict[str, Any]], end: dict[str, Any] | None) -> None:
    document = {"header": head, "turns": turns, "end": end}
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
