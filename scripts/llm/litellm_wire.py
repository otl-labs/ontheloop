"""Per-provider wire table for the LiteLLM transport (T4.32 c1, plan 2 of 2).

Pure functions and dataclasses: no network, no file I/O, no litellm import at module import, no model id, provider
name or key variable literal beyond the LiteLLM prefix table that is adapter knowledge. The only environment read is
read_secret, the secrets port's process-environment step; the env-file step and adapter selection are deferred to
T4.24 c2 (Decision 224 clause 2).

Two assumptions live in the anthropic row. Every model under that prefix is assumed to always think (true of the
current fallback; a later Anthropic model that accepts disabled thinking needs this row edited, and no persona sets
disabled today). The wire shape for an Anthropic model comes from LiteLLM's own per-model knowledge, so a model bump may
also need a LiteLLM bump (rec-5069).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from scripts.llm.tier_selection import Selection
from src.turn_capture.litellm_usage import map_usage

_OUTAGE_STATUSES = frozenset({408, 429})
_OUTAGE_NAMES = (
    "Timeout",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "BadGatewayError",
)
_FORCING_CHOICES = (None, "auto", "none")


@dataclass(frozen=True)
class CallRecord:
    persona: str
    role: str
    provider: str
    requested_model: str
    model: str | None
    billing_shape: str
    thinking: str
    reasoning_effort: str
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_creation: int
    reasoning_tokens: int | None
    attempt: int
    latency_ms: int
    response_id: str | None = None
    usage_raw: dict[str, Any] | None = None
    cache_creation_1h_tokens: int | None = None
    cache_creation_5m_tokens: int | None = None
    usage_absent: tuple[str, ...] = ()
    started_at_ms: int | None = None
    responded_at_ms: int | None = None


@dataclass(frozen=True)
class Attempt:
    role: str
    model: str
    attempt: int
    outcome: str
    latency_ms: int
    provider: str | None = None
    billing_shape: str | None = None
    started_at_ms: int | None = None


class TransportError(RuntimeError):
    def __init__(
        self,
        kind: str,
        message: str,
        *,
        role: str | None = None,
        attempts: tuple[Attempt, ...] = (),
        tag: str | None = None,
        result: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.role = role
        self.attempts = attempts
        self.tag = tag
        self.result = result
        self.probe_record: CallRecord | None = None


def load_litellm() -> Any:
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    return litellm


def read_secret(variable: str) -> str:
    """The secrets port's process-environment step; the env-file step is deferred to T4.24 c2 (Decision 224 cl.2)."""
    value = os.environ.get(variable, "")
    if not value:
        raise TransportError("missing_credential", f"environment variable {variable} is unset or empty")
    return value


def _refused(selection: Selection, tag: str, detail: str) -> TransportError:
    return TransportError(
        "refused", f"{selection.persona} {selection.role} refused [{tag}]: {detail}", tag=tag, role=selection.role
    )


def request_kwargs(
    selection: Selection,
    messages: list[dict[str, Any]],
    *,
    api_key: str,
    max_tokens: int,
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    response_format: Any,
    timeout: float,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": selection.model,
        "api_key": api_key,
        "messages": messages,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "num_retries": 0,
        "tools": tools,
        "tool_choice": tool_choice,
        "response_format": response_format,
    }
    prefix = selection.model.split("/", 1)[0]
    enabled = selection.thinking == "enabled"
    if prefix == "deepseek":
        kwargs["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            kwargs["extra_body"] = {"reasoning_effort": selection.effort}
    elif prefix == "anthropic":
        if not enabled:
            raise _refused(selection, "anthropic_thinking_always_on", "this provider's models always think")
        if tool_choice not in _FORCING_CHOICES:
            raise _refused(
                selection, "anthropic_thinking_forced_tool_choice", "a forced tool_choice cannot combine with thinking"
            )
        kwargs["reasoning_effort"] = selection.effort
    elif enabled:
        kwargs["reasoning_effort"] = selection.effort
    return kwargs


def classify(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if status == 402 or (status == 400 and "credit balance is too low" in str(exc).lower()):
        return "credit_exhausted"
    litellm = load_litellm()
    outage_types = tuple(getattr(litellm, name) for name in _OUTAGE_NAMES)
    if isinstance(exc, outage_types) or status in _OUTAGE_STATUSES or (isinstance(status, int) and status >= 500):
        return "outage"
    return "provider_error"


def _plain_tool_calls(message: Any) -> list[dict[str, Any]]:
    calls = getattr(message, "tool_calls", None) or []
    return [
        {
            "id": call.id,
            "type": call.type,
            "function": {"name": call.function.name, "arguments": call.function.arguments},
        }
        for call in calls
    ]


def _stripped(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _stripped(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_stripped(v) for v in value if v is not None]
    return value


def read_response(
    response: Any,
    *,
    selection: Selection,
    persona: str,
    role: str,
    attempt: int,
    latency_ms: int,
    started_at_ms: int | None = None,
    responded_at_ms: int | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], CallRecord]:
    message = response.choices[0].message
    content = message.content or ""
    plain = {k: v for k, v in message.model_dump().items() if v is not None}
    usage = getattr(response, "usage", None)
    raw = _stripped(usage.model_dump()) if usage is not None else None
    prefix = selection.model.split("/", 1)[0]
    columns = map_usage(prefix, raw)
    record = CallRecord(
        persona=persona,
        role=role,
        provider=selection.provider,
        requested_model=selection.model,
        model=getattr(response, "model", None),
        billing_shape=selection.billing_shape,
        thinking=selection.thinking,
        reasoning_effort=selection.effort,
        tokens_input=columns.tokens_input,
        tokens_output=columns.tokens_output,
        tokens_cache_read=columns.tokens_cache_read,
        tokens_cache_creation=columns.tokens_cache_creation,
        reasoning_tokens=columns.reasoning_tokens,
        attempt=attempt,
        latency_ms=latency_ms,
        response_id=getattr(response, "id", None) if columns.response_id == "provider" else None,
        usage_raw=raw,
        cache_creation_1h_tokens=columns.cache_creation_1h_tokens,
        cache_creation_5m_tokens=columns.cache_creation_5m_tokens,
        usage_absent=columns.usage_absent,
        started_at_ms=started_at_ms,
        responded_at_ms=responded_at_ms,
    )
    return content, _plain_tool_calls(message), plain, record
