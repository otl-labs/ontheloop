"""A stdio MCP client for the warehouse_read binding: one server per run, only the contract's named reads offered.

The server's launch parameters are injected by the caller (scripts/run_persona.py); this module imports nothing
under scripts. Error text names an exception class only (Decision 101).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TextIO

import anyio
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from src.persona_backends.read_input import READ_CAP_BYTES

if TYPE_CHECKING:
    from mcp.client.stdio import StdioServerParameters


class BridgeError(Exception):
    """Base of the bridge's typed failures."""


class BridgeTimeout(BridgeError):
    """The server did not finish starting within the startup bound."""


class BridgeUnavailable(BridgeError):
    """The server could not spawn or start, or lacks a named verb."""


class BridgeFailed(BridgeError):
    """The server session failed after it started."""


def _cut(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= READ_CAP_BYTES:
        return text
    head = data[:READ_CAP_BYTES].decode("utf-8", errors="ignore")
    return f"{head}\n[truncated: {len(data)} bytes]"


class Bridge:
    def __init__(self, session: ClientSession, verbs: tuple[str, ...], specs: list[dict[str, Any]]) -> None:
        self._session = session
        self._verbs = frozenset(verbs)
        self._specs = specs

    def tool_specs(self) -> list[dict[str, Any]]:
        return [dict(spec) for spec in self._specs]

    async def call(self, name: str, arguments_json: str) -> str:
        if name not in self._verbs:
            return "error: unknown tool"
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            return f"error: arguments are not valid JSON ({exc.msg})"
        if not isinstance(arguments, dict):
            return "error: arguments must be a JSON object"
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:  # intentionally broad: any session-level failure is class-only
            raise BridgeFailed(type(exc).__name__) from exc
        text = "\n".join(part.text for part in result.content if isinstance(part, TextContent))
        return _cut(f"error: {text}" if result.is_error else text)


def _specs(listed: Any, verbs: tuple[str, ...]) -> list[dict[str, Any]]:
    by_name = {tool.name: tool for tool in listed.tools}
    specs: list[dict[str, Any]] = []
    for verb in verbs:
        tool = by_name.get(verb)
        if tool is None:
            raise BridgeUnavailable(f"the server does not list the verb {verb}")
        specs.append(
            {
                "type": "function",
                "function": {"name": verb, "description": tool.description or "", "parameters": tool.input_schema},
            }
        )
    return specs


@asynccontextmanager
async def open_bridge(
    server: StdioServerParameters, verbs: tuple[str, ...], *, errlog: TextIO, startup_timeout: float
) -> AsyncIterator[Bridge]:
    recorded: BaseException | None = None
    try:
        async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                try:
                    with anyio.fail_after(startup_timeout):
                        await session.initialize()
                        listed = await session.list_tools()
                    specs = _specs(listed, verbs)
                except TimeoutError as exc:
                    recorded = BridgeTimeout("the server did not start within the startup bound")
                    raise recorded from exc
                except BridgeUnavailable as exc:
                    recorded = exc
                    raise
                except (MCPError, BaseExceptionGroup) as exc:
                    recorded = BridgeUnavailable(f"the server failed during startup: {type(exc).__name__}")
                    raise recorded from exc
                try:
                    yield Bridge(session, verbs, specs)
                except BaseException as exc:
                    recorded = exc
                    raise
    except (OSError, BaseExceptionGroup) as exc:
        if recorded is not None:
            raise recorded  # noqa: B904 -- the recorded exception keeps its own __cause__
        raise BridgeUnavailable(f"the server could not spawn or start: {type(exc).__name__}") from exc
