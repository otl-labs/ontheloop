"""Tests for scripts/llm/litellm_wire.py (the per-provider wire table).

A diff touching only requirements-dev.txt, requirements.txt or requirements-fast.txt selects this file, so a lock-only
bump runs these body-shape tests too (Decision 135 data edge).
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from scripts.llm import litellm_transport, litellm_wire
from scripts.llm.litellm_wire import TransportError, classify, read_response, read_secret, request_kwargs
from tests.fixtures import litellm_capture as cap

MESSAGES = [{"role": "user", "content": "hi"}]
NO_TOOLS = {"tools": None, "tool_choice": None, "response_format": None}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    cap.reset(monkeypatch)
    cap.set_keys(monkeypatch)


def _kwargs(sel, **over):
    args = {"api_key": "k", "max_tokens": 10, "timeout": 5.0, **NO_TOOLS, **over}
    return request_kwargs(sel, MESSAGES, **args)


class TestWireShapes:
    def test_primary_body(self) -> None:
        sel = cap.selection(role="primary")
        with cap.capture([cap.deepseek_body()]) as seen:
            litellm_transport.complete(cap.PERSONA, MESSAGES, max_tokens=10)
        body = seen.requests[0].body
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == sel.effort
        assert "output_config" not in body
        assert body["model"] == sel.model.split("/", 1)[1]
        assert seen.requests[0].headers["authorization"] == "Bearer sentinel-value-1234"

    def test_fallback_body(self) -> None:
        sel = cap.selection(role="warm_fetched_escape_hatch")
        with cap.capture([cap.anthropic_body()]) as seen:
            litellm_transport.complete(cap.PERSONA, MESSAGES, max_tokens=2000, role="warm_fetched_escape_hatch")
        body = seen.requests[-1].body
        assert body["thinking"]["type"] == "adaptive"
        assert body["output_config"]["effort"] == sel.effort
        assert body["model"] == sel.model.split("/", 1)[1]
        assert seen.requests[-1].headers["x-api-key"] == "sentinel-value-1234"

    def test_request_kwargs_common(self) -> None:
        fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
        tools = [{"type": "function", "function": {"name": "f"}}]
        out = _kwargs(cap.selection(role="primary"), tools=tools, tool_choice="auto", response_format=fmt)
        assert out["num_retries"] == 0
        assert "fallbacks" not in out and "_skip_mcp_handler" not in out
        assert out["tools"] == tools and out["tool_choice"] == "auto" and out["response_format"] == fmt
        assert _kwargs(cap.selection(role="primary"))["tool_choice"] is None

    def test_deepseek_disabled(self) -> None:
        sel = dataclasses.replace(cap.selection(role="primary"), thinking="disabled")
        out = _kwargs(sel)
        assert out["thinking"] == {"type": "disabled"} and "extra_body" not in out

    def test_other_prefix(self) -> None:
        base = cap.selection(role="primary")
        other = dataclasses.replace(base, model="other/some-model")
        assert _kwargs(other)["reasoning_effort"] == base.effort
        off = _kwargs(dataclasses.replace(other, thinking="disabled"))
        assert "reasoning_effort" not in off and "thinking" not in off

    def test_anthropic_refusals(self) -> None:
        sel = cap.selection(role="warm_fetched_escape_hatch")
        with pytest.raises(TransportError) as exc:
            _kwargs(dataclasses.replace(sel, thinking="disabled"))
        assert exc.value.kind == "refused" and exc.value.tag == "anthropic_thinking_always_on"
        for forced in ({"type": "function", "function": {"name": "f"}}, "required"):
            with pytest.raises(TransportError) as exc:
                _kwargs(sel, tool_choice=forced)
            assert exc.value.tag == "anthropic_thinking_forced_tool_choice"
        for ok in ("auto", "none", None):
            assert _kwargs(sel, tool_choice=ok)["reasoning_effort"] == sel.effort

    def test_read_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOME_WIRE_VARIABLE", raising=False)
        with pytest.raises(TransportError) as exc:
            read_secret("SOME_WIRE_VARIABLE")
        assert exc.value.kind == "missing_credential" and "SOME_WIRE_VARIABLE" in str(exc.value)
        monkeypatch.setenv("SOME_WIRE_VARIABLE", "")
        with pytest.raises(TransportError):
            read_secret("SOME_WIRE_VARIABLE")
        monkeypatch.setenv("SOME_WIRE_VARIABLE", "value-x")
        assert read_secret("SOME_WIRE_VARIABLE") == "value-x"
        assert "value-x" not in str(exc.value)


def _response(model, usage, message, **extra):
    return SimpleNamespace(model=model, usage=usage, choices=[SimpleNamespace(message=message)], **extra)


class _Msg:
    def __init__(self, content, tool_calls=None, **fields):
        self.content = content
        self.tool_calls = tool_calls
        self._fields = {"role": "assistant", "content": content, "tool_calls": None, **fields}

    def model_dump(self):
        return dict(self._fields)


def _read(sel, response):
    return read_response(response, selection=sel, persona="p", role=sel.role, attempt=2, latency_ms=7)


class TestRecords:
    def test_deepseek_usage_and_served_model(self) -> None:
        sel = cap.selection(role="primary")
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=30),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=9),
        )
        call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="f", arguments="{}"))
        content, calls, message, rec = _read(sel, _response("dated-build", usage, _Msg("hello", [call], x=None, y=1)))
        assert (content, message["y"]) == ("hello", 1) and "x" not in message
        assert calls == [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
        assert rec.requested_model == sel.model and rec.model == "dated-build"
        assert (rec.tokens_input, rec.tokens_cache_read, rec.tokens_cache_creation) == (70, 30, 0)
        assert (rec.tokens_output, rec.reasoning_tokens, rec.attempt, rec.latency_ms) == (20, 9, 2, 7)

    def test_deepseek_bare_usage_and_no_model(self) -> None:
        sel = cap.selection(role="primary")
        usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=3, prompt_tokens_details=None, completion_tokens_details=None
        )
        message = _Msg(None)
        response = SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message)])
        content, calls, _, rec = _read(sel, response)
        assert content == "" and calls == []
        assert rec.model is None and rec.reasoning_tokens is None
        assert (rec.tokens_input, rec.tokens_cache_read, rec.tokens_cache_creation) == (10, 0, 0)

    def test_anthropic_records_have_no_reasoning_tokens(self) -> None:
        sel = cap.selection(role="warm_fetched_escape_hatch")
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=10, cache_creation_tokens=5),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
        )
        rec = _read(sel, _response("m", usage, _Msg("x")))[3]
        assert rec.reasoning_tokens is None
        assert (rec.tokens_input, rec.tokens_cache_read, rec.tokens_cache_creation) == (85, 10, 5)


class _Status(Exception):
    def __init__(self, status, message=""):
        super().__init__(message)
        self.status_code = status


class TestClassify:
    @pytest.mark.parametrize(
        ("status", "message", "expected"),
        [
            (402, "", "credit_exhausted"),
            (400, "Your credit balance is too low to access", "credit_exhausted"),
            (401, "", "provider_error"),
            (404, "", "provider_error"),
            (400, "plain", "provider_error"),
            (408, "", "outage"),
            (429, "", "outage"),
            (500, "", "outage"),
            (503, "", "outage"),
            (529, "", "outage"),
            (None, "", "provider_error"),
        ],
    )
    def test_status_table(self, status, message, expected) -> None:
        assert classify(_Status(status, message)) == expected

    def test_exception_classes(self) -> None:
        litellm = litellm_wire.load_litellm()
        assert classify(litellm.Timeout("t", "m", "p")) == "outage"
        assert classify(litellm.UnsupportedParamsError("u", "p", "m")) == "provider_error"
