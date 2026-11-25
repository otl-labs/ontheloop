"""egress_proxy: one fixed upstream, refused request forms, header handling, marker and key header (fake connector)."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any

import pytest

from scripts.personas import egress_proxy as P

PATH = "/chat/completions"
UPSTREAM_REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\nX-Persona-Egress-Proxy: refused\r\n\r\nok"


class FakeUpstream:
    def __init__(self, reply: bytes = UPSTREAM_REPLY) -> None:
        self.sent = b""
        self.reply = reply
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        out, self.reply = self.reply[:n], self.reply[n:]
        return out

    def close(self) -> None:
        self.closed = True


def _proxy(
    key: str | None = "k3y", auth: str = "bearer", address: str | None = "93.184.216.34", upstream: Any = None
) -> tuple[P.Proxy, Any]:
    fake = upstream if upstream is not None else FakeUpstream()
    seen: list[tuple[str, str]] = []

    def connect(addr: str, host: str) -> Any:
        seen.append((addr, host))
        if isinstance(fake, Exception):
            raise fake
        return fake

    proxy = P.Proxy("api.deepseek.com", PATH, auth, key, address, connect)
    proxy.seen = seen  # type: ignore[attr-defined]
    return proxy, fake


def _exchange(proxy: P.Proxy, request: bytes) -> bytes:
    client, server = socket.socketpair()
    thread = threading.Thread(target=lambda: (proxy.serve_connection(server), server.close()))
    thread.start()
    client.sendall(request)
    client.shutdown(socket.SHUT_WR)
    data = b""
    while chunk := client.recv(4096):
        data += chunk
    thread.join(5)
    client.close()
    return data


def _post(body: bytes = b"{}", extra: str = "", path: str = PATH, method: str = "POST") -> bytes:
    head = f"{method} {path} HTTP/1.1\r\nHost: whatever.example\r\nContent-Length: {len(body)}\r\n{extra}\r\n"
    return head.encode() + body


def test_forwards_to_the_one_upstream_with_host_set_and_key_added() -> None:
    proxy, fake = _proxy()
    reply = _exchange(
        proxy, _post(extra="Authorization: Bearer client\r\nx-api-key: c\r\nProxy-Foo: 1\r\nanthropic-version: 1\r\n")
    )
    assert reply.startswith(b"HTTP/1.1 200 OK") and reply.endswith(b"ok")
    assert proxy.seen == [("93.184.216.34", "api.deepseek.com")]  # type: ignore[attr-defined]
    sent = fake.sent.decode()
    assert "Host: api.deepseek.com\r\n" in sent and "Authorization: Bearer k3y\r\n" in sent
    assert "client" not in sent and "x-api-key" not in sent and "Proxy-Foo" not in sent and "whatever.example" not in sent
    assert "anthropic-version: 1" in sent and "Connection: close" in sent and fake.closed


def test_marker_is_stripped_from_relayed_responses_and_connection_closed() -> None:
    proxy, _ = _proxy()
    reply = _exchange(proxy, _post()).lower()
    assert b"x-persona-egress-proxy" not in reply and b"connection: close" in reply and b"keep-alive" not in reply


def test_x_api_key_style_and_no_key_adds_nothing() -> None:
    proxy, fake = _proxy(auth="x-api-key")
    _exchange(proxy, _post())
    assert "x-api-key: k3y\r\n" in fake.sent.decode()
    proxy, fake = _proxy(key=None)
    _exchange(proxy, _post())
    assert "k3y" not in fake.sent.decode() and "Authorization" not in fake.sent.decode()


@pytest.mark.parametrize("exc", [OSError("down"), P.ssl.SSLError("tls")])
def test_connect_failure_is_a_502_with_the_upstream_unreachable_marker(exc: Exception) -> None:
    proxy, _ = _proxy(upstream=exc)
    assert b"502" in _exchange(proxy, _post()).split(b"\r\n")[0] and b"upstream-unreachable" in _exchange(proxy, _post())


def test_unresolved_upstream_answers_502_and_a_dropped_upstream_too() -> None:
    proxy, _ = _proxy(address=None)
    assert b"502" in _exchange(proxy, _post()) and b"upstream-unreachable" in _exchange(proxy, _post())
    proxy, _ = _proxy(upstream=FakeUpstream(reply=b""))
    assert b"upstream-unreachable" in _exchange(proxy, _post())


@pytest.mark.parametrize(
    ("request_bytes", "status"),
    [
        (_post(path="/other"), b"404"),
        (_post(method="GET"), b"404"),
        (_post(method="CONNECT", path="example.com:443"), b"405"),
        (_post(path="http://example.com/chat/completions"), b"405"),
        (b"POST /chat/completions HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", b"411"),
        (b"POST /chat/completions HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 2\r\n\r\n{}", b"400"),
        (b"POST /chat/completions HTTP/1.1\r\n\r\n", b"411"),
        (b"POST /chat/completions HTTP/1.1\r\nContent-Length: x\r\n\r\n", b"400"),
        (b"POST /chat/completions HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n{}", b"400"),
        (b"POST /chat/completions HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}", b"400"),
        (b"POST /chat/completions HTTP/1.1\r\nContent-Length: 99999999\r\n\r\n", b"400"),
        (b"garbage\r\n\r\n", b"400"),
    ],
)
def test_refused_request_forms_carry_the_refused_marker_and_reach_no_upstream(request_bytes: bytes, status: bytes) -> None:
    proxy, fake = _proxy()
    reply = _exchange(proxy, request_bytes)
    assert reply.split(b"\r\n")[0].split(b" ")[1] == status and b"X-Persona-Egress-Proxy: refused" in reply
    assert fake.sent == b"" and proxy.seen == []  # type: ignore[attr-defined]


def test_a_second_request_on_one_connection_is_never_forwarded() -> None:
    proxy, fake = _proxy()
    _exchange(proxy, _post() + _post(path="/v1/other"))
    assert fake.sent.count(b"POST ") == 1


def test_oversized_header_block_is_refused() -> None:
    proxy, _ = _proxy()
    assert b"400" in _exchange(proxy, b"POST / HTTP/1.1\r\n" + b"A: " + b"b" * 20000 + b"\r\n\r\n")


def test_concurrency_is_bounded() -> None:
    proxy, _ = _proxy()
    for _ in range(P.MAX_CONNECTIONS):
        proxy.slots.acquire()
    client, server = socket.socketpair()
    assert proxy.serve_connection(server) == 502
    assert b"upstream-unreachable" in client.recv(4096)


def test_key_with_a_control_byte_is_refused_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    class In:
        buffer = type("B", (), {"read": staticmethod(lambda: b"bad\x01key\n")})()

    monkeypatch.setattr(P.sys, "stdin", In())
    assert P.main(["--bind", "127.0.0.1", "--port", "1", "--upstream", "h", "--chat-path", PATH, "--auth", "bearer"]) == 2
    assert P.key_ok("abc123") and not P.key_ok("") and not P.key_ok("a b")


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "192.168.0.1", "172.16.0.1", "not-an-ip"])
def test_private_loopback_and_link_local_answers_are_refused(address: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not P.address_ok(address)
    monkeypatch.setattr(P.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 443))])
    assert P.resolve("x") is None


def test_a_public_answer_resolves_and_a_failure_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert P.resolve("x") == "93.184.216.34"

    def boom(*a: Any, **k: Any) -> None:
        raise OSError

    monkeypatch.setattr(P.socket, "getaddrinfo", boom)
    assert P.resolve("x") is None


def test_logs_carry_no_header_value_or_body(capsys: pytest.CaptureFixture[str]) -> None:
    proxy, _ = _proxy()
    client, server = socket.socketpair()
    client.sendall(_post(body=b'{"secret":"v"}', extra="Authorization: Bearer topsecret\r\n"))
    client.shutdown(socket.SHUT_WR)
    P.handle(proxy, server)
    err = capsys.readouterr().err
    assert "topsecret" not in err and "secret" not in err and "k3y" not in err and err.strip().endswith("200")


def _counts(capsys: pytest.CaptureFixture[str]) -> list[dict[str, int]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def _reply(status: str) -> bytes:
    return f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode()


def test_counts_line_records_ok_outage_and_inflight(capsys: pytest.CaptureFixture[str]) -> None:
    proxy, _ = _proxy()
    for status in ("200 OK", "503 Unavailable", "408 Timeout", "429 Slow", "402 Payment"):
        proxy.connect = lambda a, h, status=status: FakeUpstream(_reply(status))  # type: ignore[assignment]
        _exchange(proxy, _post())
    assert _counts(capsys)[-1] == {"ok": 1, "outage": 3, "inflight": 0}
    dead, _ = _proxy(address=None)
    _exchange(dead, _post())
    assert _counts(capsys)[-1] == {"ok": 0, "outage": 1, "inflight": 0}
    hang, _ = _proxy(upstream=OSError("boom"))
    _exchange(hang, _post())
    assert _counts(capsys)[-1] == {"ok": 0, "outage": 1, "inflight": 0}
    stuck, _ = _proxy()
    stuck.tally(inflight=1)
    assert _counts(capsys)[-1] == {"ok": 0, "outage": 0, "inflight": 1}
