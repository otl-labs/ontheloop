"""LiteLLM transport for the verdict personas (T4.32 c1 model leg, plan 2 of 2; closes T4.17 c5).

complete and acomplete send one persona's messages to the model its tier role resolves to through
scripts.llm.tier_selection over the two contracts, read at call time, so a model or provider swap is a contract edit
alone. Thinking and effort fields, request retries' bound, outcome classes and the per-call record come from
scripts.llm.litellm_wire; the cold-start assertion and fail-over gates come from scripts.llm.litellm_cold_start. This
module re-exports their public names so callers import one module.

Retries: an outage is retried up to max_attempts calls per role in total, sleeping 1 s then 2 s. Credit exhaustion,
provider errors, blank and malformed responses are never retried or failed over. LiteLLM 1.106.2 itself raises APIError
500 for a body with no choices key and APIConnectionError 500 for an Anthropic body with no model, so those reach the
transport as outages and are retried; this is accepted.

Fail-over is off by default and applies only to role primary, on a run's first call. When it happens the record's role
names the fallback, and the caller passes role=warm_fetched_escape_hatch for the rest of the run so one transcript never
mixes the two providers. No AWS or SSM call and no AWS identity (Decision 212 clause 3).
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.llm import litellm_cold_start as cold_start
from scripts.llm import tier_selection
from scripts.llm.litellm_cold_start import (
    ColdStartResult,
    aensure_asserted,
    afallback_gate,
    cold_start_check,
    ensure_asserted,
    fallback_gate,
    refuse_end_user_fail_over,
)
from scripts.llm.litellm_wire import (
    Attempt,
    CallRecord,
    TransportError,
    classify,
    load_litellm,
    read_response,
    read_secret,
    request_kwargs,
)

_PRIMARY = "primary"
_FALLBACK = "warm_fetched_escape_hatch"
_sleep = time.sleep
_asleep = asyncio.sleep
_wall = time.time


@dataclass(frozen=True)
class TransportResult:
    content: str
    tool_calls: list[dict[str, Any]]
    message: dict[str, Any]
    finish_reason: str | None
    record: CallRecord
    attempts: tuple[Attempt, ...]
    failed_over: bool
    probe_record: CallRecord | None = None


@dataclass
class _Call:
    persona: str
    messages: list[dict[str, Any]]
    max_tokens: int
    tools: list[dict[str, Any]] | None
    tool_choice: Any
    response_format: Any
    timeout: float
    credential_holder: str
    max_attempts: int
    root: Path | None
    capture: Any = None


def _forward(capture: Any) -> dict[str, Any]:
    return {} if capture is None else {"capture": capture}


def _begin(call: _Call, selection: tier_selection.Selection, role: str, attempt: int, started_ms: int) -> None:
    if call.capture is not None:
        call.capture.in_flight = _attempt(selection, role, attempt, "cancelled", 0, started_ms)


def _settle(call: _Call, tried: list[Attempt]) -> None:
    if call.capture is not None:
        call.capture.in_flight = None
        call.capture.attempts.append(tried[-1])


def _prepare(call: _Call, role: str) -> tuple[tier_selection.Selection, dict[str, Any]]:
    inference_provider, config = tier_selection.load(call.root)
    selection = tier_selection.resolve(
        call.persona,
        role,
        inference_provider=inference_provider,
        project_configuration=config,
        credential_holder=call.credential_holder,
    )
    kwargs = request_kwargs(
        selection,
        call.messages,
        api_key=read_secret(selection.env_name),
        max_tokens=call.max_tokens,
        tools=call.tools,
        tool_choice=call.tool_choice,
        response_format=call.response_format,
        timeout=call.timeout,
    )
    return selection, kwargs


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _wall_ms() -> int:
    return int(_wall() * 1000)


def _attempt(
    selection: tier_selection.Selection, role: str, attempt: int, outcome: str, latency: int, started_ms: int
) -> Attempt:
    return Attempt(role, selection.model, attempt, outcome, latency, selection.provider, selection.billing_shape, started_ms)


def _failure(
    call: _Call,
    selection: tier_selection.Selection,
    exc: BaseException,
    attempt: int,
    started: float,
    started_ms: int,
    tried: list[Attempt],
) -> tuple[str, TransportError]:
    outcome = classify(exc)
    status = getattr(exc, "status_code", None)
    tried.append(_attempt(selection, selection.role, attempt, outcome, _elapsed(started), started_ms))
    _settle(call, tried)
    error = TransportError(
        outcome,
        f"{selection.role} call failed as {outcome} after {len(tried)} attempt(s) (status {status})",
        role=selection.role,
        attempts=tuple(tried),
    )
    error.__cause__ = exc
    return outcome, error


def _checked(
    selection: tier_selection.Selection,
    response: Any,
    call: _Call,
    role: str,
    attempt: int,
    started: float,
    started_ms: int,
    tried: list[Attempt],
) -> TransportResult:
    latency = _elapsed(started)
    choices = getattr(response, "choices", None)
    if not choices:
        outcome = "malformed_output"
    else:
        message = choices[0].message
        blank = not (message.content or "").strip() and not getattr(message, "tool_calls", None)
        outcome = "empty_output" if blank else "ok"
    tried.append(_attempt(selection, role, attempt, outcome, latency, started_ms))
    _settle(call, tried)
    if outcome != "ok":
        raise TransportError(outcome, f"{role} returned {outcome}", role=role, attempts=tuple(tried))
    content, tool_calls, message_dict, record = read_response(
        response,
        selection=selection,
        persona=call.persona,
        role=role,
        attempt=attempt,
        latency_ms=latency,
        started_at_ms=started_ms,
        responded_at_ms=_wall_ms(),
    )
    if call.capture is not None:
        call.capture.record = record
    return TransportResult(content, tool_calls, message_dict, response.choices[0].finish_reason, record, tuple(tried), False)


def _run_role(call: _Call, role: str) -> TransportResult:
    selection, kwargs = _prepare(call, role)
    litellm = load_litellm()
    tried: list[Attempt] = []
    attempt = 0
    while True:
        attempt += 1
        started, started_ms = time.monotonic(), _wall_ms()
        _begin(call, selection, role, attempt, started_ms)
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:  # intentionally broad: classified below, chained, never swallowed
            outcome, error = _failure(call, selection, exc, attempt, started, started_ms, tried)
            if outcome == "outage" and attempt < call.max_attempts:
                _sleep(2 ** (attempt - 1))
                continue
            raise error from exc
        return _checked(selection, response, call, role, attempt, started, started_ms, tried)


async def _arun_role(call: _Call, role: str) -> TransportResult:
    selection, kwargs = _prepare(call, role)
    litellm = load_litellm()
    tried: list[Attempt] = []
    attempt = 0
    while True:
        attempt += 1
        started, started_ms = time.monotonic(), _wall_ms()
        _begin(call, selection, role, attempt, started_ms)
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # intentionally broad, as in _run_role
            cold_start.reraise_if_cancelling(exc)
            outcome, error = _failure(call, selection, exc, attempt, started, started_ms, tried)
            if outcome == "outage" and attempt < call.max_attempts:
                await _asleep(2 ** (attempt - 1))
                continue
            raise error from exc
        return _checked(selection, response, call, role, attempt, started, started_ms, tried)


def _merged(result: TransportResult, prior: tuple[Attempt, ...], probe: CallRecord | None) -> TransportResult:
    return TransportResult(
        result.content,
        result.tool_calls,
        result.message,
        result.finish_reason,
        result.record,
        prior + result.attempts,
        True,
        probe,
    )


def _refuse_zero_attempts(max_attempts: int) -> None:
    if max_attempts < 1:
        raise TransportError("refused", "max_attempts must be at least 1", tag="max_attempts_below_one")


def _note_probe(capture: Any, probe: CallRecord | None) -> None:
    if capture is not None and probe is not None:
        capture.probe_record = probe


def _probe_record(result: ColdStartResult) -> CallRecord | None:
    return result.record if result.fresh else None


def complete(
    persona: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    response_format: Any = None,
    timeout: float = 600.0,
    role: str = _PRIMARY,
    fail_over: bool = False,
    credential_holder: str = "on_the_loop",
    max_attempts: int = 3,
    root: Path | None = None,
    capture: Any = None,
) -> TransportResult:
    call = _Call(
        persona,
        messages,
        max_tokens,
        tools,
        tool_choice,
        response_format,
        timeout,
        credential_holder,
        max_attempts,
        root,
        capture,
    )
    if capture is not None:
        capture.filled = True
    _refuse_zero_attempts(max_attempts)
    refuse_end_user_fail_over(fail_over, credential_holder)
    probe = None
    if role == _FALLBACK:
        probe = _probe_record(ensure_asserted(role, credential_holder=credential_holder, root=root, **_forward(capture)))
        _note_probe(capture, probe)
    try:
        return dataclasses.replace(_run_role(call, role), probe_record=probe)
    except TransportError as exc:
        if not (fail_over and role == _PRIMARY and exc.kind == "outage"):
            exc.probe_record = probe
            raise
        gate = fallback_gate(messages, exc, credential_holder=credential_holder, root=root, **_forward(capture))
        probe = _probe_record(gate)
        _note_probe(capture, probe)
        try:
            return _merged(_run_role(call, _FALLBACK), exc.attempts, probe)
        except TransportError as second:
            second.attempts = exc.attempts + second.attempts
            second.probe_record = probe
            raise


async def acomplete(
    persona: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    response_format: Any = None,
    timeout: float = 600.0,  # noqa: ASYNC109 - forwarded to litellm, same signature as complete
    role: str = _PRIMARY,
    fail_over: bool = False,
    credential_holder: str = "on_the_loop",
    max_attempts: int = 3,
    root: Path | None = None,
    capture: Any = None,
) -> TransportResult:
    call = _Call(
        persona,
        messages,
        max_tokens,
        tools,
        tool_choice,
        response_format,
        timeout,
        credential_holder,
        max_attempts,
        root,
        capture,
    )
    if capture is not None:
        capture.filled = True
    _refuse_zero_attempts(max_attempts)
    refuse_end_user_fail_over(fail_over, credential_holder)
    probe = None
    if role == _FALLBACK:
        probe = _probe_record(
            await aensure_asserted(role, credential_holder=credential_holder, root=root, **_forward(capture))
        )
        _note_probe(capture, probe)
    try:
        return dataclasses.replace(await _arun_role(call, role), probe_record=probe)
    except TransportError as exc:
        if not (fail_over and role == _PRIMARY and exc.kind == "outage"):
            exc.probe_record = probe
            raise
        gate = await afallback_gate(messages, exc, credential_holder=credential_holder, root=root, **_forward(capture))
        probe = _probe_record(gate)
        _note_probe(capture, probe)
        try:
            return _merged(await _arun_role(call, _FALLBACK), exc.attempts, probe)
        except TransportError as second:
            second.attempts = exc.attempts + second.attempts
            second.probe_record = probe
            raise


__all__ = [
    "Attempt",
    "CallRecord",
    "ColdStartResult",
    "TransportError",
    "TransportResult",
    "acomplete",
    "cold_start_check",
    "complete",
    "read_secret",
]
