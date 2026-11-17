"""The litellm run_persona backend: a persona-agnostic run loop over an injected transport (T4.32 c1, backend leg).

The transport is injected by the caller (scripts/run_persona.py); this package imports nothing under scripts.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from jsonschema import Draft202012Validator

from src.persona_backends import events as ev
from src.persona_backends import output, read_input
from src.persona_backends.contract import LoadedContract, RunRefused, load

if TYPE_CHECKING:
    from mcp.client.stdio import StdioServerParameters

Transport = Callable[..., Awaitable[Any]]
_FALLBACK = "warm_fetched_escape_hatch"
_REPAIRABLE = frozenset({"empty_output", "malformed_output"})
_ERROR_CAP = 20


class RunAborted(Exception):
    def __init__(self, kind: str, events: list[dict[str, Any]], detail: str = "") -> None:
        super().__init__(f"run aborted: {kind} {detail}".strip())
        self.kind = kind
        self.events = events
        self.detail = detail


def _input_check(loaded: LoadedContract, materialised_input: dict[str, Any]) -> dict[str, Any] | None:
    errors = list(Draft202012Validator(loaded.input_schema).iter_errors(materialised_input))
    if not errors:
        return None
    paths = sorted(f"{output.json_path(err.absolute_path)}: {err.message}" for err in errors)[:_ERROR_CAP]
    required = any(err.validator == "required" for err in errors)
    listed = "input_unusable" in loaded.failure_ids
    failure_id = "input_incomplete" if required or not listed else "input_unusable"
    return output.failure(failure_id, "; ".join(paths))


def _open_devnull() -> TextIO:
    return open(os.devnull, "w", encoding="utf-8")


def _opening(loaded: LoadedContract, materialised_input: dict[str, Any]) -> str:
    lines: list[str] = []
    if loaded.blob_names:
        lines.append("Your input is attached as blobs you read with the read_input tool:")
    for name in loaded.blob_names:
        lines.append(f"{name}: {len(read_input.blob_text(materialised_input.get(name, '')).split(chr(10)))} lines")
    for key in ("change_id", "head_sha"):
        lines.append(f"{key}: {materialised_input.get(key)}")
    lines.append("Return your result as one JSON object.")
    return "\n".join(lines)


class _Run:
    def __init__(
        self,
        loaded: LoadedContract,
        materialised_input: dict[str, Any],
        transport: Transport,
        on_event: Callable[[dict[str, Any]], None] | None,
        max_tokens: int,
        clock: Callable[[], float],
        wall: Callable[[], float],
        head: dict[str, Any],
    ) -> None:
        self.loaded = loaded
        self.blobs = {name: materialised_input[name] for name in loaded.blob_names if name in materialised_input}
        self.transport = transport
        self.on_event = on_event
        self.max_tokens = max_tokens
        self.clock = clock
        self.wall = wall
        self.events: list[dict[str, Any]] = [head]
        self.turns_used = 0
        self.revisions = 0
        self.role = "primary"
        self.first = True
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": loaded.prompt},
            {"role": "user", "content": _opening(loaded, materialised_input)},
        ]
        self.tools: list[dict[str, Any]] = [read_input.tool_spec(loaded.blob_names)] if loaded.blob_names else []
        self.bridge: Any = None
        self.bridge_errors: tuple[type[BaseException], ...] = ()
        self.deadline = 0.0
        self.emit(head)

    def emit(self, record: dict[str, Any]) -> None:
        if self.events[-1] is not record:
            self.events.append(record)
        if self.on_event is not None:
            self.on_event(record)

    def now_ms(self) -> int:
        return int(self.wall() * 1000)

    def end(self, value: dict[str, Any], outcome: str | None = None, error: str | None = None) -> dict[str, Any]:
        failure_id = value.get("failure") if value.get("outcome") == "failure" else None
        record = ev.end_record(
            outcome or str(value.get("outcome")), failure_id, self.turns_used, self.revisions, self.now_ms(), error
        )
        self.emit(record)
        return value

    def turn(self, **fields: Any) -> dict[str, Any]:
        record = ev.turn_record(revision=self.revisions, role=self.role, **fields)
        self.emit(record)
        return record

    def abort(self, kind: str, detail: str = "") -> RunAborted:
        self.end({"outcome": "aborted"}, outcome="aborted", error=kind)
        return RunAborted(kind, self.events, detail)

    def budget_failure(self, detail: str) -> dict[str, Any]:
        return self.end(output.failure("budget_exhausted", detail))

    async def run_with_bridge(
        self, mcp_server: Callable[[], StdioServerParameters] | None, errlog: TextIO | None
    ) -> dict[str, Any]:
        if mcp_server is None:
            raise RunRefused("warehouse_read needs the MCP server")
        try:
            from src.persona_backends import mcp_bridge  # noqa: PLC0415 -- lazy: a run without warehouse_read never loads mcp

            server = mcp_server()
        except Exception as exc:  # intentionally broad: an image without mcp or a failing factory ends the run cleanly
            raise self.abort("tool_server_unavailable", type(exc).__name__) from exc
        log = errlog if errlog is not None else _open_devnull()
        try:
            async with mcp_bridge.open_bridge(
                server, self.loaded.warehouse_verbs, errlog=log, startup_timeout=self.deadline - self.clock()
            ) as bridge:
                self.bridge = bridge
                self.bridge_errors = (mcp_bridge.BridgeFailed,)
                self.tools = [*self.tools, *bridge.tool_specs()]
                return await self.go()
        except mcp_bridge.BridgeTimeout:
            return self.budget_failure("max_wall_clock_seconds reached while the MCP server started")
        except mcp_bridge.BridgeUnavailable as exc:
            raise self.abort("tool_server_unavailable", str(exc)) from exc
        finally:
            if errlog is None:
                log.close()

    async def go(self) -> dict[str, Any]:
        while True:
            remaining = self.deadline - self.clock()
            if self.turns_used >= self.loaded.max_turns:
                return self.budget_failure("max_turns reached")
            if remaining <= 0:
                return self.budget_failure("max_wall_clock_seconds reached")
            done = await self.step(remaining)
            if done is not None:
                return done

    async def step(self, remaining: float) -> dict[str, Any] | None:
        left = self.loaded.max_turns - self.turns_used
        fail_over = self.first and self.role == "primary" and left >= 3
        allowed = min(3, (left - 1) // 2) if fail_over else min(3, left)
        started = self.now_ms()
        upper = allowed + 1 + allowed if fail_over else allowed
        try:
            result = await asyncio.wait_for(
                self.transport(
                    self.loaded.persona,
                    list(self.messages),
                    max_tokens=self.max_tokens,
                    tools=self.tools or None,
                    response_format={"type": "json_object"},
                    timeout=min(600, remaining),
                    role=self.role,
                    fail_over=fail_over,
                    max_attempts=allowed,
                ),
                remaining,
            )
        except asyncio.TimeoutError:
            self.first = False
            self.turns_used += upper
            self.transport_turn((), False, 0, started, "transport_error")
            return self.budget_failure("max_wall_clock_seconds reached during a call")
        except Exception as exc:  # intentionally broad: anything else the transport raises aborts the run
            kind, attempts = getattr(exc, "kind", None), getattr(exc, "attempts", None)
            if not (isinstance(kind, str) and isinstance(attempts, tuple)):
                self.first = False
                raise self.abort("transport_exception", type(exc).__name__) from exc
            return self.on_transport_error(kind, attempts, fail_over, allowed, upper, started, exc)
        self.first = False
        return await self.on_result(result, started)

    def transport_turn(
        self, attempts: tuple[Any, ...], known: bool, probe: int, started: int, kind: str, **extra: Any
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "turn": len([e for e in self.events if "turn" in e]) + 1,
            "attempts": [ev.attempt_dict(a) for a in attempts],
            "attempts_known": known,
            "probe_turn": probe,
            "record": None,
            "failed_over": False,
            "finish_reason": None,
            "tool_calls": [],
            "tool_result_bytes": [],
            "message": None,
            "started_at_ms": started,
            "ended_at_ms": self.now_ms(),
            "kind": kind,
        }
        fields.update(extra)
        return self.turn(**fields)

    def on_transport_error(
        self, kind: str, attempts: tuple[Any, ...], fail_over: bool, allowed: int, upper: int, started: int, exc: Exception
    ) -> dict[str, Any] | None:
        self.first = False
        known = bool(attempts)
        primary_outages = sum(1 for a in attempts if a.role == "primary" and a.outcome == "outage")
        failed_over = any(a.role == _FALLBACK for a in attempts) or (fail_over and primary_outages == allowed and known)
        probe = 1 if failed_over else 0
        self.turns_used += len(attempts) + probe if known else upper
        if failed_over:
            self.role = _FALLBACK
        if kind not in _REPAIRABLE:
            self.transport_turn(attempts, known, probe, started, "transport_error")
            raise self.abort(kind, str(exc))
        self.transport_turn(attempts, known, probe, started, "terminal_invalid", failed_over=failed_over)
        return self.invalid([f"$: the transport returned {kind}"], None)

    def invalid(self, errors: list[str], message: dict[str, Any] | None) -> dict[str, Any] | None:
        self.revisions += 1
        if self.revisions > self.loaded.max_revisions:
            return self.end(output.failure("schema_invalid", "; ".join(errors)))
        if message is not None:
            self.messages.append(message)
        self.messages.append({"role": "user", "content": output.repair_message(errors)})
        return None

    async def on_result(self, result: Any, started: int) -> dict[str, Any] | None:
        returned = self.now_ms()
        probe = 1 if result.failed_over else 0
        self.turns_used += len(result.attempts) + probe
        if result.failed_over:
            self.role = _FALLBACK
        common: dict[str, Any] = {
            "record": dataclasses.asdict(result.record),
            "failed_over": bool(result.failed_over),
            "finish_reason": result.finish_reason,
            "message": result.message,
        }
        if result.tool_calls:
            return await self.on_tools(result, probe, started, returned, common)
        checked = output.check(result.content, result.finish_reason, self.loaded)
        kind = "terminal_valid" if checked.value is not None else "terminal_invalid"
        self.transport_turn(result.attempts, True, probe, started, kind, **common)
        if checked.value is not None:
            return self.end(checked.value)
        return self.invalid(checked.errors, result.message)

    async def on_tools(
        self, result: Any, probe: int, started: int, returned: int, common: dict[str, Any]
    ) -> dict[str, Any] | None:
        texts: list[str] = []
        problem: BaseException | None = None
        tool_errors: tuple[type[BaseException], ...] = (asyncio.TimeoutError, *self.bridge_errors)
        try:
            for call in result.tool_calls:
                texts.append(await self.tool_text(call))
        except tool_errors as exc:
            problem = exc
        self.transport_turn(
            result.attempts,
            True,
            probe,
            started,
            "tool",
            tool_calls=[str(call["function"]["name"]) for call in result.tool_calls],
            tool_result_bytes=[len(text.encode("utf-8")) for text in texts],
            ended_at_ms=returned,
            **common,
        )
        if problem is not None and not isinstance(problem, asyncio.TimeoutError):
            raise self.abort("tool_server_error", str(problem)) from problem
        if problem is not None:
            return self.budget_failure("max_wall_clock_seconds reached during a warehouse call")
        self.messages.append(result.message)
        for call, text in zip(result.tool_calls, texts, strict=True):
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
        return None

    async def tool_text(self, call: dict[str, Any]) -> str:
        function = call["function"]
        name = function["name"]
        if name == "read_input" and self.loaded.blob_names:
            return read_input.serve(function["arguments"], self.blobs)
        if self.bridge is not None and name in self.loaded.warehouse_verbs:
            return await asyncio.wait_for(self.bridge.call(name, function["arguments"]), self.deadline - self.clock())
        return "error: unknown tool"


async def run_persona(
    contract_path: Path,
    materialised_input: dict[str, Any],
    workspace: Path | None,
    backend: str,
    *,
    transport: Transport,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    max_tokens: int = 32000,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
    mcp_server: Callable[[], StdioServerParameters] | None = None,
    mcp_errlog: TextIO | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if backend != "litellm":
        raise RunRefused(f"backend {backend} is not built by this backend (claude_cli is T4.27's, codex_cli is reserved)")
    if workspace is not None:
        raise RunRefused("no workspace capability is built (T4.15:c3)")
    loaded = load(Path(contract_path))
    head = ev.header(
        str(contract_path),
        loaded.persona,
        loaded.contract_version,
        loaded.prompt_sha256,
        ev.input_sha256(materialised_input),
        int(wall() * 1000),
    )
    run = _Run(loaded, materialised_input, transport, on_event, max_tokens, clock, wall, head)
    refusal = _input_check(loaded, materialised_input)
    if refusal is not None:
        return run.end(refusal), run.events
    run.deadline = clock() + loaded.max_wall_clock_seconds
    if loaded.warehouse_verbs:
        result = await run.run_with_bridge(mcp_server, mcp_errlog)
    else:
        result = await run.go()
    return result, run.events
