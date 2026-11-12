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


@dataclass(frozen=True)
class Attempt:
    role: str
    model: str
    attempt: int
    outcome: str
    latency_ms: int


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


def read_response(
    response: Any, *, selection: Selection, persona: str, role: str, attempt: int, latency_ms: int
) -> tuple[str, list[dict[str, Any]], dict[str, Any], CallRecord]:
    message = response.choices[0].message
    content = message.content or ""
    plain = {k: v for k, v in message.model_dump().items() if v is not None}
    usage = response.usage
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(prompt_details, "cached_tokens", None) or 0
    cache_creation = getattr(prompt_details, "cache_creation_tokens", None) or 0
    reasoning = None
    if selection.model.split("/", 1)[0] == "deepseek":
        reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None)
    record = CallRecord(
        persona=persona,
        role=role,
        provider=selection.provider,
        requested_model=selection.model,
        model=getattr(response, "model", None),
        billing_shape=selection.billing_shape,
        thinking=selection.thinking,
        reasoning_effort=selection.effort,
        tokens_input=usage.prompt_tokens - cache_read - cache_creation,
        tokens_output=usage.completion_tokens,
        tokens_cache_read=cache_read,
        tokens_cache_creation=cache_creation,
        reasoning_tokens=reasoning,
        attempt=attempt,
        latency_ms=latency_ms,
    )
    return content, _plain_tool_calls(message), plain, record
