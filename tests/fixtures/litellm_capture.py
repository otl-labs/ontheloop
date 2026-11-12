"""Shared helpers for the LiteLLM transport, wire and cold-start tests (T4.32 c1, plan 2).

Plain functions and context managers; no test functions and no selection literal. Requests are captured at
httpx.Client.send and httpx.AsyncClient.send so LiteLLM's own request building and response parsing run.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from scripts.llm import litellm_cold_start, litellm_transport, tier_selection

ROOT = Path(__file__).resolve().parents[2]
PERSONA = "code_reviewer"


@dataclass
class Captured:
    body: dict[str, Any]
    headers: dict[str, str]
    via: str
    url: str


@dataclass
class Capture:
    requests: list[Captured] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.requests)


def _reply(spec: Any, request: httpx.Request) -> httpx.Response:
    if isinstance(spec, BaseException):
        raise spec
    status, body = spec
    return httpx.Response(status, json=body, request=request)


@contextmanager
def capture(responses: list[Any]) -> Iterator[Capture]:
    """Serve the given canned (status, body) responses in order, or raise an exception instance; record every request."""
    seen = Capture()
    queue = list(responses)
    original_sync = httpx.Client.send
    original_async = httpx.AsyncClient.send

    def record(request: httpx.Request, via: str) -> httpx.Response:
        content = request.content
        try:
            body = json.loads(content) if content else {}
        except ValueError:
            body = {}
        seen.requests.append(Captured(body, dict(request.headers), via, str(request.url)))
        spec = queue.pop(0) if len(queue) > 1 else queue[0]
        return _reply(spec, request)

    def sync_send(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        return record(request, "sync")

    async def async_send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        return record(request, "async")

    httpx.Client.send = sync_send  # type: ignore[method-assign]
    httpx.AsyncClient.send = async_send  # type: ignore[method-assign]
    try:
        yield seen
    finally:
        httpx.Client.send = original_sync  # type: ignore[method-assign]
        httpx.AsyncClient.send = original_async  # type: ignore[method-assign]


def _usage_deepseek(prompt: int, completion: int, cached: int | None, reasoning: int | None) -> dict[str, Any]:
    usage: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if cached is not None:
        usage["prompt_cache_hit_tokens"] = cached
        usage["prompt_cache_miss_tokens"] = prompt - cached
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return usage


def deepseek_body(
    content: str | None = "OK",
    *,
    model: str | None = "served-deepseek-build",
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    prompt: int = 100,
    completion: int = 20,
    cached: int | None = 40,
    reasoning: int | None = 5,
    finish_reason: str = "stop",
    choices: bool = True,
) -> tuple[int, dict[str, Any]]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    body: dict[str, Any] = {
        "id": "resp-1",
        "object": "chat.completion",
        "created": 1,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}] if choices else [],
        "usage": _usage_deepseek(prompt, completion, cached, reasoning),
    }
    if model is not None:
        body["model"] = model
    return 200, body


def anthropic_body(
    text: str = "OK",
    *,
    model: str = "served-anthropic-build",
    tool_use: dict[str, Any] | None = None,
    thinking: str | None = None,
    signature: str = "sig-1",
    input_tokens: int = 60,
    output_tokens: int = 15,
    cache_read: int = 0,
    cache_creation: int = 0,
    stop_reason: str = "end_turn",
) -> tuple[int, dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking, "signature": signature})
    if text:
        content.append({"type": "text", "text": text})
    if tool_use is not None:
        content.append({"type": "tool_use", **tool_use})
    return 200, {
        "id": "msg-1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "tool_use" if tool_use is not None else stop_reason,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
    }


def error_body(status: int, message: str = "provider said no") -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": "error", "code": str(status)}, "type": "error"}


def selection(persona: str = PERSONA, role: str = "primary", root: Path | None = None) -> tier_selection.Selection:
    inference_provider, config = tier_selection.load(root)
    return tier_selection.resolve(persona, role, inference_provider=inference_provider, project_configuration=config)


def contracts_copy(tmp_path: Path, mutate: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> Path:
    """Copy the two contracts to tmp_path and apply mutate(inference_provider, project_configuration) to the copies."""
    target = tmp_path / "docs" / "contracts"
    target.mkdir(parents=True)
    for rel in (tier_selection._INFERENCE_PROVIDER, tier_selection._PROJECT_CONFIGURATION):
        shutil.copy(ROOT / rel, tmp_path / rel)
    if mutate is not None:
        inference_provider, config = tier_selection.load(tmp_path)
        mutate(inference_provider, config)
        (tmp_path / tier_selection._INFERENCE_PROVIDER).write_text(
            yaml.safe_dump(inference_provider, sort_keys=False), encoding="utf-8"
        )
        (tmp_path / tier_selection._PROJECT_CONFIGURATION).write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
    return tmp_path


def reset(monkeypatch: Any) -> None:
    """Clear the cold-start cache, set the local cost-map flag and make the retry sleeps no-ops."""
    litellm_cold_start.clear_cache()
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    async def no_asleep(_: float) -> None:
        return None

    monkeypatch.setattr(litellm_transport, "_sleep", lambda _: None)
    monkeypatch.setattr(litellm_transport, "_asleep", no_asleep)


def set_keys(monkeypatch: Any, value: str = "sentinel-value-1234") -> None:
    """Set both roles' key variables, read from the contract rows, to one sentinel."""
    for role in ("primary", "warm_fetched_escape_hatch"):
        monkeypatch.setenv(selection(role=role).env_name, value)
