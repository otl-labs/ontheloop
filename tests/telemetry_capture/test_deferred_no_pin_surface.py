"""rec-4941: the in-session warning, the unpinned_tree alarm and the verify finding for a deferring capture tree."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.telemetry_capture import __main__ as cli
from scripts.telemetry_capture import capture_warning as cw
from scripts.telemetry_capture import state as capture_state
from scripts.telemetry_capture.state import CaptureState, state_dir
from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import MemTree
from src.turn_capture.witness import is_witness_record
from tests.fixtures.turn_capture_corpus import SID, WITNESS_STDERR, assistant, hook, jsonl, prompt, text_block

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / ".claude" / "hooks" / "stop_log_turn.py"
ON = {"CLAUDE_CODE_REMOTE": "true"}
PIN = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=example/project'


def make_tree(tmp_path: Path, *, pinned: bool = False) -> Path:
    root = tmp_path / f"{SID}.jsonl"
    records: list[dict[str, Any]] = [hook("h0", 0.5, "SessionStart:startup", command=PIN)] if pinned else []
    records += [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")])]
    root.write_text(jsonl(records), encoding="utf-8")
    return root


def st(root: Path) -> CaptureState:
    return CaptureState(state_dir(root, root.stem))


def stop_pass(root: Path, outcome: str = "deferred", *, event: str = "Stop", version: str | None = "2.1.318") -> None:
    record: dict[str, Any] = {"event": event, "final": False, "at": 1, "outcome": outcome, "diag": {}}
    if outcome == "deferred":
        record["diag"] = {"deferred_no_pin": 1}
        if version is not None:
            record["cli_version"] = version
    st(root).append_diagnostic(record)


def warn(root: Path) -> str | None:
    return cw.prompt_warning(root, root.stem)


def test_second_deferred_stop_emits_one_warning(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    stop_pass(root)
    assert warn(root) is None
    stop_pass(root)
    line = warn(root)
    assert line is not None
    body = json.loads(line)
    text = body["hookSpecificOutput"]["additionalContext"]
    assert body["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit" and body["systemMessage"] == text
    assert "deferred_no_pin" in text and "last 2 Stop passes" in text and "Claude Code 2.1.318" in text
    assert text.isascii()
    assert warn(root) is None


def test_first_stop_deferral_alone_stays_quiet(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    stop_pass(root)
    assert warn(root) is None and cw.persistent(root, root.stem) == (None, 1)
    assert not (state_dir(root, root.stem) / cw.WARNING_MARKER).exists()


def test_halted_streak_warns_without_filing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_tree(tmp_path)
    stop_pass(root, "halted")
    stop_pass(root, "halted", event="StopFailure")
    line = warn(root)
    assert line is not None and "halted" in json.loads(line)["systemMessage"] and "Claude Code" not in line
    import scripts.telemetry_verify.alarms as alarms

    calls: list[Any] = []
    monkeypatch.setattr(alarms, "raise_alarms", lambda *a, **k: calls.append(1))
    assert cw.file_unpinned_alarm(root, root.stem) is None and not calls
    assert cw.alarm_marker(root, root.stem) is None


def test_recovery_clears_marker_and_rearm_fires(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    marker = state_dir(root, root.stem) / cw.WARNING_MARKER
    stop_pass(root)
    stop_pass(root)
    assert warn(root) is not None and marker.exists()
    stop_pass(root, "sent")
    assert warn(root) is None and not marker.exists()
    stop_pass(root)
    stop_pass(root)
    assert warn(root) is not None
    for _ in range(cw.REARM_EVERY - 1):
        stop_pass(root)
    assert warn(root) is None
    stop_pass(root)
    assert warn(root) is not None
    assert warn(root) is None


def test_deferred_pass_records_only_a_valid_version() -> None:
    def root_with(version: Any) -> bytes:
        return (
            json.dumps({"uuid": "a", "version": "2.1.0"}) + "\n" + json.dumps({"uuid": "b", "version": version}) + "\n"
        ).encode()

    assert cw.version_field(root_with("2.1.318")) == {"cli_version": "2.1.318"}
    for hostile in ("2.1.318\n", "٢.1.3", "2.1.318 x", 5, None):
        assert cw.version_field(root_with(hostile)) == {}
    partial = root_with("2.1.318") + b'{"version": "9.9.9"'
    assert cw.version_field(partial) == {"cli_version": "2.1.318"}


def test_unknown_version_when_the_pass_value_is_hostile(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    stop_pass(root, version="2.1.318\n")
    stop_pass(root, version="2.1.318\n")
    line = warn(root)
    assert line is not None and "Claude Code unknown" in line


def test_capture_warning_imports_only_the_stdlib(tmp_path: Path) -> None:
    tree = ast.parse((ROOT / "scripts" / "telemetry_capture" / "capture_warning.py").read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names
    root = make_tree(tmp_path)
    assert cw.STATE_DIRNAME == capture_state.STATE_DIRNAME
    assert cw._dir(root, root.stem) == state_dir(root, root.stem)
    assert cw.DIAGNOSTICS == st(root).diagnostics_file.name


def test_warning_json_matches_hook_canary_shape(tmp_path: Path) -> None:
    canary = (ROOT / ".claude" / "hooks" / "hook_canary.sh").read_text(encoding="utf-8")
    printed = json.loads(canary.split("'")[3])
    root = make_tree(tmp_path)
    stop_pass(root)
    stop_pass(root)
    body = json.loads(warn(root) or "")
    assert set(body["hookSpecificOutput"]) == set(printed["hookSpecificOutput"])
    assert body["hookSpecificOutput"]["hookEventName"] == printed["hookSpecificOutput"]["hookEventName"]


def shim_env(tmp_path: Path) -> dict[str, str]:
    runner = tmp_path / "fake_capture_runner"
    runner.mkdir(exist_ok=True)
    (runner / "__init__.py").write_text("")
    (runner / "__main__.py").write_text("")
    env = {**os.environ, **ON, "PYTHONPATH": str(tmp_path)}
    for name in ("OTL_TURN_CAPTURE", "GITHUB_ACTIONS"):
        env.pop(name, None)
    return env


def run_shim(tmp_path: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    (repo / "config" / "telemetry").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\n", encoding="utf-8")
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import importlib.util, os, sys\nfrom pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('shim', {str(SHIM)!r})\n"
        "shim = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(shim)\n"
        "shim.RUNNER_MODULE = 'fake_capture_runner'\n"
        f"sys.exit(shim.main(sys.stdin.read(), env=dict(os.environ), repo_root=Path({str(repo)!r})))\n"
    )
    return subprocess.run(
        [sys.executable, str(wrapper)],
        input=json.dumps(payload),
        env=shim_env(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_shim_warns_on_prompt_and_stop_paths_stay_silent(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    base = {"transcript_path": str(root), "session_id": root.stem}
    fresh = run_shim(tmp_path, {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "p1"})
    assert (fresh.returncode, fresh.stdout, fresh.stderr) == (0, "", WITNESS_STDERR)
    stop_pass(root)
    stop_pass(root)
    for event in ("Stop", "StopFailure"):
        quiet = run_shim(tmp_path, {**base, "hook_event_name": event})
        assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, "", WITNESS_STDERR)
    warned = run_shim(tmp_path, {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "p2"})
    assert warned.returncode == 0 and warned.stderr == WITNESS_STDERR
    assert len(warned.stdout.splitlines()) == 1 and "deferred_no_pin" in json.loads(warned.stdout)["systemMessage"]
    again = run_shim(tmp_path, {**base, "hook_event_name": "UserPromptSubmit", "prompt_id": "p3"})
    assert again.stdout == ""


def _tables(result: Any) -> dict[str, int]:
    return {table: len(rows) for table, rows in result.batches}


def test_prompt_warning_record_is_not_a_witness() -> None:
    text = "[telemetry_capture] WARNING: x"
    base = [hook("h0", 0.5, "SessionStart:startup", command=PIN), prompt("u1", 1, "p1", "go")]
    tail = [assistant("a1", 3, "m1", [text_block("k")])]
    plain = hook("w1", 2, "UserPromptSubmit", command="bash x.sh", stdout=text, toolUseID="t1")
    held = hook("w2", 2, "UserPromptSubmit", command="bash x.sh", stdout=text, toolUseID="t1", heldContent=text)
    ctx = hook("w3", 2.1, "UserPromptSubmit", a_type="hook_additional_context", content=[text])
    control_rec = hook("c1", 2, "UserPromptSubmit", command="bash x.sh", stdout="", toolUseID="t1")
    for record in (plain, held, ctx, control_rec):
        assert not is_witness_record(record)

    def run(records: list[dict[str, Any]]) -> Any:
        return record_turn(MemTree({f"{SID}.jsonl": jsonl([*base, *records, *tail])}), None, project_ref="example/project")

    control, with_warning = run([control_rec]), run([plain, held, ctx])
    assert len(control.transcripts) == len(with_warning.transcripts)
    assert len(control.agents) == len(with_warning.agents) and len(control.sessions) == len(with_warning.sessions)
    assert _tables(with_warning)["telemetry_observations"] == _tables(control)["telemetry_observations"]


def test_sa02_states_the_deferral_warning_assumptions() -> None:
    text = " ".join((ROOT / "docs" / "contracts" / "ports.yaml").read_text(encoding="utf-8").split())
    sa02 = text[text.index("SA-02") :]
    assert "hookSpecificOutput.additionalContext" in sa02 and "version field" in sa02
    assert "capture_warning.py is a further function_url client" in text


def fake_alarms(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[dict[str, Any]]:
    import scripts.telemetry_verify.alarms as alarms

    calls: list[dict[str, Any]] = []

    def raise_alarms(findings: Any, **kw: Any) -> Any:
        calls.append({"findings": dict(findings), **kw})
        if fail:
            raise RuntimeError("portal down")
        return SimpleNamespace(actions={"unpinned_tree": "file"})

    monkeypatch.setattr(alarms, "raise_alarms", raise_alarms)
    return calls


def run_detached(root: Path, tmp_path: Path) -> int:
    repo = tmp_path / "repo"
    (repo / "config" / "telemetry").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\n", encoding="utf-8")
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 100.0
        return ticks[0]

    argv = ["run", "--event", "Stop", "--transcript", str(root), "--session-id", root.stem]
    return cli.main(argv, env=dict(ON), repo_root=repo, clock=clock, sleep=lambda s: None)


def versioned_root(tmp_path: Path) -> Path:
    root = tmp_path / f"{SID}.jsonl"
    records = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")])]
    for record in records:
        record["version"] = "2.1.318"
    root.write_text(jsonl(records), encoding="utf-8")
    return root


def test_detached_run_files_one_version_scoped_alarm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = versioned_root(tmp_path)
    calls = fake_alarms(monkeypatch)
    assert run_detached(root, tmp_path) == 0
    assert not calls
    assert run_detached(root, tmp_path) == 0
    assert len(calls) == 1 and calls[0]["findings"] == {"unpinned_tree": 2} and calls[0]["scope"] == "claude_code=2.1.318"
    assert calls[0]["alarm_at"] is None and calls[0]["declared"] == ()
    marker = cw.alarm_marker(root, root.stem)
    assert marker is not None and marker["action"] == "file" and marker["version"] == "2.1.318"
    assert run_detached(root, tmp_path) == 0
    assert len(calls) == 1


def test_alarm_retry_is_bounded_and_a_held_lock_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = versioned_root(tmp_path)
    stop_pass(root)
    stop_pass(root)
    calls = fake_alarms(monkeypatch, fail=True)
    lock = st(root).acquire(0.0)
    assert lock is not None
    assert cw.file_unpinned_alarm(root, root.stem) is None and not calls and cw.alarm_marker(root, root.stem) is None
    lock.release()
    for _ in range(cw.ALARM_ATTEMPTS + 2):
        cw.file_unpinned_alarm(root, root.stem)
    marker = cw.alarm_marker(root, root.stem)
    assert len(calls) == cw.ALARM_ATTEMPTS
    assert marker is not None and marker["action"] == "alarm_error:RuntimeError" and marker["attempts"] == cw.ALARM_ATTEMPTS


def boto_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    class Session:
        def __init__(self, profile_name: str | None = None) -> None:
            self.profile_name = profile_name

        def get_credentials(self) -> Any:
            return SimpleNamespace(get_frozen_credentials=lambda: None)

        def client(self, service: str, region_name: str) -> Any:
            raise RuntimeError("no AWS in this test")

    monkeypatch.setattr(boto3, "Session", Session)


def test_verify_reports_unpinned_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.telemetry_capture import verify

    boto_stub(monkeypatch)
    root = make_tree(tmp_path)
    portal_calls: list[Any] = []
    monkeypatch.setattr(verify, "raise_alarms", lambda f, **k: portal_calls.append(f) if any(f.values()) else None)

    def run(*flags: str, tree: Path = root) -> tuple[int, list[str]]:
        lines: list[str] = []
        code = cli.main(["verify", "--tree", str(tree), *flags], out=lines.append, reader_factory=lambda: object())
        return code, lines

    code, lines = run()
    assert (code, lines[0].split()[1]) == (2, "status=no_cursor")
    stop_pass(root)
    stop_pass(root)
    code, lines = run()
    assert code == 1 and lines[0].split()[1] == "status=unpinned_tree" and lines[0].endswith("alarms=unpinned_tree")
    assert run("--assert-zero", "unpinned_tree")[0] == 1
    assert run("--assert-zero", "column_mismatch")[0] == 2
    assert not portal_calls

    done = SimpleNamespace(
        completed=True,
        status="completed",
        alarms=[],
        counts={"root_checked": 1},
        compare_skipped=None,
        reader_calls=0,
        elapsed_s=0,
    )
    monkeypatch.setattr(verify, "run_verify", lambda *a, **k: done)
    assert run("--assert-zero", "unpinned_tree")[0] == 0
