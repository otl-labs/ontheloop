"""Key-adding reverse proxy with one fixed upstream (PLAN-persona-verdict-container-egress; T4.32 c7 container leg).

Started by the root launcher as a non-root user: /usr/bin/python3 -I -S egress_proxy.py --bind ADDR --port N --upstream HOST
--chat-path PATH --auth bearer|x-api-key, the key (if any) on stdin. Stdlib only; imports nothing from the checkout.
It accepts plain HTTP from the container, one request per connection, forwards only POST to the one chat path, and opens
TLS itself to the one upstream, adding the key. It never logs a header value or a body.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import ssl
import sys
import threading
from collections.abc import Callable, Sequence
from typing import Any

MARKER = "X-Persona-Egress-Proxy"
MAX_HEAD = 16 * 1024
MAX_BODY = 4 * 1024 * 1024
MAX_CONNECTIONS = 8
IO_TIMEOUT = 60.0
UPSTREAM_PORT = 443
REASONS = {400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 411: "Length Required", 502: "Bad Gateway"}
STRIPPED = {"authorization", "x-api-key", "host", "content-length", "connection", "transfer-encoding", "keep-alive"}
AUTH_STYLES = ("bearer", "x-api-key")
KEY_CHARS = range(0x21, 0x7F)


def key_ok(key: str) -> bool:
    return bool(key) and all(ord(c) in KEY_CHARS for c in key)


def address_ok(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified or ip.is_multicast or ip.is_reserved)


def resolve(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, UPSTREAM_PORT, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return None
    answers = {str(info[4][0]) for info in infos}
    if not answers or not all(address_ok(a) for a in answers):
        return None
    return sorted(answers)[0]


def tls_connect(address: str, host: str) -> Any:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    raw = socket.create_connection((address, UPSTREAM_PORT), timeout=IO_TIMEOUT)
    try:
        return context.wrap_socket(raw, server_hostname=host)
    except (OSError, ssl.SSLError):
        raw.close()
        raise


def _own(status: int, kind: str) -> bytes:
    head = f"HTTP/1.1 {status} {REASONS[status]}\r\n{MARKER}: {kind}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    return head.encode("ascii")


class Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def read_head(conn: Any) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEAD:
            raise Refused(400)
        chunk = conn.recv(4096)
        if not chunk:
            raise Refused(400)
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    if len(head) > MAX_HEAD:
        raise Refused(400)
    return head, rest


def parse_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    try:
        lines = head.decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise Refused(400) from exc
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise Refused(400)
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if not colon or not name or name != name.strip():
            raise Refused(400)
        headers.append((name, value.strip()))
    return parts[0], parts[1], headers


def content_length(headers: Sequence[tuple[str, str]]) -> int:
    names = [n.lower() for n, _ in headers]
    has_te = "transfer-encoding" in names
    lengths = [v for n, v in headers if n.lower() == "content-length"]
    if has_te and lengths:
        raise Refused(400)
    if has_te:
        raise Refused(411)
    if not lengths:
        raise Refused(411)
    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        raise Refused(400)
    size = int(lengths[0])
    if size > MAX_BODY:
        raise Refused(400)
    return size


class Proxy:
    def __init__(
        self, host: str, chat_path: str, auth: str, key: str | None, address: str | None, connect: Callable[[str, str], Any]
    ) -> None:
        self.host, self.chat_path, self.auth, self.key, self.address, self.connect = (
            host,
            chat_path,
            auth,
            key,
            address,
            connect,
        )
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def auth_header(self) -> str | None:
        if self.key is None:
            return None
        return f"Authorization: Bearer {self.key}" if self.auth == "bearer" else f"x-api-key: {self.key}"

    def request_head(self, size: int, headers: Sequence[tuple[str, str]]) -> bytes:
        lines = [f"POST {self.chat_path} HTTP/1.1", f"Host: {self.host}", f"Content-Length: {size}", "Connection: close"]
        for name, value in headers:
            low = name.lower()
            if low in STRIPPED or low.startswith("proxy-") or low == MARKER.lower():
                continue
            lines.append(f"{name}: {value}")
        auth = self.auth_header()
        if auth is not None:
            lines.append(auth)
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace")

    def read_request(self, conn: Any) -> tuple[bytes, list[tuple[str, str]], bytes]:
        head, rest = read_head(conn)
        method, target, headers = parse_head(head)
        if method == "CONNECT" or not target.startswith("/"):
            raise Refused(405)
        if method != "POST" or target != self.chat_path:
            raise Refused(404)
        size = content_length(headers)
        body = rest
        while len(body) < size:
            chunk = conn.recv(min(65536, size - len(body)))
            if not chunk:
                raise Refused(400)
            body += chunk
        return body[:size], headers, rest

    def relay(self, conn: Any, upstream: Any) -> int:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = upstream.recv(4096)
            if not chunk:
                raise OSError("upstream closed before a response head")
            buffer += chunk
            if len(buffer) > MAX_HEAD * 4:
                raise OSError("upstream head too large")
        head, _, rest = buffer.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        kept = [lines[0]]
        for line in lines[1:]:
            low = line.split(":", 1)[0].strip().lower()
            if low in (MARKER.lower(), "connection", "keep-alive"):
                continue
            kept.append(line)
        kept.append("Connection: close")
        conn.sendall(("\r\n".join(kept) + "\r\n\r\n").encode("latin-1") + rest)
        while True:
            chunk = upstream.recv(65536)
            if not chunk:
                break
            conn.sendall(chunk)
        try:
            return int(lines[0].split(" ")[1])
        except (IndexError, ValueError):
            return 0

    def serve_connection(self, conn: Any) -> int:
        if not self.slots.acquire(blocking=False):
            conn.sendall(_own(502, "upstream-unreachable"))
            return 502
        try:
            return self._serve(conn)
        finally:
            self.slots.release()

    def _serve(self, conn: Any) -> int:
        try:
            conn.settimeout(IO_TIMEOUT)
            body, headers, _ = self.read_request(conn)
        except Refused as exc:
            conn.sendall(_own(exc.status, "refused"))
            return exc.status
        except OSError:
            return 0
        if self.address is None:
            conn.sendall(_own(502, "upstream-unreachable"))
            return 502
        try:
            upstream = self.connect(self.address, self.host)
        except (OSError, ssl.SSLError):
            conn.sendall(_own(502, "upstream-unreachable"))
            return 502
        try:
            upstream.sendall(self.request_head(len(body), headers) + body)
            return self.relay(conn, upstream)
        except (OSError, ssl.SSLError):
            try:
                conn.sendall(_own(502, "upstream-unreachable"))
            except OSError:
                pass
            return 502
        finally:
            upstream.close()


def handle(proxy: Proxy, conn: Any) -> None:
    try:
        status = proxy.serve_connection(conn)
        sys.stderr.write(f"egress-proxy POST {proxy.chat_path} {status}\n")
        sys.stderr.flush()
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve(proxy: Proxy, bind: str, port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, port))
    listener.listen(16)
    while True:
        conn, _ = listener.accept()
        threading.Thread(target=handle, args=(proxy, conn), daemon=True).start()


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="egress_proxy")
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--chat-path", required=True)
    parser.add_argument("--auth", choices=AUTH_STYLES, required=True)
    args = parser.parse_args(list(argv))
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip("\r\n")
    key: str | None = raw if raw else None
    if key is not None and not key_ok(key):
        print("egress-proxy refused: key has a character outside printable ASCII", file=sys.stderr)
        return 2
    address = resolve(args.upstream)
    if address is None:
        print("egress-proxy: upstream did not resolve to a public address; every request answers 502", file=sys.stderr)
    serve(Proxy(args.upstream, args.chat_path, args.auth, key, address, tls_connect), args.bind, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
