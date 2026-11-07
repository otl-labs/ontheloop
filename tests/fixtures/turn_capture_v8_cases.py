"""Builders for the four parser-v8 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case: a claude -p root with a host-run sidecar
(<session id>/host-run.json) beside it. Timestamps are the seconds after the corpus epoch; every sha is a repeated hex digit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    api_error,
    assistant,
    make_files,
    pin_attachment,
    pin_facts_text,
    prompt,
    text_block,
    thinking_block,
    tool_result,
    tool_use_block,
    witness,
)

TRIGGER_SHA = "a" * 40
CHECKOUT_SHA = "c" * 40


def sidecar(
    *, policy: str = "full", ordinal: int = 1, checkout: str | None = None, attempt: str = "1", agent: str = "ci_rca"
) -> str:
    host: dict[str, Any] = {
        "kind": "gha",
        "run_id": "123456789",
        "run_attempt": attempt,
        "job": "rca",
        "retry_ordinal": ordinal,
        "event_name": "workflow_run",
        "repository": "example/repo",
        "workflow_path": ".github/workflows/ci-rca.yml",
        "ref": "refs/heads/main",
        "sha": TRIGGER_SHA,
    }
    if checkout is not None:
        host["checkout_sha"] = checkout
    body = {"schema_version": 1, "project_ref": PROJECT_REF, "agent": agent, "content_policy": policy, "host": host}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _with_sidecar(files: dict[str, str], body: str) -> dict[str, str]:
    return {**files, f"{SID}/host-run.json": body}


def ci_rca_hook_pin_and_host_run_files() -> dict[str, str]:
    """A two-turn claude -p root whose repo pin hook ran (checkout facts) and whose sidecar carries a different checkout."""
    root = [
        pin_attachment("s1", 0.5, PROJECT_REF, pin_facts_text()),
        prompt("u1", 1, "p1", "diagnose the failing run"),
        assistant("a1", 2, "m1", [text_block("looking")]),
        witness("w1", 3),
        prompt("u2", 5, "p2", "summarise"),
        assistant("a2", 6, "m2", [text_block("done")]),
        witness("w2", 7),
    ]
    return _with_sidecar(make_files(root), sidecar(checkout=CHECKOUT_SHA))


def host_run_only_withheld_files() -> dict[str, str]:
    """A hookless root (no pin, plan-review shape) with a withheld-policy sidecar: every content row is an omission."""
    root = [
        prompt("u1", 1, "p1", "review the plan"),
        assistant("a1", 2, "m1", [thinking_block("weighing"), tool_use_block("tu1", "Read", {"file_path": "/w/plan.yaml"})]),
        tool_result("r1", 3, "tu1", "plan body", pid="p1"),
        assistant("a2", 4, "m2", [text_block("PROCEED")]),
    ]
    return _with_sidecar(make_files(root), sidecar(policy="withheld", checkout=CHECKOUT_SHA, agent="code-review"))


def host_run_malformed_files() -> dict[str, str]:
    """A hook-pinned root whose sidecar is not valid: rows as without a sidecar, host_run_malformed counted."""
    root = [
        pin_attachment("s1", 0.5, PROJECT_REF, pin_facts_text()),
        prompt("u1", 1, "p1", "hello"),
        assistant("a1", 2, "m1", [text_block("hi")]),
        witness("w1", 3),
    ]
    return _with_sidecar(make_files(root), '{"schema_version":1,"host":{"kind":"gha"}')


def host_run_retry_attempts_files() -> dict[str, str]:
    """A retry's second session (retry_ordinal 2) that ended on an API error."""
    root = [
        pin_attachment("s1", 0.5, PROJECT_REF, pin_facts_text()),
        prompt("u1", 1, "p1", "diagnose again"),
        api_error("e1", 2),
    ]
    return _with_sidecar(make_files(root), sidecar(ordinal=2, attempt="2"))


V8_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "ci_rca_hook_pin_and_host_run.json": ci_rca_hook_pin_and_host_run_files,
    "host_run_only_withheld.json": host_run_only_withheld_files,
    "host_run_malformed.json": host_run_malformed_files,
    "host_run_retry_attempts.json": host_run_retry_attempts_files,
}
