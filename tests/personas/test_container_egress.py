"""Container egress (rec-5549): the hermetic acceptance test and the local twin of the red fixture."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import egress_floor as base
from scripts.personas import egress_floor_container as floor
from scripts.personas import egress_launcher as L
from scripts.personas import egress_proxy as P

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.example/img@sha256:" + "a" * 64


class _Upstream:
    def __init__(self) -> None:
        self.sent = b""
        self.reply = b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n"

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        out, self.reply = self.reply[:n], self.reply[n:]
        return out

    def close(self) -> None:
        pass


def test_container_on_internal_network_refuses_unlisted_hosts() -> None:
    network = floor.network_argv()
    assert "--internal" in network and "--ipv6=false" in network and network[-1] == L.NETWORK
    provider = floor.PROVIDERS["deepseek/"]
    argv = L.container_argv("run", IMAGE, "code_reviewer", provider, "/in", "/ev")
    assert argv[argv.index("--network") + 1] == network[-1] == "persona-egress"
    assert not any(a in ("-p", "--publish", "--add-host", "--env-file", "--privileged") for a in argv)
    assert "REAL-KEY" not in " ".join(argv) and argv.count("-e") == 2
    assert argv[argv.index("--dns") + 1] == "0.0.0.0"

    bridges = floor.bridge_rules()
    assert bridges["v4"]["EGRESS_BRIDGE_IN"][0].endswith(f"-d {L.GATEWAY}/32 -p tcp -m tcp --dport {L.PROXY_PORT} -j RETURN")
    assert bridges["v4"]["EGRESS_BRIDGE_IN"][-1].endswith("-j DROP")
    assert all(r.endswith("-j DROP") for r in bridges["v4"]["EGRESS_BRIDGE_FWD"] + bridges["v6"]["EGRESS_BRIDGE_IN"])
    assert floor.PROXY_PORT == L.PROXY_PORT and floor.GATEWAY == L.GATEWAY and floor.BRIDGE == "br-persona"

    seen: list[tuple[str, str]] = []
    upstream = _Upstream()
    proxy = P.Proxy(
        "api.deepseek.com",
        provider["chat_path"],
        "bearer",
        "REAL-KEY",
        "93.184.216.34",
        lambda a, h: (seen.append((a, h)), upstream)[1],
    )
    client, server = socket.socketpair()
    client.sendall(b"POST /chat/completions HTTP/1.1\r\nHost: unlisted.example\r\nContent-Length: 2\r\n\r\n{}")
    proxy.serve_connection(server)
    assert seen == [("93.184.216.34", "api.deepseek.com")]
    assert b"Host: api.deepseek.com\r\n" in upstream.sent and b"unlisted.example" not in upstream.sent


def _twin_ready() -> str | None:
    if os.environ.get("PERSONA_TWIN") != "1":
        return "PERSONA_TWIN=1 not set"
    if os.geteuid() != 0:
        return "not root"
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0:
        return "no dockerd"
    return None


def _sh(*argv: str) -> None:
    subprocess.run(list(argv), capture_output=True, check=False, timeout=300)


@pytest.mark.integration
def test_local_twin_red_fixture(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    reason = _twin_ready()
    if reason:
        pytest.skip("env: the local twin needs PERSONA_TWIN=1, root and a dockerd")
    from pytest_socket import _remove_restrictions, enable_socket  # noqa: PLC0415

    enable_socket()
    _remove_restrictions()
    docker = base.binary("docker")
    temp = Path(tempfile.mkdtemp(prefix="twin-", dir="/tmp"))
    os.chmod(temp, 0o755)
    install, run_dir = temp / "install", temp / "run"
    created_user = (
        subprocess.run(
            ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", floor.PROXY_USER],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    image, digest = floor.read_image_ref(ROOT)
    try:
        pulled = subprocess.run(
            [docker, "pull", f"{image}@{digest}"], capture_output=True, text=True, timeout=300, check=False
        )
        assert pulled.returncode == 0, "the twin cannot pull the pinned image: " + pulled.stderr[-300:]
        floor.install_files(floor.snapshot_text(image, digest), ROOT, install)
        _sh(docker, "network", "rm", floor.NETWORK)
        assert subprocess.run(floor.network_argv(), capture_output=True, check=False).returncode == 0
        bridges = floor.bridge_rules()
        for tool, key in (("iptables", "v4"), ("ip6tables", "v6")):
            for chain, rules in bridges[key].items():
                assert base._chain(tool, chain, rules)
            for jump in floor.bridge_jumps():
                assert floor._jump_at(tool, jump)
        monkeypatch.setattr(L, "INSTALL_DIR", install)
        monkeypatch.setattr(L, "RUN_DIR", run_dir)
        monkeypatch.setattr(
            L.sys,
            "stdin",
            type(
                "I",
                (),
                {
                    "buffer": type(
                        "B",
                        (),
                        {
                            "read": staticmethod(
                                lambda n: json.dumps(
                                    {"persona": "fixture_probe", "provider": "deepseek/", "input": {}}
                                ).encode()
                            )
                        },
                    )()
                },
            )(),
        )
        code = L.main(["probe"])
        err = capfd.readouterr().err
        lines = {}
        for line in err.splitlines():
            if line.startswith("CONTAINER-PROBE case="):
                fields: dict[str, Any] = dict(p.split("=", 1) for p in line.split()[1:] if "=" in p)
                lines[fields["case"]] = fields["got"]
        refused = {"unlisted_name", "dns_txt_system", "raw_ip_443", "imds", "wireserver", "private", "ipv6", "decoy_port"}
        assert {c: lines.get(c) for c in refused} == dict.fromkeys(refused, "refused"), err
        for case in ("proxy_upstream", "proxy_fixed_upstream"):
            assert lines.get(case) in ("reached", "upstream-unreachable"), err
        print(f"TWIN upstream result: {lines.get('proxy_upstream')} (exit {code})")
    finally:
        for tool in ("iptables", "ip6tables"):
            for jump in floor.bridge_jumps():
                spec = jump.split()
                _sh(base.binary(tool), "-w", "-t", "mangle", "-D", spec[1], *spec[2:])
            for chain in (floor.BRIDGE_IN, floor.BRIDGE_FWD):
                _sh(base.binary(tool), "-w", "-t", "mangle", "-F", chain)
                _sh(base.binary(tool), "-w", "-t", "mangle", "-X", chain)
        _sh(docker, "kill", L.CONTAINER)
        _sh(docker, "network", "rm", floor.NETWORK)
        if created_user:
            _sh("userdel", floor.PROXY_USER)
        shutil.rmtree(temp, ignore_errors=True)
        threading.Event()
