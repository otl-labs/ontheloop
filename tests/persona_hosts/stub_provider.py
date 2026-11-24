"""A stdlib OpenAI-compatible stub for the conformance suite: one read_input tool call, then canary_persona's task."""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_HITS = 0
_TOOL_ARGS = {"blob": "notes", "mode": "lines"}
_NUMBERED = re.compile(r"^(\d+): (.*)$")
_PREFIXES = (("BLOCKING:", "blocking"), ("NIT:", "nit"))
INJECTION_MARKER = "INJECTION FIXTURE"


def _completion(message: dict, finish_reason: str) -> dict:
    return {
        "id": "stub-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _numbered_lines(tool_text: str) -> list[tuple[int, str]]:
    rows = []
    for raw in tool_text.split("\n"):
        match = _NUMBERED.match(raw)
        if match:
            rows.append((int(match.group(1)), match.group(2)))
    return rows


def final_object(tool_text: str) -> dict:
    """canary_persona's task over read_input's text: one finding per BLOCKING: or NIT: line; REVISE on any blocking."""
    rows = _numbered_lines(tool_text)
    if any(line == INJECTION_MARKER for _, line in rows):
        return {"outcome": "failure", "failure": "injection_suspected", "detail": "stub"}
    findings = []
    for number, line in rows:
        for prefix, severity in _PREFIXES:
            if line.startswith(prefix):
                summary = line[len(prefix) :].strip() or line
                findings.append(
                    {"id": f"line:{number}", "severity": severity, "origin": "mechanical", "anchor": line, "summary": summary}
                )
                break
    verdict = "REVISE" if any(f["severity"] == "blocking" for f in findings) else "PROCEED"
    return {"outcome": "success", "verdict": verdict, "findings": findings}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        global _HITS
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        _HITS += 1
        print(f"STUB_HIT {_HITS}", flush=True)
        tool_messages = [m for m in body.get("messages", []) if m.get("role") == "tool"]
        if tool_messages:
            text = str(tool_messages[-1].get("content") or "")
            reply = _completion({"role": "assistant", "content": json.dumps(final_object(text))}, "stop")
        else:
            call = {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_input", "arguments": json.dumps(_TOOL_ARGS)},
            }
            reply = _completion({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls")
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("127.0.0.1" if port == 0 else "0.0.0.0", port), Handler)  # noqa: S104 -- in-network stub
    print(f"STUB_PORT {server.server_address[1]}", flush=True)
    server.serve_forever()
