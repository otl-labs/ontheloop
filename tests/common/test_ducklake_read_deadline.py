"""Read deadline runner for the event verbs, served by the telemetry reader since slice 2c (rec-4474, Decision 236)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.common import ducklake_event_reads as ev
from src.common import ducklake_handler_errors as he
from src.common import ducklake_read_deadline as dl
from src.common.ducklake_handler_errors import TypedFault

pytestmark = pytest.mark.unit

_CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "ducklake_telemetry_reader.yaml"


class InterruptException(Exception):
    """Named like DuckDB's class; matched by name and module prefix."""


class FakeInterrupt(InterruptException):
    pass


InterruptException.__module__ = "_duckdb"
FakeInterrupt.__module__ = "_duckdb"


class BlockingCon:
    """execute() blocks until interrupt() is called; sends before execute starts (or the first `drop`) are dropped."""

    def __init__(self, drop: int = 0) -> None:
        self.sent = 0
        self.drop = drop
        self.executing = threading.Event()
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        self.sent += 1
        if self.executing.is_set() and self.sent > self.drop:
            self._interrupted.set()

    def execute(self) -> str:
        self.executing.set()
        if not self._interrupted.wait(5):
            return "finished"
        raise InterruptException("INTERRUPT Error: Interrupted!")


def _timer_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if isinstance(t, threading.Timer)]


def test_deadline_interrupts_and_answers_typed_504() -> None:
    con, fired = BlockingCon(), []
    before = len(_timer_threads())
    with pytest.raises(TypedFault) as info:
        dl.run(con, con.execute, 0.05, lambda: fired.append(True))
    assert (info.value.status, info.value.error_type, info.value.reason) == (504, "read_deadline_exceeded", "reader_deadline")
    assert fired and con.sent >= 1
    assert len(_timer_threads()) == before


def test_interrupt_is_resent_until_the_call_returns() -> None:
    con = BlockingCon(drop=3)
    with pytest.raises(TypedFault) as info:
        dl.run(con, con.execute, 0.01, lambda: None)
    assert info.value.status == 504 and con.sent >= 4


def test_spent_deadline_answers_504_without_running_the_call() -> None:
    ran, fired = [], []
    for spent in (0.0, -3.0):
        with pytest.raises(TypedFault) as info:
            dl.run(object(), lambda: ran.append(1), spent, lambda: fired.append(1))
        assert info.value.status == 504
    assert ran == [] and fired == [1, 1]


def test_runner_reads_remaining_time_when_invoked() -> None:
    remaining = {"ms": 60_000.0}
    run = dl.runner(lambda: remaining["ms"], None, lambda: None)
    remaining["ms"] = 5_000.0  # falls under the margin between building the runner and invoking it
    ran: list[int] = []
    with pytest.raises(TypedFault):
        run(object(), lambda: ran.append(1))
    assert ran == []
    remaining["ms"] = 600_000.0
    assert run(object(), lambda: "ok") == "ok"


def test_unflagged_interrupt_is_not_a_deadline() -> None:
    def call() -> None:
        raise FakeInterrupt("someone else interrupted")

    with pytest.raises(FakeInterrupt):
        dl.run(object(), call, 600.0, lambda: None)


def test_other_errors_pass_through_and_cancel_the_timer() -> None:
    before = len(_timer_threads())
    con = BlockingCon()

    def call() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        dl.run(con, call, 600.0, lambda: None)
    assert len(_timer_threads()) == before and con.sent == 0
    after_fire = threading.Event()

    def late_fail() -> None:
        time.sleep(0.15)
        raise ValueError("not an interrupt")

    with pytest.raises(ValueError):
        dl.run(con, late_fail, 0.02, after_fire.set)
    assert after_fire.is_set()


def test_no_deadline_runs_the_call_directly() -> None:
    assert dl.run(None, lambda: 7, None, lambda: pytest.fail("never fires")) == 7
    assert dl.runner(None, None, lambda: None)(None, lambda: 8) == 8


def test_fire_after_is_the_earlier_deadline() -> None:
    assert dl.fire_after_s(None, None) is None
    assert dl.fire_after_s(60_000, None) == pytest.approx(50.0)
    assert dl.fire_after_s(60_000, 5_000) == pytest.approx(5.0)
    assert dl.fire_after_s(60_000, 90_000) == pytest.approx(50.0)
    assert dl.fire_after_s(4_000, None) == pytest.approx(-6.0)
    assert dl.fire_after_s(None, 2_500) == pytest.approx(2.5)


def test_contract_declares_the_deadline_answer() -> None:
    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    verbs = next(v for v in _walk(contract, "named_read"))
    assert "504" in {str(code) for code in verbs["response_codes"]}
    assert "read_deadline_exceeded" in verbs["typed_errors"]
    assert "deadline_ms" in verbs["payload_schema_ref"]
    text = _CONTRACT.read_text(encoding="utf-8")
    assert "answers 504 read_deadline_exceeded" in text and "remain the runtime's 502" in text
    assert any("telemetry-cold-read-deadline" in str(e.get("summary", "")) for e in contract["amendment_log"])


def test_deadline_fault_answers_through_typed_fault(capsys: pytest.CaptureFixture[str]) -> None:
    fault = dl._deadline_fault()
    assert isinstance(fault, TypedFault) and fault.status == 504
    status, body = he.terminal(fault, function="telemetry_reader", action="named_read", request_id="req-1", elapsed_ms=1.0)
    assert status == 504
    assert body == {
        "ok": False,
        "error_type": "read_deadline_exceeded",
        "reason": "reader_deadline",
        "request_id": "req-1",
        "action": "named_read",
    }
    capsys.readouterr()


def _walk(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict) and "response_codes" in node[key]:
            yield node[key]
        for value in node.values():
            yield from _walk(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


_RANGE = "SELECT count(*) FROM range(100000000000) a, range(1000) b"


def _duckdb() -> Any:
    return pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")


def test_real_duckdb_interrupt_answers_504_and_the_connection_survives() -> None:
    con = _duckdb().connect()
    started = time.perf_counter()
    with pytest.raises(TypedFault) as info:
        dl.run(con, lambda: con.execute(_RANGE).fetchall(), 0.3, lambda: None)
    assert info.value.status == 504 and time.perf_counter() - started < 10
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_real_duckdb_early_interrupt_is_resent() -> None:
    con = _duckdb().connect()
    started = time.perf_counter()
    with pytest.raises(TypedFault):
        dl.run(con, lambda: con.execute(_RANGE).fetchall(), 0.0001, lambda: None)
    assert time.perf_counter() - started < 2
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_real_duckdb_spent_deadline_never_runs_the_query() -> None:
    con = _duckdb().connect()
    with pytest.raises(TypedFault):
        dl.run(con, lambda: pytest.fail("the query must not run"), 0, lambda: None)
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_real_duckdb_accepts_the_telemetry_slot_settings() -> None:
    con = _duckdb().connect()
    con.execute("LOAD postgres")
    con.execute(f"SET temp_directory='{ev.TELEMETRY_TEMP_DIRECTORY}'")
    con.execute(f"SET max_temp_directory_size='{ev.TELEMETRY_MAX_TEMP_SIZE}'")
    assert con.execute("SELECT current_setting('temp_directory')").fetchone() == (ev.TELEMETRY_TEMP_DIRECTORY,)
    assert con.execute("SELECT current_setting('max_temp_directory_size')").fetchone() is not None


def test_real_duckdb_spills_to_the_configured_directory(tmp_path: Path) -> None:
    con = _duckdb().connect()
    spill = tmp_path / "spill"
    con.execute("SET memory_limit='150MB'")
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("SET threads=4")
    rows = con.execute("SELECT count(*) FROM (SELECT i, md5(i::VARCHAR) h FROM range(10000000) t(i) ORDER BY h)").fetchone()
    assert rows == (10000000,)
    assert any(spill.iterdir())
