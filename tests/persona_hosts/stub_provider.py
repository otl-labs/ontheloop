"""A stdlib OpenAI-compatible stub for the conformance suite: one read_input tool call, then a valid final object."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_HITS = 0
_FINAL = {"outcome": "success", "verdict": "PROCEED", "findings": []}
_TOOL_ARGS = {"blob": "notes", "mode": "lines"}


def _completion(message: dict, finish_reason: str) -> dict:
    return {
        "id": "stub-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


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
        if any(m.get("role") == "tool" for m in body.get("messages", [])):
            reply = _completion({"role": "assistant", "content": json.dumps(_FINAL)}, "stop")
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
    HTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8080), Handler).serve_forever()
