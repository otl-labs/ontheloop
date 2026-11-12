"""READ-ONLY MCP stdio server projecting DuckLake NAMED_READS as agent tools (T0.8, Decision 81 cl.2).

On startup, calls DuckLakeReader.describe() ONCE and projects one MCP tool per returned verb -- the
tool list is DERIVED from the warehouse's own registry, never hand-maintained: adding a verb to
NAMED_READS surfaces a new tool automatically, with zero edits to this module. Projects nothing
else: no write verb, no infra/probe action, and no raw-SQL parameter (Decision 84 I-3 -- the
named-verb boundary only). A failed describe() at startup RAISES; there is no fallback to a
hardcoded verb list (Decision 55).

WRITE LEG DEFERRED (operator decision, round-1 plan critique): the write verbs' only prospective
bound was a module-level Python literal inside this agent-editable server. Like any guard in this
agent-editable module, that is the in-handler kind Decision 143 cl.1/cl.3 rejects. Decision 143
requires the write leg's identity boundary to be enforced by IAM and resource shape, kept
outside the agent merge loop, and T4.10a c7 gates the write leg on it. This module therefore
projects reads only until that boundary exists.

KNOWN LIMITATION: the tool list is fetched once at process startup (stdio transport is one process
per session) -- a verb added to NAMED_READS after this process started surfaces only on the next
session (server restart). The derivation property still holds; freshness is per-session, not live.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, cast

import anyio
import jsonschema
import mcp_types as types

from scripts.agent_sdk.errors import AgentToolError, map_reader_error
from src.common.ducklake_reader_client import DuckLakeReader, ReaderInvokeError, make_reader

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

logger = logging.getLogger(__name__)

SERVER_NAME = "agent-platform-ducklake-reads"

# Decision 126 ambience mitigation: agents reach for whatever surface is most VISIBLE, not
# whichever is sanctioned. Every projected tool's description carries this cache-first guidance so
# a verb added to NAMED_READS later inherits it automatically -- applied once, here, by the shared
# projection template, never restated per-verb.
_CACHE_FIRST_NOTE = " Read recommendations through these named verbs; there is no local recommendations cache (Decision 215)."


def _tool_description(meta: dict[str, Any]) -> str:
    """Build one tool's description: the verb's own text plus the shared cache-first guidance."""
    return f"{meta['description']}{_CACHE_FIRST_NOTE}"


def build_tool_registry(verbs: dict[str, dict[str, Any]]) -> list[types.Tool]:
    """Project one MCP Tool per entry in *verbs* (a describe()-shaped verb -> schema mapping).

    Derived, not hand-maintained: this function iterates whatever *verbs* contains and must never
    filter against a local list of known verb names -- doing so would reinstate the
    hand-registration this design exists to eliminate. A verb is projected unless the registry itself
    declares it not agent-facing (a describe without the key projects everything).
    """
    return [
        types.Tool(name=verb, description=_tool_description(meta), input_schema=meta["params_schema"])
        for verb, meta in verbs.items()
        if meta.get("agent_facing", True)
    ]


def _invoke_verb(reader: Any, verb: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Call the reader's named() verb, mapping a boundary failure onto a typed exception.

    Never swallows an error: a ReaderInvokeError is mapped via errors.map_reader_error and
    re-raised, so a boundary rejection surfaces as a distinct type rather than a generic failure.
    """
    try:
        return reader.named(verb, **arguments)
    except ReaderInvokeError as exc:
        raise map_reader_error(exc) from exc


def _error_result(text: str, structured: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], structured_content=structured, is_error=True
    )


def _call_tool_result(
    reader: Any, tool_by_name: dict[str, types.Tool], name: str, arguments: dict[str, Any]
) -> types.CallToolResult:
    """Run one tool call and return a CallToolResult on every path.

    A raised exception under mcp 2.x becomes a JSON-RPC -32603 error rather than an isError result, so nothing
    escapes. Only the AgentToolError family's text reaches the client; any other exception is reported by class
    name alone (Decision 101).
    """
    tool = tool_by_name.get(name)
    if tool is None:
        text = f"unknown tool {name!r}: expected one of {sorted(tool_by_name)}"
        return _error_result(text, {"error_type": "unknown_tool", "message": text})
    try:
        jsonschema.validate(arguments, tool.input_schema)
        rows = _invoke_verb(reader, name, arguments)
        payload = {"rows": rows, "row_count": len(rows)}
        text = json.dumps(payload, indent=2)
    except jsonschema.ValidationError as exc:
        text = f"Input validation error: {exc.message}"
        return _error_result(text, {"error_type": "input_validation", "message": text})
    except AgentToolError as exc:
        return _error_result(str(exc), {"error_type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 -- class-only text; the exception text may carry a URL or a value
        return _error_result(
            f"{name} failed: class={type(exc).__name__}", {"error_type": "internal", "error_class": type(exc).__name__}
        )
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=payload)


def _default_surface() -> tuple[Any, dict[str, dict[str, Any]]]:
    """The routed reader and its merged describe; the ops tools alone when the event route is unavailable.

    The ops describe stays fail-loud. The routed describe reuses it, so on the smoke route a transient failure there cannot
    drop the event tools the shared reader serves. If the routed describe raises (the catalog deploy output, the telemetry
    reader's URL or its describe), the server serves the ops describe's non-event verbs, and one stderr line names the
    failure class only, never a URL or a value (Decision 101).
    """
    from scripts.telemetry_capture import read_route  # noqa: PLC0415

    # make_reader() is annotated -> Reader (the Protocol, which deliberately does not declare
    # describe() -- see src/common/ducklake_reader_client.py's Reader docstring) but only ever
    # constructs a DuckLakeReader in practice; cast narrows the type at this one call site
    # rather than widening the Protocol by omission.
    ops = cast(DuckLakeReader, make_reader())
    ops_verbs = ops.describe()
    routed = read_route.make_routed_reader(ops=ops, ops_verbs=ops_verbs)
    try:
        return routed, routed.describe()
    except Exception as exc:  # noqa: BLE001 -- the event route degrades; the ops tools stand
        print(f"{SERVER_NAME}: event tools unavailable class={type(exc).__name__}", file=sys.stderr)
        return ops, {verb: meta for verb, meta in ops_verbs.items() if verb not in read_route.EVENT_VERBS}


def create_server(reader: DuckLakeReader | None = None) -> Server:
    """Build the MCP server: fetch the verb schema ONCE and register the derived tool surface.

    Fails loudly (Decision 55): an injected reader's describe() failure, or the default ops reader's (unreachable reader,
    credentials, a boundary error), raises here rather than falling back to a hardcoded verb list. The default path
    routes event verbs by the writer's catalog (scripts/telemetry_capture/read_route.py) and serves every tool through the
    routed reader, or serves the ops tools alone when the event route is unavailable (_default_surface).
    """
    from mcp.server.lowlevel import Server  # noqa: PLC0415 -- lazy: importing mcp loads mcp.server.stdio, which imports fcntl off Windows

    if reader is not None:
        active_reader: Any = reader
        verbs = reader.describe()
    else:
        active_reader, verbs = _default_surface()

    tools = build_tool_registry(verbs)
    tool_by_name = {tool.name: tool for tool in tools}

    async def _list_tools(_ctx: Any, _params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def _call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return _call_tool_result(active_reader, tool_by_name, params.name, params.arguments or {})

    server: Server = Server(SERVER_NAME, on_list_tools=_list_tools, on_call_tool=_call_tool)
    return server


def stdio_server() -> AbstractAsyncContextManager[tuple[Any, Any]]:
    from mcp.server.stdio import stdio_server as sdk_stdio_server  # noqa: PLC0415 -- lazy: importing mcp loads mcp.server.stdio, which imports fcntl off Windows

    return sdk_stdio_server()


async def _run_stdio(server: Server) -> None:
    from mcp.server.lowlevel import NotificationOptions  # noqa: PLC0415 -- lazy: importing mcp loads mcp.server.stdio, which imports fcntl off Windows

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(notification_options=NotificationOptions()),
        )


def main() -> None:
    server = create_server()
    anyio.run(_run_stdio, server)


if __name__ == "__main__":
    main()
