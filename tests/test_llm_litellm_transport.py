"""Tests for scripts/llm/litellm_transport.py (call path, retries, fail-over wiring, records).

A diff touching only requirements-dev.txt, requirements.txt or requirements-fast.txt selects this file (Decision 135).
"""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.deps.affected_tests import derive_affected_tests
from scripts.llm import litellm_cold_start, tier_selection
from scripts.llm import litellm_transport as t
from scripts.llm.litellm_wire import TransportError
from src.persona_backends.events import Capture
from tests.fixtures import litellm_capture as cap

ROOT = Path(__file__).resolve().parents[1]
PRIMARY, FALLBACK = "primary", "warm_fetched_escape_hatch"
USER = [{"role": "user", "content": "hi"}]
TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}]
DS_CALL = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
AN_CALL = {"id": "toolu_1", "name": "f", "input": {}}
SENTINEL = "sentinel-value-1234"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    cap.reset(monkeypatch)
    cap.set_keys(monkeypatch, SENTINEL)


def run(call: Callable[..., Any], *args: Any, is_async: bool, **kwargs: Any) -> Any:
    if is_async:
        return asyncio.run(call(*args, **kwargs))
    return call(*args, **kwargs)


def go(is_async: bool, messages=USER, **kwargs: Any) -> Any:
    kwargs.setdefault("max_tokens", 100)
    return run(t.acomplete if is_async else t.complete, cap.PERSONA, messages, is_async=is_async, **kwargs)


BOTH = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


class TestToolsCallUnderPins:
    """A tools call works under the pinned litellm with mcp>=2 in the lock.

    The class needs mcp>=2 in the lock: litellm imports its MCP handler on any tools call. The pins live in
    requirements-dev.txt (litellm), requirements.txt (mcp) and requirements-fast.txt (mcp floor); a diff touching only
    one of them selects this file.
    """

    @BOTH
    def test_tools_call_returns_tool_call(self, is_async: bool) -> None:
        with cap.capture([cap.deepseek_body(content=None, tool_calls=[DS_CALL])]):
            result = go(is_async, tools=TOOLS)
        assert result.tool_calls[0]["function"]["name"] == "f"

    @BOTH
    def test_tools_call_through_fail_over(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body(text="", tool_use=AN_CALL)]) as seen:
            result = go(is_async, tools=TOOLS, fail_over=True)
        assert result.failed_over and result.record.role == FALLBACK and result.tool_calls[0]["function"]["name"] == "f"
        assert len(result.attempts) == 4 and len(seen) == 3 + 1 + 1

    def test_second_turn_replays_reasoning_state(self) -> None:
        with cap.capture([cap.deepseek_body(content=None, tool_calls=[DS_CALL], reasoning_content="deepseek-thoughts")]):
            first = go(False, tools=TOOLS)
        turn2 = [*USER, first.message, {"role": "tool", "tool_call_id": "c1", "content": "done"}]
        with cap.capture([cap.deepseek_body()]) as seen:
            go(False, turn2, tools=TOOLS)
        assert "deepseek-thoughts" in str(seen.requests[0].body["messages"])

        with cap.capture([cap.anthropic_body(text="", tool_use=AN_CALL, thinking="claude-thoughts", signature="sig-xyz")]):
            first = go(False, tools=TOOLS, role=FALLBACK)
        turn2 = [*USER, first.message, {"role": "tool", "tool_call_id": "toolu_1", "content": "done"}]
        with cap.capture([cap.anthropic_body()]) as seen:
            go(False, turn2, tools=TOOLS, role=FALLBACK)
        sent = str(seen.requests[0].body["messages"])
        assert "claude-thoughts" in sent and "sig-xyz" in sent

    @pytest.mark.parametrize("name", ["requirements-dev.txt", "requirements.txt", "requirements-fast.txt"])
    def test_lock_only_diff_selects_this_file(self, name: str) -> None:
        assert "tests/test_llm_litellm_transport.py" in derive_affected_tests([("M", name)])["selected"]


class TestOutcomes:
    @BOTH
    @pytest.mark.parametrize("response", [cap.error_body(402), cap.error_body(400, "Your credit balance is too low")])
    def test_credit_exhausted_never_retried_or_failed_over(self, is_async: bool, response) -> None:
        with cap.capture([response]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True)
        assert exc.value.kind == "credit_exhausted" and len(exc.value.attempts) == 1 and len(seen) == 1

    @BOTH
    @pytest.mark.parametrize("response", [cap.error_body(401), cap.error_body(404), cap.error_body(400, "bad")])
    def test_provider_error_single_attempt(self, is_async: bool, response) -> None:
        with cap.capture([response]):
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True)
        assert exc.value.kind == "provider_error" and len(exc.value.attempts) == 1

    @BOTH
    @pytest.mark.parametrize("response", [cap.error_body(503), cap.error_body(529), cap.error_body(429)])
    def test_outage_retried_with_backoff(self, is_async: bool, response, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def asleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(t, "_sleep", sleeps.append)
        monkeypatch.setattr(t, "_asleep", asleep)
        with cap.capture([response]):
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.kind == "outage" and [a.outcome for a in exc.value.attempts] == ["outage"] * 3
        assert sleeps == [1, 2]

    @BOTH
    def test_transient_connection_errors_retried(self, is_async: bool) -> None:
        import httpx

        for failure in (httpx.ReadTimeout("slow"), httpx.ConnectError("refused")):
            with cap.capture([failure]):
                with pytest.raises(TransportError) as exc:
                    go(is_async)
            assert exc.value.kind == "outage" and len(exc.value.attempts) >= 3

    @BOTH
    def test_recovers_after_an_outage(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503), cap.deepseek_body()]):
            result = go(is_async)
        assert [a.outcome for a in result.attempts] == ["outage", "ok"] and not result.failed_over
        assert result.record.attempt == 2

    @BOTH
    def test_blank_and_malformed_never_retried(self, is_async: bool) -> None:
        for body, kind in (
            (cap.deepseek_body(content=""), "empty_output"),
            (cap.deepseek_body(choices=False), "malformed_output"),
        ):
            with cap.capture([body]) as seen:
                with pytest.raises(TransportError) as exc:
                    go(is_async, fail_over=True)
            assert exc.value.kind == kind and len(exc.value.attempts) == 1 and len(seen) == 1


class TestFailOver:
    @BOTH
    def test_off_by_default(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.kind == "outage" and len(seen) == 3

    @BOTH
    def test_first_turn_fail_over_serves_fallback(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body()]):
            result = go(is_async, fail_over=True)
        assert result.failed_over and result.record.role == FALLBACK
        assert result.record.requested_model == cap.selection(role=FALLBACK).model
        assert [a.role for a in result.attempts] == [PRIMARY] * 3 + [FALLBACK]

    @BOTH
    def test_mid_run_surfaces_and_fallback_never_called(self, is_async: bool) -> None:
        turn2 = [*USER, {"role": "assistant", "content": "x"}, {"role": "user", "content": "more"}]
        with cap.capture([cap.error_body(503)]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async, turn2, fail_over=True)
        assert exc.value.kind == "fail_over_mid_run" and exc.value.__cause__ is not None and len(seen) == 3

    @BOTH
    def test_fallback_unavailable_chained(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.error_body(402)]):
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True)
        assert exc.value.kind == "fallback_unavailable" and exc.value.__cause__.kind == "outage"

    @BOTH
    def test_fallback_call_failure_keeps_primary_attempts(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body(), cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True)
        assert exc.value.kind == "provider_error" and [a.role for a in exc.value.attempts] == [PRIMARY] * 3 + [FALLBACK]

    @BOTH
    def test_direct_fallback_after_asserted_cold_start(self, is_async: bool) -> None:
        with cap.capture([cap.anthropic_body()]) as seen:
            result = go(is_async, role=FALLBACK)
        assert result.record.role == FALLBACK and len(seen) == 2 and len(result.attempts) == 1
        assert seen.requests[0].via == ("async" if is_async else "sync")

    @BOTH
    def test_direct_fallback_after_spent_cold_start(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(402)]):
            with pytest.raises(TransportError) as exc:
                go(is_async, role=FALLBACK)
        assert exc.value.kind == "fallback_unavailable"

    @BOTH
    def test_end_user_primary_alone(self, is_async: bool) -> None:
        with cap.capture([cap.deepseek_body()]):
            assert go(is_async, credential_holder="end_user").record.role == PRIMARY
        with cap.capture([cap.deepseek_body()]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async, credential_holder="end_user", fail_over=True)
        assert exc.value.tag == "end_user_primary_only" and len(seen) == 0

    @BOTH
    def test_missing_key_and_refused_shape(self, is_async: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(cap.selection(role=PRIMARY).env_name)
        with cap.capture([cap.deepseek_body()]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.kind == "missing_credential" and exc.value.attempts == () and len(seen) == 0

        def disable(provider: dict, _config: dict) -> None:
            personas = provider["model_id_formats"]["litellm_persona_selection"]["personas"]
            personas[list(personas)[-1]][FALLBACK]["thinking"] = "disabled"

        root = cap.contracts_copy(tmp_path, disable)
        persona = list(tier_selection.load(root)[0]["model_id_formats"]["litellm_persona_selection"]["personas"])[-1]
        with cap.capture([cap.anthropic_body()]):
            litellm_cold_start.ensure_asserted(FALLBACK, root=root)
        with cap.capture([cap.anthropic_body()]) as seen:
            with pytest.raises(TransportError) as exc:
                run(
                    t.acomplete if is_async else t.complete,
                    persona,
                    USER,
                    is_async=is_async,
                    max_tokens=10,
                    role=FALLBACK,
                    root=root,
                )
        assert exc.value.kind == "refused" and exc.value.tag == "anthropic_thinking_always_on" and len(seen) == 0


class TestSecretsAndSelection:
    @BOTH
    def test_key_never_leaks(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(401, "denied")]):
            with pytest.raises(TransportError) as exc:
                go(is_async)
        link: BaseException | None = exc.value
        while link is not None:
            assert SENTINEL not in str(link)
            link = link.__cause__
        with cap.capture([cap.deepseek_body()]):
            result = go(is_async)
        assert SENTINEL not in repr(result) and SENTINEL not in repr(result.record)

    def test_tier_selection_error_propagates(self, tmp_path: Path) -> None:
        def retire(provider: dict, config: dict) -> None:
            row = provider["model_id_formats"]["litellm_tier_models"]["tier_1"]
            row["models"] = ["gone/model"]
            row["default_model"] = "gone/model"
            row["provider"] = "Gone-direct"
            provider["retired_providers"] = [*provider.get("retired_providers", []), {"id": "gone"}]
            config_row = next(r for r in config["settings"] if r["id"].endswith("api_key.primary"))
            config_row["variable"] = "GONE_API_KEY"

        def same_vendor(provider: dict, config: dict) -> None:
            tiers = provider["model_id_formats"]["litellm_tier_models"]
            tiers["tier_2"] = dict(tiers["tier_1"])

        for mutate, tag in ((retire, "2(a)"), (same_vendor, "2(b)")):
            root = cap.contracts_copy(tmp_path / tag[:4].strip("()"), mutate)
            with pytest.raises(tier_selection.TierSelectionError, match=tag.replace("(", r"\(").replace(")", r"\)")):
                t.complete(cap.PERSONA, USER, max_tokens=10, root=root)


class TestContractSwap:
    def test_model_swap_is_a_contract_edit(self, tmp_path: Path) -> None:
        def swap(provider: dict, _config: dict) -> None:
            row = provider["model_id_formats"]["litellm_tier_models"]["tier_1"]
            row["models"] = ["deepseek/another-model"]
            row["default_model"] = "deepseek/another-model"

        root = cap.contracts_copy(tmp_path, swap)
        with cap.capture([cap.deepseek_body()]) as seen:
            result = t.complete(cap.PERSONA, USER, max_tokens=10, root=root)
        assert seen.requests[0].body["model"] == "another-model" and result.record.requested_model == "deepseek/another-model"


class TestAttemptFields:
    @BOTH
    def test_ok_and_failed_attempts_carry_selection_and_start(self, is_async: bool, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(t, "_wall", lambda: 1234.5)
        sel = cap.selection(role=PRIMARY)
        with cap.capture([cap.error_body(503), cap.deepseek_body()]):
            result = go(is_async)
        assert [a.outcome for a in result.attempts] == ["outage", "ok"]
        for attempt in result.attempts:
            assert (attempt.provider, attempt.billing_shape, attempt.started_at_ms) == (
                sel.provider,
                sel.billing_shape,
                1234500,
            )
        assert result.record.started_at_ms == 1234500 and result.record.responded_at_ms == 1234500
        with cap.capture([cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.attempts[0].provider == sel.provider and exc.value.attempts[0].started_at_ms == 1234500

    @BOTH
    def test_malformed_attempt_carries_fields(self, is_async: bool) -> None:
        with cap.capture([cap.deepseek_body(content="")]):
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.attempts[0].outcome == "empty_output" and exc.value.attempts[0].billing_shape is not None


class TestProbeRecord:
    @BOTH
    def test_fail_over_result_carries_the_fresh_probe_once(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body()]):
            first = go(is_async, fail_over=True)
        assert first.probe_record is not None and first.probe_record.role == FALLBACK
        assert first.probe_record is not first.record and first.probe_record.attempt == 1
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body()]) as seen:
            second = go(is_async, fail_over=True)
        assert second.failed_over and second.probe_record is None and len(seen) == 3 + 1

    @BOTH
    def test_fallback_raise_after_fresh_probe_carries_it_on_the_error(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body(), cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True)
        assert exc.value.kind == "provider_error" and exc.value.probe_record.role == FALLBACK

    @BOTH
    def test_direct_fallback_role_carries_fresh_probe_then_none(self, is_async: bool) -> None:
        with cap.capture([cap.anthropic_body()]):
            first = go(is_async, role=FALLBACK)
        assert first.probe_record is not None and first.probe_record.role == FALLBACK and first.failed_over is False
        with cap.capture([cap.anthropic_body()]) as seen:
            cached = go(is_async, role=FALLBACK)
        assert cached.probe_record is None and len(seen) == 1

    @BOTH
    def test_direct_fallback_error_carries_probe(self, is_async: bool) -> None:
        with cap.capture([cap.anthropic_body(), cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                go(is_async, role=FALLBACK)
        assert exc.value.probe_record is not None
        with cap.capture([cap.error_body(401)]):
            with pytest.raises(TransportError) as again:
                go(is_async, role=FALLBACK)
        assert again.value.probe_record is None

    @BOTH
    def test_primary_error_has_no_probe(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                go(is_async)
        assert exc.value.probe_record is None


def _connection_error() -> Exception:
    import litellm

    return litellm.APIConnectionError(message="cancelled", llm_provider="p", model="m")


async def _until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never held")


class TestCapture:
    @BOTH
    def test_capture_attempts_equal_returned_and_raised_attempts(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body()]):
            seen = Capture()
            result = go(is_async, fail_over=True, capture=seen)
        assert seen.filled and seen.attempts == list(result.attempts) and seen.record is result.record
        assert seen.in_flight is None
        litellm_cold_start.clear_cache()
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body(), cap.error_body(401)]):
            seen = Capture()
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True, capture=seen)
        assert seen.attempts == list(exc.value.attempts) and seen.record is None
        with cap.capture([cap.deepseek_body(content="")]):
            seen = Capture()
            with pytest.raises(TransportError):
                go(is_async, capture=seen)
        assert [a.outcome for a in seen.attempts] == ["empty_output"] and seen.record is None

    @BOTH
    def test_in_flight_set_during_call_and_cleared_after(self, is_async: bool, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        seen = Capture()
        during: list[Any] = []
        real_sync, real_async = litellm.completion, litellm.acompletion

        def sync_spy(**kwargs: Any) -> Any:
            during.append(seen.in_flight)
            return real_sync(**kwargs)

        async def async_spy(**kwargs: Any) -> Any:
            during.append(seen.in_flight)
            return await real_async(**kwargs)

        monkeypatch.setattr(litellm, "completion", sync_spy)
        monkeypatch.setattr(litellm, "acompletion", async_spy)
        with cap.capture([cap.deepseek_body()]):
            go(is_async, capture=seen)
        assert (during[0].role, during[0].attempt, during[0].outcome) == (PRIMARY, 1, "cancelled")
        assert during[0].provider is not None and during[0].started_at_ms is not None and seen.in_flight is None

    def test_cancel_leaves_in_flight_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        async def hang(**_kwargs: Any) -> Any:
            await asyncio.sleep(3600)

        monkeypatch.setattr(litellm, "acompletion", hang)

        async def main() -> Capture:
            seen = Capture()
            task = asyncio.ensure_future(t.acomplete(cap.PERSONA, USER, max_tokens=100, capture=seen))
            await _until(lambda: seen.in_flight is not None)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return seen

        seen = asyncio.run(main())
        assert seen.in_flight.outcome == "cancelled" and seen.attempts == []

    def test_cancel_in_backoff_leaves_no_in_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def hang(_seconds: float) -> None:
            await asyncio.sleep(3600)

        monkeypatch.setattr(t, "_asleep", hang)

        async def main() -> Capture:
            seen = Capture()
            with cap.capture([cap.error_body(503)]):
                task = asyncio.ensure_future(t.acomplete(cap.PERSONA, USER, max_tokens=100, capture=seen))
                await _until(lambda: bool(seen.attempts))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            return seen

        seen = asyncio.run(main())
        assert seen.in_flight is None and [a.outcome for a in seen.attempts] == ["outage"]

    def test_cancel_raised_as_error_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        calls: list[int] = []
        sleeps: list[float] = []

        async def turns_cancel_into_error(**_kwargs: Any) -> Any:
            calls.append(1)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise _connection_error() from None

        async def asleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(litellm, "acompletion", turns_cancel_into_error)
        monkeypatch.setattr(t, "_asleep", asleep)

        async def main() -> Capture:
            seen = Capture()
            task = asyncio.ensure_future(t.acomplete(cap.PERSONA, USER, max_tokens=100, capture=seen))
            await _until(lambda: bool(calls))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return seen

        seen = asyncio.run(main())
        assert calls == [1] and sleeps == [] and seen.in_flight is not None and seen.attempts == []

    @BOTH
    def test_probe_record_on_capture(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.anthropic_body()]):
            seen = Capture()
            result = go(is_async, fail_over=True, capture=seen)
        assert seen.probe_record is result.probe_record and seen.probe_record.role == FALLBACK
        litellm_cold_start.clear_cache()
        with cap.capture([cap.anthropic_body()]):
            direct = Capture()
            go(is_async, role=FALLBACK, capture=direct)
        assert direct.probe_record is not None and direct.filled
        with cap.capture([cap.deepseek_body()]):
            plain = Capture()
            go(is_async, capture=plain)
        assert plain.probe_record is None

    @BOTH
    def test_fallback_unavailable_carries_primary_and_probe_attempts(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(503)] * 3 + [cap.error_body(401)]):
            seen = Capture()
            with pytest.raises(TransportError) as exc:
                go(is_async, fail_over=True, capture=seen)
        assert exc.value.kind == "fallback_unavailable"
        assert [(a.role, a.attempt) for a in exc.value.attempts] == [(PRIMARY, 1), (PRIMARY, 2), (PRIMARY, 3), (FALLBACK, 0)]
        assert seen.attempts == list(exc.value.attempts)

    def test_outage_inside_task_group_is_still_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import anyio

        async def asleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(t, "_asleep", asleep)

        async def main() -> TransportError:
            async with anyio.create_task_group():
                with anyio.move_on_after(0):
                    await anyio.sleep(1)
                with cap.capture([cap.error_body(503)]):
                    with pytest.raises(TransportError) as exc:
                        await t.acomplete(cap.PERSONA, USER, max_tokens=100, capture=Capture())
            return exc.value

        assert [a.outcome for a in asyncio.run(main()).attempts] == ["outage"] * 3


class TestMaxAttempts:
    @BOTH
    @pytest.mark.parametrize("role", [PRIMARY, FALLBACK])
    def test_below_one_is_refused_before_any_request(self, is_async: bool, role: str) -> None:
        with cap.capture([cap.deepseek_body()]) as seen:
            with pytest.raises(TransportError) as exc:
                go(is_async, role=role, max_attempts=0)
        assert exc.value.kind == "refused" and exc.value.tag == "max_attempts_below_one" and len(seen) == 0


class TestModuleHygiene:
    def test_import_is_cheap_and_makes_no_litellm_import(self) -> None:
        code = (
            "import scripts.llm.litellm_transport, scripts.llm.litellm_wire, scripts.llm.litellm_cold_start, sys; "
            "assert 'litellm' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)

    def test_no_selection_literals_in_adapter_code(self) -> None:
        provider, config = tier_selection.load()
        tiers = provider["model_id_formats"]["litellm_tier_models"]
        needles = [m for row in tiers.values() for m in [row.get("default_model"), *row.get("models", [])] if m]
        needles += [r["variable"] for r in config["settings"] if r["id"].startswith("ports.run_persona.litellm.api_key.")]
        needles += [
            r["this_repository"]["current_home"]
            for r in config["settings"]
            if r["id"].startswith("ports.run_persona.litellm.api_key.")
        ]
        for name in (
            "scripts/llm/litellm_transport.py",
            "scripts/llm/litellm_wire.py",
            "scripts/llm/litellm_cold_start.py",
            "scripts/smoke_test_inference_credentials.py",
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert [n for n in needles if n in text] == [], name

    def test_dataclass_records_are_frozen(self) -> None:
        assert dataclasses.is_dataclass(t.CallRecord) and t.CallRecord.__dataclass_params__.frozen
