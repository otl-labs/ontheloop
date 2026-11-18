"""Egress probe cases for the verdict shell's network filter (PLAN-persona-verdict-shell-deny; T4.32 c7 shell leg).

Stdlib only: the filtered job runs it on the runner's system python3. A case is "reached" when it completes (a DNS
answer arrives, a connect or handshake succeeds, sudo exits 0) and "refused" otherwise, each attempt bounded by a
5 s timeout. An expectation is reached, refused or reported; a reported case prints its result and never fails a run.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import struct
import subprocess
from collections.abc import Callable, Sequence

LISTED = ("sts.amazonaws.com", 443)
AGENT_SHA256 = "765b8bdb631bac93645d8039c9b8719c40105d3f444a155d7073859ded0b020a"  # pragma: allowlist secret
AGENT_PATH = "/home/agent/agent"
SINKHOLE = "54.185.253.63"
TIMEOUT = 5.0
AZURE_DNS = "168.63.129.16"
Case = tuple[str, str, str, int]
Result = tuple[str, str, str]

CASES: tuple[Case, ...] = (
    ("listed", "tls", LISTED[0], LISTED[1]),
    ("unlisted_name", "name", "example.com", 443),
    ("dns_txt_system", "dns_txt_system", "example.com", 53),
    ("raw_ip_443", "tcp", "1.1.1.1", 443),
    ("raw_ip_80", "tcp", "1.1.1.1", 80),
    ("raw_ip_other", "tcp", "9.9.9.9", 443),
    ("dns_udp_public", "dns_udp", "8.8.8.8", 53),
    ("dns_tcp_public", "dns_tcp", "8.8.8.8", 53),
    ("dns_udp_azure", "dns_udp", AZURE_DNS, 53),
    ("dns_tcp_azure", "dns_tcp", AZURE_DNS, 53),
    ("dot", "tcp", "1.1.1.1", 853),
    ("ipv6", "tcp", "2606:4700:4700::1111", 443),
    ("sudo", "sudo", "sudo", 0),
    ("agent_binary", "agent_binary", AGENT_PATH, 0),
)
BY_ID = {case[0]: case for case in CASES}


def dns_query(name: str, qtype: int) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split("."))
    return struct.pack(">HHHHHH", 0x4E47, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", qtype, 1)


def dns_answers(reply: bytes) -> int | None:
    if len(reply) < 12 or reply[:2] != b"\x4e\x47":
        return None
    return int(struct.unpack(">H", reply[6:8])[0])


def _connect(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=TIMEOUT).close()
    except OSError:
        return False
    return True


def _tls(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as raw:
            ssl.create_default_context().wrap_socket(raw, server_hostname=host).close()
    except (OSError, ssl.SSLError):
        return False
    return True


def _udp_dns(server: str, qtype: int, need_answer: bool) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendto(dns_query("example.com", qtype), (server, 53))
            count = dns_answers(sock.recvfrom(4096)[0])
    except OSError:
        return False
    return count is not None and (count > 0 or not need_answer)


def _tcp_dns(server: str) -> bool:
    query = dns_query("example.com", 1)
    try:
        with socket.create_connection((server, 53), timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall(struct.pack(">H", len(query)) + query)
            reply = sock.recv(4096)
    except OSError:
        return False
    return dns_answers(reply[2:]) is not None


def _system_nameserver() -> str | None:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
    except OSError:
        return None
    return None


def _name(host: str, port: int) -> bool:
    try:
        found = {str(info[4][0]) for info in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)}
    except OSError:
        return False
    if found - {SINKHOLE}:
        return True
    return _connect(SINKHOLE, port)


def _txt_system(host: str) -> bool:
    server = _system_nameserver()
    return server is not None and _udp_dns(server, 16, True)


def _sudo() -> bool:
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=TIMEOUT, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _agent_binary(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return True
    return digest != AGENT_SHA256


def attempt(case: Case) -> bool:
    _, kind, target, port = case
    dispatch: dict[str, Callable[[], bool]] = {
        "tls": lambda: _tls(target, port),
        "name": lambda: _name(target, port),
        "dns_txt_system": lambda: _txt_system(target),
        "tcp": lambda: _connect(target, port),
        "dns_udp": lambda: _udp_dns(target, 1, False),
        "dns_tcp": lambda: _tcp_dns(target),
        "sudo": _sudo,
        "agent_binary": lambda: _agent_binary(target),
    }
    return dispatch[kind]()


def run_cases(cases: Sequence[Case], expect: dict[str, str]) -> list[Result]:
    return [(case[0], expect[case[0]], "reached" if attempt(case) else "refused") for case in cases]


def failures(results: Sequence[Result]) -> list[str]:
    return [case_id for case_id, want, got in results if want != "reported" and want != got]


def emit(prefix: str, results: Sequence[Result]) -> None:
    for case_id, want, got in results:
        print(f"{prefix} case={case_id} expect={want} got={got}")


def main(argv: Sequence[str]) -> int:
    expect = {case[0]: "refused" for case in CASES}
    expect["listed"] = "reached"
    expect["dns_tcp_azure"] = "reported"
    results = run_cases(CASES, expect)
    emit("EGRESS-PROBE", results)
    bad = failures(results)
    print("EGRESS-PROBE PASS" if not bad else "EGRESS-PROBE FAIL " + " ".join(bad))
    return 0 if not bad else 1
