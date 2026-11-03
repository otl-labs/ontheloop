"""The SessionEnd hook (rule HK-4): the final pass, the deadline against a hung writer, detaching, silent failure."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import WriterClient
from tests.fixtures.turn_capture_corpus import SID, assistant, hook, jsonl, prompt, text_block

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "session_end_log_turn.py"
PIN = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=example/project'
URL = "https://abc.lambda-url.eu-west-2.on.aws"
ON = {"CLAUDE_CODE_REMOTE": "true"}
DESCRIBE = {
    "ok": True,
    "actions": ["append_telemetry"],
    "verbs": {
        "append_telemetry": {
            "caps": {"max_invoke_body_bytes": 5242880, "max_rows_per_table": 2000, "max_rows_per_request": 5000}
        }
    },
    "catalog": {"meta_schema": "ducklake_smoke", "data_path_is_smoke": True},
    "blob_protocol": {"version": 1, "inline_threshold_bytes": 65536, "cap_bytes": 8388608, "max_items": 200},
}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_session_end_log_turn_under_test", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Writer:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.appends: list[dict[str, Any]] = []
        self.timeouts: list[float] = []

    def __call__(self, method: str, url: str, data: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        payload = json.loads(data)
        if payload["action"] == "describe":
            return 200, json.dumps(DESCRIBE).encode()
        self.timeouts.append(timeout)
        if self.hang:
            time.sleep(timeout)
            raise TimeoutError("hung writer")
        self.appends.append(payload)
        tables = {b["table"]: {"submitted": len(b["rows"]), "inserted": len(b["rows"])} for b in payload["batches"]}
        return 200, json.dumps({"ok": True, "tables": tables}).encode()


def factory(writer: Writer):
    def make(env, state, budget, **kw):
        return WriterClient(URL, lambda body: {}, budget, http=writer, rng=lambda: 0.0)

    return make


def setup(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    (repo_root / "config" / "telemetry").mkdir(parents=True)
    (repo_root / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\n", encoding="utf-8")
    root = tmp_path / f"{SID}.jsonl"
    records = [hook("h0", 0.5, "SessionStart:startup", command=PIN), prompt("u1", 1, "p1", "go")]
    root.write_text(jsonl([*records, assistant("a1", 2, "m1", [text_block("done")])]), encoding="utf-8")
    return repo_root, root


def stdin(root: Path) -> str:
    return json.dumps({"hook_event_name": "SessionEnd", "reason": "other", "transcript_path": str(root), "session_id": SID})


def test_the_final_pass_emits_the_root_close_row(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    repo_root, root = setup(tmp_path)
    writer = Writer()
    assert load().main(stdin(root), env=ON, repo_root=repo_root, client_factory=factory(writer)) == 0
    sessions = [r for p in writer.appends for b in p["batches"] if b["table"] == "telemetry_sessions" for r in b["rows"]]
    assert any(r["event_kind"] == "close" and r["entity_ref"] == SID for r in sessions)
    state = CaptureState(state_dir(root, SID))
    assert state.cursor_on_disk().finalized is True and state.diagnostics()[-1]["event"] == "SessionEnd"
    assert capfd.readouterr() == ("", "")


def test_session_end_returns_within_deadline_against_a_hung_writer(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    repo_root, root = setup(tmp_path)
    writer = Writer(hang=True)
    deadline_s = 1.5
    started = time.monotonic()
    code = load().main(
        stdin(root),
        env=ON,
        repo_root=repo_root,
        deadline_s=deadline_s,
        lock_wait_s=0.1,
        detach_below_s=0.2,
        client_factory=factory(writer),
        detach=lambda args, env: pytest.fail("a held lock with budget left must not detach"),
    )
    elapsed = time.monotonic() - started
    assert code == 0 and elapsed < deadline_s * 60 / 50
    assert writer.timeouts and all(t <= deadline_s for t in writer.timeouts)
    state = CaptureState(state_dir(root, SID))
    assert state.diagnostics()[-1]["outcome"] == "stopped" and state.halt() is None
    assert not state.state_file.exists()
    assert capfd.readouterr() == ("", "")


def test_a_busy_lock_or_a_short_budget_detaches_a_final_pass(tmp_path: Path) -> None:
    repo_root, root = setup(tmp_path)
    hook_module, calls = load(), []
    expected = ["--event", "SessionEnd", "--transcript", str(root), "--session-id", SID, "--final", "--lock-wait", "600.0"]
    held = CaptureState(state_dir(root, SID)).acquire(0)
    try:
        assert (
            hook_module.main(stdin(root), env=ON, repo_root=repo_root, lock_wait_s=0.05, detach=lambda a, e: calls.append(a))
            == 0
        )
    finally:
        held.release()
    writer = Writer()
    kwargs = {"env": ON, "repo_root": repo_root, "client_factory": factory(writer), "detach_below_s": 10_000.0}
    assert hook_module.main(stdin(root), detach=lambda a, e: calls.append(a), **kwargs) == 0
    assert calls == [expected, expected] and writer.appends == []
    assert not CaptureState(state_dir(root, SID)).state_file.exists()


def test_every_failure_exits_zero_silently(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    repo_root, root = setup(tmp_path)
    hook_module = load()

    def boom(*args, **kw):
        raise RuntimeError("no writer")

    for text in ("", "not json", "[]", json.dumps({"session_id": SID})):
        assert hook_module.main(text, env=ON, repo_root=repo_root) == 0
    assert hook_module.main(stdin(root), env=ON, repo_root=repo_root, client_factory=boom) == 0
    assert hook_module.main(stdin(root), env={}, repo_root=repo_root, client_factory=boom) == 0
    assert capfd.readouterr() == ("", "")


def test_the_default_detach_uses_the_runner_module_and_puts_the_repo_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys  # noqa: PLC0415

    import scripts.telemetry_capture as package  # noqa: PLC0415

    repo_root, root = setup(tmp_path)
    seen: list[Any] = []
    monkeypatch.setattr(package, "detach_runner", lambda *a, **kw: seen.append((a, kw)))
    code_root = str(HOOK.parents[2])
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != code_root])
    held = CaptureState(state_dir(root, SID)).acquire(0)
    try:
        assert load().main(stdin(root), env=ON, repo_root=repo_root, lock_wait_s=0.05) == 0
    finally:
        held.release()
    (module, args, where), kw = seen[0]
    assert module == "scripts.telemetry_capture" and where == repo_root and kw == {"env": ON}
    assert args[-3:] == ["--final", "--lock-wait", "600.0"] and code_root in sys.path
