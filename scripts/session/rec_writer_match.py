"""Pure classifier for the rec writer session tripwire: does a shell command call the ops writer directly.

An early warning for honest mistakes only, never a control (docs/contracts/ducklake_writer.yaml audit_invariants).
No I/O. A lexer ValueError from the shell walker propagates; the hook fails open on it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from scripts.session import protected_action_shell as shell

HTTP_CLIENTS = ("curl", "wget", "xh", "http", "https", "awscurl")
ROUTE_TOKENS = ("ops/writer", "/ducklake/writer_url", "DUCKLAKE_WRITER_URL")
TRANSPORT_CALL = (
    re.compile(r"_ducklake_write\s*\("),
    re.compile(r"_resolve_writer_url\s*\("),
    re.compile(r"scripts\.ops_portal\.writer_transport"),
    re.compile(r"from\s+scripts\.ops_portal\s+import\s+[^\n]*\bwriter_transport\b"),
)
HTTP_LIB = (
    re.compile(r"requests\.(?:post|put|request)\s*\("),
    re.compile(r"urllib\.request"),
    re.compile(r"httpx\."),
    re.compile(r"http\.client"),
    re.compile(r"AWSRequest\s*\("),
)
LAMBDA_OPS = frozenset({"invoke", "invoke-async", "invoke-with-response-stream"})
WRITER_FN = re.compile(r"(^|[:/=])agent-platform-ducklake-writer(:[^:\s]+)?$")
_FAST_TOKENS = (*ROUTE_TOKENS[:1], "/ducklake/writer_url", "DUCKLAKE_WRITER_URL", "_ducklake_write")
_FAST_TOKENS += ("_resolve_writer_url", "writer_transport", "ducklake-writer")
_RESERVED = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!", "{"})
_PORTAL = "scripts.ops_data_portal"


@dataclass(frozen=True)
class Hit:
    shape: str
    token: str


def _client_word(words: list[str]) -> str | None:
    i = 0
    while i < len(words) and words[i] in _RESERVED:
        i += 1
    if i >= len(words):
        return None
    if os.path.basename(words[i]) in HTTP_CLIENTS:
        return words[i]
    if os.path.basename(words[i]) not in shell.WRAPPERS:
        return None
    for word in words[i + 1 :]:
        if os.path.basename(word) in HTTP_CLIENTS:
            return word
    return None


def _runs_portal(parsed: shell.Walk) -> bool:
    for simple in parsed.commands:
        words = simple.words
        if any(a == "-m" and b == _PORTAL for a, b in zip(words, words[1:], strict=False)):
            return True
    return any("ops_data_portal" in code for code in parsed.inline)


def classify(command: str) -> Hit | None:
    if not any(token in command for token in _FAST_TOKENS):
        return None
    parsed = shell.walk(command, HTTP_CLIENTS)
    if _runs_portal(parsed):
        return None
    for simple in parsed.commands:
        if _client_word(simple.words) is None:
            continue
        for word in (w for s in parsed.commands for w in s.words):
            for token in ROUTE_TOKENS:
                if token in word:
                    return Hit("http_client", token)
    for code in parsed.inline:
        for pattern in TRANSPORT_CALL:
            if pattern.search(code):
                return Hit("inline_code", pattern.pattern)
        for token in ROUTE_TOKENS:
            if token in code and any(lib.search(code) for lib in HTTP_LIB):
                return Hit("inline_code", token)
    for simple in parsed.commands:
        if simple.command == "aws" and "lambda" in simple.words:
            if any(w in LAMBDA_OPS for w in simple.words) and any(WRITER_FN.search(w) for w in simple.words):
                return Hit("lambda_invoke", "agent-platform-ducklake-writer")
    return None
