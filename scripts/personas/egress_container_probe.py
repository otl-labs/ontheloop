"""Red fixture run inside the persona container (PLAN-persona-verdict-container-egress; T4.32 c7 container leg).

Stdlib only; imports nothing from the checkout. The root launcher runs it from its read-only in directory as
`python -I /run/persona/in/egress_container_probe.py`; the control job runs the same file on Docker's default bridge. A case
is reached when the connect, the answer or the HTTP reply arrives and refused otherwise, each attempt bounded by 5 s.
Prints one CONTAINER-PROBE line per case and CONTAINER-PROBE PASS or FAIL, writes the result to events/events.json and
exits 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
from collections.abc import Callable, Sequence

GATEWAY = "172.31.250.1"
DECOY_PORT = 18444
TIMEOUT = 5.0
MARKER = "x-persona-egress-proxy"
EVENTS = "/run/persona/events/events.json"
BASE_SUFFIX = "_API_BASE"
CHAT_PATH_ENV = "PERSONA_PROBE_CHAT_PATH"
Case = tuple[str, str, str, int]
Result = tuple[str, str, str]

CASES: tuple[Case, ...] = (
    ("unlisted_name", "name", "example.com", 443),
    ("dns_txt_system", "dns_txt_system", "example.com", 53),
    ("raw_ip_443", "tcp", "1.1.1.1", 443),
    ("imds", "tcp", "169.254.169.254", 80),
    ("wireserver", "tcp", "168.63.129.16", 80),
    ("private", "tcp", "10.0.0.1", 443),
    ("ipv6", "tcp6", "2606:4700:4700::1111", 443),
    ("decoy_port", "tcp", GATEWAY, DECOY_PORT),
    ("proxy_upstream", "proxy", "", 0),
    ("proxy_fixed_upstream", "proxy_host", "example.com", 0),
)
REACHED_CASES = ("proxy_upstream", "proxy_fixed_upstream")


def dns_query(name: str) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split("."))
    return struct.pack(">HHHHHH", 0x4E47, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", 16, 1)


def _connect(host: str, port: int, family: int = socket.AF_INET) -> bool:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((host, port))
    except OSError:
        return False
    return True


def _name(host: str, port: int) -> bool:
    try:
        return bool(socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM))
    except OSError:
        return False


def _nameserver() -> str | None:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
    except OSError:
        return None
    return None


def _dns_txt(host: str) -> bool:
    server = _nameserver()
    if server is None:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendto(dns_query(host), (server, 53))
            reply = sock.recvfrom(4096)[0]
    except OSError:
        return False
    return len(reply) >= 12 and reply[:2] == b"\x4e\x47" and struct.unpack(">H", reply[6:8])[0] > 0


def base_url() -> tuple[str, int] | None:
    for key, value in os.environ.items():
        if key.endswith(BASE_SUFFIX) and value.startswith("http://"):
            host, _, port = value[len("http://") :].partition(":")
            if host and port.isdigit():
                return host, int(port)
    return None


def proxy_reply(host_header: str | None) -> str:
    target = base_url()
    if target is None:
        return "refused"
    path = os.environ.get(CHAT_PATH_ENV, "/chat/completions")
    host = host_header or f"{target[0]}:{target[1]}"
    body = b"{}"
    request = (
        f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    try:
        with socket.create_connection(target, timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall(request)
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return "refused"
    head = data.partition(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")
    if not head or not head[0].startswith("HTTP/1."):
        return "refused"
    for line in head[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == MARKER:
            return "proxy-refused" if value.strip() == "refused" else "upstream-unreachable"
    return "reached"


def attempt(case: Case) -> str:
    _, kind, target, port = case
    dispatch: dict[str, Callable[[], str]] = {
        "name": lambda: "reached" if _name(target, port) else "refused",
        "dns_txt_system": lambda: "reached" if _dns_txt(target) else "refused",
        "tcp": lambda: "reached" if _connect(target, port) else "refused",
        "tcp6": lambda: "reached" if _connect(target, port, socket.AF_INET6) else "refused",
        "proxy": lambda: proxy_reply(None),
        "proxy_host": lambda: proxy_reply(target),
    }
    return dispatch[kind]()


def run_cases(cases: Sequence[Case]) -> list[Result]:
    return [(case[0], "reached" if case[0] in REACHED_CASES else "refused", attempt(case)) for case in cases]


def failures(results: Sequence[Result]) -> list[str]:
    return [case_id for case_id, want, got in results if want != got]


def emit(results: Sequence[Result]) -> None:
    for case_id, want, got in results:
        print(f"CONTAINER-PROBE case={case_id} expect={want} got={got}", flush=True)


def write_result(line: str, path: str | None = None) -> None:
    try:
        with open(path or EVENTS, "w", encoding="utf-8") as handle:
            json.dump({"result": line}, handle)
    except OSError:
        pass


def main(argv: Sequence[str]) -> int:
    results = run_cases(CASES)
    emit(results)
    bad = failures(results)
    line = "CONTAINER-PROBE PASS" if not bad else "CONTAINER-PROBE FAIL " + " ".join(bad)
    print(line, flush=True)
    write_result(line)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
