"""Mirror test for scripts/telemetry_capture/runner.py (RN-1..RN-8) on an in-memory writer behind the HTTP seam.

The catalog rules (RN-5's pairing, RN-9 and RN-10) are in test_runner_catalog.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import runner
from scripts.telemetry_capture.transport import Budget, CaptureUnavailable
from tests.fixtures.telemetry_capture_writer import ON, PIN, REF, Writer, factory, records, repo, run, state, write
from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    build_secret,
    hook,
    jsonl,
    make_files,
    prompt,
    secrets_files,
    text_block,
    tool_result,
    tool_use_block,
)


def test_off_touches_nothing(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    assert run(tmp_path, writer, env={}).outcome == "off"
    assert not (tmp_path / SID).exists() and writer.posts == []


def test_a_pass_sends_every_table_once_sessions_last_and_persists_after_ack(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    result = run(tmp_path, writer, final=True)
    assert result.outcome == "sent"
    tables = [b["table"] for b in writer.appends()[0]["batches"]]
    assert tables[-1] == "telemetry_sessions" and len(tables) == len(set(tables))
    assert state(tmp_path).cursor_on_disk().finalized is True
    assert writer.appends()[0]["component"] == "turn_capture"
    line = state(tmp_path).diagnostics()[-1]
    assert line["outcome"] == "sent" and line["requests"][0]["telemetry_sessions"]["inserted"] >= 1
    assert run(tmp_path, writer, final=True).outcome == "no_rows" and len(writer.appends()) == 1


def test_deferral_without_a_pin_sends_nothing(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()[1:]))
    writer = Writer()
    result = run(tmp_path, writer)
    assert result.outcome == "deferred" and result.diag["diag"]["deferred_no_pin"] == 1
    assert writer.posts == [] and not state(tmp_path).state_file.exists()


def test_busy_lock_returns_busy(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    held = state(tmp_path).acquire(0)
    try:
        assert run(tmp_path, Writer()).outcome == "busy"
    finally:
        held.release()


def test_producer_pin_error_halts_and_later_passes_send_nothing(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    assert run(tmp_path, writer).outcome == "sent"
    st = state(tmp_path)
    envelope = json.loads(st.state_file.read_text())
    envelope["cursor"]["session_started_at_ms"] -= 1
    st.state_file.write_text(json.dumps(envelope))
    result = run(tmp_path, writer)
    assert result.outcome == "halted" and st.halt() == {"reason": "producer_error", "error_class": "CursorError"}
    sent = len(writer.posts)
    assert run(tmp_path, writer).outcome == "halted" and len(writer.posts) == sent


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (409, {"error_type": "grain_conflict", "row": 0}, "grain_conflict"),
        (422, {"error_type": "row_rule", "rule": "pattern_when", "column": "name", "row": 3}, "writer_refused"),
        (400, {"error_type": "bad_request"}, "writer_refused"),
        (403, {"error_type": "project_unregistered"}, "writer_refused"),
    ],
)
def test_permanent_refusals_halt_value_free_and_keep_the_cursor(tmp_path: Path, status, body, reason) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    assert run(tmp_path, writer).outcome == "sent"
    before = state(tmp_path).state_file.read_text()
    write(tmp_path, make_files(records(2)))
    writer.answers = [(status, {"ok": False, "error": "a message with a value", **body})]
    assert run(tmp_path, writer).outcome == "halted"
    halt = state(tmp_path).halt()
    assert halt["reason"] == reason and halt["status"] == status and "error" not in halt
    assert {k: halt[k] for k in body if k in halt} == body
    assert state(tmp_path).state_file.read_text() == before


def test_lost_cursor_replays_identical_rows_as_no_ops(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    run(tmp_path, writer)
    stored = len(writer.rows)
    state(tmp_path).state_file.write_text("not json")
    result = run(tmp_path, writer)
    assert result.outcome == "sent" and result.diag["diag"]["cursor_lost"] == 1
    assert len(writer.rows) == stored and all(c["inserted"] == 0 for c in result.diag["requests"][0].values())
    assert state(tmp_path).cursor_on_disk() is not None


def test_tail_mismatch_replays_and_a_diverged_key_halts_as_grain_conflict_after_loss(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    run(tmp_path, writer)
    st = state(tmp_path)
    envelope = json.loads(st.state_file.read_text())
    envelope["root_tail_uuid"] = "elsewhere"
    st.state_file.write_text(json.dumps(envelope))
    key = next(iter(writer.rows))
    writer.rows[key] = {**writer.rows[key], "name": "diverged"}
    result = run(tmp_path, writer)
    assert result.diag["diag"]["cursor_tail_mismatch"] == 1 and result.outcome == "halted"
    assert st.halt()["reason"] == "grain_conflict_after_cursor_loss"


def test_413_halves_down_to_one_row_then_halts(tmp_path: Path) -> None:
    write(tmp_path, make_files(records(3)))
    writer = Writer()
    too_big = (413, {"ok": False, "error_type": "too_many_blobs"})
    writer.answers = [too_big, too_big]
    assert run(tmp_path, writer).outcome == "sent"
    sizes = [sum(len(b["rows"]) for b in p["batches"]) for p in writer.appends()]
    assert sizes[1] == sizes[0] // 2 and sum(sizes[2:]) == sizes[0]
    write(tmp_path, make_files(records(4)))
    writer.answers = [(413, {"ok": False, "error_type": "payload_too_large"})] * 40
    assert run(tmp_path, writer).outcome == "halted" and state(tmp_path).halt()["status"] == 413


@pytest.mark.parametrize(("status", "reason"), [(500, "status_500"), (502, "status_502"), (401, "status_401")])
def test_other_statuses_stop_without_halting(tmp_path: Path, status: int, reason: str) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    writer.answers = [(status, {"ok": False})]
    result = run(tmp_path, writer)
    assert result.outcome == "stopped" and result.diag["diag"][reason] == 1
    assert state(tmp_path).halt() is None and not state(tmp_path).state_file.exists()


def test_a_failed_last_chunk_leaves_cursor_and_finalize_cuts_unchanged(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    run(tmp_path, writer)
    before = state(tmp_path).state_file.read_text()
    writer.answers = [(500, {"ok": False})]
    assert run(tmp_path, writer, final=True).outcome == "stopped"
    assert state(tmp_path).state_file.read_text() == before
    assert state(tmp_path).cursor_on_disk().finalize_cuts == []


def test_tenant_is_registered_once_per_pass(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer(tenant=False)
    assert run(tmp_path, writer).outcome == "sent"
    assert [p["action"] for p in writer.posts].count("register_tenant") == 1
    write(tmp_path, make_files(records(2)))
    writer.answers = [(403, {"error_type": "tenant_unresolved"})] * 2
    result = run(tmp_path, writer)
    assert result.outcome == "stopped" and result.diag["diag"]["tenant_unresolved"] == 1


@pytest.mark.parametrize("smoke", [False, None])
def test_describe_mismatch_stops_without_halting(tmp_path: Path, smoke) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer(data_path_is_smoke=bool(smoke))
    if smoke is None:
        writer.answer = lambda payload: (200, {"ok": True})  # type: ignore[method-assign]
    result = run(tmp_path, writer)
    assert result.outcome == "stopped" and result.diag["diag"]["describe_mismatch"] == 1
    assert state(tmp_path).halt() is None and writer.appends() == []


def _big_files(n_big: int, ref: str = REF, secret: str = "") -> dict[str, str]:
    root = [hook("h0", 0.5, "SessionStart:startup", command=PIN.format(ref)), prompt("u1", 1, "p1", "go")]
    for i in range(n_big):
        root.append(assistant(f"a{i}", 2 + i, f"m{i}", [tool_use_block(f"tu{i}", "Bash", {"command": "big"})]))
        root.append(tool_result(f"r{i}", 2.5 + i, f"tu{i}", f"{i}" * 70000 + secret, pid="p1"))
    root += [assistant("az", 50, "mz", [text_block("done")]), prompt("uz", 60, "pz", "next")]
    return make_files(root)


def test_blob_rows_are_delivered_capped_per_request_and_redelivered_once(tmp_path: Path) -> None:
    write(tmp_path, _big_files(2))
    writer = Writer(max_items=1)
    assert run(tmp_path, writer).outcome == "sent"
    assert len([r for r in writer.raw if not r.startswith(b"{")]) == 2
    blob_counts = [
        sum(1 for b in p["batches"] for r in b["rows"] if "content_sha256" in r and "content" not in r)
        for p in writer.appends()
    ]
    assert max(blob_counts) == 1 and sum(blob_counts) == 2
    line = state(tmp_path).diagnostics()[-1]
    assert line["diag"]["blobs_uploaded"] == 2


def test_blob_not_verified_redelivers_once_then_halts(tmp_path: Path) -> None:
    write(tmp_path, _big_files(1))
    writer = Writer()
    writer.answers = [(422, {"error_type": "blob_not_verified", "row": 0})]
    assert run(tmp_path, writer).outcome == "sent"
    assert [p["action"] for p in writer.posts].count("begin_blobs") == 2
    write(tmp_path, _big_files(2))
    writer.answers = [(422, {"error_type": "blob_not_verified", "row": 0})] * 2
    assert run(tmp_path, writer).outcome == "halted" and state(tmp_path).halt()["error_type"] == "blob_not_verified"


def test_blob_refusal_halts_and_transient_failure_stops(tmp_path: Path) -> None:
    write(tmp_path, _big_files(1))
    writer = Writer()
    original = writer.answer

    def refuse(payload):
        return (400, {"ok": False, "error_type": "bad_request"}) if payload["action"] == "begin_blobs" else original(payload)

    writer.answer = refuse  # type: ignore[method-assign]
    assert run(tmp_path, writer).outcome == "halted" and state(tmp_path).halt()["reason"] == "blob_refused"
    state(tmp_path).clear_halt()
    writer.answer = lambda p: (503, {}) if p["action"] == "begin_blobs" else original(p)  # type: ignore[method-assign]
    result = run(tmp_path, writer)
    assert result.outcome == "stopped" and result.diag["diag"]["blob_transient"] == 1


def test_no_credentials_and_short_budget_send_nothing(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))

    def missing(*args, **kw):
        raise CaptureUnavailable("no_credentials")

    result = run(tmp_path, Writer(), client_factory=missing)
    assert result.outcome == "stopped" and result.diag["diag"]["no_credentials"] == 1
    writer = Writer()
    assert run(tmp_path, writer, detach_below=10_000.0).outcome == "short_budget"
    assert writer.posts == [] and not state(tmp_path).state_file.exists()


def test_deadline_stops_the_pass(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    original = writer.answer
    clock = {"t": 0.0}

    def slow(payload):
        clock["t"] += 700.0 if payload["action"] == "describe" else 0.0
        return original(payload)

    writer.answer = slow  # type: ignore[method-assign]
    result = runner.run_pass(
        tmp_path / f"{SID}.jsonl",
        SID,
        "Stop",
        final=False,
        budget=Budget(600.0, lambda: clock["t"]),
        env=ON,
        repo_root=repo(tmp_path),
        client_factory=factory(writer),
        sleep=lambda s: None,
    )
    assert result.outcome == "stopped" and result.diag["diag"]["deadline"] == 1


def test_smoke_override_needs_the_matching_synthetic_pin(tmp_path: Path) -> None:
    ref = "synthetic:pc-smoke-0000abcd"
    write(tmp_path, make_files(records(ref=ref)))
    writer = Writer()
    off_repo = repo(tmp_path, enabled=False)
    other = {"OTL_TURN_CAPTURE_SMOKE_REF": "synthetic:other"}
    assert run(tmp_path, writer, env=other, repo_root=off_repo).outcome == "off" and writer.posts == []
    assert not (tmp_path / SID).exists()
    assert run(tmp_path, writer, env={"OTL_TURN_CAPTURE_SMOKE_REF": ref}, repo_root=off_repo).outcome == "sent"


def test_diagnostics_record_the_switch_source(tmp_path: Path) -> None:
    write(tmp_path, make_files(records(1)))
    result = run(tmp_path, Writer())
    assert result.diag["diag"]["switch_source:working_tree"] == 1
    ref = "synthetic:pc-smoke-0000abcd"
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir()
    write(smoke_dir, make_files(records(1, ref=ref)))
    smoked = run(smoke_dir, Writer(), env={"OTL_TURN_CAPTURE_SMOKE_REF": ref}, repo_root=repo(smoke_dir, enabled=False))
    assert smoked.diag["diag"]["switch_source:none"] == 1
    assert not any(key.startswith("switch_source:working") for key in smoked.diag["diag"])


def test_first_stop_pass_after_resume_closes_the_previous_segment(tmp_path: Path) -> None:
    resumed = [
        hook("h0", 0.5, "SessionStart:startup", command=PIN.format(REF)),
        prompt("u0", 1, "p0", "go"),
        assistant("a0", 2, "m0", [text_block("ok")]),
        hook("h1", 20, "SessionStart:resume", command=PIN.format(REF)),
        prompt("u1", 21, "p1", "again"),
        assistant("a1", 22, "m1", [text_block("ok")]),
        prompt("u2", 30, "p2", "more"),
    ]
    root = write(tmp_path, make_files(resumed))
    writer = Writer()
    assert run(tmp_path, writer).outcome == "sent"
    closes = [r for (table, _), r in writer.rows.items() if table == "telemetry_sessions" and r["event_kind"] == "close"]
    assert [(c["outcome"], c["source_ordinal"]) for c in closes] == [("success", 2)]
    loaded = state(tmp_path).load_cursor(root.read_bytes())
    assert loaded.cursor is not None and loaded.cursor.finalize_cuts == []


def test_sent_payloads_carry_no_secret_shaped_value(tmp_path: Path) -> None:
    files = secrets_files()
    pin = jsonl([hook("h0", 0.5, "SessionStart:startup", command=PIN.format(REF))])
    files[f"{SID}.jsonl"] = pin + files[f"{SID}.jsonl"]
    big = _big_files(1, secret=" key " + build_secret("aws_access"))
    files[f"{SID}.jsonl"] += "".join(big[f"{SID}.jsonl"].splitlines(keepends=True)[2:])
    write(tmp_path, files)
    writer = Writer()
    assert run(tmp_path, writer, final=True).outcome == "sent"
    puts = [r for r in writer.raw if not r.startswith(b"{")]
    assert puts and all(b"[REDACTED:" in p for p in puts)
    sent = b"\n".join(writer.raw)
    kinds = ("aws_access", "aws_secret", "aws_session", "anthropic", "github", "github_pat", "bearer")
    for kind in kinds:
        secret = build_secret(kind)
        assert secret.encode() not in sent and json.dumps(secret).encode() not in sent, kind
    assert b"[REDACTED:" in b"\n".join(r for r in writer.raw if r.startswith(b"{"))


def test_a_cursor_whose_pin_is_not_the_smoke_ref_is_off_inside_the_lock(tmp_path: Path) -> None:
    ref = "synthetic:pc-smoke-0000abcd"
    write(tmp_path, make_files(records(ref=ref)))
    writer, env = Writer(), {"OTL_TURN_CAPTURE_SMOKE_REF": ref}
    off_repo = repo(tmp_path, enabled=False)
    assert run(tmp_path, writer, env=env, repo_root=off_repo).outcome == "sent"
    envelope = json.loads(state(tmp_path).state_file.read_text())
    envelope["cursor"]["project_ref"] = "synthetic:other"
    state(tmp_path).state_file.write_text(json.dumps(envelope))
    result = run(tmp_path, writer, env=env, repo_root=off_repo)
    assert result.outcome == "off" and result.diag["diag"]["smoke_pin_mismatch"] == 1


def test_a_regressing_cursor_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    run(tmp_path, writer)
    stored = state(tmp_path).cursor_on_disk()
    behind = runner.record_turn.__globals__["CaptureResult"]([], [], [], [], replace(stored, lines_consumed={"root": 1}), {})
    monkeypatch.setattr(runner, "record_turn", lambda *a, **kw: behind)
    result = run(tmp_path, writer)
    assert result.outcome == "no_rows" and result.diag["diag"]["cursor_regression"] == 1
    assert state(tmp_path).cursor_on_disk() == stored


def test_the_default_factory_builds_the_production_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(runner, "make_client", lambda env, st, budget, **kw: seen.append((env, st, budget, kw)) or "client")
    budget = Budget.start(1.0)
    assert runner.default_client_factory({"A": "1"}, None, budget, sleep=print, rng=float) == "client"
    assert seen == [({"A": "1"}, None, budget, {"sleep": print, "rng": float})]


def _hook_state(tmp_path: Path) -> dict[str, Any]:
    return state(tmp_path).verify_state()


def test_verify_due_never_changes_the_pass_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = []
    for index, due in enumerate((False, True, "raise")):
        root = tmp_path / str(index)
        root.mkdir()
        write(root, make_files(records()))
        if due == "raise":
            monkeypatch.setattr(runner.verify_due, "verify_due", lambda *a, **k: 1 / 0)
        else:
            monkeypatch.setattr(runner.verify_due, "verify_due", lambda *a, _d=due, **k: _d)
        writer = Writer()
        result = run(root, writer)
        outcomes.append((result.outcome, len(writer.appends()), state(root).cursor_on_disk()))
        monkeypatch.undo()
    assert outcomes[0] == outcomes[1] == outcomes[2], "a due, not-due or failing check leaves the pass as it was"
    assert outcomes[0][0] == "sent"


def test_verify_due_is_recorded_only_after_a_persisted_non_final_pass(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    first = run(tmp_path, writer)
    assert first.outcome == "sent" and first.diag["verify_due"] is False
    assert state(tmp_path).diagnostics()[-1]["verify_due"] is False
    assert _hook_state(tmp_path)["sent_passes"] == 1
    again = run(tmp_path, writer)
    assert again.outcome == "no_rows" and again.diag["verify_due"] is False
    assert _hook_state(tmp_path)["sent_passes"] == 1, "only a sent pass advances the counter"
    final = run(tmp_path, writer, final=True)
    assert final.outcome == "sent" and "verify_due" not in final.diag and _hook_state(tmp_path)["sent_passes"] == 1
    stopped = tmp_path / "stopped"
    stopped.mkdir()
    write(stopped, make_files(records()))
    failing = Writer()
    failing.answers = [(500, {"ok": False})]
    result = run(stopped, failing)
    assert result.outcome == "stopped" and "verify_due" not in result.diag and _hook_state(stopped) == {}
    deferred = tmp_path / "deferred"
    deferred.mkdir()
    write(deferred, make_files(records()[1:]))
    assert "verify_due" not in run(deferred, Writer()).diag


def test_a_verify_due_error_leaves_the_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, make_files(records()))
    monkeypatch.setattr(runner.verify_due, "producer_fingerprint", lambda: 1 / 0)
    result = run(tmp_path, Writer())
    assert (
        result.outcome == "sent" and result.diag["verify_due_error"] == "ZeroDivisionError" and "verify_due" not in result.diag
    )
    line = state(tmp_path).diagnostics()[-1]
    assert line["outcome"] == "sent" and line["verify_due_error"] == "ZeroDivisionError"
    assert state(tmp_path).cursor_on_disk() is not None


def test_sent_passes_record_the_producer_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    monkeypatch.setattr(runner.verify_due, "producer_fingerprint", lambda: "aaa")
    run(tmp_path, writer)
    assert _hook_state(tmp_path)["producer_fingerprint"] == "aaa" and "producer_changed" not in _hook_state(tmp_path)
    write(tmp_path, make_files(records(2)))
    monkeypatch.setattr(runner.verify_due, "producer_fingerprint", lambda: "bbb")
    run(tmp_path, writer)
    held = _hook_state(tmp_path)
    assert held["producer_fingerprint"] == "aaa" and held["producer_changed"] is True and held["sent_passes"] == 2
    write(tmp_path, make_files(records(3)))
    monkeypatch.setattr(runner.verify_due, "producer_fingerprint", lambda: "aaa")
    run(tmp_path, writer)
    assert _hook_state(tmp_path)["producer_changed"] is True, "sticky once a sent pass's fingerprint differs"


def test_a_due_pass_schedules_the_check_and_counts_it(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    state(tmp_path).save_verify_state({"sent_passes": runner.verify_due.VERIFY_FIRST_SENT_PASS - 1, "settled_at": 1.0})
    before = time.time()
    result = run(tmp_path, Writer())
    held = _hook_state(tmp_path)
    assert result.diag["verify_due"] is True and held["sent_passes"] == runner.verify_due.VERIFY_FIRST_SENT_PASS
    assert held["checks_by_day"] == {runner.verify_due.utc_day(before): 1}
    assert before <= held["scheduled_at"] <= time.time() and held["settled_at"] == 1.0
    assert held["next_attempt_at"] == pytest.approx(held["scheduled_at"] + runner.verify_due.VERIFY_GUARD_S)
    write(tmp_path, make_files(records(2)))
    assert run(tmp_path, Writer()).diag["verify_due"] is False, "the guard holds the next passes"


def test_a_not_due_pass_that_changes_nothing_does_not_rewrite_the_state(tmp_path: Path) -> None:
    write(tmp_path, make_files(records()))
    writer = Writer()
    run(tmp_path, writer)
    before = state(tmp_path).verify_file.stat().st_mtime_ns
    assert run(tmp_path, writer).outcome == "no_rows" and state(tmp_path).verify_file.stat().st_mtime_ns == before
