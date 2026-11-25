"""egress_container_probe: case table, PASS/FAIL lines, exit codes and a marked reply never counted as reached."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from scripts.personas import egress_container_probe as P


def test_case_table_names_every_planned_case() -> None:
    ids = {c[0] for c in P.CASES}
    assert ids == {
        "unlisted_name", "dns_txt_system", "raw_ip_443", "imds", "wireserver", "private", "ipv6", "decoy_port",
        "proxy_upstream", "proxy_fixed_upstream",
    }  # fmt: skip
    assert P.REACHED_CASES == ("proxy_upstream", "proxy_fixed_upstream")


def _fake(monkeypatch: pytest.MonkeyPatch, got: dict[str, str]) -> None:
    monkeypatch.setattr(P, "attempt", lambda case: got[case[0]])


def _all(value: str, **over: str) -> dict[str, str]:
    got = {c[0]: ("reached" if c[0] in P.REACHED_CASES else value) for c in P.CASES}
    return {**got, **over}


def test_pass_writes_the_result_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake(monkeypatch, _all("refused"))
    target = tmp_path / "events.json"
    monkeypatch.setattr(P, "EVENTS", str(target))
    assert P.main([]) == 0
    out = capsys.readouterr().out
    assert "CONTAINER-PROBE case=imds expect=refused got=refused" in out and out.strip().endswith("CONTAINER-PROBE PASS")
    assert json.loads(target.read_text())["result"] == "CONTAINER-PROBE PASS"


def test_a_reached_refusal_or_refused_proxy_case_fails_with_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(P, "EVENTS", "/nonexistent/events.json")
    _fake(monkeypatch, _all("refused", raw_ip_443="reached", proxy_upstream="upstream-unreachable"))
    assert P.main([]) == 1
    assert "CONTAINER-PROBE FAIL raw_ip_443 proxy_upstream" in capsys.readouterr().out


def test_write_result_survives_an_unwritable_path(tmp_path: Path) -> None:
    P.write_result("x", str(tmp_path / "missing" / "events.json"))
    P.write_result("y", str(tmp_path / "ok.json"))
    assert json.loads((tmp_path / "ok.json").read_text()) == {"result": "y"}


def _serve(reply: bytes) -> int:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def run() -> None:
        conn, _ = listener.accept()
        conn.recv(65536)
        conn.sendall(reply)
        conn.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    return int(listener.getsockname()[1])


@pytest.mark.parametrize(
    ("reply", "want"),
    [
        (b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n", "reached"),
        (b"HTTP/1.1 502 Bad Gateway\r\nX-Persona-Egress-Proxy: upstream-unreachable\r\n\r\n", "upstream-unreachable"),
        (b"HTTP/1.1 404 Not Found\r\nX-Persona-Egress-Proxy: refused\r\n\r\n", "proxy-refused"),
        (b"not http", "refused"),
    ],
)
def test_a_marked_proxy_reply_is_never_counted_as_reached(monkeypatch: pytest.MonkeyPatch, reply: bytes, want: str) -> None:
    monkeypatch.setenv("DEEPSEEK_API_BASE", f"http://127.0.0.1:{_serve(reply)}")
    assert P.proxy_reply(None) == want


def test_no_base_url_means_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in P.os.environ if k.endswith("_API_BASE")]:
        monkeypatch.delenv(key)
    assert P.base_url() is None and P.proxy_reply("x") == "refused"


def test_dns_query_shape_and_tcp_refusal() -> None:
    assert P.dns_query("a.b")[12:] == b"\x01a\x01b\x00\x00\x10\x00\x01"
    assert P._connect("127.0.0.1", 1) is False


def _report_file(tmp_path: Path, content: str | None) -> str:
    path = tmp_path / "input.json"
    if content is not None:
        path.write_text(content)
    return str(path)


def test_report_mode_reports_unreachable_upstream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(P, "EVENTS", str(tmp_path / "events.json"))
    monkeypatch.setattr(P, "INPUT", _report_file(tmp_path, '{"upstream": "report"}'))
    _fake(monkeypatch, _all("refused", proxy_upstream="upstream-unreachable", proxy_fixed_upstream="upstream-unreachable"))
    assert P.main([]) == 0
    out = capsys.readouterr().out
    assert "case=proxy_upstream expect=reached got=upstream-unreachable REPORT" in out and out.strip().endswith("PASS")
    _fake(monkeypatch, _all("refused", proxy_upstream="proxy-refused"))
    assert P.main([]) == 1
    _fake(monkeypatch, _all("refused", raw_ip_443="reached", proxy_upstream="upstream-unreachable"))
    assert P.main([]) == 1


@pytest.mark.parametrize("content", ["{}", '{"upstream": "other"}', '{"upstream": "report", "x": 1}', "[1]", "nope", None])
def test_other_inputs_stay_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    monkeypatch.setattr(P, "EVENTS", str(tmp_path / "events.json"))
    monkeypatch.setattr(P, "INPUT", _report_file(tmp_path, content))
    _fake(monkeypatch, _all("refused", proxy_upstream="upstream-unreachable"))
    assert P.main([]) == 1
    assert "REPORT" not in capsys.readouterr().out
