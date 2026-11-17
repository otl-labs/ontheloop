"""rec-5112's TestMissingUsage: a response with no usage attribute gives a record, never an exception.

The other three rec-5112 fixes are tested in each source's own mapped test file.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scripts.llm import litellm_cold_start as cs
from scripts.llm import litellm_transport as t
from tests.fixtures import litellm_capture as cap

PRIMARY_ABSENT = ("tokens_cache_read", "tokens_input", "tokens_output")
FALLBACK_ABSENT = ("tokens_cache_creation", "tokens_cache_read", "tokens_input", "tokens_output")


class _Message:
    content = "OK"
    tool_calls = None

    def model_dump(self) -> dict[str, Any]:
        return {"role": "assistant", "content": "OK"}


def _no_usage_response() -> SimpleNamespace:
    return SimpleNamespace(model="served", choices=[SimpleNamespace(message=_Message(), finish_reason="stop")])


class _FakeLitellm:
    def completion(self, **_: Any) -> SimpleNamespace:
        return _no_usage_response()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    cap.reset(monkeypatch)
    cap.set_keys(monkeypatch)
    monkeypatch.setattr(t, "load_litellm", lambda: _FakeLitellm())
    monkeypatch.setattr(cs, "load_litellm", lambda: _FakeLitellm())


class TestMissingUsage:
    def test_primary_role_through_the_transport(self) -> None:
        result = t.complete(cap.PERSONA, [{"role": "user", "content": "hi"}], max_tokens=10)
        assert result.record.usage_absent == PRIMARY_ABSENT and result.record.usage_raw is None
        assert (result.record.tokens_input, result.record.tokens_output) == (0, 0)

    def test_fallback_role_through_cold_start(self) -> None:
        result = cs.cold_start_check("warm_fetched_escape_hatch")
        assert result.outcome == "asserted" and result.record is not None
        assert result.record.usage_absent == FALLBACK_ABSENT
