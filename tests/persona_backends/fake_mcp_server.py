"""A stdio MCP server for tests: the real server projection over a fake reader (synthetic values only).

Run as `python -m tests.persona_backends.fake_mcp_server [mode]` from the repository root. Modes: ok (default), big
(rows past the byte cap), crash (describe() raises at startup), die (the process exits on the first call), hang
(initialize never returns), slow (initialize and list_tools answer, a call never returns).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import anyio

from scripts.agent_sdk import mcp_server

MARKER = "FAKE-SERVER-STDERR-MARKER"
_OBJECT = {"type": "object", "additionalProperties": False}


class FakeReader:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def describe(self) -> dict[str, dict[str, Any]]:
        if self.mode == "crash":
            raise RuntimeError("fake describe failure")
        return {
            "fake_rows": {
                "description": "Fake rows.",
                "params_schema": {**_OBJECT, "properties": {"limit": {"type": "integer"}}},
                "agent_facing": True,
            },
            "fake_by_ids": {
                "description": "Fake rows by id.",
                "params_schema": {
                    **_OBJECT,
                    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["ids"],
                },
                "agent_facing": True,
            },
            "fake_hidden": {"description": "Not agent facing.", "params_schema": dict(_OBJECT), "agent_facing": False},
        }

    def named(self, verb: str, **arguments: Any) -> list[dict[str, Any]]:
        if self.mode == "die":
            os._exit(1)
        if self.mode == "slow":
            time.sleep(3600)
        if self.mode == "big":
            return [{"verb": verb, "text": "x" * 70000}]
        return [{"verb": verb, "arguments": arguments, "text": "synthetic row"}]


def main(argv: list[str]) -> None:
    mode = argv[1] if len(argv) > 1 else "ok"
    ran = os.environ.get("FAKE_MCP_RAN_FILE")
    if ran:
        Path(ran).write_text("ran", encoding="utf-8")
    if mode == "crash":
        print(MARKER, file=sys.stderr, flush=True)
    if mode == "hang":
        time.sleep(3600)
    server = mcp_server.create_server(reader=FakeReader(mode))  # type: ignore[arg-type]
    anyio.run(mcp_server._run_stdio, server)


if __name__ == "__main__":
    main(sys.argv)
