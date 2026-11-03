"""The monitor and completeness commands: pinned grammar, exit contract, counts only (VP step 7)."""

from __future__ import annotations

import json
import logging
import re
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.telemetry_capture import read_route
from scripts.telemetry_verify import __main__ as cli
from scripts.telemetry_verify import completeness, monitor
from src.common.ducklake_reader_client import ReaderInvokeError

NOW = datetime(2026, 1, 20, 8, tzinfo=timezone.utc)
MONITOR_LINE = re.compile(
    r"MONITOR quiet_day=\d{4}-\d\d-\d\d window=\d{4}-\d\d-\d\d\.\.\d{4}-\d\d-\d\d roots=\d+ quiet=\d+ "
    r"skip_trees=\d+ trees_read=\d+ "
    r"tree_moved=\d+ tree_collision=\d+ tree_read_failed=\d+ not_read_budget=\d+ next_skip=\d+ run_level_days_unread=\d+ "
    r"day_level_unread=\d+ tree_too_large=\d+ sessions_not_compared=\d+ session_verb_failed=\d+ verb_skew=\d+ agree=\S+ "
    r"window_agree=\d+/\d+ orphan_days=\d+ orphan_quiescent=\d+/\d+ orphan_pending=\d+ orphan_undercount=\d+/\d+ "
    r"rootless_trees=\d+ conflicted_keys=\d+ silent=\S+ reader_calls=\d+ run_level_s=\d+ elapsed_s=\d+ max_call_s=\d+ "
    r"max_response_bytes=\d+ alarms=\S+"
)
COMPLETENESS_LINE = re.compile(
    r"COMPLETENESS judged_day=\d{4}-\d\d-\d\d status=(completed|before_v4|before_catalog|lists_short) listed=\d+ "
    r"listed_and_captured=\d+ listed_not_captured=\d+ unexplained_not_captured=\d+ captured_not_listed=\d+ "
    r"listed_unjoinable=\d+ listed_scope_unknown=\d+ captured_without_key=\d+ alarms=\S+"
)


@pytest.fixture(autouse=True)
def _early_v4_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(completeness, "V4_FIRST_JUDGED_DAY", "2026-01-01")
    monkeypatch.setenv(read_route.CATALOG_ENV, read_route.DEFAULT_CATALOG)


PLANTED = "PLANTED-body-7f3a https://planted-host.lambda-url.eu-west-2.on.aws"


class FakeReader:
    def __init__(self, roots: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.roots, self.error = roots or [], error

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        return self.roots

    def describe(self) -> dict[str, Any]:
        return {}

    def current_state(self, table: str, **kw: Any) -> list[dict[str, Any]]:
        return []


class Portal:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    def __call__(self, action: str, fields: dict[str, Any]) -> str:
        if self.error is not None:
            raise self.error
        self.calls.append((action, fields))
        return "rec-9000"


def _report(**kw: Any) -> monitor.MonitorReport:
    base: dict[str, Any] = {
        "quiet_day": "2026-01-19",
        "window": ("2025-12-22", "2026-01-18"),
        "roots": 5,
        "quiet": 3,
        "trees_read": 3,
        "compared_trees": 3,
        "agree": {"session_state_and_duration": [3, 3], "agent_run_token_rollup": [2, 2]},
        "window_agree": (3, 3),
        "orphan_days": 5,
        "orphan_child_rows": {"j1": 40},
        "orphan_independent_rows": {"j1": 12},
        "silent": {"harness_key_missing": (0, 3), "checkout_sha_missing": (2, 4)},
        "reader_calls": 90,
    }
    return monitor.MonitorReport(**{**base, **kw})


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], report: Any, **kw: Any) -> int:
    def fake(*a: Any, **k: Any) -> monitor.MonitorReport:
        if isinstance(report, Exception):
            raise report
        return report

    monkeypatch.setattr(cli.monitor, "run_monitor", fake)
    return cli.main(argv, reader=kw.pop("reader", FakeReader()), now=NOW, **kw)


def test_monitor_command_prints_the_pinned_grammar_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    portal = Portal()
    assert _run(monkeypatch, ["monitor"], _report(orphan_quiescent={"j1": 2}), portal=portal) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and MONITOR_LINE.fullmatch(lines[0])
    assert "alarms=orphan_quiescent:j1" in lines[0] and "silent=harness_key_missing:0/3,checkout_sha_missing:2/4" in lines[0]
    assert [c[0] for c in portal.calls] == ["file"] and portal.calls[0][1]["source"] == "telemetry_verify"
    assert "checkout_sha_missing" not in json.dumps(portal.calls)
    assert _run(monkeypatch, ["monitor", "--no-alarms"], _report(), portal=Portal()) == 0
    assert "alarms=none" in capsys.readouterr().out and not portal.calls[1:]


def test_completeness_command_prints_the_pinned_grammar_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / completeness.THREAD_FILE).write_text(
        json.dumps([{"sessions": [{"session_id": "cse_AAA", "created_at": "2026-01-18T10:00:00Z"}]}]), encoding="utf-8"
    )
    (tmp_path / completeness.SESSIONS_FILE).write_text(
        json.dumps([{"ccr": {"data": [], "has_more": False}}]), encoding="utf-8"
    )
    roots = [{"harness_session_key": "session_AAA", "session_started_at": "2026-01-18T09:59:00+00:00"}]
    portal = Portal()
    argv = ["completeness", "--lists-dir", str(tmp_path), "--judged-day", "2026-01-18"]
    assert cli.main(argv, reader=FakeReader(roots), now=NOW, portal=portal) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert (
        len(out) == 1 and COMPLETENESS_LINE.fullmatch(out[0]) and "status=completed listed=1 listed_and_captured=1" in out[0]
    )
    assert cli.main(argv, reader=FakeReader([]), now=NOW, portal=portal) == 0
    assert "unexplained_not_captured=1" in capsys.readouterr().out and [c[0] for c in portal.calls] == ["file"]
    assert cli.main([*argv, "--assert-zero", "completeness_listed_not_captured"], reader=FakeReader([]), now=NOW) == 2


def test_a_reader_error_prints_class_and_status_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logging.disable(logging.NOTSET)
    logging.getLogger("planted").warning(PLANTED)
    failing = ReaderInvokeError("named_read", 502, PLANTED, {"error": PLANTED})
    assert _run(monkeypatch, ["monitor"], failing) == 1
    out = capsys.readouterr()
    assert out.out.strip() == "MONITOR_STOP class=ReaderInvokeError" and "PLANTED" not in out.err + out.out
    portal = Portal(ReaderInvokeError("file", 503, PLANTED, None))
    assert _run(monkeypatch, ["monitor"], _report(orphan_quiescent={"j1": 1}), portal=portal) == 1
    printed = capsys.readouterr().out
    assert "ALARMS_FAILED class=ReaderInvokeError status=503" in printed and "PLANTED" not in printed
    refused = Portal(ConnectionError(PLANTED))
    assert _run(monkeypatch, ["monitor"], _report(orphan_quiescent={"j1": 1}), portal=refused) == 1
    assert "ALARMS_FAILED class=ConnectionError status=none" in capsys.readouterr().out
    lists_error = cli.main(
        ["completeness", "--lists-dir", "/nonexistent", "--judged-day", "2026-01-18"], reader=FakeReader(), now=NOW
    )
    assert lists_error == 1 and "PLANTED" not in capsys.readouterr().out
    logging.disable(logging.CRITICAL)


def test_assert_zero_needs_a_completed_run_and_a_denominator(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _report()
    cls = "rederivation_mismatch:session_state_and_duration"
    assert _run(monkeypatch, ["monitor", "--assert-zero", cls], clean) == 0
    assert _run(monkeypatch, ["monitor", "--assert-zero", "rederivation_mismatch:agent_run_state_and_duration"], clean) == 2
    assert _run(monkeypatch, ["monitor", "--assert-zero", cls], _report(agree={"session_state_and_duration": [2, 3]})) == 2
    assert _run(monkeypatch, ["monitor", "--assert-zero", "orphan_undercount:j1"], clean) == 0
    assert _run(monkeypatch, ["monitor", "--assert-zero", "orphan_quiescent:j1"], _report(orphan_quiescent={"j1": 1})) == 2
    assert (
        _run(monkeypatch, ["monitor", "--assert-zero", "harness_key_missing"], _report(silent={"harness_key_missing": (0, 0)}))
        == 2
    )
    assert _run(monkeypatch, ["monitor", "--assert-zero", "monitor_coverage_incomplete"], clean) == 0
    assert _run(monkeypatch, ["monitor", "--assert-zero", "monitor_coverage_incomplete"], _report(quiet=0)) == 2
    assert _run(monkeypatch, ["monitor", "--assert-zero", cls], monitor.MonitorIncomplete("x")) == 2
    capsys.readouterr()


def test_acceptances_lint_as_decidable() -> None:
    classes = [
        "rederivation_mismatch:session_state_and_duration",
        "orphan_quiescent:parent_observation_id_join_observations_self_tree",
        "monitor_coverage_incomplete",
        "harness_key_missing",
        "completeness_listed_not_captured",
    ]
    for cls in classes:
        command = cli.acceptance_for(cls)
        assert lint_acceptance_command(command, require_decidable=True) == (True, None)
        assert "--no-alarms" in command and f"--assert-zero {cls}" in command and not re.search(r"\d{4}-\d\d-\d\d", command)
    assert "completeness" in cli.acceptance_for("completeness_listed_not_captured")


def test_a_failed_filing_exits_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(monkeypatch, ["monitor"], _report(orphan_quiescent={"j1": 1}), portal=Portal(RuntimeError("x"))) == 1
    assert "ALARMS_FAILED class=RuntimeError" in capsys.readouterr().out


def test_arguments_are_validated(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(monkeypatch, ["monitor", "--quiet-day", "2025-11-01"], _report()) == 1
    assert _run(monkeypatch, ["monitor", "--quiet-day", "2026-01-20"], _report()) == 1
    assert "MONITOR_REFUSED" in capsys.readouterr().out
    for bad in (
        ["monitor", "--skip-trees", "-1"],
        ["monitor", "--quiet-day", "yesterday"],
        ["completeness", "--judged-day", "x"],
    ):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(bad)
    assert _run(monkeypatch, ["monitor", "--quiet-day", "2026-01-19", "--skip-trees", "2"], _report(skip_trees=2)) == 0
    before = cli.main(["completeness", "--judged-day", "2025-01-01"], reader=FakeReader(), now=NOW)
    out = capsys.readouterr().out
    assert before == 2 and "status=before_v4" in out and COMPLETENESS_LINE.search(out)


def test_a_short_list_is_refused_and_the_entry_point_builds_its_own_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / completeness.THREAD_FILE).write_text(json.dumps([{"sessions": [], "next_cursor": None}]), encoding="utf-8")
    newest = {"id": "session_X", "created_at": "2026-01-19T00:00:00Z"}
    (tmp_path / completeness.SESSIONS_FILE).write_text(
        json.dumps([{"ccr": {"data": [newest], "has_more": True}}]), encoding="utf-8"
    )
    argv = ["completeness", "--lists-dir", str(tmp_path), "--judged-day", "2026-01-18"]
    assert cli.main(argv, reader=FakeReader(), now=NOW) == 1
    assert "status=lists_short" in capsys.readouterr().out
    assert cli.main([*argv, "--assert-zero", "completeness_listed_not_captured"], reader=FakeReader(), now=NOW) == 2
    capsys.readouterr()

    (tmp_path / completeness.SESSIONS_FILE).write_text(
        json.dumps([{"ccr": {"data": [], "has_more": False}}]), encoding="utf-8"
    )
    assert cli.main([*argv, "--no-alarms"], reader=FakeReader(), now=NOW) == 0
    capsys.readouterr()

    monkeypatch.setattr(read_route, "make_routed_reader", lambda *a, **k: FakeReader())
    assert cli.main([*argv, "--no-alarms"], now=NOW) == 0
    monkeypatch.setattr("sys.argv", ["telemetry_verify", *argv, "--no-alarms"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("scripts.telemetry_verify", run_name="__main__")
    assert exited.value.code in (0, 1)


def test_main_builds_the_routed_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[Any] = []
    monkeypatch.setattr(read_route, "make_routed_reader", lambda *a, **k: built.append((a, k)) or FakeReader())
    assert cli.main(["monitor", "--quiet-day", "2026-01-19", "--no-alarms"], now=NOW) == 0
    assert MONITOR_LINE.fullmatch(capsys.readouterr().out.strip())
    assert cli.main(["completeness", "--lists-dir", str(tmp_path), "--judged-day", "2026-01-18"], now=NOW) == 1
    assert capsys.readouterr().out.startswith("COMPLETENESS_STOP class=") and built == [((), {}), ((), {})]


class CatalogReader(FakeReader):
    catalog = "ducklake_telemetry"


def test_completeness_refuses_a_day_before_the_catalogs_first_judged_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["completeness", "--judged-day", "2026-01-10", "--no-alarms"]
    assert cli.main(argv, reader=CatalogReader(), now=NOW) == 2
    out = capsys.readouterr().out
    assert "status=before_catalog" in out and COMPLETENESS_LINE.fullmatch(out.strip())
    monkeypatch.setattr(completeness, "PRODUCTION_FIRST_JUDGED_DAY", "2026-01-11")
    assert cli.main(argv, reader=CatalogReader(), now=NOW) == 2 and "status=before_catalog" in capsys.readouterr().out
    monkeypatch.setattr(completeness, "PRODUCTION_FIRST_JUDGED_DAY", "2026-01-10")
    assert cli.main([*argv, "--lists-dir", str(tmp_path)], reader=CatalogReader(), now=NOW) == 1
    assert capsys.readouterr().out.startswith("COMPLETENESS_STOP class="), "the judged day passes the gate"
    monkeypatch.setenv(read_route.CATALOG_ENV, "ducklake_telemetry")
    monkeypatch.setattr(completeness, "PRODUCTION_FIRST_JUDGED_DAY", None)
    assert cli.main(argv, reader=FakeReader(), now=NOW) == 2 and "status=before_catalog" in capsys.readouterr().out


def test_an_unresolved_catalog_prints_a_class_only_stop_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(read_route.CATALOG_ENV, "ducklake_ops")
    monkeypatch.setattr(read_route, "make_routed_reader", lambda: read_route.RoutedReader(FakeReader()))
    stops = {
        ("monitor", "--quiet-day", "2026-01-19"): "MONITOR_STOP class=TelemetryCatalogUnresolved",
        ("completeness", "--judged-day", "2026-01-18"): "COMPLETENESS_STOP class=TelemetryCatalogUnresolved",
    }
    for argv, line in stops.items():
        assert cli.main([*argv, "--no-alarms"], now=NOW) == 1
        assert cli.main([*argv, "--assert-zero", "x"], now=NOW) == 2
        out = capsys.readouterr().out
        assert out == f"{line}\n{line}\n" and "ducklake_ops" not in out and "Traceback" not in out
