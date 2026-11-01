"""Mirror test for scripts/telemetry_capture/__main__.py: the detached entry and every operator subcommand on fakes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.telemetry_capture import __main__ as cli
from scripts.telemetry_capture.runner import PassResult
from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import CaptureUnavailable
from src.turn_capture.cursor import CaptureCursor
from tests.fixtures.turn_capture_corpus import SID, assistant, hook, jsonl, prompt, text_block, tool_result, tool_use_block

PIN = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=example/project'


class Client:
    def __init__(self, answers: list[tuple[int, Any]]) -> None:
        self.answers, self.payloads = answers, []

    def invoke(self, payload: dict[str, Any]) -> tuple[int, Any]:
        self.payloads.append(payload)
        return self.answers.pop(0)


def tree(tmp_path: Path) -> Path:
    records = [hook("h0", 0.5, "SessionStart:startup", command=PIN)]
    records += [
        hook("h1", 0.6, "SessionStart:resume", command=PIN),
        hook("h2", 0.7, "SessionStart:resume", command="bash x.sh"),
    ]
    use = {"persistedOutputPath": "/elsewhere/tool-results/out.txt", "stdout": "p"}
    records += [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "x"})]),
        tool_result("r1", 3, "tu1", "p", pid="p1", tur=use),
        assistant("a2", 4, "m2", [text_block("done")]),
    ]
    root = tmp_path / f"{SID}.jsonl"
    root.write_text(jsonl(records), encoding="utf-8")
    (tmp_path / SID / "tool-results").mkdir(parents=True)
    (tmp_path / SID / "tool-results" / "out.txt").write_text("full")
    return root


def state(root: Path) -> CaptureState:
    return CaptureState(state_dir(root, root.stem))


def save_cursor(root: Path) -> None:
    cursor = CaptureCursor("claude_code", 3, SID, "example/project", "fixed_non_rollover_allowance", 1767225600500)
    st = state(root)
    st.dir.mkdir(parents=True, exist_ok=True)
    st.state_file.write_text(json.dumps({"cursor": json.loads(cursor.to_json()), "root_tail_uuid": None}))


def test_run_is_silent_and_always_exits_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[dict[str, Any]] = []

    def fake(path, session_id, event, **kw):
        seen.append({"path": path, "sid": session_id, "event": event, **kw})
        raise RuntimeError("anything")

    monkeypatch.setattr(cli, "run_pass", fake)
    argv = ["run", "--event", "SessionEnd", "--transcript", "/t/s.jsonl", "--session-id", "s", "--final", "--lock-wait", "600"]
    assert cli.main(argv, env={"A": "1"}) == 0
    assert capsys.readouterr() == ("", "")
    call = seen[0]
    assert (call["path"], call["sid"], call["event"], call["final"], call["lock_wait"]) == (
        Path("/t/s.jsonl"),
        "s",
        "SessionEnd",
        True,
        600.0,
    )
    assert call["env"] == {"A": "1"} and 1100 < call["budget"].remaining() <= 1200


def test_pass_refuses_an_unpinned_tree_and_reports_the_outcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out: list[str] = []
    unpinned = tmp_path / "unpinned.jsonl"
    unpinned.write_text(jsonl([prompt("u1", 1, "p1", "go")]), encoding="utf-8")
    assert cli.main(["pass", "--tree", str(unpinned)], out=out.append) == 1 and out == [
        "refused: the tree has no pinning SessionStart attachment"
    ]
    monkeypatch.setattr(cli, "run_pass", lambda *a, **kw: PassResult("sent", {"rows": {"t": 2}, "diag": {}}))
    root = tree(tmp_path)
    assert cli.main(["pass", "--tree", str(root), "--final"], out=out.append) == 0
    assert json.loads(out[-1]) == {"outcome": "sent", "rows": {"t": 2}, "diag": {}}
    monkeypatch.setattr(cli, "run_pass", lambda *a, **kw: PassResult("halted", {}))
    assert cli.main(["pass", "--tree", str(root)], out=out.append) == 1


def test_status_prints_counts_only(tmp_path: Path) -> None:
    root = tree(tmp_path)
    out: list[str] = []
    assert cli.main(["status", "--tree", str(root)], out=out.append) == 0
    assert json.loads(out[-1])["lines_consumed"] is None
    save_cursor(root)
    state(root).append_diagnostic({"event": "Stop", "outcome": "sent", "rows": {"t": 1}, "record_turn": {"x": 1}})
    state(root).write_halt({"status": 409})
    assert cli.main(["status", "--tree", str(root)], out=out.append) == 0
    summary = json.loads(out[-1])
    assert summary["halt"] == {"status": 409} and summary["last"] == {"event": "Stop", "outcome": "sent", "rows": {"t": 1}}


def test_probe_reads_grain_counts_and_enforces_required_tables(tmp_path: Path) -> None:
    root = tree(tmp_path)
    out: list[str] = []
    assert cli.main(["probe", "--tree", str(root)], out=out.append) == 1 and out[-1] == "probe: no cursor for this tree"
    save_cursor(root)
    counts = {"telemetry_observations": {"rows": 3, "keys": 3}, "telemetry_transcripts": {"rows": 0, "keys": 0}}
    client = Client([(200, {"ok": True, "grain_counts": counts})] * 3 + [(500, {"ok": False})])
    factory = lambda env, st, budget, **kw: client  # noqa: E731
    assert (
        cli.main(["probe", "--tree", str(root), "--require", "telemetry_observations"], client_factory=factory, out=out.append)
        == 0
    )
    assert client.payloads[0] == {
        "action": "telemetry_probe",
        "project_ref": "example/project",
        "session_ref": SID,
        "session_started_at": "2026-01-01T00:00:00.500Z",
    }
    assert "telemetry_observations rows=3 keys=3" in out
    required = ["probe", "--tree", str(root), "--require", "telemetry_observations,telemetry_transcripts"]
    assert (
        cli.main(required, client_factory=factory, out=out.append) == 1
        and out[-1] == "probe: zero rows in telemetry_transcripts"
    )
    state(root).write_halt({"status": 409})
    assert cli.main(["probe", "--tree", str(root)], client_factory=factory, out=out.append) == 1
    assert cli.main(["probe", "--tree", str(root)], client_factory=factory, out=out.append) == 1
    assert out[-1] == "probe: status 500, no grain counts"

    def missing(*a, **kw):
        raise CaptureUnavailable("no_credentials")

    assert cli.main(["probe", "--tree", str(root)], client_factory=missing, out=out.append) == 1
    assert out[-1] == "probe: no_credentials"


def test_mark_and_observe_report_reclaim_evidence(tmp_path: Path) -> None:
    root = tree(tmp_path)
    boot = tmp_path / "boot_id"
    boot.write_text("boot-1\n")
    sidecar = tmp_path / SID / "tool-results" / "out.txt"
    old = time.mktime(time.strptime("2025-01-01", "%Y-%m-%d"))
    os.utime(sidecar, (old, old))
    out: list[str] = []
    assert cli.main(["observe", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    assert json.loads(out[0]) == {"session_end_passes": 0, "session_start_by_source": {"resume": 1, "startup": 1}}
    assert out[1:] == ["tool_results_sidecars late=0 seen=1", "reclaim: unproven (no marker)"]
    assert cli.main(["mark", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    state(root).append_diagnostic({"event": "SessionEnd", "outcome": "sent"})
    os.utime(sidecar, None)
    out.clear()
    assert cli.main(["observe", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    assert json.loads(out[0])["session_end_passes"] == 1 and out[1] == "tool_results_sidecars late=1 seen=1"
    assert out[2] == "reclaim: no reclaim seen, unproven; marker_present=True prefix_hash_equal=True"
    boot.write_text("boot-2\n")
    with root.open("a") as handle:
        handle.write("{}\n")
    assert cli.main(["observe", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    assert out[-1] == "reclaim: boot_id_changed=True marker_present=True prefix_hash_equal=True"
    root.write_text("rewritten\n")
    assert cli.main(["observe", "--tree", str(root)], out=out.append, boot_id=tmp_path / "absent") == 0
    assert out[-1] == "reclaim: no reclaim seen, unproven; marker_present=True prefix_hash_equal=False"


def test_clear_halt(tmp_path: Path) -> None:
    root = tree(tmp_path)
    out: list[str] = []
    state(root).write_halt({"status": 409})
    assert cli.main(["clear-halt", "--tree", str(root)], out=out.append) == 0
    assert cli.main(["clear-halt", "--tree", str(root)], out=out.append) == 0
    assert out == ["clear-halt: cleared", "clear-halt: no halt recorded"]


def test_register_registers_the_tenant_then_the_project() -> None:
    out: list[str] = []
    client = Client([(200, {"ok": True, "created": False}), (200, {"ok": True, "created": True})])
    argv = ["register", "--project-ref", "ontheloop", "--name", "n", "--remote", "https://example.invalid/r.git"]
    assert cli.main(argv, client_factory=lambda env, st, budget, **kw: client, out=out.append) == 0
    assert [p["action"] for p in client.payloads] == ["register_tenant", "register_project"]
    assert client.payloads[1] == {
        "action": "register_project",
        "project_ref": "ontheloop",
        "name": "n",
        "remote": "https://example.invalid/r.git",
    }
    assert out == ["register_tenant: ok created=False", "register_project: ok created=True"]
    failing = Client([(403, {"ok": False, "error_type": "forbidden"})])
    assert cli.main(argv, client_factory=lambda *a, **kw: failing, out=out.append) == 1
    assert out[-1] == "register_tenant: failed status=403 error_type=forbidden"

    def missing(*a, **kw):
        raise CaptureUnavailable("no_writer_url")

    assert cli.main(argv, client_factory=missing, out=out.append) == 1 and out[-1] == "register: no_writer_url"


def test_observe_reports_an_unreadable_prefix_as_unequal(tmp_path: Path) -> None:
    root = tree(tmp_path)
    boot = tmp_path / "boot_id"
    boot.write_text("boot-1\n")
    out: list[str] = []
    assert cli.main(["mark", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    root.unlink()
    assert cli.main(["observe", "--tree", str(root)], out=out.append, boot_id=boot) == 0
    assert out[-1] == "reclaim: no reclaim seen, unproven; marker_present=True prefix_hash_equal=False"


def test_detach_runner_starts_a_fully_detached_module_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess  # noqa: PLC0415

    import scripts.telemetry_capture as package  # noqa: PLC0415

    seen: dict[str, Any] = {}

    def popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return "child"

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("never run"))
    assert package.detach_runner("m.x", ["--event", "Stop"], tmp_path, env={"A": "1"}) == "child"
    assert seen["argv"][1:] == ["-m", "m.x", "run", "--event", "Stop"] and seen["cwd"] == tmp_path
    assert seen["env"] == {"A": "1"} and seen["start_new_session"] is True and seen["close_fds"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    package.detach_runner("m.x", [], tmp_path)
    assert seen["env"] is None


def _verify_result(**over: Any) -> Any:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    base: dict[str, Any] = {
        "status": "completed",
        "trigger": "operator",
        "counts": {"rows_equal": 4, "ledger_records": 1, "stored_ledger_tested": 1, "root_calls": 2, "sync_spawns": 1},
        "reader_calls": 2,
        "elapsed_s": 1,
    }
    return verify.VerifyResult(**{**base, **over})


def _run_verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: Any, *flags: str) -> tuple[int, list[str]]:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    seen: list[dict[str, Any]] = []

    def fake(path: Path, session_id: str, **kw: Any) -> Any:
        seen.append({"path": path, "session_id": session_id, **kw})
        return result

    monkeypatch.setattr(verify, "run_verify", fake)
    lines: list[str] = []
    code = cli.main(
        ["verify", "--tree", str(tmp_path / f"{SID}.jsonl"), *flags], out=lines.append, reader_factory=lambda: object()
    )
    assert seen and seen[0]["trigger"] == "operator" and seen[0]["scheduling_record"] is None and seen[0]["portal"] is None
    assert seen[0]["alarms"] is ("--no-alarms" not in flags) and seen[0]["session_id"] == SID
    return code, lines


def test_verify_command_prints_counts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path)
    lines: list[str] = []
    code = cli.main(["verify", "--tree", str(root), "--no-alarms"], out=lines.append, reader_factory=lambda: object())
    assert code == 2 and len(lines) == 1 and lines[0].startswith("VERIFY status=no_cursor compare_skipped=none rows_equal=0 ")
    assert " alarms=none" in lines[0] and all("=" in part for part in lines[0].split()[1:])
    assert "example/project" not in lines[0] and SID not in lines[0] and str(tmp_path) not in lines[0]
    code, out = _run_verify(
        monkeypatch,
        tmp_path,
        _verify_result(compare_skipped="root_not_due", alarms=["column_mismatch", "ledger_output_excess"]),
        "--no-alarms",
    )
    assert code == 1 and len(out) == 1
    fields = dict(part.split("=", 1) for part in out[0].split()[1:])
    assert list(fields)[:2] == ["status", "compare_skipped"] and fields["alarms"] == "column_mismatch,ledger_output_excess"
    assert {"rows_equal", "missing_in_store", "r7_boundary_divergence", "reader_calls", "elapsed_s"} <= set(fields)
    assert all(value.isdigit() for key, value in fields.items() if key not in ("status", "compare_skipped", "alarms"))


def test_verify_exit_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def code(result: Any, *flags: str) -> int:
        return _run_verify(monkeypatch, tmp_path, result, *flags)[0]

    assert code(_verify_result()) == 0
    assert code(_verify_result(alarms=["column_mismatch"])) == 1
    assert code(_verify_result(status="tree_moved")) == 2 and code(_verify_result(status="verify_error:ValueError")) == 2
    ok = _verify_result()
    assert code(ok, "--assert-zero", "column_mismatch") == 0 and code(ok, "--assert-zero", "ledger_output_excess") == 0
    assert code(ok, "--assert-zero", "stored_ledger_input_class_mismatch") == 0
    assert code(ok, "--assert-zero", "nonfinal_root_call") == 0 and code(ok, "--assert-zero", "spawn_usage_mismatch") == 0
    assert code(ok, "--assert-zero", "verify_incomplete") == 0, "any completed check is its own denominator"
    positive = _verify_result(counts={**ok.counts, "column_mismatch": 2}, alarms=["column_mismatch"])
    assert code(positive, "--assert-zero", "column_mismatch") == 1
    assert code(positive, "--assert-zero", "key_mismatch") == 0, "another class is judged on its own count"
    skipped = _verify_result(status="no_credentials")
    assert code(skipped, "--assert-zero", "column_mismatch") == 2
    bare = {"rows_equal": 0, "root_checked": 0}
    assert code(_verify_result(counts=bare, compare_skipped="tree_collision"), "--assert-zero", "column_mismatch") == 2
    assert code(_verify_result(counts={**ok.counts, "ledger_records": 0}), "--assert-zero", "ledger_input_class_mismatch") == 2
    assert (
        code(_verify_result(counts={**ok.counts, "stored_ledger_tested": 0}), "--assert-zero", "stored_ledger_bound_exceeded")
        == 2
    )
    assert code(_verify_result(counts={**ok.counts, "sync_spawns": 0}), "--assert-zero", "spawn_usage_mismatch") == 2
    assert code(_verify_result(counts={**ok.counts, "root_calls": 0}), "--assert-zero", "nonfinal_root_call") == 2
    assert (
        code(_verify_result(counts={**ok.counts, "pinned_absent": 0}), "--assert-zero", "sidecar_pinned_absent_present") == 2
    )
    assert (
        code(_verify_result(counts={**ok.counts, "pinned_absent": 1}), "--assert-zero", "sidecar_pinned_absent_present") == 0
    )
    assert code(_verify_result(counts={**ok.counts, "root_checked": 0}), "--assert-zero", "root_absent") == 2
    masked = _verify_result(counts={**ok.counts, "r7_boundary_divergence": 1})
    assert code(masked, "--assert-zero", "column_mismatch") == 2, (
        "a tree whose compare classes were declared away is never green"
    )
    lines: list[str] = []
    assert cli.main(["verify", "--tree", str(tmp_path / "x.jsonl"), "--assert-zero", "not_a_class"], out=lines.append) == 2
    assert lines == ["verify: not_a_class is not an alarm class"]


def _detached(
    monkeypatch: pytest.MonkeyPatch, diag: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> list[dict[str, Any]]:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    ran: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: PassResult("sent", diag))
    monkeypatch.setattr(verify, "run_verify", lambda path, sid, **kw: ran.append({"path": path, "sid": sid, **kw}))
    argv = ["run", "--event", "Stop", "--transcript", "/t/s.jsonl", "--session-id", "s"]
    assert cli.main(argv, env={}, reader_factory=lambda: "reader") == 0
    assert capsys.readouterr() == ("", "")
    return ran


def test_run_runs_verify_in_process_only_when_due(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _detached(monkeypatch, {"event": "Stop", "at": 1, "outcome": "sent", "verify_due": False}, capsys) == []
    assert _detached(monkeypatch, {"event": "Stop", "at": 1, "outcome": "sent"}, capsys) == []
    diag = {"event": "Stop", "at": 1, "outcome": "sent", "verify_due": True}
    (call,) = _detached(monkeypatch, diag, capsys)
    assert (call["path"], call["sid"], call["trigger"], call["reader"], call["portal"]) == (
        Path("/t/s.jsonl"),
        "s",
        "detached",
        "reader",
        None,
    )
    assert call["scheduling_record"] is diag, "the scheduling pass record is the one the runner appended"
    assert 590 < call["budget"].remaining() <= 600, "the check has its own budget, not the pass's"
    assert "alarms" not in call, "a detached check raises alarms"


def test_a_verify_import_failure_leaves_run_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "scripts.telemetry_capture.verify", None)
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: PassResult("sent", {"verify_due": True}))
    argv = ["run", "--event", "Stop", "--transcript", "/t/s.jsonl", "--session-id", "s"]
    assert cli.main(argv, env={}) == 0
    assert capsys.readouterr() == ("", "")
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pass failed")))
    assert cli.main(argv, env={}) == 0 and capsys.readouterr() == ("", "")


def test_verify_builds_the_default_reader_when_none_is_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.telemetry_capture import read_route  # noqa: PLC0415

    built: list[str] = []
    monkeypatch.setattr(read_route, "make_routed_reader", lambda: built.append("reader") or "reader")
    _run_verify_with(monkeypatch, tmp_path)
    assert built == ["reader"]


def test_verify_builds_the_routed_reader(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.telemetry_capture import read_route, verify  # noqa: PLC0415

    readers: list[Any] = []
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: PassResult("sent", {"verify_due": True}))
    monkeypatch.setattr(verify, "run_verify", lambda *a, **kw: readers.append(kw["reader"]))
    argv = ["run", "--event", "Stop", "--transcript", "/t/s.jsonl", "--session-id", "s"]
    assert cli.main(argv, env={}) == 0 and capsys.readouterr() == ("", "")
    (reader,) = readers
    assert isinstance(reader, read_route.RoutedReader) and reader._catalog is None and reader._events is None


class _Boto3Session:
    """Credentials resolve and every API client fails, so the catalog deploy output is unreadable."""

    def __init__(self, profile_name: str | None = None) -> None:
        self.profile_name = profile_name

    def get_credentials(self) -> Any:
        return SimpleNamespace(get_frozen_credentials=lambda: None)

    def client(self, service: str, region_name: str) -> Any:
        raise RuntimeError("no AWS in this test")


def test_an_unresolved_catalog_is_a_counted_check_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import boto3  # noqa: PLC0415

    from scripts.telemetry_capture import read_route, verify_due  # noqa: PLC0415

    monkeypatch.setattr(boto3, "Session", _Boto3Session)
    for name in (read_route.CATALOG_ENV, "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)
    root = tree(tmp_path)
    diag = {"event": "Stop", "at": 1, "outcome": "sent", "verify_due": True}
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: PassResult("sent", diag))
    before = time.time()
    assert cli.main(["run", "--event", "Stop", "--transcript", str(root), "--session-id", SID], env={}) == 0
    assert capsys.readouterr() == ("", "")
    (record,) = [line for line in state(root).diagnostics() if line.get("event") == "verify"]
    assert (record["trigger"], record["status"]) == ("detached", "verify_error:TelemetryCatalogUnresolved")
    held = state(root).verify_state()
    assert held["incomplete_streak"] == 1 and held["retry_s"] == 2 * verify_due.VERIFY_RETRY_S
    assert held["next_attempt_at"] >= before + verify_due.VERIFY_RETRY_S and "verify_catalog" not in held


def _run_verify_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> int:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    monkeypatch.setattr(verify, "run_verify", lambda *a, **k: _verify_result())
    return cli.main(["verify", "--tree", str(tmp_path / f"{SID}.jsonl")], out=lambda line: None)


def test_every_alarm_class_has_a_defined_denominator() -> None:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    zero: dict[str, int] = {}
    for cls in sorted(verify.ALARM_CLASSES):
        assert not cli._denominator_met(cls, zero) or cls == "verify_incomplete", cls
        counts = {cli.DENOMINATOR_OF.get(cls, "x"): 1, "rows_equal": 1, "ledger_records": 1, "stored_ledger_tested": 1}
        assert cli._denominator_met(cls, counts), f"{cls} has no denominator rule"


def test_run_builds_the_event_wait_and_passes_the_lock_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def fake(path, session_id, event, **kw):
        seen.append({"event": event, **kw})
        return PassResult("sent", {})

    monkeypatch.setattr(cli, "run_pass", fake)
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 10.0
        return ticks[0]

    base = ["run", "--transcript", str(tmp_path / "s.jsonl"), "--session-id", "s", "--lock-wait", "120"]
    assert cli.main([*base, "--event", "Stop"], env={}, clock=clock, sleep=lambda s: None) == 0
    assert (
        cli.main([*base, "--event", "UserPromptSubmit", "--prompt-id", "p1"], env={}, clock=clock, sleep=lambda s: None) == 0
    )
    assert cli.main([*base, "--event", "StopFailure"], env={}, clock=clock, sleep=lambda s: None) == 0
    stop, prompt_call, failure = seen
    assert stop["lock_wait"] == prompt_call["lock_wait"] == failure["lock_wait"] == 120.0
    assert (
        stop["wait"]() == "witness_wait_timeout" and prompt_call["wait"]() == "prompt_wait_timeout" and failure["wait"] is None
    )
    assert 750 < stop["budget"].remaining() <= 755 and 725 < prompt_call["budget"].remaining() <= 730
