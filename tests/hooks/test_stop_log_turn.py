"""The Stop, StopFailure and UserPromptSubmit shim (rules HK-3, WT-1): rows once per turn, the witness line, detach."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.telemetry_capture import __main__ as cli
from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import WriterClient
from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import FsTree
from src.turn_capture.witness import WITNESS_LINE, is_witness_record
from tests.fixtures.turn_capture_corpus import SID, WITNESS_STDERR, assistant, hook, jsonl, prompt, text_block

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / ".claude" / "hooks" / "stop_log_turn.py"
PIN = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=example/project'
URL = "https://abc.lambda-url.eu-west-2.on.aws"
ON = {"CLAUDE_CODE_REMOTE": "true"}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_stop_log_turn_under_test", SHIM)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Writer:
    """describe and append_telemetry at the HTTP seam; an identical re-send is a no-op, scripted failures first."""

    def __init__(self) -> None:
        self.appends: list[dict[str, Any]] = []
        self.acked: list[dict[str, Any]] = []
        self.keys: set[tuple[str, str]] = set()
        self.fail: list[int] = []

    def __call__(self, method: str, url: str, data: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        payload = json.loads(data)
        if payload["action"] == "describe":
            caps = {"max_invoke_body_bytes": 5242880, "max_rows_per_table": 2000, "max_rows_per_request": 5000}
            body = {
                "ok": True,
                "actions": ["append_telemetry"],
                "verbs": {"append_telemetry": {"caps": caps}},
                "catalog": {"meta_schema": "ducklake_smoke", "data_path_is_smoke": True},
                "blob_protocol": {"version": 1, "inline_threshold_bytes": 65536, "cap_bytes": 8388608, "max_items": 200},
            }
            return 200, json.dumps(body).encode()
        self.appends.append(payload)
        if self.fail:
            return self.fail.pop(0), b'{"ok": false}'
        self.acked.append(payload)
        tables = {}
        for batch in payload["batches"]:
            fresh = {(batch["table"], r["external_ref"]) for r in batch["rows"]} - self.keys
            self.keys |= fresh
            tables[batch["table"]] = {"submitted": len(batch["rows"]), "collapsed": 0, "inserted": len(fresh)}
        return 200, json.dumps({"ok": True, "tables": tables}).encode()


def on_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "telemetry").mkdir(parents=True)
    (root / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\n", encoding="utf-8")
    return root


def turns(n: int) -> str:
    records = [hook("h0", 0.5, "SessionStart:startup", command=PIN)]
    for i in range(n):
        records += [
            prompt(f"u{i}", 1 + 10 * i, f"p{i}", f"go {i}"),
            assistant(f"a{i}", 2 + 10 * i, f"m{i}", [text_block("k")]),
        ]
    return jsonl([*records, prompt(f"u{n}", 1 + 10 * n, f"p{n}", f"go {n}")])


def in_process(writer: Writer, repo_root: Path):
    def factory(env, state, budget, **kw):
        return WriterClient(URL, lambda body: {}, budget, http=writer, sleep=lambda s: None, rng=lambda: 0.0)

    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 0.5
        return ticks[0]

    def detach(args, env):
        assert (
            cli.main(["run", *args], env=env, repo_root=repo_root, client_factory=factory, clock=clock, sleep=lambda s: None)
            == 0
        )

    return detach


def stop_stdin(root: Path, **extra: Any) -> str:
    return json.dumps({"hook_event_name": "Stop", "transcript_path": str(root), "session_id": SID, **extra})


def sent_keys(payloads: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(b["table"], r["external_ref"]) for p in payloads for b in p["batches"] for r in b["rows"]]


def test_turn_rows_written_once_per_turn(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    shim, writer, repo_root = load(), Writer(), on_repo(tmp_path)
    root = tmp_path / f"{SID}.jsonl"
    state = CaptureState(state_dir(root, SID))
    detach = in_process(writer, repo_root)
    root.write_text(turns(1), encoding="utf-8")
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=detach) == 0
    first = sent_keys(writer.acked)
    assert first and state.cursor_on_disk() is not None
    with root.open("a", encoding="utf-8") as handle:
        handle.write(turns(2)[len(turns(1)) :])
    cursor_before = state.state_file.read_bytes()
    writer.fail = [500]
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=detach) == 0
    assert state.state_file.read_bytes() == cursor_before
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=detach) == 0
    keys = sent_keys(writer.acked)
    assert len(keys) == len(set(keys)) and len(keys) > len(first)
    expected = record_turn(FsTree(root), None, project_ref="example/project")
    assert set(keys) == {(t, r["external_ref"]) for t, rows in expected.batches for r in rows}
    for payload in writer.appends:
        tables = [b["table"] for b in payload["batches"]]
        assert "telemetry_sessions" not in tables[:-1]
    assert state.cursor_on_disk().lines_consumed["root"] == len(turns(2).splitlines())
    assert capfd.readouterr() == ("", (WITNESS_LINE + "\n") * 3)


def test_malformed_input_and_switch_off_are_inert(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    shim, calls = load(), []
    root = tmp_path / f"{SID}.jsonl"
    repo_root = on_repo(tmp_path)

    def detach(args, env):
        calls.append(args)

    for stdin in ("", "not json", "[1]", json.dumps({"hook_event_name": "Stop"})):
        assert shim.main(stdin, env=ON, repo_root=repo_root, detach=detach) == 0
    assert shim.main(stop_stdin(root), env={}, repo_root=repo_root, detach=detach) == 0
    assert shim.main(stop_stdin(root), env={**ON, "OTL_TURN_CAPTURE": "off"}, repo_root=repo_root, detach=detach) == 0
    assert calls == []

    def broken(args, env):
        raise RuntimeError("boom")

    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=broken) == 0
    failure = json.dumps({"hook_event_name": "StopFailure", "transcript_path": str(root), "session_id": SID})
    assert shim.main(failure, env=ON, repo_root=repo_root, detach=detach) == 0
    assert calls == [["--event", "StopFailure", "--transcript", str(root), "--session-id", SID, "--lock-wait", "120"]]
    assert capfd.readouterr() == ("", (WITNESS_LINE + "\n") * 8)
    source = SHIM.read_text(encoding="utf-8")
    assert shim.RUNNER_MODULE == "scripts.telemetry_capture" and "getenv" not in source and "environ.get" not in source


FAKE_RUNNER = """
import os, sys, time
from pathlib import Path
fds = [os.readlink(f"/proc/self/fd/{n}") for n in (0, 1, 2)]
Path(sys.argv[sys.argv.index("--transcript") + 1] + ".fake").write_text(
    "{} {} {} {}".format(os.getpid(), os.getsid(0), " ".join(fds), " ".join(sys.argv[1:]))
)
time.sleep(60)
"""

WRAPPER = """
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("shim", {shim!r})
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)
shim.RUNNER_MODULE = "fake_capture_runner"
sys.exit(shim.main(sys.stdin.read(), env=dict(os.environ), repo_root=__import__("pathlib").Path({repo!r})))
"""


def test_shim_detaches_silently(tmp_path: Path) -> None:
    (tmp_path / "fake_capture_runner").mkdir()
    (tmp_path / "fake_capture_runner" / "__init__.py").write_text("")
    (tmp_path / "fake_capture_runner" / "__main__.py").write_text(FAKE_RUNNER)
    repo_root = on_repo(tmp_path)
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(WRAPPER.format(shim=str(SHIM), repo=str(repo_root)))
    root = tmp_path / f"{SID}.jsonl"
    env = {**os.environ, **ON, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT)])}
    env.pop("OTL_TURN_CAPTURE", None)
    env.pop("GITHUB_ACTIONS", None)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(wrapper)],
        input=stop_stdin(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", WITNESS_LINE + "\n")
    assert time.monotonic() - started < 20
    record = Path(str(root) + ".fake")
    deadline = time.monotonic() + 20
    while not record.exists() or not record.read_text():
        assert time.monotonic() < deadline, "the detached runner never started"
        time.sleep(0.05)
    pid, sid, fd0, fd1, fd2, *argv = record.read_text().split()
    try:
        os.kill(int(pid), 0)
        assert int(sid) == int(pid) and int(sid) != os.getsid(0)
        assert (fd0, fd1, fd2) == ("/dev/null", "/dev/null", "/dev/null")
        assert argv == ["run", "--event", "Stop", "--transcript", str(root), "--session-id", SID, "--lock-wait", "120"]
    finally:
        os.kill(int(pid), signal.SIGKILL)
        for _ in range(100):
            try:
                if os.waitpid(int(pid), os.WNOHANG) != (0, 0):
                    break
            except ChildProcessError:
                break
            time.sleep(0.05)


def test_the_default_detach_uses_the_runner_module_and_puts_the_repo_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.telemetry_capture as package  # noqa: PLC0415

    shim, seen = load(), []
    monkeypatch.setattr(package, "detach_runner", lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])
    root, repo_root = tmp_path / f"{SID}.jsonl", on_repo(tmp_path)
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root) == 0
    args = ["--event", "Stop", "--transcript", str(root), "--session-id", SID, "--lock-wait", "120"]
    assert seen == [(("scripts.telemetry_capture", args, repo_root), {"env": ON})] and str(ROOT) in sys.path


def payload(event: str, root: Path, **extra: Any) -> str:
    return json.dumps({"hook_event_name": event, "transcript_path": str(root), "session_id": SID, **extra})


def test_witness_line_is_printed_first_on_every_path(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shim, root, repo_root = load(), tmp_path / f"{SID}.jsonl", on_repo(tmp_path)
    inputs = [
        "",
        "not json",
        "[1]",
        json.dumps({"hook_event_name": "Stop"}),
        stop_stdin(root),
        stop_stdin(root, stop_hook_active=True),
    ]
    inputs += [payload("UserPromptSubmit", root), payload("UserPromptSubmit", root, prompt_id="p-1")]
    for stdin in inputs:
        for env in (ON, {}):
            assert shim.main(stdin, env=env, repo_root=repo_root, detach=lambda args, e: None) == 0
            assert capfd.readouterr() == ("", WITNESS_LINE + "\n")
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=lambda a, e: 1 / 0) == 0
    assert capfd.readouterr() == ("", WITNESS_LINE + "\n")

    seen: list[str] = []

    class Stdin:
        def read(self) -> str:
            seen.append(capfd.readouterr().err)
            return stop_stdin(root)

    monkeypatch.setattr(sys, "stdin", Stdin())
    assert shim.main(env=ON, repo_root=repo_root, detach=lambda args, e: None) == 0
    assert seen == [WITNESS_LINE + "\n"]


def test_every_stop_and_prompt_detaches_a_pass_with_its_prompt_id(tmp_path: Path) -> None:
    shim, calls = load(), []
    root, repo_root = tmp_path / f"{SID}.jsonl", on_repo(tmp_path)

    def detach(args, env):
        calls.append(args)

    base = ["--transcript", str(root), "--session-id", SID, "--lock-wait", "120"]
    for stdin in (stop_stdin(root), stop_stdin(root, stop_hook_active=True)):
        assert shim.main(stdin, env=ON, repo_root=repo_root, detach=detach) == 0
    assert shim.main(payload("StopFailure", root, stop_hook_active=True), env=ON, repo_root=repo_root, detach=detach) == 0
    assert shim.main(payload("UserPromptSubmit", root, prompt_id="abc-123"), env=ON, repo_root=repo_root, detach=detach) == 0
    assert calls == [
        ["--event", "Stop", *base],
        ["--event", "Stop", *base],
        ["--event", "StopFailure", *base],
        ["--event", "UserPromptSubmit", *base, "--prompt-id", "abc-123"],
    ]
    calls.clear()
    for bad in ({}, {"prompt_id": ""}, {"prompt_id": 7}, {"prompt_id": "has space"}, {"prompt_id": "x" * 65}):
        assert shim.main(payload("UserPromptSubmit", root, **bad), env=ON, repo_root=repo_root, detach=detach) == 0
    assert calls == []


def test_prompt_path_detaches_without_reading_the_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.telemetry_capture.switch as switch  # noqa: PLC0415

    def refuse(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("the prompt path must not read the switch")

    monkeypatch.setattr(switch, "capture_allowed", refuse)
    shim, calls = load(), []
    root, repo_root = tmp_path / f"{SID}.jsonl", on_repo(tmp_path)
    off = {"OTL_TURN_CAPTURE": "off"}
    assert (
        shim.main(
            payload("UserPromptSubmit", root, prompt_id="p1"),
            env=off,
            repo_root=repo_root,
            detach=lambda a, e: calls.append(a),
        )
        == 0
    )
    assert len(calls) == 1
    assert shim.main(stop_stdin(root), env=ON, repo_root=repo_root, detach=lambda a, e: calls.append(a)) == 0
    assert len(calls) == 1


def test_the_witness_line_equals_the_parser_constant() -> None:
    assert load().WITNESS_LINE == WITNESS_LINE and WITNESS_STDERR == WITNESS_LINE + "\n"
    record = hook(
        "w",
        1,
        "Stop",
        command='"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_log_turn.py"',
        stderr=WITNESS_LINE + "\n",
    )
    assert is_witness_record(record) and not is_witness_record({**record, "type": "user"})
