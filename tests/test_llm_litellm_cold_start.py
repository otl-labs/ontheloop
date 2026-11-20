"""Tests for scripts/llm/litellm_cold_start.py (cold-start assertion and fail-over gates).

A diff touching only requirements-dev.txt, requirements.txt or requirements-fast.txt selects this file (Decision 135).
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading

import pytest

from scripts.llm import litellm_cold_start as cs
from scripts.llm import tier_selection
from scripts.llm.litellm_wire import Attempt, TransportError
from src.persona_backends.events import Capture
from tests.fixtures import litellm_capture as cap

FALLBACK = "warm_fetched_escape_hatch"
USER = [{"role": "user", "content": "hi"}]
AFTER_TURN = [*USER, {"role": "assistant", "content": "x"}]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    cap.reset(monkeypatch)
    cap.set_keys(monkeypatch)


def _outage() -> TransportError:
    return TransportError("outage", "down", role="primary")


class TestColdStartCheck:
    def test_body_carries_persona_settings(self) -> None:
        sel = cap.selection(cs._resolve("primary", "on_the_loop", None).persona, "primary")
        with cap.capture([cap.deepseek_body()]) as seen:
            result = cs.cold_start_check("primary")
        body = seen.requests[0].body
        assert body["max_tokens"] == 512 and body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == sel.effort
        assert result.outcome == "asserted" and result.record.requested_model == sel.model

    def test_blank_content_length_finish_is_asserted(self) -> None:
        with cap.capture([cap.deepseek_body(content="", finish_reason="length")]):
            assert cs.cold_start_check("primary").outcome == "asserted"

    @pytest.mark.parametrize("response", [cap.error_body(402), cap.error_body(400, "Your credit balance is too low")])
    def test_spent(self, response) -> None:
        with cap.capture([response]):
            assert cs.cold_start_check(FALLBACK).outcome == "spent"

    @pytest.mark.parametrize("response", [cap.error_body(401), cap.error_body(503), cap.deepseek_body(choices=False)])
    def test_not_asserted(self, response) -> None:
        with cap.capture([response]):
            assert cs.cold_start_check("primary").outcome == "not_asserted"

    def test_missing_key_makes_no_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(cap.selection(role="primary").env_name)
        with cap.capture([cap.deepseek_body()]) as seen:
            result = cs.cold_start_check("primary")
        assert result.outcome == "not_asserted" and "missing_credential" in result.detail and len(seen) == 0

    def test_async_missing_key_makes_no_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(cap.selection(role="primary").env_name)
        with cap.capture([cap.deepseek_body()]) as seen:
            result = asyncio.run(cs.acold_start_check("primary"))
        assert result.outcome == "not_asserted" and len(seen) == 0

    def test_not_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = tier_selection.resolve

        def patched(*args, **kwargs):
            return dataclasses.replace(real(*args, **kwargs), cold_start_assertion="not_applicable")

        monkeypatch.setattr(tier_selection, "resolve", patched)
        with cap.capture([cap.deepseek_body()]) as seen:
            assert cs.cold_start_check("primary").outcome == "not_applicable"
            assert asyncio.run(cs.acold_start_check("primary")).outcome == "not_applicable"
        assert len(seen) == 0

    def test_end_user_fallback_refused(self) -> None:
        with pytest.raises(tier_selection.TierSelectionError):
            cs.cold_start_check(FALLBACK, credential_holder="end_user")


def _same_but_fresh(a: cs.ColdStartResult, b: cs.ColdStartResult) -> bool:
    return dataclasses.replace(a, fresh=False) == dataclasses.replace(b, fresh=False)


class TestGates:
    def test_ensure_caches_asserted(self) -> None:
        with cap.capture([cap.anthropic_body()]) as seen:
            first = cs.ensure_asserted(FALLBACK)
            again = cs.ensure_asserted(FALLBACK)
        assert first.fresh is True and again.fresh is False and first.record is not None
        assert _same_but_fresh(first, again) and len(seen) == 1 and {r.via for r in seen.requests} == {"sync"}

    @pytest.mark.parametrize("response", [cap.error_body(402), cap.error_body(401)])
    def test_ensure_unavailable_is_not_cached(self, response) -> None:
        with cap.capture([response]) as seen:
            for _ in range(2):
                with pytest.raises(TransportError) as exc:
                    cs.ensure_asserted(FALLBACK)
                assert exc.value.kind == "fallback_unavailable" and exc.value.result.outcome != "asserted"
        assert len(seen) == 2

    def test_aensure_caches_and_uses_async_send(self) -> None:
        async def go():
            return await cs.aensure_asserted(FALLBACK), await cs.aensure_asserted(FALLBACK)

        with cap.capture([cap.anthropic_body()]) as seen:
            first, again = asyncio.run(go())
        assert first.fresh is True and again.fresh is False
        assert _same_but_fresh(first, again) and len(seen) == 1 and {r.via for r in seen.requests} == {"async"}

    def test_aensure_failing_probe_not_cached(self) -> None:
        async def go():
            with pytest.raises(TransportError) as exc:
                await cs.aensure_asserted(FALLBACK)
            return exc.value

        with cap.capture([cap.error_body(503)]) as seen:
            error = asyncio.run(go())
            asyncio.run(go())
        assert error.kind == "fallback_unavailable" and len(seen) == 2

    def test_concurrent_async_probes_once(self) -> None:
        async def go():
            return await asyncio.gather(cs.aensure_asserted(FALLBACK), cs.aensure_asserted(FALLBACK))

        with cap.capture([cap.anthropic_body()]) as seen:
            asyncio.run(go())
        assert len(seen) == 1

    def test_concurrent_threads_probe_once(self) -> None:
        with cap.capture([cap.anthropic_body()]) as seen:
            threads = [threading.Thread(target=cs.ensure_asserted, args=(FALLBACK,)) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert len(seen) == 1

    def test_fallback_gate_mid_run_and_clear(self) -> None:
        primary = _outage()
        with cap.capture([cap.anthropic_body()]) as seen:
            with pytest.raises(TransportError) as exc:
                cs.fallback_gate(AFTER_TURN, primary)
            assert exc.value.kind == "fail_over_mid_run" and exc.value.__cause__ is primary and len(seen) == 0
            assert cs.fallback_gate(USER, primary).outcome == "asserted"
            assert len(seen) == 1
            cs.clear_cache()
            assert cs._asserted == {}

    def test_fallback_gate_unavailable_chained(self) -> None:
        primary = _outage()
        with cap.capture([cap.error_body(402)]):
            with pytest.raises(TransportError) as exc:
                cs.fallback_gate(USER, primary)
        assert exc.value.kind == "fallback_unavailable" and exc.value.__cause__ is primary

    def test_afallback_gate(self) -> None:
        primary = _outage()

        async def go(messages):
            return await cs.afallback_gate(messages, primary)

        with cap.capture([cap.anthropic_body()]):
            with pytest.raises(TransportError) as exc:
                asyncio.run(go(AFTER_TURN))
            assert exc.value.kind == "fail_over_mid_run"
            assert asyncio.run(go(USER)).outcome == "asserted"
        cs.clear_cache()
        with cap.capture([cap.error_body(402)]):
            with pytest.raises(TransportError) as exc:
                asyncio.run(go(USER))
        assert exc.value.__cause__ is primary

    def test_refuse_end_user_fail_over(self) -> None:
        cs.refuse_end_user_fail_over(True, "on_the_loop")
        cs.refuse_end_user_fail_over(False, "end_user")
        with pytest.raises(TransportError) as exc:
            cs.refuse_end_user_fail_over(True, "end_user")
        assert exc.value.kind == "refused" and exc.value.tag == "end_user_primary_only"


def _asserted(role: str) -> cs.ColdStartResult:
    return cs.ColdStartResult(role, "remaining_allowance", "asserted", None, "fake probe", True)


def _probe(is_async: bool, role: str = FALLBACK, **kwargs):
    if is_async:
        return asyncio.run(cs.acold_start_check(role, **kwargs))
    return cs.cold_start_check(role, **kwargs)


BOTH = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


class TestProbeAttempt:
    @BOTH
    def test_raising_probe_carries_attempt(self, is_async: bool) -> None:
        sel = cs._resolve(FALLBACK, "on_the_loop", None)
        with cap.capture([cap.error_body(401)]):
            result = _probe(is_async)
        attempt = result.attempt
        assert (attempt.role, attempt.attempt, attempt.outcome) == (FALLBACK, 0, "provider_error")
        assert (attempt.model, attempt.provider, attempt.billing_shape) == (sel.model, sel.provider, sel.billing_shape)
        assert attempt.latency_ms >= 0 and isinstance(attempt.started_at_ms, int)

    @BOTH
    def test_no_choices_and_no_request_probes_carry_none(self, is_async: bool, monkeypatch: pytest.MonkeyPatch) -> None:
        with cap.capture([cap.deepseek_body(choices=False)]):
            assert _probe(is_async, "primary").attempt is None
        monkeypatch.delenv(cap.selection(role="primary").env_name)
        assert _probe(is_async, "primary").attempt is None

    @BOTH
    def test_unavailable_error_carries_it(self, is_async: bool) -> None:
        with cap.capture([cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                if is_async:
                    asyncio.run(cs.aensure_asserted(FALLBACK))
                else:
                    cs.ensure_asserted(FALLBACK)
        assert exc.value.kind == "fallback_unavailable" and [a.attempt for a in exc.value.attempts] == [0]
        assert exc.value.attempts[0] is exc.value.result.attempt

    @BOTH
    def test_gates_prepend_primary_attempts_and_fill_capture(self, is_async: bool) -> None:
        prior = Attempt("primary", "m/x", 1, "outage", 5)
        primary = TransportError("outage", "down", role="primary", attempts=(prior,))
        seen = Capture()
        with cap.capture([cap.error_body(401)]):
            with pytest.raises(TransportError) as exc:
                if is_async:
                    asyncio.run(cs.afallback_gate(USER, primary, capture=seen))
                else:
                    cs.fallback_gate(USER, primary, capture=seen)
        assert exc.value.__cause__ is primary and exc.value.attempts[0] is prior and exc.value.attempts[1].attempt == 0
        assert seen.attempts == [exc.value.attempts[1]] and seen.in_flight is None

    @BOTH
    def test_asserted_probe_fills_nothing_and_clears_in_flight(self, is_async: bool) -> None:
        seen = Capture()
        with cap.capture([cap.anthropic_body()]):
            if is_async:
                asyncio.run(cs.aensure_asserted(FALLBACK, capture=seen))
            else:
                cs.ensure_asserted(FALLBACK, capture=seen)
        assert seen.attempts == [] and seen.in_flight is None

    def test_cancelled_async_probe_leaves_in_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        async def hang(**_kwargs):
            await asyncio.sleep(3600)

        monkeypatch.setattr(litellm, "acompletion", hang)

        async def main() -> Capture:
            seen = Capture()
            task = asyncio.ensure_future(cs.acold_start_check(FALLBACK, capture=seen))
            for _ in range(200):
                if seen.in_flight is not None:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return seen

        seen = asyncio.run(main())
        assert (seen.in_flight.role, seen.in_flight.attempt, seen.in_flight.outcome) == (FALLBACK, 0, "cancelled")
        assert seen.attempts == []

    def test_probe_cancel_raised_as_error_is_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        async def turns_cancel_into_error(**_kwargs):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise litellm.APIConnectionError(message="cancelled", llm_provider="p", model="m") from None

        monkeypatch.setattr(litellm, "acompletion", turns_cancel_into_error)

        async def main() -> Capture:
            seen = Capture()
            task = asyncio.ensure_future(cs.acold_start_check(FALLBACK, capture=seen))
            for _ in range(200):
                if seen.in_flight is not None:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return seen

        seen = asyncio.run(main())
        assert seen.in_flight is not None and seen.attempts == []


class TestColdStartLock:
    def _roots(self, tmp_path):
        return cap.contracts_copy(tmp_path / "a"), cap.contracts_copy(tmp_path / "b")

    def test_two_keys_probe_concurrently(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        first_root, second_root = self._roots(tmp_path)
        first_in, second_in, overlapped = threading.Event(), threading.Event(), []

        def fake(role, *, credential_holder, root):
            if root == first_root:
                first_in.set()
                overlapped.append(second_in.wait(timeout=5))
            else:
                second_in.set()
            return _asserted(role)

        monkeypatch.setattr(cs, "cold_start_check", fake)
        worker = threading.Thread(target=cs.ensure_asserted, args=(FALLBACK,), kwargs={"root": first_root})
        worker.start()
        assert first_in.wait(timeout=5)
        cs.ensure_asserted(FALLBACK, root=second_root)
        worker.join(timeout=5)
        assert overlapped == [True]

    def test_one_key_probes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake(role, *, credential_holder, root):
            calls.append(role)
            return _asserted(role)

        monkeypatch.setattr(cs, "cold_start_check", fake)
        results = [cs.ensure_asserted(FALLBACK) for _ in range(3)]
        assert len(calls) == 1 and [r.fresh for r in results] == [True, False, False]

    def test_two_keys_probe_concurrently_async(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        first_root, second_root = self._roots(tmp_path)

        async def go() -> list[bool]:
            second_in = asyncio.Event()
            overlapped: list[bool] = []

            async def fake(role, *, credential_holder, root):
                if root == first_root:
                    await asyncio.wait_for(second_in.wait(), timeout=5)
                    overlapped.append(True)
                else:
                    second_in.set()
                return _asserted(role)

            monkeypatch.setattr(cs, "acold_start_check", fake)
            await asyncio.gather(
                cs.aensure_asserted(FALLBACK, root=first_root), cs.aensure_asserted(FALLBACK, root=second_root)
            )
            return overlapped

        assert asyncio.run(go()) == [True]

    def test_one_key_probes_once_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        async def fake(role, *, credential_holder, root):
            calls.append(role)
            return _asserted(role)

        async def go() -> list[cs.ColdStartResult]:
            return [await cs.aensure_asserted(FALLBACK) for _ in range(3)]

        monkeypatch.setattr(cs, "acold_start_check", fake)
        results = asyncio.run(go())
        assert len(calls) == 1 and [r.fresh for r in results] == [True, False, False]
