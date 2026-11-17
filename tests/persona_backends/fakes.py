"""Scripted fake transport and fake clocks keeping the real transport's shapes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.llm.litellm_transport import TransportResult
from scripts.llm.litellm_wire import Attempt, CallRecord, TransportError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "persona_backends" / "fixture_persona.yaml"
PROMPT = FIXTURE.parent / "rendered" / "fixture_persona.txt"
SHA = "a" * 40
VALID = json.dumps({"outcome": "success", "verdict": "PROCEED", "findings": []})


def good_input(notes: str = "line one\nline two\nline three") -> dict[str, Any]:
    return {"change_id": "c1", "head_sha": SHA, "notes": notes}


def attempt(role: str = "primary", outcome: str = "ok", n: int = 1) -> Attempt:
    return Attempt(role, "m/x", n, outcome, 5)


def record(role: str = "primary") -> CallRecord:
    return CallRecord("fixture_persona", role, "p", "m/x", "m/x", "b", "enabled", "high", 10, 5, 0, 0, None, 1, 5)


def final(content: str = VALID, *, attempts: tuple[Attempt, ...] | None = None, failed_over: bool = False, finish="stop"):
    message = {"role": "assistant", "content": content}
    return TransportResult(content, [], message, finish, record(), attempts or (attempt(),), failed_over)


def tool(arguments: dict[str, Any] | str, name: str = "read_input", *, attempts: tuple[Attempt, ...] | None = None):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": raw}}
    message = {"role": "assistant", "content": "", "tool_calls": [call], "reasoning_content": "thinking"}
    return TransportResult("", [call], message, "tool_calls", record(), attempts or (attempt(),), False)


def error(kind: str, attempts: tuple[Attempt, ...] = ()) -> TransportError:
    return TransportError(kind, f"fake {kind}", attempts=attempts)


class ScriptedTransport:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, persona: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.calls.append({"persona": persona, "messages": messages, **kwargs})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    def __init__(self, step: float = 0.0) -> None:
        self.now = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def drive(transport, materialised=None, *, contract=FIXTURE, workspace=None, backend="litellm", **kwargs):
    import asyncio

    from src.persona_backends import run_persona

    return asyncio.run(
        run_persona(
            contract, good_input() if materialised is None else materialised, workspace, backend, transport=transport, **kwargs
        )
    )
