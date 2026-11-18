"""The egress probe, self-check and control verbs with socket, ssl, subprocess and urllib stubbed."""

from __future__ import annotations

import hashlib
import io
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import egress_control, egress_probe, egress_self_check

P = egress_probe


class _Sock:
    def __init__(self, reply: bytes = b"", fail: bool = False) -> None:
        self.reply, self.fail, self.sent = reply, fail, b""

    def __enter__(self) -> _Sock:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def close(self) -> None:
        return None

    def settimeout(self, t: float) -> None:
        return None

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent = data

    def sendall(self, data: bytes) -> None:
        self.sent = data

    def recvfrom(self, n: int) -> tuple[bytes, Any]:
        if self.fail:
            raise OSError("timeout")
        return self.reply, None

    def recv(self, n: int) -> bytes:
        if self.fail:
            raise OSError("timeout")
        return self.reply


def _reply(answers: int, qid: bytes = b"\x4e\x47") -> bytes:
    return qid + struct.pack(">HHHHH", 0x8180, 1, answers, 0, 0)


def _connect(monkeypatch: pytest.MonkeyPatch, ok: bool = True, calls: list[tuple[str, int]] | None = None) -> None:
    def fake(addr: tuple[str, int], timeout: float = 0) -> _Sock:
        if calls is not None:
            calls.append(addr)
        if not ok:
            raise OSError("refused")
        return _Sock(_reply(1))

    monkeypatch.setattr(P.socket, "create_connection", fake)


def _case(case_id: str) -> P.Case:
    return P.BY_ID[case_id]


def test_dns_query_and_answer_parsing() -> None:
    q = P.dns_query("example.com", 16)
    assert q.endswith(b"\x00" + struct.pack(">HH", 16, 1)) and b"\x07example\x03com" in q
    assert P.dns_answers(_reply(2)) == 2
    assert P.dns_answers(b"short") is None and P.dns_answers(_reply(1, b"\x00\x00")) is None


def test_tcp_cases_are_reached_or_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(monkeypatch, True)
    assert P.attempt(_case("raw_ip_443")) is True
    _connect(monkeypatch, False)
    assert P.attempt(_case("raw_ip_443")) is False and P.attempt(_case("ipv6")) is False


def test_listed_is_a_tls_handshake_with_sni(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class Ctx:
        def wrap_socket(self, raw: Any, server_hostname: str) -> _Sock:
            seen.append(server_hostname)
            return _Sock()

    _connect(monkeypatch, True)
    monkeypatch.setattr(P.ssl, "create_default_context", lambda: Ctx())
    assert P.attempt(_case("listed")) is True and seen == ["sts.amazonaws.com"]
    _connect(monkeypatch, False)
    assert P.attempt(_case("listed")) is False
    _connect(monkeypatch, True)

    class Bad:
        def wrap_socket(self, raw: Any, server_hostname: str) -> _Sock:
            raise P.ssl.SSLError("bad")

    monkeypatch.setattr(P.ssl, "create_default_context", lambda: Bad())
    assert P.attempt(_case("listed")) is False


def _addrs(*ips: str) -> Any:
    return lambda host, port, family=0, kind=0: [(0, 0, 0, "", (ip, port)) for ip in ips]


def test_unlisted_name_is_reached_on_a_real_answer_even_if_the_connect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(monkeypatch, False)
    monkeypatch.setattr(P.socket, "getaddrinfo", _addrs("93.184.216.34"))
    assert P.attempt(_case("unlisted_name")) is True


def test_unlisted_name_on_the_sinkhole_is_refused_unless_the_connect_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P.socket, "getaddrinfo", _addrs(P.SINKHOLE))
    calls: list[tuple[str, int]] = []
    _connect(monkeypatch, False, calls)
    assert P.attempt(_case("unlisted_name")) is False and calls == [(P.SINKHOLE, 443)]
    _connect(monkeypatch, True)
    assert P.attempt(_case("unlisted_name")) is True


def test_unlisted_name_that_fails_to_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("nxdomain")

    monkeypatch.setattr(P.socket, "getaddrinfo", boom)
    assert P.attempt(_case("unlisted_name")) is False


def _udp(monkeypatch: pytest.MonkeyPatch, sock: _Sock) -> None:
    monkeypatch.setattr(P.socket, "socket", lambda *a: sock)


def test_dns_udp_is_reached_on_any_response_and_refused_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _udp(monkeypatch, _Sock(_reply(0)))
    assert P.attempt(_case("dns_udp_public")) is True
    _udp(monkeypatch, _Sock(fail=True))
    assert P.attempt(_case("dns_udp_azure")) is False
    _udp(monkeypatch, _Sock(b"junk"))
    assert P.attempt(_case("dns_udp_public")) is False


def test_dns_txt_system_needs_a_txt_answer_from_the_first_nameserver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("# c\nsearch x\nnameserver 10.1.1.1\nnameserver 10.2.2.2\n")
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real_open(resolv if p == "/etc/resolv.conf" else p, *a, **k))
    _udp(monkeypatch, _Sock(_reply(1)))
    assert P.attempt(_case("dns_txt_system")) is True
    _udp(monkeypatch, _Sock(_reply(0)))
    assert P.attempt(_case("dns_txt_system")) is False
    resolv.write_text("search x\n")
    assert P.attempt(_case("dns_txt_system")) is False
    resolv.unlink()
    assert P.attempt(_case("dns_txt_system")) is False


def test_dns_tcp_prefixes_the_length(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _Sock(b"\x00\x20" + _reply(1))
    monkeypatch.setattr(P.socket, "create_connection", lambda addr, timeout=0: sock)
    assert P.attempt(_case("dns_tcp_public")) is True
    assert struct.unpack(">H", sock.sent[:2])[0] == len(sock.sent) - 2
    monkeypatch.setattr(P.socket, "create_connection", lambda addr, timeout=0: _Sock(fail=True))
    assert P.attempt(_case("dns_tcp_azure")) is False
    _connect(monkeypatch, False)
    assert P.attempt(_case("dns_tcp_azure")) is False


def test_sudo_is_reached_only_on_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def runner(rc: int | Exception) -> Any:
        def run(*a: Any, **k: Any) -> Any:
            if isinstance(rc, Exception):
                raise rc
            return type("R", (), {"returncode": rc})()

        return run

    monkeypatch.setattr(P.subprocess, "run", runner(0))
    assert P.attempt(_case("sudo")) is True
    monkeypatch.setattr(P.subprocess, "run", runner(1))
    assert P.attempt(_case("sudo")) is False
    monkeypatch.setattr(P.subprocess, "run", runner(FileNotFoundError()))
    assert P.attempt(_case("sudo")) is False
    monkeypatch.setattr(P.subprocess, "run", runner(P.subprocess.TimeoutExpired("sudo", 5)))
    assert P.attempt(_case("sudo")) is False


def _agent(monkeypatch: pytest.MonkeyPatch, content: bytes | None, tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    if content is not None:
        agent.write_bytes(content)
    monkeypatch.setattr(P, "AGENT_PATH", str(agent))
    monkeypatch.setitem(P.BY_ID, "agent_binary", ("agent_binary", "agent_binary", str(agent), 0))


def test_agent_binary_matches_only_the_read_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    good = b"the read agent"
    monkeypatch.setattr(P, "AGENT_SHA256", hashlib.sha256(good).hexdigest())
    _agent(monkeypatch, good, tmp_path)
    assert P.attempt(_case("agent_binary")) is False
    _agent(monkeypatch, b"another binary", tmp_path)
    assert P.attempt(_case("agent_binary")) is True
    _agent(monkeypatch, None, tmp_path / "missing")
    (tmp_path / "missing").mkdir()
    assert P.attempt(_case("agent_binary")) is True


def test_a_reported_case_never_fails_and_a_mismatch_does() -> None:
    results = [("a", "reached", "reached"), ("b", "reported", "reached"), ("c", "refused", "reached")]
    assert P.failures(results) == ["c"]


def _patch_attempts(monkeypatch: pytest.MonkeyPatch, reached: set[str]) -> None:
    monkeypatch.setattr(P, "attempt", lambda case: case[0] in reached)


def test_probe_main_passes_when_only_listed_is_reached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_attempts(monkeypatch, {"listed", "dns_tcp_azure"})
    assert P.main([]) == 0
    out = capsys.readouterr().out
    assert "EGRESS-PROBE case=listed expect=reached got=reached" in out
    assert "EGRESS-PROBE case=dns_tcp_azure expect=reported got=reached" in out and out.rstrip().endswith("EGRESS-PROBE PASS")


@pytest.mark.parametrize("leak", ["dns_udp_azure", "unlisted_name", "dns_txt_system", "ipv6", "agent_binary", "sudo"])
def test_probe_main_fails_when_a_refused_case_is_reached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], leak: str
) -> None:
    _patch_attempts(monkeypatch, {"listed", leak})
    assert P.main([]) == 1
    assert f"EGRESS-PROBE FAIL {leak}" in capsys.readouterr().out


def test_probe_main_fails_when_the_listed_host_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_attempts(monkeypatch, set())
    assert P.main([]) == 1 and "EGRESS-PROBE FAIL listed" in capsys.readouterr().out


def test_self_check_runs_agent_binary_first_and_passes_when_all_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_attempts(monkeypatch, set())
    assert egress_self_check.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("EGRESS-SELF-CHECK case=agent_binary") and lines[-1] == "EGRESS-SELF-CHECK PASS"
    assert [ln.split()[1] for ln in lines[:-1]] == [f"case={c}" for c in egress_self_check.SELF_CHECK_IDS]


@pytest.mark.parametrize("leak", list(egress_self_check.SELF_CHECK_IDS))
def test_self_check_fails_when_any_of_its_cases_is_reached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], leak: str
) -> None:
    _patch_attempts(monkeypatch, {leak})
    assert egress_self_check.main([]) == 1
    assert f"EGRESS-SELF-CHECK FAIL {leak}" in capsys.readouterr().out


def test_normalise_folds_the_store_hosts_and_drops_hosts_a_wildcard_matches() -> None:
    served = [
        "a.github.com",
        "github.com",
        "*.github.com",
        "productionresultssa3.blob.core.windows.net",
        "ghcr.io",
        "x.actions.githubusercontent.com",
    ]
    assert egress_control.normalise(served, ["*.actions.githubusercontent.com"]) == [
        "*.github.com",
        "ghcr.io",
        "github.com",
        "productionresultssa*.blob.core.windows.net",
    ]
    assert egress_control.normalise([" ", "Ghcr.IO."]) == ["ghcr.io"]
    assert egress_control.wildcard_matches("ab*bc", "abc") is False


def test_vendor_widening_reports_only_hosts_the_snapshot_lacks() -> None:
    snapshot = ["*.github.com", "ghcr.io", "productionresultssa*.blob.core.windows.net"]
    served = ["x.github.com", "ghcr.io", "productionresultssa9.blob.core.windows.net", "evil.example.com"]
    assert egress_control.vendor_widening(served, snapshot) == ["evil.example.com"]
    assert egress_control.vendor_widening(served[:3], snapshot) == []


def test_fetch_vendor_hosts_reads_the_actions_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"domains": {"actions": ["a.com"]}}).encode()
    monkeypatch.setattr(egress_control.urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(body))
    assert egress_control.fetch_vendor_hosts() == ["a.com"]


def _control(monkeypatch: pytest.MonkeyPatch, reached: set[str], served: list[str] | Exception, tmp_path: Path) -> None:
    _patch_attempts(monkeypatch, reached)
    config = tmp_path / "egress.yaml"
    config.write_text("filter_supplied:\n  - ghcr.io\n")
    monkeypatch.setattr(egress_control, "CONFIG", config)

    def fetch() -> list[str]:
        if isinstance(served, Exception):
            raise served
        return served

    monkeypatch.setattr(egress_control, "fetch_vendor_hosts", fetch)


def _all_but(*ids: str) -> set[str]:
    return {c[0] for c in P.CASES} - set(ids)


def test_control_passes_with_every_asserted_case_reached_and_a_matching_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _control(monkeypatch, _all_but("agent_binary", "ipv6", "dns_tcp_azure"), ["ghcr.io"], tmp_path)
    assert egress_control.main([]) == 0
    out = capsys.readouterr().out
    assert "case=agent_binary" not in out and "EGRESS-CONTROL case=ipv6 expect=reported got=refused" in out
    assert "EGRESS-CONTROL VENDOR-LIST ok" in out and out.rstrip().endswith("EGRESS-CONTROL PASS")


def test_control_fails_when_a_case_is_refused_without_the_filter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _control(monkeypatch, _all_but("agent_binary", "sudo"), ["ghcr.io"], tmp_path)
    assert egress_control.main([]) == 1
    assert "EGRESS-CONTROL FAIL sudo" in capsys.readouterr().out


def test_control_fails_on_a_wider_vendor_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _control(monkeypatch, _all_but("agent_binary"), ["ghcr.io", "new.example.com"], tmp_path)
    assert egress_control.main([]) == 1
    out = capsys.readouterr().out
    assert "VENDOR-LIST wider: new.example.com" in out and "EGRESS-CONTROL FAIL vendor_list" in out


@pytest.mark.parametrize("error", [OSError("down"), ValueError("bad json"), KeyError("domains")])
def test_control_fails_when_the_vendor_list_cannot_be_fetched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path, error: Exception
) -> None:
    _control(monkeypatch, _all_but("agent_binary"), error, tmp_path)
    assert egress_control.main([]) == 1
    assert "VENDOR-LIST fetch-failed" in capsys.readouterr().out
