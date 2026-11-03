"""Mirror tests for scripts/telemetry_capture/verify.py over fakes: an on-disk tree, an in-memory reader and a fake portal."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.telemetry_capture import cursor_tree, verify, verify_due
from scripts.telemetry_capture import state as st
from scripts.telemetry_capture.state import CaptureState
from scripts.telemetry_capture.transport import Budget
from scripts.telemetry_verify.alarms import raise_alarms
from src.turn_capture.record_turn import PARSER_VERSION, CaptureResult
from tests.fixtures import telemetry_verification_trees as trees
from tests.fixtures.telemetry_golden_chain import (
    NOW,
    ROOT_ID,
    Portal,
    Tree,
    build,
    captured_rows,
    corpus,
    ledger_tree,
)
from tests.fixtures.turn_capture_corpus import SID, make_files, prompt


def test_a_clean_tree_completes_with_every_row_equal(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    result = tree.run()
    stored = sum(len(rows) for rows in captured_rows(tree.result).values())
    assert result.completed and result.compare_skipped == "none" and result.alarms == []
    assert result.counts["rows_equal"] == stored and result.counts["root_checked"] == 1
    assert tree.portal.calls == [] and result.reader_calls == 2
    record = tree.verify_records()[-1]
    assert record["status"] == "completed" and record["counts"]["rows_equal"] == stored
    state = tree.state.verify_state()
    assert state["root_session_id"] == ROOT_ID and state["incomplete_streak"] == 0 and "next_attempt_at" not in state


def test_a_failing_check_is_recorded_silent_and_backs_off(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = corpus(tmp_path)
    tree.reader.error = RuntimeError("boom with https://abc.lambda-url.eu-west-2.on.aws and arn:aws:iam::1:role/x")
    result = tree.run()
    assert result.status == "verify_error:RuntimeError" and not result.completed
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    record = tree.verify_records()[-1]
    assert "lambda-url" not in json.dumps(record) and "arn:" not in json.dumps(record)
    state = tree.state.verify_state()
    assert state["next_attempt_at"] == NOW + verify_due.VERIFY_RETRY_S and state["retry_s"] == 2 * verify_due.VERIFY_RETRY_S
    assert state["incomplete_streak"] == 1 and "settled_at" not in state, "a non-benign skip never settles"
    again = tree.run(now=NOW + 2)
    assert tree.state.verify_state()["retry_s"] == min(4 * verify_due.VERIFY_RETRY_S, verify_due.VERIFY_RETRY_MAX_S)
    assert not again.completed


def test_a_pass_during_the_read_is_tree_moved_never_a_finding(tmp_path: Path) -> None:
    tree = corpus(tmp_path)

    def pass_runs(verb: str, params: dict[str, Any]) -> None:
        if verb == "session_tree_rows":
            tree.state.append_diagnostic({"event": "Stop", "final": False, "at": int(NOW) + 1, "outcome": "sent"})

    tree.reader.hook = pass_runs
    result = tree.run()
    assert result.status == "tree_moved" and tree.portal.calls == []
    assert tree.state.verify_state().get("incomplete_streak", 0) == 0, "tree_moved is benign"
    moved_cursor = corpus(tmp_path / "other")
    cursor = moved_cursor.state.cursor_on_disk()
    assert cursor is not None
    moved_cursor.reader.hook = lambda verb, params: moved_cursor.state.save_cursor(
        replace(cursor, lines_consumed={"root": cursor.lines_consumed["root"] + 1}),
        moved_cursor.path.read_bytes(),
        st.Loaded(None, st.LOST),
    )
    assert moved_cursor.run().status == "tree_moved"


def test_writes_happen_only_under_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = corpus(tmp_path)
    writes: list[str] = []

    def guarded(name: str) -> Any:
        original = getattr(CaptureState, name)

        def wrapper(self: CaptureState, *args: Any, **kw: Any) -> Any:
            probe = CaptureState(self.dir).acquire(0.0)
            assert probe is None, f"{name} ran without holding the tree lock"
            writes.append(name)
            return original(self, *args, **kw)

        return wrapper

    for name in ("append_diagnostic", "save_verify_state"):
        monkeypatch.setattr(CaptureState, name, guarded(name))
    assert tree.run().completed
    assert writes == ["append_diagnostic", "save_verify_state"]
    held = tree.state.acquire(0.0)
    assert held is not None
    busy = tree.run()
    held.release()
    assert busy.status == "busy" and writes == ["append_diagnostic", "save_verify_state"], "busy writes nothing"


def test_a_newer_pass_record_skips_the_check(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    scheduled = tree.scheduling()
    tree.state.append_diagnostic({"event": "Stop", "final": False, "at": int(NOW), "outcome": "stopped"})
    result = tree.run(scheduling_record=scheduled)
    assert result.status == "superseded_by_pass" and tree.reader.calls == []
    assert tree.run(scheduling_record=None, trigger="operator").completed, "an operator check has no scheduling pass"


def test_state_fields_are_merged_by_owner(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    tree.state.save_verify_state({"scheduled_at": 7, "sent_passes": 10, "checks_by_day": {"d": 1}})

    def runner_writes(verb: str, params: dict[str, Any]) -> None:
        if verb == "session_tree_rows":
            tree.state.save_verify_state({**tree.state.verify_state(), "sent_passes": 11, "last_ledger_line": 999})

    tree.reader.hook = runner_writes
    assert tree.run().completed
    merged = tree.state.verify_state()
    assert merged["sent_passes"] == 11, "the runner's count survives the check"
    assert merged["checks_by_day"] == {"d": 1} and merged["settled_at"] == 7 and merged["root_session_id"] == ROOT_ID
    assert merged["last_ledger_line"] == 999, "the check only writes last_ledger_line when it covered a record"
    assert "next_attempt_at" not in merged and merged["incomplete_streak"] == 0


def test_moving_and_tail_mismatched_trees_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = corpus(tmp_path / "a")
    monkeypatch.setattr(verify, "PAGE_ROWS", 3)
    moving.reader.totals = [8, 9]
    result = moving.run()
    assert result.status == "tree_moved" and moving.portal.calls == []
    monkeypatch.undo()
    tail = corpus(tmp_path / "b")
    head, marker, last = tail.path.read_bytes().rpartition(b'"uuid": "')
    tail.path.write_bytes(head + marker + b"x" + last)
    skipped = tail.run()
    assert skipped.status == "cursor_tail_mismatch" and tail.reader.calls == []
    halted = corpus(tmp_path / "c")
    halted.state.write_halt({"reason": "x"})
    assert halted.run().status == "halted"
    other = corpus(tmp_path / "d")
    cursor = other.state.cursor_on_disk()
    assert cursor is not None
    other.state.save_cursor(
        replace(cursor, parser_version=PARSER_VERSION - 1), other.path.read_bytes(), st.Loaded(None, st.LOST)
    )
    assert other.run().status == "parser_version_skip"
    synthetic = corpus(tmp_path / "e")
    cursor = synthetic.state.cursor_on_disk()
    assert cursor is not None
    synthetic.state.save_cursor(
        replace(cursor, project_ref="synthetic:x"), synthetic.path.read_bytes(), st.Loaded(None, st.LOST)
    )
    assert synthetic.run().status == "non_real_project"
    other.state.save_verify_state({"scheduled_at": 42})
    other.run()
    assert other.state.verify_state()["settled_at"] == 42, "a per-tree condition settles the scheduled check"


def test_compare_skips_keep_the_transcript_legs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def legs(result: verify.VerifyResult) -> None:
        assert result.completed and result.counts["ledger_records"] >= 1 and result.counts["root_calls"] >= 1
        assert "rows_equal" not in result.counts and "missing_in_store" not in result.counts
        assert "stored_ledger_tested" not in result.counts

    absent = ledger_tree(tmp_path / "absent", root=False)
    result = absent.run()
    legs(result)
    assert result.compare_skipped == "root_absent" and result.counts["root_absent"] == 1 and result.counts["root_checked"] == 1
    assert absent.portal.classes() == ["root_absent"] and result.alarms == ["root_absent"]

    modified = ledger_tree(tmp_path / "modified")
    modified.state.save_verify_state({"producer_changed": True})
    legs(modified.run())
    assert modified.reader.calls == [] and modified.verify_records()[-1]["compare_skipped"] == "producer_modified"
    different = ledger_tree(tmp_path / "different")
    different.state.save_verify_state({"producer_fingerprint": "0" * 64})
    assert different.run().compare_skipped == "producer_modified"

    collision = ledger_tree(tmp_path / "collision", roots_in_tree=2)
    result = collision.run()
    legs(result)
    assert result.compare_skipped == "tree_collision"

    big = ledger_tree(tmp_path / "big")
    monkeypatch.setattr(verify, "MAX_TREE_PAGES", 0)
    result = big.run()
    legs(result)
    assert result.compare_skipped == "tree_too_large" and big.reader.calls == [], "judged from the expected rows"

    monkeypatch.setattr(verify, "MAX_TREE_PAGES", 10)
    early = build(tmp_path / "early", make_files([prompt("u1", 1, "p1", "go")]), root=False)
    result = early.run()
    assert result.completed and result.compare_skipped == "root_not_due" and result.counts.get("root_checked", 0) == 0
    assert early.portal.calls == [] and "missing_in_store" not in result.counts


def test_tree_reads_stop_at_the_call_cap(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    tree.reader.too_large = True
    result = tree.run()
    assert result.completed and result.compare_skipped == "tree_too_large"
    tree_calls = [c for c in tree.reader.calls if c[0] == "session_tree_rows"]
    assert len(tree_calls) == verify.MAX_TREE_PAGES and result.reader_calls == verify.MAX_TREE_PAGES + 1


def test_no_credentials_is_benign(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    result = tree.run(credentials=lambda: False)
    assert result.status == "no_credentials" and tree.reader.calls == [] and tree.portal.calls == []
    state = tree.state.verify_state()
    assert state["next_attempt_at"] == NOW + verify_due.VERIFY_RETRY_S
    assert state.get("incomplete_streak", 0) == 0 and "settled_at" not in state


def test_an_operator_check_never_writes_scheduling_fields(tmp_path: Path) -> None:
    tree = ledger_tree(tmp_path)
    tree.state.save_verify_state({"scheduled_at": 5, "next_attempt_at": 3, "last_ledger_line": 1, "sent_passes": 4})
    before = tree.state.verify_state()
    result = tree.run(trigger="operator", scheduling_record=None, alarms=False)
    assert result.completed
    after = tree.state.verify_state()
    for field in ("last_ledger_line", "held_ledger_line", "settled_at", "next_attempt_at", "retry_s", "incomplete_streak"):
        assert after.get(field) == before.get(field), field
    assert after["root_session_id"] == ROOT_ID and after["sent_passes"] == 4
    assert tree.verify_records()[-1]["trigger"] == "operator"


def test_a_persistent_non_completion_files_one_incomplete_alarm(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    tree.reader.error = ValueError("x")
    for attempt in range(verify.VERIFY_INCOMPLETE_STREAK - 1):
        tree.run(now=NOW + attempt)
        assert tree.portal.calls == []
    benign = tree.run(now=NOW + 10, credentials=lambda: False)
    assert benign.status == "no_credentials" and tree.portal.calls == [], "a benign token neither counts nor files"
    assert tree.state.verify_state()["incomplete_streak"] == verify.VERIFY_INCOMPLETE_STREAK - 1, "nor resets"
    third = tree.run(now=NOW + 20)
    assert not third.completed and tree.portal.classes() == ["verify_incomplete"]
    assert tree.portal.calls[0][1]["acceptance"].endswith("--assert-zero verify_incomplete")
    tree.run(now=NOW + 30)
    assert tree.portal.classes() == ["verify_incomplete"], "the helper's rate limit bounds repeats"
    halted = corpus(tmp_path / "h")
    halted.state.write_halt({"reason": "x"})
    halted.state.save_verify_state({"incomplete_streak": 9})
    halted.run()
    assert halted.portal.calls == [], "a halted tree is a per-tree condition, not a failure"


def test_malformed_lines_keep_one_index_space(tmp_path: Path) -> None:
    ledger_record = {"type": "cost-state", "modelUsage": {}}
    forked = {"type": "cost-state", "forkedFrom": "x", "modelUsage": {"claude-fork": {"inputTokens": 7}}}
    files = trees.resume_with_cost_state()
    lines = files[f"{SID}.jsonl"].split("\n")
    cost_index = next(i for i, line in enumerate(lines) if '"cost-state"' in line)
    lines.insert(cost_index, "this is not json")
    lines.insert(0, "neither is this")
    lines.insert(1, json.dumps(forked))
    lines.insert(2, "{not closed")
    files[f"{SID}.jsonl"] = "\n".join(lines)
    del ledger_record
    tree = build(tmp_path, files)
    result = tree.run()
    assert result.completed, result.status
    state = tree.state.verify_state()
    file_lines = files[f"{SID}.jsonl"].split("\n")
    real_cost = [i for i, line in enumerate(file_lines) if '"cost-state"' in line and "forkedFrom" not in line]
    assert result.counts["stored_ledger_records_inherited"] == 1
    assert result.counts["ledger_fork_unmeasured"] == 1 and "ledger_model_unexplained" not in result.counts
    assert state["held_ledger_line"] == real_cost[-1] and state["last_ledger_line"] == real_cost[-1]
    root = tree.path.read_bytes()
    cursor = tree.state.cursor_on_disk()
    assert cursor is not None
    assert not verify_due.verify_due(
        cursor, root, {**state, "sent_passes": 3}, event="Stop", final=False, outcome="sent", now=NOW + 1
    )


def _background_files() -> dict[str, str]:
    from tests.fixtures.turn_capture_corpus import (  # noqa: PLC0415
        assistant,
        child,
        text_block,
        tool_result,
        tool_use_block,
    )

    launch = tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p", "run_in_background": True})
    led = {"inputTokens": 20, "outputTokens": 40, "cacheReadInputTokens": 60, "cacheCreationInputTokens": 80}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [launch]),
        tool_result("r1", 3, "tuA", "launched", pid="p1", tur={"agentId": "bg", "isAsync": True, "status": "async_launched"}),
        {"type": "cost-state", "modelUsage": {"claude-test-1": led}},
        prompt("u2", 20, "p2", "again"),
    ]
    kid = [child(prompt("c1", 4, "p1", "task"), "bg"), child(assistant("c2", 6, "mc", [text_block("work")]), "bg")]
    return make_files(root, {"bg": kid})


def test_a_pending_record_is_held_not_covered(tmp_path: Path) -> None:
    files = _background_files()
    cost_line = next(i for i, line in enumerate(files[f"{SID}.jsonl"].split("\n")) if "cost-state" in line)
    tree = build(tmp_path, files, final=False)
    result = tree.run()
    assert result.completed, result.status
    assert {cls for _, _, cls in result.stored_models} == {"stored_ledger_not_compared"}
    state = tree.state.verify_state()
    assert state["held_ledger_line"] == cost_line and "last_ledger_line" not in state, "a pending record is held, not covered"
    cursor = tree.state.cursor_on_disk()
    assert cursor is not None
    root = tree.path.read_bytes()
    args = {"event": "Stop", "final": False, "outcome": "sent", "now": NOW + 1}
    assert not verify_due.verify_due(cursor, root, {**state, "sent_passes": 3}, **args), "held: the next passes are not due"

    closed = build(tmp_path / "closed", files, final=True)
    done = closed.run()
    assert done.completed and closed.state.verify_state()["last_ledger_line"] == cost_line


def _plant(tree: Tree, table: str, column: str, value: Any, index: int = 0) -> None:
    rows = [(t, json.loads(j)) for t, j in tree.reader.entries]
    seen = -1
    for position, (t, row) in enumerate(rows):
        if t == table:
            seen += 1
            if seen == index:
                row[column] = value
                tree.reader.entries[position] = (t, json.dumps(row))
                return
    raise AssertionError("no row to plant on")


def test_an_r7_boundary_divergence_is_declared_never_filed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = cursor_tree.expected_rows

    def with_notice(*args: Any, **kw: Any) -> CaptureResult:
        result = real(*args, **kw)
        return replace(result, diagnostics={**result.diagnostics, "notice_after_boundary": 1})

    tree = corpus(tmp_path / "a")
    _plant(tree, "telemetry_observations", "name", "changed")
    monkeypatch.setattr(cursor_tree, "expected_rows", with_notice)
    result = tree.run()
    assert result.completed and result.counts["r7_boundary_divergence"] == 1 and "column_mismatch" not in result.counts
    assert tree.portal.calls == [] and result.masked["column_mismatch"] == 1
    assert tree.verify_records()[-1]["r7_masked"]["column_mismatch"] == 1, "the per-class counts stay in the record"

    duplicate = corpus(tmp_path / "b")
    row = json.loads(duplicate.reader.entries[0][1])
    twin = dict(row, event_id=row["event_id"][:-1] + ("0" if row["event_id"][-1] != "0" else "1"))
    duplicate.reader.entries.append((duplicate.reader.entries[0][0], json.dumps(twin)))
    result = duplicate.run()
    assert duplicate.portal.classes() == ["duplicate_ref_rows"], "a keep-first-emitted row cannot produce a duplicate"

    monkeypatch.undo()
    plain = corpus(tmp_path / "c")
    _plant(plain, "telemetry_observations", "name", "changed")
    result = plain.run()
    assert result.counts["column_mismatch"] == 1 and plain.portal.classes() == ["column_mismatch"]


def test_alarm_classes_reach_the_helper_with_scope_and_rate_state(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    _plant(tree, "telemetry_sessions", "branch", "changed")
    first = tree.run()
    assert first.alarms == ["column_mismatch"] and first.outcome is not None and first.outcome.filed == 1
    action, fields = tree.portal.calls[0]
    assert action == "file" and f"scope={SID}" in fields["context"] and "count=1" in fields["context"]
    assert fields["source"] == "telemetry_verify" and fields["file"] == verify.FILE
    assert tree.state.verify_state()["alarm_at"]["column_mismatch"] == NOW
    again = tree.run(now=NOW + 60)
    assert again.outcome is not None and again.outcome.rate_limited == 1 and len(tree.portal.calls) == 1
    quiet = tree.run(now=NOW + 120, alarms=False)
    assert quiet.alarms == ["column_mismatch"] and len(tree.portal.calls) == 1, "--no-alarms files nothing"


def test_declared_classes_are_passed_as_declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def spy(findings: Any, **kw: Any) -> Any:
        seen.update(kw, findings=dict(findings))
        return raise_alarms(findings, **kw)

    monkeypatch.setattr(verify, "raise_alarms", spy)
    tree = ledger_tree(tmp_path)
    tree.run()
    assert set(seen["declared"]) == verify.DECLARED_CLASSES
    required = {
        "ledger_exact", "ledger_output_residual_nonfinal", "ledger_auxiliary_only", "ledger_bounded_background",
        "nonfinal_call", "spawn_equal", "spawn_corrects_nonfinal", "background_status_mismatch",
        "stored_ledger_exact", "stored_ledger_untested", "stored_ledger_not_compared", "stored_ledger_bounded",
        "stored_ledger_output_residual_final_false", "race_duplicate_rows", "ledger_fork_unmeasured", "r7_boundary_divergence",
    }  # fmt: skip
    assert required <= verify.DECLARED_CLASSES
    portal = Portal()
    outcome = raise_alarms(
        dict.fromkeys(sorted(verify.DECLARED_CLASSES), 2),
        scope="s", alarm_at=None, now=datetime.fromtimestamp(NOW), portal=portal, file=verify.FILE,
        acceptance_for=verify.acceptance_for, declared=verify.DECLARED_CLASSES, rows=[],
    )  # fmt: skip
    assert (
        portal.calls == []
        and outcome.declared == len(verify.DECLARED_CLASSES)
        and not (verify.DECLARED_CLASSES & verify.ALARM_CLASSES)
    )


def test_acceptance_is_the_verify_command_for_the_class() -> None:
    assert verify.ALARM_CLASSES
    for cls in sorted(verify.ALARM_CLASSES):
        command = verify.acceptance_for(cls)
        assert command.endswith(f"--no-alarms --assert-zero {cls}") and "scripts.telemetry_capture verify --tree" in command
        ok, why = lint_acceptance_command(command, require_decidable=True)
        assert ok, (cls, why)
    assert time.time() > 0


def test_budget_exhaustion_is_a_counted_token(tmp_path: Path) -> None:
    short = corpus(tmp_path / "short")
    result = short.run(budget=Budget.start(100))
    assert result.status == "budget_exhausted" and short.reader.calls == [], "a reader call needs 180 s of budget"
    assert short.state.verify_state()["incomplete_streak"] == 1
    none = corpus(tmp_path / "none")
    assert none.run(budget=Budget.start(0)).status == "budget_exhausted"


def test_a_tree_announcing_too_many_rows_stops_after_its_first_page(tmp_path: Path) -> None:
    tree = corpus(tmp_path)
    tree.reader.totals = [verify.MAX_TREE_PAGES * verify.PAGE_ROWS + 1]
    result = tree.run()
    assert result.completed and result.compare_skipped == "tree_too_large"
    assert len([c for c in tree.reader.calls if c[0] == "session_tree_rows"]) == 1


def test_credentials_are_resolved_the_way_the_writer_client_does(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    class Creds:
        def get_frozen_credentials(self) -> None:
            return None

    answers: list[Any] = [Creds(), None, RuntimeError("no profile")]

    class Session:
        def __init__(self, profile_name: str) -> None:
            self.profile_name = profile_name

        def get_credentials(self) -> Any:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=Session))
    assert [verify.credentials_available() for _ in range(3)] == [True, False, False]


def _forked_ledger_files() -> dict[str, str]:
    from tests.fixtures.turn_capture_corpus import assistant, text_block  # noqa: PLC0415

    led = {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40}
    root = [
        {**prompt("u0", 1, "p0", "copied"), "forkedFrom": {"sessionId": "x"}},
        {**assistant("a0", 2, "m0", [text_block("copied")]), "forkedFrom": {"sessionId": "x"}},
        prompt("u1", 3, "p1", "own"),
        assistant("a1", 4, "m1", [text_block("own")]),
        {"type": "cost-state", "modelUsage": {"claude-test-1": led}},
        prompt("u2", 10, "p2", "again"),
    ]
    return make_files(root)


def test_a_model_with_a_fork_copied_call_is_not_compared_and_its_alarm_is_declared(tmp_path: Path) -> None:
    tree = build(tmp_path, _forked_ledger_files(), final=True)
    result = tree.run()
    assert result.completed
    classes = {m: c for _, m, c in result.stored_models}
    assert classes == {"claude-test-1": "stored_ledger_not_compared"}
    assert result.counts.get("ledger_fork_unmeasured", 0) == 1 and result.counts.get("ledger_input_class_mismatch", 0) == 0
    assert tree.portal.calls == [], "how a fork's cost-state counts copied calls is unmeasured: declared, never filed"


def test_a_sidecar_changed_after_the_cursor_pinned_it_is_a_counted_token(tmp_path: Path) -> None:
    tree = corpus(tmp_path, "sidecar_present.json")
    sidecars = sorted((tmp_path / SID / "tool-results").glob("*"))
    assert sidecars, "the case carries a sidecar the cursor pins present"
    sidecars[0].write_text("changed after the pass", encoding="utf-8")
    result = tree.run()
    assert result.status == "sidecar_mutated" and tree.reader.calls == []
    assert tree.state.verify_state()["incomplete_streak"] == 1


def test_a_load_error_a_busy_lock_a_failing_portal_and_a_dropped_write_are_all_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = corpus(tmp_path / "load")
    monkeypatch.setattr(CaptureState, "load_cursor", lambda self, root: 1 / 0)
    assert load.run().status == "verify_error:ZeroDivisionError"
    monkeypatch.undo()

    busy = corpus(tmp_path / "busy")
    held: list[Any] = []

    def take(verb: str, params: dict[str, Any]) -> None:
        if verb == "session_tree_rows":
            held.append(busy.state.acquire(0.0))

    busy.reader.hook = take
    result = busy.run()
    held[0].release()
    assert result.status == "busy" and busy.verify_records() == [], (
        "a pass holds the lock: the check yields and writes nothing"
    )

    portal = corpus(tmp_path / "portal")
    _plant(portal, "telemetry_sessions", "branch", "changed")
    portal.portal = lambda action, fields: 1 / 0  # type: ignore[assignment]
    result = portal.run()
    assert result.completed and result.counts["alarm_error:ZeroDivisionError"] == 1

    dropped = corpus(tmp_path / "dropped")
    monkeypatch.setattr(CaptureState, "save_verify_state", lambda self, state: (_ for _ in ()).throw(OSError("disk")))
    result = dropped.run()
    assert result.completed, "a dropped final write never changes the verdict"
    assert "settled_at" not in dropped.state.verify_state() and "root_session_id" not in dropped.state.verify_state()
    monkeypatch.undo()

    broken = corpus(tmp_path / "broken")
    monkeypatch.setattr(verify._Check, "run", lambda self: 1 / 0)
    assert broken.run().status == "verify_error:ZeroDivisionError"
