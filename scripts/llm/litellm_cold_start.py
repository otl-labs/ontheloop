"""Decision 173 clause 4 cold-start assertion for each tier role, and the fail-over policy gates (T4.32 c1, plan 2).

The probe is one minimal billable completion at the resolved persona's own thinking and effort, read on the raw LiteLLM
response: any response carrying choices is asserted, a credit refusal is spent, every other failure is not_asserted.
Only an asserted outcome is cached, per process per (contracts root, model). No AWS or SSM call; litellm is imported
lazily.
"""

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.llm import tier_selection
from scripts.llm.litellm_wire import (
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


_asserted: dict[tuple[str, str], ColdStartResult] = {}
_sync_lock = threading.Lock()
_async_locks: weakref.WeakKeyDictionary[Any, asyncio.Lock] = weakref.WeakKeyDictionary()


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


def _failed(selection: tier_selection.Selection, exc: BaseException) -> ColdStartResult:
    kind = classify(exc)
    status = getattr(exc, "status_code", None)
    return _result(selection, "spent" if kind == "credit_exhausted" else "not_asserted", f"{kind} (status {status})")


def _interpreted(selection: tier_selection.Selection, response: Any, started: float) -> ColdStartResult:
    if not getattr(response, "choices", None):
        return _result(selection, "not_asserted", "the response carried no choices")
    latency = int((time.monotonic() - started) * 1000)
    record = read_response(
        response, selection=selection, persona=selection.persona, role=selection.role, attempt=1, latency_ms=latency
    )[3]
    return _result(selection, "asserted", "one minimal completion succeeded", record)


def _not_applicable(selection: tier_selection.Selection) -> ColdStartResult:
    return _result(selection, "not_applicable", "the role declares no assertion")


def cold_start_check(role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    if selection.cold_start_assertion == "not_applicable":
        return _not_applicable(selection)
    prepared = _prepare(selection)
    if isinstance(prepared, ColdStartResult):
        return prepared
    started = time.monotonic()
    try:
        response = load_litellm().completion(**prepared)
    except Exception as exc:  # intentionally broad: a probe failure is an outcome, never a raise
        return _failed(selection, exc)
    return _interpreted(selection, response, started)


async def _acold_start_probe(selection: tier_selection.Selection) -> ColdStartResult:
    prepared = _prepare(selection)
    if isinstance(prepared, ColdStartResult):
        return prepared
    started = time.monotonic()
    try:
        response = await load_litellm().acompletion(**prepared)
    except Exception as exc:  # intentionally broad, as in cold_start_check
        return _failed(selection, exc)
    return _interpreted(selection, response, started)


async def acold_start_check(role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    if selection.cold_start_assertion == "not_applicable":
        return _not_applicable(selection)
    return await _acold_start_probe(selection)


def _cache_key(selection: tier_selection.Selection, root: Path | None) -> tuple[str, str]:
    return (str(root or ""), selection.model)


def _unavailable(result: ColdStartResult) -> TransportError:
    return TransportError(
        "fallback_unavailable",
        f"{result.role} cold start was {result.outcome}: {result.detail}",
        role=result.role,
        result=result,
    )


def ensure_asserted(role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    key = _cache_key(selection, root)
    with _sync_lock:
        cached = _asserted.get(key)
        if cached is not None:
            return cached
        result = cold_start_check(role, credential_holder=credential_holder, root=root)
        if result.outcome != "asserted":
            raise _unavailable(result)
        _asserted[key] = result
        return result


async def aensure_asserted(role: str, *, credential_holder: str = "on_the_loop", root: Path | None = None) -> ColdStartResult:
    selection = _resolve(role, credential_holder, root)
    key = _cache_key(selection, root)
    loop = asyncio.get_running_loop()
    lock = _async_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        cached = _asserted.get(key)
        if cached is not None:
            return cached
        result = await acold_start_check(role, credential_holder=credential_holder, root=root)
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
) -> ColdStartResult:
    mid_run = _mid_run(messages, primary_error)
    if mid_run is not None:
        raise mid_run
    try:
        return ensure_asserted(_FALLBACK_ROLE, credential_holder=credential_holder, root=root)
    except TransportError as exc:
        raise exc from primary_error


async def afallback_gate(
    messages: list[dict[str, Any]],
    primary_error: TransportError,
    *,
    credential_holder: str = "on_the_loop",
    root: Path | None = None,
) -> ColdStartResult:
    mid_run = _mid_run(messages, primary_error)
    if mid_run is not None:
        raise mid_run
    try:
        return await aensure_asserted(_FALLBACK_ROLE, credential_holder=credential_holder, root=root)
    except TransportError as exc:
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
