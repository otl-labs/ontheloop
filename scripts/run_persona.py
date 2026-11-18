"""Entry point for the litellm run_persona backend: input JSON in, the result JSON alone on stdout, events to a file.

Usage: <python> [-I] scripts/run_persona.py --contract <path> --input <json path> --events <path> [--max-tokens N]
Exit 0: a result (success or failure value) was produced. 2: refused or unreadable arguments. 4: the run aborted
(transport outage or exception, tool_server_unavailable, tool_server_error).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.client.stdio import StdioServerParameters

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.llm import litellm_transport  # noqa: E402
from src.persona_backends import RunAborted, RunRefused, run_persona, write_atomic  # noqa: E402

MCP_SERVER_MODULE = "scripts.agent_sdk.mcp_server"
_ENV_PREFIXES = ("AWS_", "DUCKLAKE_", "OTL_")
_ENV_NAMES = frozenset(
    {
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)


def server_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in environ.items() if k.startswith(_ENV_PREFIXES) or k in _ENV_NAMES}


def server_parameters(environ: Mapping[str, str] = os.environ) -> StdioServerParameters:
    from mcp.client.stdio import StdioServerParameters  # noqa: PLC0415 -- lazy: a run without warehouse_read loads no mcp

    return StdioServerParameters(command=sys.executable, args=["-m", MCP_SERVER_MODULE], cwd=_ROOT, env=server_env(environ))


async def _transport(*args: Any, **kwargs: Any) -> Any:
    return await litellm_transport.acomplete(*args, **kwargs)


class _EventsFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.head: dict[str, Any] | None = None
        self.turns: list[dict[str, Any]] = []
        self.end: dict[str, Any] | None = None

    def __call__(self, record: dict[str, Any]) -> None:
        if "schema" in record:
            self.head = record
        elif "turns_used" in record:
            self.end = record
        else:
            self.turns.append(record)
        if self.head is not None:
            write_atomic(self.path, self.head, self.turns, self.end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_persona")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--max-tokens", type=int, default=32000)
    args = parser.parse_args(argv)
    try:
        materialised = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"unreadable input: {exc}", file=sys.stderr)
        return 2
    if not isinstance(materialised, dict):
        print("unreadable input: not a JSON object", file=sys.stderr)
        return 2
    sink = _EventsFile(args.events)
    try:
        result, _ = asyncio.run(
            run_persona(
                args.contract,
                materialised,
                None,
                "litellm",
                transport=_transport,
                on_event=sink,
                max_tokens=args.max_tokens,
                mcp_server=server_parameters,
            )
        )
    except RunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except RunAborted as exc:
        print(json.dumps({"error": exc.kind}), file=sys.stderr)
        return 4
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
