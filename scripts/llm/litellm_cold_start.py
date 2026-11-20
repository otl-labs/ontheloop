"""Decision 173 clause 4 cold-start assertion for each tier role, and the fail-over policy gates (T4.32 c1, plan 2).

The probe is one minimal billable completion at the resolved persona's own thinking and effort, read on the raw LiteLLM
response: any response carrying choices is asserted, a credit refusal is spent, every other failure is not_asserted.
Only an asserted outcome is cached, per process per (contracts root, model). No AWS or SSM call; litellm is imported
lazily.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.llm import tier_selection
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

_FALLBACK_ROLE = "warm_fetched_escape_hatch"
_PROBE_MESSAGES = [{"role": "user", "content": "Reply with one word: OK"}]
_PROBE_MAX_TOKENS = 512
_PROBE_TIMEOUT = 120.0


@dataclass(frozen=True)
class ColdStartResult:
    role: str
    assertion: str
    outcome: str
    record: CallRecord | None
    detail: str
    fresh: bool = False
    attempt: Attempt | None = None


_asserted: dict[tuple[str, str], ColdStartResult] = {}
_sync_guard = threading.Lock()
_sync_locks: dict[tuple[str, str], threading.Lock] = {}
_async_locks: weakref.WeakKeyDictionary[Any, dict[tuple[str, str], asyncio.Lock]] = weakref.WeakKeyDictionary()
_wall = time.time


def clear_cache() -> None:
    _asserted.clear()


def _resolve(role: str, credential_holder: str, root: Path | None) -> tier_selection.Selection:
    inference_provider, config = tier_selection.load(root)
    personas = inference_provider["model_id_formats"]["litellm_persona_selection"]["personas"]
    return tier_selection.resolve(
        next(iter(personas)),
        role,
        inference_provider=inference_provider,
        project_configuration=config,
        credential_holder=credential_holder,
    )


def _prepare(selection: tier_selection.Selection) -> dict[str, Any] | ColdStartResult:
    try:
        api_key = read_secret(selection.env_name)
        return request_kwargs(
            selection,
            list(_PROBE_MESSAGES),
            api_key=api_key,
            max_tokens=_PROBE_MAX_TOKENS,
            tools=None,
            tool_choice=None,
            response_format=None,
            timeout=_PROBE_TIMEOUT,
        )
    except TransportError as exc:
        return _result(selection, "not_asserted", f"no request made: {exc.kind}")


def _result(
    selection: tier_selection.Selection, outcome: str, detail: str, record: CallRecord | None = None
) -> ColdStartResult:
    return ColdStartResult(selection.role, selection.cold_start_assertion, outcome, record, detail)


def _probe_attempt(selection: tier_selection.Selection, outcome: str, latency: int, started_ms: int) -> Attempt:
    return Attempt(
        selection.role, selection.model, 0, outcome, latency, selection.provider, selection.billing_shape, started_ms
    )


def reraise_if_cancelling(exc: BaseException) -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        raise asyncio.CancelledError from exc


def _begin(capture: Any, selection: tier_selection.Selection, started_ms: int) -> None:
    if capture is not None:
        capture.in_flight = _probe_attempt(selection, "cancelled", 0, started_ms)


def _failed(
    selection: tier_selection.Selection, exc: BaseException, started: float, started_ms: int, capture: Any = None
) -> ColdStartResult:
    kind = classify(exc)
    status = getattr(exc, "status_code", None)
    result = _result(selection, "spent" if kind == "credit_exhausted" else "not_asserted", f"{kind} (status {status})")
    attempt = _probe_attempt(selection, kind, int((time.monotonic() - started) * 1000), started_ms)
    if capture is not None:
        capture.in_flight = None
        capture.attempts.append(attempt)
    return _fresh(dataclasses.replace(result, attempt=attempt))


def _fresh(result: ColdStartResult) -> ColdStartResult:
    return dataclasses.replace(result, fresh=True)


def _interpreted(
    selection: tier_selection.Selection, response: Any, started: float, started_ms: int, capture: Any = None
) -> ColdStartResult:
    if capture is not None:
        capture.in_flight = None
    if not getattr(response, "choices", None):
        return _fresh(_result(selection, "not_asserted", "the response carried no choices"))
    latency = int((time.monotonic() - started) * 1000)
    record = read_response(
        response,
        selection=selection,
        persona=selection.persona,
        role=selection.role,
        attempt=1,
        latency_ms=latency,
        started_at_ms=started_ms,
        responded_at_ms=int(_wall() * 1000),
    )[3]
    return _fresh(_result(selection, "asserted", "one minimal completion succeeded", record))


def _not_applicable(selection: tier_selection.Selection) -> ColdStartResult:
    return _result(selection, "not_applicable", "the role declares no assertion")


def cold_start_check(
    role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None, capture: Any = None
) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    if selection.cold_start_assertion == "not_applicable":
        return _not_applicable(selection)
    prepared = _prepare(selection)
    if isinstance(prepared, ColdStartResult):
        return prepared
    started, started_ms = time.monotonic(), int(_wall() * 1000)
    _begin(capture, selection, started_ms)
    try:
        response = load_litellm().completion(**prepared)
    except Exception as exc:  # intentionally broad: a probe failure is an outcome, never a raise
        return _failed(selection, exc, started, started_ms, capture)
    return _interpreted(selection, response, started, started_ms, capture)


async def _acold_start_probe(selection: tier_selection.Selection, capture: Any = None) -> ColdStartResult:
    prepared = _prepare(selection)
    if isinstance(prepared, ColdStartResult):
        return prepared
    started, started_ms = time.monotonic(), int(_wall() * 1000)
    _begin(capture, selection, started_ms)
    try:
        response = await load_litellm().acompletion(**prepared)
    except Exception as exc:  # intentionally broad, as in cold_start_check
        reraise_if_cancelling(exc)
        return _failed(selection, exc, started, started_ms, capture)
    return _interpreted(selection, response, started, started_ms, capture)


async def acold_start_check(
    role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None, capture: Any = None
) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    if selection.cold_start_assertion == "not_applicable":
        return _not_applicable(selection)
    return await _acold_start_probe(selection, capture)


def _cache_key(selection: tier_selection.Selection, root: Path | None) -> tuple[str, str]:
    return (str(root or ""), selection.model)


def _unavailable(result: ColdStartResult) -> TransportError:
    return TransportError(
        "fallback_unavailable",
        f"{result.role} cold start was {result.outcome}: {result.detail}",
        role=result.role,
        attempts=() if result.attempt is None else (result.attempt,),
        result=result,
    )


def _forward(capture: Any) -> dict[str, Any]:
    return {} if capture is None else {"capture": capture}


def ensure_asserted(
    role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None, capture: Any = None
) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    key = _cache_key(selection, root)
    with _sync_guard:
        lock = _sync_locks.setdefault(key, threading.Lock())
    with lock:
        cached = _asserted.get(key)
        if cached is not None:
            return dataclasses.replace(cached, fresh=False)
        result = cold_start_check(role, credential_holder=credential_holder, root=root, **_forward(capture))
        if result.outcome != "asserted":
            raise _unavailable(result)
        _asserted[key] = result
        return result


async def aensure_asserted(
    role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None, capture: Any = None
) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    key = _cache_key(selection, root)
    loop = asyncio.get_running_loop()
    lock = _async_locks.setdefault(loop, {}).setdefault(key, asyncio.Lock())
    async with lock:
        cached = _asserted.get(key)
        if cached is not None:
            return dataclasses.replace(cached, fresh=False)
        result = await acold_start_check(role, credential_holder=credential_holder, root=root, **_forward(capture))
        if result.outcome != "asserted":
            raise _unavailable(result)
        _asserted[key] = result
        return result


def refuse_end_user_fail_over(fail_over: bool, credential_holder: str) -> None:
    if fail_over and credential_holder == "end_user":
        raise TransportError(
            "refused",
            "end_user_primary_only: one BYOK key fills the primary role alone, with no fallback (Decision 206 clause 3)",
            tag="end_user_primary_only",
        )


def _mid_run(messages: list[dict[str, Any]], primary_error: TransportError) -> TransportError | None:
    if any(m.get("role") == "assistant" for m in messages):
        error = TransportError(
            "fail_over_mid_run",
            "a provider's reasoning state cannot be replayed to the other; restart the run on the fallback role",
            role=primary_error.role,
            attempts=primary_error.attempts,
        )
        error.__cause__ = primary_error
        return error
    return None


def fallback_gate(
    messages: list[dict[str, Any]],
    primary_error: TransportError,
    *,
    credential_holder: str = "on_the_loop",
    root: Path | None = None,
    capture: Any = None,
) -> ColdStartResult:
    mid_run = _mid_run(messages, primary_error)
    if mid_run is not None:
        raise mid_run
    try:
        return ensure_asserted(_FALLBACK_ROLE, credential_holder=credential_holder, root=root, **_forward(capture))
    except TransportError as exc:
        exc.attempts = primary_error.attempts + exc.attempts
        raise exc from primary_error


async def afallback_gate(
    messages: list[dict[str, Any]],
    primary_error: TransportError,
    *,
    credential_holder: str = "on_the_loop",
    root: Path | None = None,
    capture: Any = None,
) -> ColdStartResult:
    mid_run = _mid_run(messages, primary_error)
    if mid_run is not None:
        raise mid_run
    try:
        return await aensure_asserted(_FALLBACK_ROLE, credential_holder=credential_holder, root=root, **_forward(capture))
    except TransportError as exc:
        exc.attempts = primary_error.attempts + exc.attempts
        raise exc from primary_error


__all__ = [
    "ColdStartResult",
    "acold_start_check",
    "afallback_gate",
    "aensure_asserted",
    "clear_cache",
    "cold_start_check",
    "ensure_asserted",
    "fallback_gate",
    "refuse_end_user_fail_over",
]
