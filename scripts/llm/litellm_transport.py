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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class TransportResult:
    content: str
    tool_calls: list[dict[str, Any]]
    message: dict[str, Any]
    finish_reason: str | None
    record: CallRecord
    attempts: tuple[Attempt, ...]
    failed_over: bool


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


def _failure(
    selection: tier_selection.Selection, exc: BaseException, attempt: int, started: float, tried: list[Attempt]
) -> tuple[str, TransportError]:
    outcome = classify(exc)
    status = getattr(exc, "status_code", None)
    tried.append(Attempt(selection.role, selection.model, attempt, outcome, _elapsed(started)))
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
    tried.append(Attempt(role, selection.model, attempt, outcome, latency))
    if outcome != "ok":
        raise TransportError(outcome, f"{role} returned {outcome}", role=role, attempts=tuple(tried))
    content, tool_calls, message_dict, record = read_response(
        response, selection=selection, persona=call.persona, role=role, attempt=attempt, latency_ms=latency
    )
    return TransportResult(content, tool_calls, message_dict, response.choices[0].finish_reason, record, tuple(tried), False)


def _run_role(call: _Call, role: str) -> TransportResult:
    selection, kwargs = _prepare(call, role)
    litellm = load_litellm()
    tried: list[Attempt] = []
    for attempt in range(1, call.max_attempts + 1):
        started = time.monotonic()
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:  # intentionally broad: classified below, chained, never swallowed
            outcome, error = _failure(selection, exc, attempt, started, tried)
            if outcome == "outage" and attempt < call.max_attempts:
                _sleep(2 ** (attempt - 1))
                continue
            raise error from exc
        return _checked(selection, response, call, role, attempt, started, tried)
    raise AssertionError("max_attempts must be at least 1")  # pragma: no cover


async def _arun_role(call: _Call, role: str) -> TransportResult:
    selection, kwargs = _prepare(call, role)
    litellm = load_litellm()
    tried: list[Attempt] = []
    for attempt in range(1, call.max_attempts + 1):
        started = time.monotonic()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # intentionally broad, as in _run_role
            outcome, error = _failure(selection, exc, attempt, started, tried)
            if outcome == "outage" and attempt < call.max_attempts:
                await _asleep(2 ** (attempt - 1))
                continue
            raise error from exc
        return _checked(selection, response, call, role, attempt, started, tried)
    raise AssertionError("max_attempts must be at least 1")  # pragma: no cover


def _merged(result: TransportResult, prior: tuple[Attempt, ...]) -> TransportResult:
    return TransportResult(
        result.content,
        result.tool_calls,
        result.message,
        result.finish_reason,
        result.record,
        prior + result.attempts,
        True,
    )


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
) -> TransportResult:
    call = _Call(
        persona, messages, max_tokens, tools, tool_choice, response_format, timeout, credential_holder, max_attempts, root
    )
    refuse_end_user_fail_over(fail_over, credential_holder)
    if role == _FALLBACK:
        ensure_asserted(role, credential_holder=credential_holder, root=root)
    try:
        return _run_role(call, role)
    except TransportError as exc:
        if not (fail_over and role == _PRIMARY and exc.kind == "outage"):
            raise
        fallback_gate(messages, exc, credential_holder=credential_holder, root=root)
        try:
            return _merged(_run_role(call, _FALLBACK), exc.attempts)
        except TransportError as second:
            second.attempts = exc.attempts + second.attempts
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
) -> TransportResult:
    call = _Call(
        persona, messages, max_tokens, tools, tool_choice, response_format, timeout, credential_holder, max_attempts, root
    )
    refuse_end_user_fail_over(fail_over, credential_holder)
    if role == _FALLBACK:
        await aensure_asserted(role, credential_holder=credential_holder, root=root)
    try:
        return await _arun_role(call, role)
    except TransportError as exc:
        if not (fail_over and role == _PRIMARY and exc.kind == "outage"):
            raise
        await afallback_gate(messages, exc, credential_holder=credential_holder, root=root)
        try:
            return _merged(await _arun_role(call, _FALLBACK), exc.attempts)
        except TransportError as second:
            second.attempts = exc.attempts + second.attempts
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
