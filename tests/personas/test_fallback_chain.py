"""The fallback signal chain, hermetic: proxy counts line -> launcher counts reader -> run result -> primary_outage output."""

from __future__ import annotations

import io
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import egress_launcher as launcher
from scripts.personas import egress_proxy as proxy_module
from scripts.personas import keyed_run

ROOT = Path(__file__).resolve().parents[2]
GOOD = {"change_id": "o/r#1", "head_sha": "a" * 40, "plan": "", "notes": "BLOCKING: x", "fixture_guidance": ""}


class Answering:
    def __init__(self) -> None:
        self.reply = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

    def sendall(self, data: bytes) -> None:
        pass

    def recv(self, n: int) -> bytes:
        out, self.reply = self.reply[:n], self.reply[n:]
        return out

    def close(self) -> None:
        pass


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _drive(connect: Any) -> bytes:
    proxy = proxy_module.Proxy("api.deepseek.com", "/chat/completions", "bearer", "k", "93.184.216.34", connect)
    client, server = socket.socketpair()
    thread = threading.Thread(target=lambda: (proxy.serve_connection(server), server.close()))
    thread.start()
    body = b"{}"
    client.sendall(b"POST /chat/completions HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    client.shutdown(socket.SHUT_WR)
    while client.recv(4096):
        pass
    thread.join(5)
    client.close()
    return b""


def _output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], connect: Any, code: int
) -> str:
    _drive(connect)
    session = launcher.Session()
    launcher.read_counts(session, io.BytesIO(capsys.readouterr().out.encode()))
    result = {"exit_code": code, "timed_out": False, "stdout": "", "upstream": session.upstream}
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": ["canary_persona"], "plan_paths": []}))
    (work / "prepared" / "input-canary_persona.json").write_text(json.dumps(GOOD))
    out = tmp_path / "out"
    assert keyed_run.record({"GITHUB_JOB": "run", "GITHUB_OUTPUT": str(out)}, work, ROOT, json.dumps(result).encode()) == 0
    return out.read_text()


def test_unreachable_primary_sets_primary_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    port = _closed_port()

    def refused(address: str, host: str) -> Any:
        return socket.create_connection(("127.0.0.1", port), timeout=2)

    assert _output(tmp_path, monkeypatch, capsys, refused, 4) == "primary_outage=true\n"


def test_an_answering_primary_does_not_set_primary_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _output(tmp_path, monkeypatch, capsys, lambda address, host: Answering(), 1) == "primary_outage=false\n"
