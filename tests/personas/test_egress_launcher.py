"""egress_launcher: install checks, each refused stdin form, argv parity with run_argv, results read, cleanup, exit codes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import egress_floor_container as floor
from scripts.personas import egress_launcher as L
from src.persona_hosts.local_container import adapter

HANDLERS: dict[Any, Any] = {}
PROVIDERS = floor.PROVIDERS
PROVIDER = PROVIDERS["deepseek/"]
IMAGE = "registry.example/img@sha256:" + "a" * 64


def _install(tmp_path: Path, mutate: str | None = None) -> Path:
    for name in ("egress_launcher.py", "egress_proxy.py", "egress_container_probe.py"):
        (tmp_path / name).write_bytes((Path(L.__file__).parent / name).read_bytes())
    (tmp_path / L.SNAPSHOT_NAME).write_text(json.dumps({"image": IMAGE, "providers": PROVIDERS}))
    files = {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in L.FILES}
    (tmp_path / L.MANIFEST_NAME).write_text(json.dumps({"files": files}))
    if mutate:
        (tmp_path / mutate).write_text("tampered")
    return tmp_path


def _sub(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _req(**over: Any) -> bytes:
    body = {"persona": "code_reviewer", "provider": "deepseek/", "input": {}, "key": "k3y"}
    body.update(over)
    return json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def _parse(raw: bytes, mode: str = "run") -> dict[str, Any]:
    return L.parse_request(raw, mode, PROVIDERS)


@pytest.mark.parametrize(
    "raw",
    [
        b"[]", b"1", b"nope", b"\xff\xfe", b'{"persona":"a","persona":"b"}',
        _req(extra=1), _req(persona="Bad Name"), _req(persona=3), _req(provider="other/"), _req(provider=1),
        _req(input=[]), _req(key=""), _req(key="a b"), _req(key="a\x01b"), _req(key=5), _req(key=None),
    ],
)  # fmt: skip
def test_each_refused_stdin_form_in_run_mode(raw: bytes) -> None:
    with pytest.raises(L.Refused):
        _parse(raw)


def test_probe_mode_refuses_a_key_and_run_mode_accepts_a_good_request() -> None:
    with pytest.raises(L.Refused):
        _parse(_req(), "probe")
    assert _parse(_req(key=None), "probe")["persona"] == "code_reviewer"
    assert _parse(_req())["key"] == "k3y"
    with pytest.raises(L.Refused):
        _parse(b" " * (L.MAX_REQUEST + 1))


def test_install_check_refuses_a_tampered_file_wrong_owner_and_unknown_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _install(_sub(tmp_path, "a"))
    own = good / "egress_launcher.py"
    monkeypatch.setattr(L.os, "stat", lambda p: type("S", (), {"st_uid": 0, "st_mode": 0o100644})())
    assert L.check_install(good, own)["image"] == IMAGE
    bad = _install(_sub(tmp_path, "b"), "egress_proxy.py")
    with pytest.raises(L.Refused):
        L.check_install(bad, bad / "egress_launcher.py")
    monkeypatch.setattr(L.os, "stat", lambda p: type("S", (), {"st_uid": 1000, "st_mode": 0o100644})())
    with pytest.raises(L.Refused):
        L.check_install(good, own)
    monkeypatch.setattr(L.os, "stat", lambda p: type("S", (), {"st_uid": 0, "st_mode": 0o100666})())
    with pytest.raises(L.Refused):
        L.check_install(good, own)


def test_main_refuses_unless_root_and_on_bad_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    assert L.main([]) == 2 and L.main(["x"]) == 2 and L.main(["probe", "run"]) == 2
    monkeypatch.setattr(L.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit) as exc:
        L.main(["probe"])
    assert exc.value.code == 2


def test_proxy_runs_with_dash_i_dash_s_and_one_fixed_upstream() -> None:
    argv = L.proxy_argv(PROVIDER, Path("/i"))
    assert argv[:4] == ["/usr/bin/python3", "-I", "-S", "/i/egress_proxy.py"]
    assert argv[argv.index("--upstream") + 1] == "api.deepseek.com" and "--bind" in argv and L.GATEWAY in argv
    assert not any("k3y" in a for a in argv)


def test_run_argv_parity_with_the_adapter_hardening_flags_mounts_and_entrypoint_tail() -> None:
    mine = L.container_argv("run", IMAGE, "code_reviewer", PROVIDER, "/in", "/ev")
    theirs = adapter.run_argv(IMAGE, "code_reviewer", "/in", "/ev", key_variable=None, network=L.NETWORK, docker=L.DOCKER)
    for flag in ("--rm", "--read-only", "--cap-drop", "--security-opt", "--user", "--network"):
        assert flag in mine
    assert mine[mine.index("--tmpfs") : mine.index("--tmpfs") + 2] == ["--tmpfs", "/tmp"]
    assert mine[mine.index("--cap-drop") + 1] == "ALL" and mine[mine.index("--user") + 1] == "10001:10001"
    for mount in ("/in:/run/persona/in:ro", "/ev:/run/persona/events"):
        assert mount in mine and mount in theirs
    assert mine[mine.index(IMAGE) :] == theirs[theirs.index(IMAGE) :]


def test_no_caller_chosen_value_or_key_reaches_argv() -> None:
    argv = L.container_argv("run", IMAGE, "code_reviewer", PROVIDER, "/in", "/ev")
    assert "k3y" not in " ".join(argv) and "-p" not in argv and "--add-host" not in argv and "--publish" not in argv
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert envs == [f"DEEPSEEK_API_BASE=http://{L.GATEWAY}:{L.PROXY_PORT}", f"DEEPSEEK_API_KEY={L.PLACEHOLDER}"]
    assert argv[argv.index("--network") + 1] == "persona-egress" and argv[argv.index("--dns") + 1] == "0.0.0.0"
    assert "--pids-limit" in argv and "--memory" in argv and argv.count("--entrypoint") == 0


def test_probe_mode_uses_a_fixed_entrypoint_and_the_chat_path() -> None:
    argv = L.container_argv("probe", IMAGE, "x", PROVIDER, "/in", "/ev")
    assert argv[argv.index("--entrypoint") + 1] == "python" and argv[-1].endswith("egress_container_probe.py")
    assert "PERSONA_PROBE_CHAT_PATH=/chat/completions" in argv  # pragma: allowlist secret


def test_events_read_is_nofollow_regular_and_size_capped(tmp_path: Path) -> None:
    (tmp_path / "events.json").write_text('{"a": 1}')
    assert L.read_events(str(tmp_path)) == {"a": 1}
    other = tmp_path / "o"
    other.mkdir()
    (other / "real").write_text("{}")
    os.symlink(other / "real", other / "events.json")
    assert L.read_events(str(other)) is None
    fifo = tmp_path / "f"
    fifo.mkdir()
    os.mkfifo(fifo / "events.json")
    assert L.read_events(str(fifo)) is None
    big = tmp_path / "b"
    big.mkdir()
    (big / "events.json").write_text(" " * 10 + "{}")
    L.MAX_RESULT, saved = 5, L.MAX_RESULT
    try:
        assert L.read_events(str(big)) is None
    finally:
        L.MAX_RESULT = saved
    assert L.read_events(str(tmp_path / "missing")) is None
    (tmp_path / "events.json").write_text("[1]")
    assert L.read_events(str(tmp_path)) is None


def test_make_dirs_sets_owner_and_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "RUN_DIR", tmp_path)
    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(L.os, "chown", lambda p, u, g: chowned.append((p, u, g)))
    install = _install(_sub(tmp_path, "i"))
    session = L.Session()
    in_dir, events = L.make_dirs(session, "run", {"a": 1}, install)
    assert oct(os.stat(in_dir).st_mode & 0o777) == "0o755" and oct(os.stat(events).st_mode & 0o777) == "0o700"
    assert chowned == [(events, 10001, 10001)] and json.loads(Path(in_dir, "input.json").read_text()) == {"a": 1}
    assert oct(os.stat(Path(in_dir, "input.json")).st_mode & 0o777) == "0o644"
    in2, _ = L.make_dirs(session, "probe", {}, install)
    assert Path(in2, "egress_container_probe.py").exists()


def test_a_held_lock_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "RUN_DIR", tmp_path / "run")
    first, second = L.Session(), L.Session()
    L.take_lock(first)
    with pytest.raises(L.Refused):
        L.take_lock(second)
    first.cleanup()
    L.take_lock(second)
    second.cleanup()


def _drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, code: int | None, events: dict[str, Any] | None, stdin: bytes
) -> tuple[int, list[str]]:
    install = _install(_sub(tmp_path, "i"))
    monkeypatch.setattr(L, "INSTALL_DIR", install)
    monkeypatch.setattr(L, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(L.os, "geteuid", lambda: 0)
    monkeypatch.setattr(L.os, "chown", lambda *a: None)
    monkeypatch.setattr(L, "check_install", lambda d, o: json.loads((install / L.SNAPSHOT_NAME).read_text()))
    monkeypatch.setattr(L, "port_in_use", lambda p: False)
    monkeypatch.setattr(L, "name_in_use", lambda: False)
    monkeypatch.setattr(L, "start_proxy", lambda *a: None)
    monkeypatch.setattr(L, "start_decoy", lambda s: None)
    seen: list[str] = []
    monkeypatch.setattr(L, "run_container", lambda m, argv: (seen.extend(argv), code)[1])
    monkeypatch.setattr(L, "read_events", lambda d: events)
    monkeypatch.setattr(L, "kill_container", lambda: seen.append("killed"))
    monkeypatch.setattr(L.signal, "signal", lambda num, handler: HANDLERS.update({num: handler}))
    monkeypatch.setattr(L.sys, "stdin", type("I", (), {"buffer": type("B", (), {"read": staticmethod(lambda n: stdin)})()})())
    return L.main([mode]), seen


def test_probe_exit_is_zero_only_on_a_clean_exit_with_a_pass_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"result": "CONTAINER-PROBE PASS"}
    assert _drive(monkeypatch, tmp_path / "1", "probe", 0, ok, _req(key=None))[0] == 0
    assert _drive(monkeypatch, tmp_path / "2", "probe", 1, ok, _req(key=None))[0] == 1
    assert _drive(monkeypatch, tmp_path / "3", "probe", 0, {"result": "CONTAINER-PROBE FAIL x"}, _req(key=None))[0] == 1
    assert _drive(monkeypatch, tmp_path / "4", "probe", 0, None, _req(key=None))[0] == 1
    assert _drive(monkeypatch, tmp_path / "5", "probe", None, ok, _req(key=None))[0] == 1


def test_run_mode_returns_the_container_code_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, seen = _drive(monkeypatch, tmp_path, "run", 7, {"a": 1}, _req())
    assert code == 7 and seen[-1] == "killed" and not any("k3y" in a for a in seen)
    assert not list((tmp_path / "run").glob("run-*"))


def test_a_refused_request_exits_two_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, seen = _drive(monkeypatch, tmp_path, "run", 0, None, b"[]")
    assert code == 2 and "docker" not in " ".join(seen) and "LAUNCHER-REFUSED" in capsys.readouterr().err


def test_run_container_kills_by_name_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 0

        def communicate(self, timeout: int | None = None) -> tuple[bytes, None]:
            if timeout:
                raise subprocess.TimeoutExpired("x", timeout)
            return b"", None

        def kill(self) -> None:
            killed.append("proc")

    killed: list[str] = []
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(L, "kill_container", lambda: killed.append("name"))
    assert L.run_container("probe", ["x"]) is None and killed == ["name", "proc"]


def test_run_container_relays_output_to_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class Proc:
        returncode = 3

        def communicate(self, timeout: int | None = None) -> tuple[bytes, None]:
            return b"CONTAINER-PROBE PASS\n", None

    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: Proc())
    assert L.run_container("probe", ["x"]) == 3 and "CONTAINER-PROBE PASS" in capsys.readouterr().err


def test_the_key_goes_only_to_the_proxys_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[bytes] = []

    class Stdin:
        def write(self, b: bytes) -> None:
            written.append(b)

        def close(self) -> None:
            pass

    class Proc:
        stdin = Stdin()

        def poll(self) -> None:
            return None

    captured: dict[str, Any] = {}
    monkeypatch.setattr(L.pwd, "getpwnam", lambda n: type("E", (), {"pw_uid": 999, "pw_gid": 998})())
    monkeypatch.setattr(L.subprocess, "Popen", lambda argv, **k: (captured.update(argv=argv, **k), Proc())[1])
    monkeypatch.setattr(L, "port_in_use", lambda p: True)
    L.start_proxy(L.Session(), PROVIDER, "k3y", Path("/i"))
    assert written == [b"k3y"] and "k3y" not in " ".join(captured["argv"])
    assert captured["user"] == 999 and captured["extra_groups"] == [] and captured["stdin"] == subprocess.PIPE


def test_sigterm_and_sigint_kill_the_container_and_proxy_then_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, seen = _drive(monkeypatch, tmp_path, "run", 0, None, _req())
    assert set(HANDLERS) == {L.signal.SIGTERM, L.signal.SIGINT}
    seen.clear()
    with pytest.raises(SystemExit) as exc:
        HANDLERS[L.signal.SIGTERM](15, None)
    assert exc.value.code == 1 and seen == ["killed"]
    assert L.BOUNDS == {"probe": 120, "run": 600}
