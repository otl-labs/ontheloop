"""The verification monitor over the golden chain's catalog (PLAN-telemetry-verification-monitor, VP step 5)."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import pytest

from scripts.telemetry_verify import completeness, monitor
from scripts.telemetry_verify.monitor_compare import STAMP_COLUMNS
from src.common import ducklake_event_reads as ev
from src.common.ducklake_reader_client import VERB_FIELDS, ReaderInvokeError, ResponseTooLargeError
from tests.fixtures import telemetry_golden_chain as g

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

QUIET_DAY = "2026-01-02"
AS_OF = datetime(2026, 1, 4, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
PER_SESSION = (*monitor.ROOT_VERBS, *monitor.CHILD_VERBS)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class World:
    """One catalog holding every golden tree under its own project, with a recording named() and describe()."""

    def __init__(self) -> None:
        self.con = g.open_catalog()
        self.projects = g.load_world(self.con)
        self.clock = Clock()
        self.cost = 0.0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.before: Callable[[str, dict[str, Any]], None] | None = None
        self.after: Callable[[str, dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None = None
        self.described: list[float] = []
        self.versions = ev.describe_all()
        self.describe_error: Exception | None = None

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((verb, params))
        if self.before is not None:
            self.before(verb, params)
        rows = g.run_verb(self.con, verb, **params)
        self.clock.now += self.cost
        return rows if self.after is None else self.after(verb, params, rows)

    def describe(self) -> dict[str, Any]:
        self.described.append(self.clock.now)
        if self.describe_error is not None and len(self.described) > 1:
            raise self.describe_error
        return copy.deepcopy(self.versions)

    def run(self, **kw: Any) -> monitor.MonitorReport:
        args: dict[str, Any] = {"quiet_day": QUIET_DAY, "as_of": AS_OF, "budget": monitor.Budget(2000, self.clock)}
        return monitor.run_monitor(self.named, self.describe, **{**args, **kw})

    def project(self, tree: str) -> str:
        return self.projects[tree][0]

    def grow(self, tree: str, at: datetime = LATER) -> None:
        stamp = at.isoformat()
        g.clone_row(
            self.con,
            "telemetry_observations",
            self.project(tree),
            {"event_id": f"grow-{tree}-{stamp}", "event_timestamp": at},
        )

    def calls_of(self, verb: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == verb]


@pytest.fixture()
def world() -> World:
    return World()


def test_monitor_reports_rederivation_orphan_and_completeness_counts(world: World) -> None:
    report = world.run()
    findings = report.findings()
    assert report.quiet == report.roots == len(world.projects) - 1 or report.quiet == report.roots
    assert report.trees_read == report.quiet and report.compared_trees + report.tree_collision == report.quiet
    assert all(agreeing == compared > 0 for agreeing, compared in report.agree.values())
    assert set(report.agree) == set(PER_SESSION)
    assert report.window_agree[0] == report.window_agree[1] > 0
    assert report.orphan_quiescent == {} or sum(report.orphan_quiescent.values()) == 0
    assert report.verb_skew == report.tree_moved == report.session_verb_failed == report.not_read_budget == 0
    assert not [c for c in findings if c.startswith(("rederivation", "orphan", "monitor_"))]
    assert set(findings) <= {
        "harness_key_missing",
        "checkout_sha_missing",
        "background_notice_unknown",
        "resume_without_prior_close",
    }
    for name in ("harness_key_missing", "background_notice_unknown", "resume_without_prior_close"):
        numerator, denominator = report.silent[name]
        assert denominator > 0 and numerator <= denominator
    roots = world.named("telemetry_root_sessions", day_from="2026-01-01", day_to="2026-01-01")
    listed = [
        completeness.Listed(
            completeness.normalise_key(r["harness_session_key"]), datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), "thread"
        )
        for r in roots
        if r["harness_session_key"]
    ]
    result = completeness.compare_sessions(roots, listed, judged_day="2026-01-01")
    assert result.listed_and_captured == len(listed) > 0
    assert result.unexplained_not_captured == 0 and result.captured_not_listed == 0


def _mutate(rows: list[dict[str, Any]], verb: str) -> list[dict[str, Any]]:
    if not rows:
        return [{"session_id": "none"}]
    column = next(c for c in VERB_FIELDS[verb] if c in rows[0] and c not in STAMP_COLUMNS and c != "session_id")
    return [{**rows[0], column: "planted"}, *rows[1:], copy.deepcopy(rows[0])]


@pytest.mark.parametrize("verb", [*PER_SESSION, "sessions_window"])
def test_monitor_reports_a_planted_verb_defect_by_verb(world: World, verb: str) -> None:
    world.after = lambda name, params, rows: _mutate(rows, verb) if name == verb else rows
    findings = world.run().findings()
    assert f"rederivation_mismatch:{verb}" in findings
    assert [c for c in findings if c.startswith("rederivation_mismatch") and not c.endswith(verb)] == []


def test_a_planted_column_defect_is_reported(world: World) -> None:
    def flip(name: str, params: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if name != "session_state_and_duration":
            return rows
        column = next(
            c for c in VERB_FIELDS[name] if c not in STAMP_COLUMNS and c != "session_id" and rows and rows[0][c] is not None
        )
        return [{**rows[0], column: "planted"}, *rows[1:]]

    world.after = flip
    assert "rederivation_mismatch:session_state_and_duration" in world.run().findings()


@pytest.mark.parametrize("when", ["before_read", "before_reread"])
def test_a_tree_that_grows_during_the_run_is_tree_moved(world: World, when: str) -> None:
    tree = "corpus:subagent_sync"
    root = _root_of(world, tree)
    done: list[bool] = []

    def grow(verb: str, params: dict[str, Any]) -> None:
        if done or verb != "session_tree_rows" or params["session_id"] != root:
            return
        if (params["max_rows"] == 1) == (when == "before_reread"):
            done.append(True)
            world.grow(tree)

    world.before = grow
    report = world.run()
    assert done and report.tree_moved == 1
    assert report.compared_trees == report.trees_read - 1 - report.tree_collision


def _root_of(world: World, tree: str) -> str:
    cursor = world.con.execute(
        f"SELECT session_id FROM {g.db.ALIAS}.telemetry_sessions WHERE project_id = ? AND parent_session_id IS NULL LIMIT 1",
        [world.project(tree)],
    )
    return cursor.fetchone()[0]


def test_only_trees_that_went_quiet_on_the_quiet_day_are_read(world: World) -> None:
    assert world.run(quiet_day="2026-01-03").quiet == 0
    world.calls.clear()
    for tree in ("corpus:subagent_sync", "corpus:resume_after_interrupt"):
        world.grow(tree, datetime(2026, 1, 3, 12, tzinfo=timezone.utc))
    full = world.run(quiet_day="2026-01-02")
    assert full.quiet == full.roots - 2 and full.trees_read == full.quiet
    later = world.run(quiet_day="2026-01-04")
    assert later.quiet == 2 and later.trees_read == 2


def test_budget_overflow_raises_the_coverage_alarm(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", 120)
    first = world.run()
    assert first.not_read_budget > 0 and first.findings()["monitor_coverage_incomplete"] >= first.not_read_budget
    assert first.reader_calls <= 120 and first.next_skip < first.quiet
    monkeypatch.setattr(monitor, "MAX_READER_CALLS", 800)
    second = world.run(skip_trees=first.next_skip)
    assert second.not_read_budget == 0
    assert first.compared_trees + second.compared_trees + first.tree_collision + second.tree_collision == first.quiet


def test_a_time_budget_stop_leaves_trees_unread(world: World) -> None:
    world.cost = 25.0
    report = world.run()
    assert report.not_read_budget > 0 and "monitor_coverage_incomplete" in report.findings()
    assert report.elapsed_s <= monitor.MONITOR_BUDGET_S and len(world.described) == 2


@pytest.mark.parametrize(
    "error", [ResponseTooLargeError("named_read", "big", {}), ReaderInvokeError("named_read", 503, "x", None)]
)
def test_a_failed_run_level_day_is_unread_and_alarmed(world: World, error: Exception) -> None:
    def fail(verb: str, params: dict[str, Any]) -> None:
        if verb == "telemetry_root_sessions" and params["day_from"] == "2025-12-20":
            raise error

    world.before = fail
    report = world.run()
    assert report.run_level_days_unread == 1 and report.findings()["monitor_coverage_incomplete"] >= 1
    assert report.trees_read == report.quiet > 0


def test_orphan_days_are_the_read_trees_days_plus_the_weekly_rotation(world: World) -> None:
    report = world.run()
    days = {p["day_from"] for p in world.calls_of("telemetry_orphan_counts")}
    assert days == {"2026-01-01", *monitor.rotation_days(date(2026, 1, 2))} and report.orphan_days == len(days)
    covered: set[str] = set()
    start = date(2026, 1, 2)
    for offset in range(7):
        day = start + timedelta(days=offset)
        covered |= set(monitor.rotation_days(day))
    window = {(start + timedelta(days=i)).isoformat() for i in range(-monitor.WINDOW_DAYS, 0)}
    assert window <= covered


@pytest.mark.parametrize("when", ["start", "between"])
def test_a_skewed_verb_is_reported_never_alarmed(world: World, when: str) -> None:
    verb = "session_friction_rollup"
    skewed = copy.deepcopy(world.versions)
    skewed[verb]["read_version"] = "other"
    world.after = lambda name, params, rows: _mutate(rows, verb) if name == verb else rows
    original = world.describe

    def describe() -> dict[str, Any]:
        out = original()
        if (when == "start") or len(world.described) > 1:
            out = copy.deepcopy(skewed)
        return out

    report = monitor.run_monitor(
        world.named, describe, quiet_day=QUIET_DAY, as_of=AS_OF, budget=monitor.Budget(2000, world.clock)
    )
    assert report.verb_skew == 1 and report.skewed == (verb,) and verb not in report.agree
    assert [c for c in report.findings() if c.startswith("rederivation")] == []
    assert report.findings().get("monitor_coverage_incomplete") is None


def test_every_call_leaves_the_closing_describe_its_worst_case(world: World) -> None:
    world.cost = 100.0
    starts: list[float] = []
    world.before = lambda verb, params: starts.append(world.clock.now)
    report = world.run()
    assert starts and all(at + 2 * monitor.CALL_WORST_CASE_S <= monitor.MONITOR_BUDGET_S for at in starts)
    assert len(world.described) == 2 and report.run_level_days_unread > 0
    assert world.described[1] + monitor.CALL_WORST_CASE_S <= monitor.MONITOR_BUDGET_S + monitor.CALL_WORST_CASE_S


def test_a_failed_tree_call_is_counted_never_ends_the_run(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = [
        r["session_id"] for r in g.run_verb(world.con, "telemetry_root_sessions", day_from="2026-01-01", day_to="2026-01-01")
    ]
    bad_5xx, bad_413, bad_verb = roots[0], roots[1], roots[2]

    def fail(verb: str, params: dict[str, Any]) -> None:
        if verb == "session_tree_rows" and params["session_id"] == bad_5xx:
            raise ReaderInvokeError("named_read", 503, "x", None)
        if verb == "session_tree_rows" and params["session_id"] == bad_413:
            raise ResponseTooLargeError("named_read", "big", {})
        if verb == "agent_run_token_rollup" and params["session_id"] != bad_verb and params["session_id"] not in roots:
            raise ReaderInvokeError("named_read", 502, "x", None)

    world.before = fail
    report = world.run()
    assert report.tree_read_failed == 2
    assert report.session_verb_failed > 0
    assert report.findings()["monitor_coverage_incomplete"] >= report.tree_read_failed + report.session_verb_failed
    assert report.compared_trees > 0
    quiet_order = [
        r["session_id"]
        for r in sorted(
            g.run_verb(world.con, "telemetry_root_sessions", day_from="2026-01-01", day_to="2026-01-01"),
            key=lambda r: (r["session_started_at"], r["session_id"]),
        )
    ]
    assert report.next_skip == min(quiet_order.index(bad_5xx), report.next_skip)


def test_an_oversized_tree_costs_no_page_call_past_the_cap(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "PAGE_ROWS", 5)
    monkeypatch.setattr(monitor, "MAX_TREE_CALLS", 20)
    report = world.run()
    assert report.tree_too_large > 0 and report.compared_trees > 0
    assert report.findings()["monitor_coverage_incomplete"] >= report.tree_too_large
    per_tree: dict[str, int] = {}
    for verb, params in world.calls:
        if verb == "session_tree_rows":
            per_tree[params["session_id"]] = per_tree.get(params["session_id"], 0) + 1
    assert max(per_tree.values()) <= monitor.MAX_TREE_CALLS


def test_a_failed_closing_describe_ends_the_run_incomplete(world: World) -> None:
    world.describe_error = ReaderInvokeError("describe", 500, "secret-host", None)
    with pytest.raises(monitor.MonitorIncomplete) as caught:
        world.run()
    assert "secret-host" not in str(caught.value)


def test_a_retired_generation_row_never_reads_as_moved(world: World) -> None:
    tree = "corpus:subagent_sync"
    g.clone_row(
        world.con,
        "telemetry_sessions",
        world.project(tree),
        {"event_id": "old-generation", "parser_version": 3, "event_timestamp": datetime(2026, 1, 1, 12, tzinfo=timezone.utc)},
    )
    report = world.run()
    assert report.tree_moved == 0 and report.compared_trees == report.trees_read - report.tree_collision


def test_a_run_that_compares_no_quiet_tree_raises_the_coverage_alarm(world: World) -> None:
    seen: set[str] = set()

    def grow(verb: str, params: dict[str, Any]) -> None:
        if (
            verb == "session_tree_rows"
            and params["offset"] == 0
            and params["max_rows"] > 1
            and params["session_id"] not in seen
        ):
            seen.add(params["session_id"])
            row = world.con.execute(
                f"SELECT project_id FROM {g.db.ALIAS}.telemetry_sessions WHERE session_id = ? LIMIT 1", [params["session_id"]]
            ).fetchone()
            tree = next(name for name, (pid, _) in world.projects.items() if pid == row[0])
            world.grow(tree)

    world.before = grow
    report = world.run()
    assert report.compared_trees == 0 and report.tree_moved == report.quiet > 0
    assert report.findings()["monitor_coverage_incomplete"] == 1


def test_a_tree_that_grows_between_pages_is_tree_moved(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "PAGE_ROWS", 5)
    monkeypatch.setattr(monitor, "MAX_TREE_CALLS", 200)
    tree = "corpus:subagent_sync"
    root = _root_of(world, tree)
    done: list[bool] = []

    def grow(verb: str, params: dict[str, Any]) -> None:
        if not done and verb == "session_tree_rows" and params["session_id"] == root and params["offset"] > 0:
            done.append(True)
            world.grow(tree)

    world.before = grow
    report = world.run()
    assert done and report.tree_moved == 1


def test_a_tree_past_the_call_cap_during_its_verbs_is_tree_too_large(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "_VERB_CALLS_PER_TREE", 0)
    monkeypatch.setattr(monitor, "MAX_TREE_CALLS", 4)
    report = world.run()
    assert report.tree_too_large == report.quiet - report.tree_collision > 0 and report.compared_trees == 0


def test_a_wider_run_level_call_falls_back_to_one_day_calls(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "RUN_LEVEL_WINDOW_DAYS", 7)

    def fail(verb: str, params: dict[str, Any]) -> None:
        wide = params.get("day_from") != params.get("day_to")
        if verb == "telemetry_root_sessions" and (wide or params["day_from"] == "2025-12-20"):
            raise ReaderInvokeError("named_read", 500, "x", None)

    world.before = fail
    report = world.run()
    assert report.run_level_days_unread == 1 and report.trees_read == report.quiet > 0
    assert len(world.calls_of("telemetry_root_sessions")) == 4 + monitor.WINDOW_DAYS


def test_a_tree_without_rows_and_a_collided_tree_are_counted(world: World) -> None:
    roots = {
        r["session_id"] for r in g.run_verb(world.con, "telemetry_root_sessions", day_from="2026-01-01", day_to="2026-01-01")
    }
    first, second = sorted(roots)[:2]

    def shape(verb: str, params: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if verb != "session_tree_rows":
            return rows
        if params["session_id"] == first:
            return []
        if params["session_id"] == second:
            return [{**row, "roots_in_tree": 2} for row in rows]
        return rows

    world.after = shape
    report = world.run()
    assert report.tree_read_failed == 1 and report.tree_collision == 1
    assert report.compared_trees == report.quiet - 2


def test_as_of_is_sent_at_millisecond_precision(world: World) -> None:
    world.run(as_of=AS_OF.replace(microsecond=123456))
    sent = {params["as_of"] for verb, params in world.calls if "as_of" in params}
    assert sent == {"2026-01-04T00:00:00.123000+00:00"}


@pytest.mark.parametrize(
    "verb", ["session_tree_rows", "telemetry_root_sessions", "sessions_window", "telemetry_orphan_counts"]
)
def test_a_skewed_structural_verb_skips_what_depends_on_it(world: World, verb: str) -> None:
    world.versions[verb]["read_version"] = "checkout"
    deployed = copy.deepcopy(world.versions)
    deployed[verb]["read_version"] = "deployed"
    report = monitor.run_monitor(
        world.named,
        lambda: copy.deepcopy(deployed),
        quiet_day=QUIET_DAY,
        as_of=AS_OF,
        budget=monitor.Budget(2000, world.clock),
    )
    assert report.skewed == (verb,)
    if verb in ("session_tree_rows", "telemetry_root_sessions"):
        assert report.agree == {} and report.window_agree == (0, 0) and report.orphan_child_rows == {}
        assert report.findings().get("monitor_coverage_incomplete") is None
    elif verb == "sessions_window":
        assert report.window_agree == (0, 0) and report.agree
    else:
        assert report.orphan_child_rows == {} and report.window_agree[1] > 0
