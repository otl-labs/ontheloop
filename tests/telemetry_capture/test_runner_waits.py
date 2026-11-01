"""Mirror test for scripts/telemetry_capture/waits.py and the runner's opt-in wait (rules RW-1..RW-4), no real waits."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import __main__ as cli
from scripts.telemetry_capture import runner, waits
from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import Budget
from tests.fixtures.turn_capture_corpus import SID, assistant, jsonl, prompt, stop_feedback, text_block, witness

ON = {"CLAUDE_CODE_REMOTE": "true"}


class Clock:
    """A clock that only moves when the code sleeps; each sleep may run a scripted action on the file."""

    def __init__(self, path: Path, script: list[Callable[[], None]] | None = None) -> None:
        self.now, self.path, self.script, self.sleeps = 0.0, path, list(script or []), 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.sleeps += 1
        if self.script:
            self.script.pop(0)()

    def append(self, *records: dict[str, Any]) -> Callable[[], None]:
        def run() -> None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(jsonl(list(records)))

        return run


def tree(tmp_path: Path, *records: dict[str, Any]) -> Path:
    path = tmp_path / f"{SID}.jsonl"
    path.write_text(jsonl(list(records)), encoding="utf-8")
    return path


def test_stop_pass_waits_for_its_own_witness(tmp_path: Path) -> None:
    path = tree(
        tmp_path,
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("k")]),
        witness("w1", 3),
        prompt("u2", 5, "p2", "again"),
        assistant("a2", 6, "m2", [text_block("k")]),
    )
    pace = Clock(path)
    pace.script = [pace.append(stop_feedback("f1", 6.5, "p2")), pace.append(witness("w2", 7))]
    assert waits.wait_for_witness(path, pace.clock, pace.sleep) == waits.WITNESS_SEEN
    assert pace.sleeps >= 2 + int(waits.SETTLE_QUIET_S / waits.POLL_S)
    ready = tmp_path / "late.jsonl"
    late = Clock(ready)
    late.script = [late.append(prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness("w1", 3))]
    assert waits.wait_for_witness(ready, late.clock, late.sleep) == waits.WITNESS_SEEN


def test_the_witness_wait_reads_only_complete_lines_and_survives_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / f"{SID}.jsonl"
    whole = jsonl([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness("w1", 3)])
    split = whole.rindex('{"attachment"')
    pace = Clock(path)

    def create() -> None:
        path.write_text(whole[:split] + "not json\n[1]\n" + whole[split : split + 20], encoding="utf-8")

    def finish() -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(whole[split + 20 :])

    pace.script = [create, finish]
    assert waits.wait_for_witness(path, pace.clock, pace.sleep) == waits.WITNESS_SEEN
    assert pace.sleeps >= 2


def test_wait_timeout_still_runs_the_pass_and_is_counted(tmp_path: Path) -> None:
    path = tree(tmp_path, prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]))
    pace = Clock(path)
    assert waits.wait_for_witness(path, pace.clock, pace.sleep) == waits.WITNESS_TIMEOUT
    assert pace.now >= waits.WITNESS_WAIT_S
    busy = Clock(path)
    busy.script = [busy.append(prompt(f"x{n}", 10 + n, f"q{n}", "tick")) for n in range(40)]
    path.write_text(jsonl([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness("w1", 3)]))
    assert waits.wait_for_witness(path, busy.clock, busy.sleep) == waits.SETTLE_TIMEOUT
    nothing = Clock(path)
    assert waits.wait_for_prompt(path, "never", nothing.clock, nothing.sleep) == waits.PROMPT_TIMEOUT
    assert nothing.now >= waits.PROMPT_WAIT_S
    result = runner.run_pass(
        path,
        SID,
        "Stop",
        final=False,
        budget=Budget.start(60),
        env=ON,
        repo_root=repo(tmp_path),
        wait=lambda: waits.WITNESS_TIMEOUT,
    )
    assert result.outcome == "deferred" and result.diag["diag"][waits.WITNESS_TIMEOUT] == 1


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "telemetry").mkdir(parents=True, exist_ok=True)
    (root / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\n", encoding="utf-8")
    return root


def test_stop_failure_pass_does_not_wait() -> None:
    def args(event: str, prompt_id: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(event=event, transcript="/t/s.jsonl", prompt_id=prompt_id)

    pace = Clock(Path("/none"))
    for event in ("StopFailure", "SessionEnd", "operator"):
        assert cli._event_wait(args(event), pace.clock, pace.sleep) is None
    assert cli._event_wait(args("UserPromptSubmit"), pace.clock, pace.sleep) is None
    assert cli._event_wait(args("Stop"), pace.clock, pace.sleep) is not None
    assert cli._event_wait(args("UserPromptSubmit", "p1"), pace.clock, pace.sleep) is not None


def test_prompt_pass_waits_for_its_prompt_record(tmp_path: Path) -> None:
    path = tree(tmp_path, prompt("u1", 1, "p1", "go"))
    pace = Clock(path)
    pace.script = [
        pace.append(stop_feedback("f1", 2, "p9")),
        pace.append(assistant("a1", 3, "m1", [text_block("k")])),
        pace.append(prompt("u2", 5, "p9", "now")),
    ]
    assert waits.wait_for_prompt(path, "p9", pace.clock, pace.sleep) == waits.PROMPT_SEEN
    assert pace.sleeps == 3


def held_lock(tmp_path: Path) -> Any:
    return CaptureState(state_dir(tmp_path / f"{SID}.jsonl", SID)).acquire(0)


def test_stop_and_prompt_passes_wait_for_the_lock(tmp_path: Path) -> None:
    path = tree(tmp_path, prompt("u1", 1, "p1", "go"))
    released: list[bool] = []
    holder: list[Any] = []

    def sleep(seconds: float) -> None:
        if not released:
            holder[0].release()
            released.append(True)

    for event in ("Stop", "UserPromptSubmit"):
        released.clear()
        holder[:] = [held_lock(tmp_path)]
        result = runner.run_pass(
            path,
            SID,
            event,
            final=False,
            budget=Budget.start(600),
            env=ON,
            repo_root=repo(tmp_path),
            lock_wait=120.0,
            sleep=sleep,
        )
        assert result.outcome == "deferred" and result.diag["diag"]["lock_waited"] == 1
    stuck = held_lock(tmp_path)
    before = len(CaptureState(state_dir(path, SID)).diagnostics())
    busy = runner.run_pass(
        path,
        SID,
        "Stop",
        final=False,
        budget=Budget.start(600),
        env=ON,
        repo_root=repo(tmp_path),
        lock_wait=0.5,
        sleep=lambda s: None,
    )
    assert busy.outcome == "busy" and len(CaptureState(state_dir(path, SID)).diagnostics()) == before
    stuck.release()


def test_a_pass_without_a_wait_is_unchanged(tmp_path: Path) -> None:
    path = tree(tmp_path, prompt("u1", 1, "p1", "go"))
    result = runner.run_pass(path, SID, "SessionEnd", final=True, budget=Budget.start(60), env=ON, repo_root=repo(tmp_path))
    assert result.outcome == "deferred"
    assert not {"lock_waited", *[name for name in result.diag["diag"] if name.endswith("_seen")]} & set(result.diag["diag"])
    lock = held_lock(tmp_path)
    assert (
        runner.run_pass(path, SID, "SessionEnd", final=True, budget=Budget.start(60), env=ON, repo_root=repo(tmp_path)).outcome
        == "busy"
    )
    lock.release()


def test_switch_off_pass_returns_off_before_waiting(tmp_path: Path) -> None:
    path = tree(tmp_path, prompt("u1", 1, "p1", "go"))

    def fail() -> str:
        raise AssertionError("an off pass must not wait")

    for env in ({**ON, "GITHUB_ACTIONS": "true"}, {**ON, "OTL_TURN_CAPTURE": "off"}, {}):
        result = runner.run_pass(
            path, SID, "UserPromptSubmit", final=False, budget=Budget.start(60), env=env, repo_root=repo(tmp_path), wait=fail
        )
        assert result.outcome == "off"
    assert not state_dir(path, SID).exists()
