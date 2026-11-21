"""PLAN-monitor-read-budget (rec-5385, rec-5420): the monitor's capacity helpers, gate and golden-world behaviour."""

from __future__ import annotations

import hashlib
import threading
from typing import Any

import pytest

from scripts.telemetry_verify import monitor
from scripts.telemetry_verify import monitor_capacity as cap
from src.turn_capture import refs


def _roots(*specs: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"session_id": sid, "entity_ref": f"litellm:{sid}" if lit else f"claude:{sid}"} for sid, lit in specs]


def test_read_order_puts_non_litellm_roots_first_in_given_order() -> None:
    quiet = _roots(("b", True), ("z", False), ("a", False), ("c", True))
    assert [r["session_id"] for r in cap.read_order(quiet)][:2] == ["z", "a"]
    assert all(cap.is_litellm(r) for r in cap.read_order(quiet)[2:])


def test_litellm_roots_follow_in_a_stable_hash_order() -> None:
    quiet = _roots(*((f"s{i}", True) for i in range(8)))
    ordered = [r["session_id"] for r in cap.read_order(quiet)]
    assert ordered == sorted(ordered, key=lambda s: hashlib.sha256(s.encode()).hexdigest())
    assert ordered == [r["session_id"] for r in cap.read_order(list(reversed(quiet)))]


def test_floor_met_excuses_unread_litellm_trees() -> None:
    statuses = ["compared"] * 3 + ["budget"] * 2
    flags = [False, True, True, True, True]
    assert cap.sampled_out(statuses, flags, 2) == 2
    assert cap.sampled_out(statuses, flags, 0) == 2
    assert cap.litellm_read(statuses, flags) == 2


def test_floor_missed_or_non_litellm_unread_excuses_nothing() -> None:
    flags = [False, True, True]
    assert cap.sampled_out(["compared", "compared", "budget"], flags, 2) == 0
    assert cap.sampled_out(["budget", "compared", "budget"], flags, 0) == 0
    assert cap.sampled_out(["compared", "moved", "budget"], flags, 1) == 0
    assert cap.sampled_out(["compared", "collision", "budget"], flags, 1) == 0


class _Clock:
    def __init__(self, step: float) -> None:
        self._lock, self.now, self.step = threading.Lock(), 0.0, step

    def __call__(self) -> float:
        with self._lock:
            value = self.now
            self.now += self.step
            return value


def _two_callers(gate: monitor._Gate) -> list[bool]:
    barrier, got = threading.Barrier(2), []

    def attempt() -> None:
        barrier.wait()
        try:
            gate("v")
            got.append(True)
        except monitor._Refused:
            got.append(False)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return got


def test_gate_holds_cap_and_time_rule_across_two_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as real_time

    reached: list[int] = []

    def named(verb: str, **params: Any) -> list[dict[str, Any]]:
        reached.append(1)
        return []

    monkeypatch.setattr(monitor, "MAX_READER_CALLS", 2)

    def slow_clock() -> float:
        real_time.sleep(0.05)
        return 0.0

    got = _two_callers(monitor._Gate(named, monitor.Budget(2000, slow_clock)))
    assert sorted(got) == [False, True] and len(reached) == 1

    reached.clear()
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", 10**6)
    total = 2 * monitor.CALL_WORST_CASE_S + 1.5
    got = _two_callers(monitor._Gate(named, monitor.Budget(total, _Clock(1.0))))
    assert sorted(got) == [False, True] and len(reached) == 1


def test_call_stats_count_slow_calls_and_p95() -> None:
    stats = cap.CallStats()
    assert (stats.calls_over_10s, stats.call_p95_s) == (0, 0)
    for s in [1.0] * 18 + [10.0, 10.4]:
        stats.record(s)
    assert stats.calls_over_10s == 1
    assert stats.call_p95_s == 10
    stats.record(11.2)
    assert stats.call_p95_s == 11


def test_litellm_prefix_matches_the_producer_root_ref() -> None:
    assert refs.litellm_root_ref("x").startswith(cap.LITELLM_ROOT_PREFIX)
    assert cap.LITELLM_ROOT_PREFIX == "litellm:"


def test_two_workers_report_equals_one_worker() -> None:
    pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
    from tests.telemetry_verify import test_monitor as tm

    skip = {"run_level_s", "elapsed_s", "max_call_s", "calls_over_10s", "call_p95_s", "workers"}
    one = _locked_world().run(workers=1)
    two = _locked_world().run(workers=2)
    assert {k: v for k, v in vars(one).items() if k not in skip} == {k: v for k, v in vars(two).items() if k not in skip}
    assert two.workers == 2 and one.workers == 1
    for workers in (1, 2):
        world = _locked_world()
        world.cost = 11.0
        r = world.run(workers=workers, budget=monitor.Budget(10**6, world.clock))
        assert r.workers == workers and r.calls_over_10s == r.reader_calls - 2
        assert (r.call_p95_s == 11) if workers == 1 else (r.call_p95_s >= 11)
    assert tm  # module imported for World


def _locked_world() -> Any:
    from tests.telemetry_verify import test_monitor as tm

    world = tm.World()
    lock = threading.RLock()
    inner, inner_describe = world.named, world.describe

    def named(verb: str, **params: Any) -> list[dict[str, Any]]:
        with lock:
            return inner(verb, **params)

    def describe() -> dict[str, Any]:
        with lock:
            return inner_describe()

    world.named, world.describe = named, describe  # type: ignore[method-assign]
    return world


def _litellm_world(monkeypatch: pytest.MonkeyPatch, floor: int) -> Any:
    from types import SimpleNamespace

    from tests.fixtures import telemetry_golden_chain as g
    from tests.fixtures.telemetry_litellm_tree import litellm_batches

    world = _locked_world()
    project = g.register_project(world.con, len(world.projects) + 1)
    g.append_batches(world.con, SimpleNamespace(batches=litellm_batches(ended=False)), project=project)
    monkeypatch.setattr(cap, "LITELLM_FLOOR", floor)
    return world


def _refuse_last_litellm(monkeypatch: pytest.MonkeyPatch, floor: int) -> monitor.MonitorReport:
    full = _litellm_world(monkeypatch, floor).run()
    assert full.litellm_quiet == 1 and full.litellm_read == 1
    baseline = _locked_world().run()
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", baseline.reader_calls)
    return _litellm_world(monkeypatch, floor).run()


def test_floor_met_excuses_litellm_without_coverage_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
    r = _refuse_last_litellm(monkeypatch, 0)
    assert r.litellm_sampled_out == 1 and r.not_read_budget == 0
    assert "monitor_coverage_incomplete" not in r.findings()


def test_floor_missed_raises_coverage_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
    r = _refuse_last_litellm(monkeypatch, 1)
    assert r.litellm_sampled_out == 0 and r.not_read_budget == 1
    assert r.findings()["monitor_coverage_incomplete"] == 1


def test_oversize_day_counts_sum_and_backfill_reads_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
    world = _locked_world()
    full = world.run()
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", max(10, full.reader_calls // 2))
    r = _locked_world().run()
    total = r.trees_read + r.tree_too_large + r.tree_read_failed + r.not_read_budget + r.litellm_sampled_out
    assert total == r.quiet - r.skip_trees
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", 3000)
    rest = _locked_world().run(skip_trees=r.next_skip)
    assert rest.trees_read + rest.tree_too_large + rest.tree_read_failed + rest.not_read_budget == r.quiet - r.next_skip
