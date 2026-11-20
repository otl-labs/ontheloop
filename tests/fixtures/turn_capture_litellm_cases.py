"""Golden corpus builders for the litellm producer: each case runs the real backend with a scripted fake transport.

The litellm twins of turn_capture_corpus.case_names and load_case. Stdlib plus the repo's own packages; no test_ prefix.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.llm.litellm_transport import TransportResult
from scripts.llm.litellm_wire import Attempt, CallRecord
from scripts.run_persona import _EventsFile
from src.persona_backends import RunAborted, run_persona
from src.persona_backends.events import write_atomic
from tests.persona_backends import fakes

LITELLM_GOLDEN_DIR = Path(__file__).parent / "turn_capture" / "golden" / "litellm"
T0 = 1_767_225_600
FB = "warm_fetched_escape_hatch"
HOST_RUN = json.dumps(
    {
        "agent": "ci_rca",
        "content_policy": "full",
        "host": {
            "checkout_sha": "c" * 40,
            "event_name": "workflow_run",
            "job": "rca",
            "kind": "gha",
            "ref": "refs/heads/main",
            "repository": "example/repo",
            "retry_ordinal": 1,
            "run_attempt": "1",
            "run_id": "123456789",
            "sha": "a" * 40,
            "workflow_path": ".github/workflows/ci-rca.yml",
        },
        "project_ref": "example/project",
        "schema_version": 1,
    },
    sort_keys=True,
)
READ = {"blob": "notes", "mode": "lines"}


def litellm_case_names() -> list[str]:
    return list(json.loads((LITELLM_GOLDEN_DIR / "index.json").read_text(encoding="utf-8"))["cases"])


def load_litellm_case(name: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    body = json.loads((LITELLM_GOLDEN_DIR / name).read_text(encoding="utf-8"))
    return body["files"], body["expected_rows"]


def call(role: str = "primary", outcome: str = "ok", n: int = 1, at: int = 100, provider: str = "deepseek") -> Attempt:
    return Attempt(role, "deepseek/deepseek-flash", n, outcome, 40, provider, "metered_marginal", T0 * 1000 + at)


def rec(role: str = "primary", n: int = 1, at: int = 100, **over: Any) -> CallRecord:
    base: dict[str, Any] = {
        "persona": "fixture_persona",
        "role": role,
        "provider": "deepseek",
        "requested_model": "deepseek/deepseek-flash",
        "model": "deepseek-v4-flash",
        "billing_shape": "metered_marginal",
        "thinking": "enabled",
        "reasoning_effort": "high",
        "tokens_input": 100,
        "tokens_output": 50,
        "tokens_cache_read": 20,
        "tokens_cache_creation": 0,
        "reasoning_tokens": 30,
        "attempt": n,
        "latency_ms": 40,
        "response_id": f"resp-{role}-{n}-{at}",
        "started_at_ms": T0 * 1000 + at,
        "responded_at_ms": T0 * 1000 + at + 40,
    }
    base.update(over)
    return CallRecord(**base)


def result(record: CallRecord, attempts: tuple[Attempt, ...], message: dict[str, Any] | None = None, **kw: Any) -> Any:
    content = kw.pop("content", fakes.VALID)
    tool_calls = kw.pop("tool_calls", [])
    return TransportResult(
        content,
        tool_calls,
        message or {"role": "assistant", "content": content},
        kw.pop("finish_reason", "tool_calls" if tool_calls else "stop"),
        record,
        attempts,
        kw.pop("failed_over", False),
        kw.pop("probe_record", None),
    )


def tool_result(record: CallRecord, attempts: tuple[Attempt, ...], message: dict[str, Any]) -> Any:
    calls = [{"id": "call_1", "type": "function", "function": {"name": "read_input", "arguments": json.dumps(READ)}}]
    return result(record, attempts, {**message, "tool_calls": calls}, content="", tool_calls=calls)


class Wall:
    def __init__(self) -> None:
        self.now = float(T0)

    def __call__(self) -> float:
        self.now += 0.05
        return self.now


def _run(script: list[Any], *, cut: bool = False) -> tuple[dict[str, Any], Path]:
    folder = Path(tempfile.mkdtemp())
    sink = _EventsFile(folder / "events.json")
    transport = fakes.CapturingTransport(script)

    async def go() -> None:
        try:
            await run_persona(
                fakes.FIXTURE,
                fakes.good_input(),
                None,
                "litellm",
                transport=transport,
                on_event=sink,
                clock=fakes.FakeClock(),
                wall=Wall(),
            )
        except RunAborted:
            pass

    if cut:
        real = asyncio.wait_for

        async def short(coro: Any, _limit: Any) -> Any:
            return await real(coro, 0.01)

        with mock.patch.object(asyncio, "wait_for", short):
            asyncio.run(go())
    else:
        asyncio.run(go())
    return json.loads((folder / "events.json").read_text(encoding="utf-8")), folder


def _bodies(events: dict[str, Any]) -> dict[str, str]:
    events["header"]["contract_path"] = "tests/fixtures/persona_backends/fixture_persona.yaml"
    return {"events.json": json.dumps(events, sort_keys=True, indent=2) + "\n", "host-run.json": HOST_RUN}


def _single_call() -> dict[str, str]:
    message = {"role": "assistant", "content": fakes.VALID, "reasoning_content": "weighing the notes"}
    events, _ = _run([result(rec(), (call(),), message)])
    return _bodies(events)


def _two_turn() -> tuple[Any, Any]:
    anthropic: dict[str, Any] = {
        "provider": "anthropic",
        "requested_model": "anthropic/claude-opus-5-5",
        "model": "claude-opus-5-5",
        "response_id": None,
        "tokens_cache_read": 0,
        "tokens_cache_creation": 64,
        "cache_creation_1h_tokens": 16,
        "cache_creation_5m_tokens": 48,
        "reasoning_tokens": None,
    }
    thinking = {"type": "thinking", "thinking": "a short summary of the thinking", "signature": "sig"}
    first = tool_result(
        rec(**anthropic), (call(provider="anthropic"),), {"role": "assistant", "content": "", "thinking_blocks": [thinking]}
    )
    second = result(rec(n=1, at=300, **anthropic), (call(provider="anthropic", at=300),))
    return first, second


def _anthropic_thinking() -> dict[str, str]:
    events, _ = _run(list(_two_turn()))
    return _bodies(events)


def _fail_over_probe() -> dict[str, str]:
    attempts = (call(outcome="outage", at=100), call(outcome="outage", n=2, at=200), call(FB, at=400))
    probe = rec(FB, at=300, response_id=None)
    events, _ = _run([result(rec(FB, at=400), attempts, failed_over=True, probe_record=probe)])
    return _bodies(events)


def _deadline_cancelled() -> dict[str, str]:
    flight = dataclasses.replace(call(n=2, outcome="cancelled", at=200), latency_ms=0)
    events, _ = _run([fakes.Fill((call(outcome="outage", at=100),), flight)], cut=True)
    return _bodies(events)


def _repair_loss_markers() -> dict[str, str]:
    two = (call(outcome="malformed_output", at=100), call(outcome="empty_output", n=2, at=150))
    script = [fakes.error("empty_output", two), result(rec(at=300), (call(at=300),))]
    events, _ = _run(script)
    return _bodies(events)


def _aborted_transport() -> dict[str, str]:
    events, _ = _run([RuntimeError("boom")])
    return _bodies(events)


def _partial_no_end() -> dict[str, str]:
    events, folder = _run(list(_two_turn()))
    write_atomic(folder / "partial.json", events["header"], events["turns"][:1], None)
    return _bodies(json.loads((folder / "partial.json").read_text(encoding="utf-8")))


LITELLM_BUILDERS = {
    "single_call.json": _single_call,
    "anthropic_thinking.json": _anthropic_thinking,
    "fail_over_probe.json": _fail_over_probe,
    "deadline_cancelled.json": _deadline_cancelled,
    "repair_loss_markers.json": _repair_loss_markers,
    "aborted_transport.json": _aborted_transport,
    "partial_no_end.json": _partial_no_end,
}
