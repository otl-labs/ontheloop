"""Mirror test for scripts/telemetry_capture/state.py (ST-1..ST-6), including the tail-check mismatch."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.telemetry_capture import state as st
from scripts.telemetry_capture.state import CaptureState, Loaded
from src.turn_capture.cursor import CaptureCursor


def cursor(root_lines: int) -> CaptureCursor:
    return CaptureCursor(
        "claude_code", 3, "sid", "a/b", "fixed_non_rollover_allowance", 1, lines_consumed={"root": root_lines}
    )


ROOT = b'{"uuid": "u1"}\n{"type": "x"}\n{"uuid": "u2"}\n{"uuid": "u3"}\n'


def test_state_dir_is_under_the_session_directory(tmp_path: Path) -> None:
    assert st.state_dir(tmp_path / "sid.jsonl", "sid") == tmp_path / "sid" / "telemetry-capture"


def test_root_tail_uuid_is_the_last_uuid_below_lines_consumed() -> None:
    assert st.root_tail_uuid(ROOT, 2) == "u1"
    assert st.root_tail_uuid(ROOT, 4) == "u3"
    assert st.root_tail_uuid(ROOT, 0) is None and st.root_tail_uuid(b'{"x": 1}\n', 1) is None


def test_cursor_round_trip_tail_mismatch_and_lost_cursor(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.load_cursor(ROOT) == Loaded(None, st.LOST)
    assert state.save_cursor(cursor(3), ROOT, Loaded(None, st.LOST))
    assert json.loads(state.state_file.read_text())["root_tail_uuid"] == "u2"
    loaded = state.load_cursor(ROOT)
    assert loaded.valid and loaded.cursor == cursor(3)
    assert state.load_cursor(ROOT.replace(b"u2", b"zz")) == Loaded(None, st.TAIL_MISMATCH)
    state.state_file.write_text("{")
    assert state.load_cursor(ROOT) == Loaded(None, st.LOST) and state.cursor_on_disk() is None
    state.state_file.write_text(json.dumps({"cursor": {"cursor_format": 2}, "root_tail_uuid": None}))
    assert state.load_cursor(ROOT).status == st.LOST


def test_save_refuses_a_regression_below_a_valid_cursor_but_replaces_a_lost_one(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert not state.save_cursor(cursor(1), ROOT, Loaded(cursor(3), st.VALID))
    assert not state.state_file.exists()
    assert state.save_cursor(cursor(1), ROOT, Loaded(cursor(3), st.TAIL_MISMATCH))
    assert state.cursor_on_disk() == cursor(1)
    assert [p.name for p in state.dir.iterdir()] == ["state.json"]


def test_lock_is_exclusive_and_waits_within_its_bound(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    held = state.acquire(0)
    assert held is not None
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    assert state.acquire(1.0, sleep=sleep, clock=lambda: clock["t"]) is None and sum(sleeps) <= 1.0 and sleeps
    held.release()
    again = state.acquire(0)
    assert again is not None
    again.release()


def test_halt_record_lifecycle(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.halt() is None and state.clear_halt() is False
    state.write_halt({"status": 409})
    assert state.halt() == {"status": 409}
    state.halt_file.write_text("[1]")
    assert state.halt() == {"halt": "unreadable"}
    state.halt_file.write_text("{")
    assert state.halt() == {"halt": "unreadable"}
    assert state.clear_halt() is True and state.halt() is None


def test_diagnostics_keep_the_last_500_lines(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.diagnostics() == []
    for i in range(st.DIAGNOSTICS_KEEP + 3):
        state.append_diagnostic({"n": i})
    lines = state.diagnostics()
    assert len(lines) == st.DIAGNOSTICS_KEEP and lines[0] == {"n": 3} and lines[-1] == {"n": st.DIAGNOSTICS_KEEP + 2}
    state.diagnostics_file.write_text('{"n": 1}\nnot json\n[2]\n')
    assert state.diagnostics() == [{"n": 1}]


def test_writer_url_and_marker(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.writer_url() is None and state.marker() is None
    state.remember_writer_url("https://w")
    state.write_marker({"boot_id": "b"})
    assert state.writer_url() == "https://w" and state.marker() == {"boot_id": "b"}
    state.url_file.write_text("\n")
    state.marker_file.write_text("[]")
    assert state.writer_url() is None and state.marker() is None


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    state = CaptureState(tmp_path / "d")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(st.os, "replace", refuse)
    try:
        state.write_halt({"status": 409})
    except OSError:
        pass
    else:
        raise AssertionError("the write must fail")
    assert list(state.dir.iterdir()) == []


def test_verify_state_round_trips_and_a_malformed_file_reads_empty(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.verify_state() == {}
    saved = {"sent_passes": 4, "checks_by_day": {"2026-10-17": 1}, "alarm_at": {"column_mismatch": 1.5}}
    state.save_verify_state(saved)
    assert state.verify_state() == saved and state.verify_file.parent == state.dir
    state.verify_file.write_text("{")
    assert state.verify_state() == {}
    state.verify_file.write_text("[1]")
    assert state.verify_state() == {}
    assert not list(state.dir.glob(".verify.json.*")), "every write is a temp file plus replace"


def test_verify_state_fields_are_merged_by_owner() -> None:
    current = {"sent_passes": 9, "last_ledger_line": 5, "next_attempt_at": 100, "alarm_at": {"a": 1}}
    runner = {"sent_passes": 10, "last_ledger_line": 99, "scheduled_at": 7, "next_attempt_at": 200}
    merged = st.merge_verify_state(current, runner, st.RUNNER)
    assert merged == {
        "sent_passes": 10,
        "last_ledger_line": 5,
        "scheduled_at": 7,
        "next_attempt_at": 200,
        "alarm_at": {"a": 1},
    }
    check = {"sent_passes": 0, "last_ledger_line": 8, "next_attempt_at": None, "alarm_at": {"a": 2}}
    merged = st.merge_verify_state(merged, check, st.CHECK)
    assert merged == {"sent_passes": 10, "last_ledger_line": 8, "scheduled_at": 7, "alarm_at": {"a": 2}}
    operator = {"settled_at": 1, "root_session_id": "S", "alarm_at": {"b": 3}, "next_attempt_at": 5}
    merged = st.merge_verify_state(merged, operator, st.OPERATOR)
    assert merged == {
        "sent_passes": 10,
        "last_ledger_line": 8,
        "scheduled_at": 7,
        "root_session_id": "S",
        "alarm_at": {"b": 3},
    }


def test_state_records_the_catalog_and_reads_absent_as_smoke(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "d")
    assert state.save_cursor(cursor(3), ROOT, Loaded(None, st.LOST), catalog="ducklake_telemetry", reset_at=12.5)
    envelope = json.loads(state.state_file.read_text())
    assert (envelope["catalog"], envelope["reset_at"]) == ("ducklake_telemetry", 12.5)
    assert state.load_cursor(ROOT) == Loaded(cursor(3), st.VALID, "ducklake_telemetry", 12.5)
    assert state.load_cursor(ROOT.replace(b"u2", b"zz")) == Loaded(None, st.TAIL_MISMATCH, "ducklake_telemetry")

    assert state.save_cursor(cursor(3), ROOT, state.load_cursor(ROOT))
    envelope = json.loads(state.state_file.read_text())
    assert envelope["catalog"] == st.DEFAULT_CATALOG == "ducklake_smoke" and "reset_at" not in envelope

    legacy = {key: value for key, value in envelope.items() if key != "catalog"}
    state.state_file.write_text(json.dumps(legacy))
    loaded = state.load_cursor(ROOT)
    assert loaded.valid and (loaded.catalog, loaded.reset_at) == ("ducklake_smoke", None)
    assert not state.save_cursor(cursor(1), ROOT, loaded, catalog="ducklake_telemetry")
    assert json.loads(state.state_file.read_text()) == legacy

    for bad in ({"catalog": 7}, {"reset_at": "soon"}, {"reset_at": True}):
        state.state_file.write_text(json.dumps({**legacy, **bad}))
        assert state.load_cursor(ROOT) == Loaded(None, st.LOST)


def test_verify_view_drops_catalog_keyed_fields_on_a_catalog_change() -> None:
    held = {"root_session_id": "S", "last_ledger_line": 4, "held_ledger_line": 6, "sent_passes": 3, "settled_at": 1.0}
    assert st.CATALOG_KEYED == ("root_session_id", "last_ledger_line", "held_ledger_line")
    assert st.verify_view(held, "ducklake_smoke") == held
    assert st.verify_view(held, "ducklake_telemetry") == {"sent_passes": 3, "settled_at": 1.0}
    recorded = {**held, "verify_catalog": "ducklake_telemetry"}
    assert st.verify_view(recorded, "ducklake_telemetry") == recorded
    assert st.verify_view(recorded, "ducklake_smoke") == {
        "sent_passes": 3,
        "settled_at": 1.0,
        "verify_catalog": "ducklake_telemetry",
    }
    assert st.verify_view(held, "ducklake_smoke") is not held

    assert "verify_catalog" in st.VERIFY_OWNERS[st.CHECK]
    assert "verify_catalog" not in st.VERIFY_OWNERS[st.OPERATOR] | st.VERIFY_OWNERS[st.RUNNER]
    assert st.merge_verify_state({}, {"verify_catalog": "ducklake_telemetry"}, st.OPERATOR) == {}
    assert st.merge_verify_state({}, {"verify_catalog": "ducklake_telemetry"}, st.CHECK) == {
        "verify_catalog": "ducklake_telemetry"
    }


def test_paired_catalog_accepts_only_a_known_catalog_with_its_data_path() -> None:
    assert st.KNOWN_CATALOGS == ("ducklake_smoke", "ducklake_telemetry")
    assert st.paired_catalog({"meta_schema": "ducklake_smoke", "data_path_is_smoke": True}) == "ducklake_smoke"
    assert st.paired_catalog({"meta_schema": "ducklake_telemetry", "data_path_is_smoke": False}) == "ducklake_telemetry"
    for bad in (
        {"meta_schema": "ducklake_smoke", "data_path_is_smoke": False},
        {"meta_schema": "ducklake_telemetry", "data_path_is_smoke": True},
        {"meta_schema": "ducklake_telemetry", "data_path_is_smoke": 0},
        {"meta_schema": "ducklake_ops", "data_path_is_smoke": False},
        {"data_path_is_smoke": True},
        None,
        "ducklake_smoke",
    ):
        assert st.paired_catalog(bad) is None
