"""python -m scripts.telemetry_verify monitor|completeness (PLAN-telemetry-verification-monitor).

Each command prints one line of counts, class names, verb names and join ids (Decision 101: the output lands in public Actions
logs, so never a row value, session id, ref, branch, error body or URL host). Exit 0 when the run completed (whatever the
findings), 1 when it did not complete or an alarm filing failed (Decision 84 I-4); with --assert-zero, 0 only after a
completed run whose class denominator is above zero and whose class count is zero, else 2. All logging is disabled: the
reader client's and the writer transport's warnings can carry an ARN or a Function URL host.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.rec_episode import find_recs
from scripts.telemetry_capture import read_route
from scripts.telemetry_verify import completeness, monitor
from scripts.telemetry_verify.alarms import ALARM_SOURCE, raise_alarms
from scripts.telemetry_verify.silent_checks import REPORT_ONLY_CHECKS

DEFAULT_LISTS_DIR = "/mnt/project-files/telemetry-slice4/completeness/latest"
MONITOR_FILE = "scripts/telemetry_verify/monitor.py"
COMPLETENESS_FILE = "scripts/telemetry_verify/completeness.py"
_COMPLETENESS_CLASS = "completeness_listed_not_captured"
_MONITOR_DEFAULTS = "AWS_PROFILE=agent_platform bin/venv-python -m scripts.telemetry_verify monitor"
_COMPLETENESS_DEFAULTS = "AWS_PROFILE=agent_platform bin/venv-python -m scripts.telemetry_verify completeness"


def acceptance_for(cls: str) -> str:
    base = _COMPLETENESS_DEFAULTS if cls == _COMPLETENESS_CLASS else _MONITOR_DEFAULTS
    return f"{base} --no-alarms --assert-zero {cls}"


def _day(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from None


def _skip(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return int(text)


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(prog="python -m scripts.telemetry_verify")
    sub = built.add_subparsers(dest="command", required=True)
    mon = sub.add_parser("monitor")
    mon.add_argument("--quiet-day", type=_day, default=None)
    mon.add_argument("--skip-trees", type=_skip, default=0)
    comp = sub.add_parser("completeness")
    comp.add_argument("--lists-dir", default=DEFAULT_LISTS_DIR)
    comp.add_argument("--judged-day", type=_day, default=None)
    for each in (mon, comp):
        each.add_argument("--no-alarms", action="store_true")
        each.add_argument("--assert-zero", default=None, metavar="CLASS")
    return built


def _fmt(pairs: Mapping[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in pairs.items())


def _ratios(items: Mapping[str, Any]) -> str:
    return ",".join(f"{k}:{a}/{b}" for k, (a, b) in items.items()) or "none"


def _alarms(findings: Mapping[str, int]) -> str:
    return ",".join(sorted(c for c in findings if c not in REPORT_ONLY_CHECKS)) or "none"


def monitor_line(report: monitor.MonitorReport, findings: Mapping[str, int]) -> str:
    agree = {verb: report.agree[verb] for verb in (*monitor.ROOT_VERBS, *monitor.CHILD_VERBS) if verb in report.agree}
    pairs: dict[str, Any] = {
        "quiet_day": report.quiet_day,
        "window": f"{report.window[0]}..{report.window[1]}",
        "roots": report.roots,
        "quiet": report.quiet,
        "skip_trees": report.skip_trees,
        "trees_read": report.trees_read,
        "tree_moved": report.tree_moved,
        "tree_collision": report.tree_collision,
        "tree_read_failed": report.tree_read_failed,
        "not_read_budget": report.not_read_budget,
        "next_skip": report.next_skip,
        "run_level_days_unread": report.run_level_days_unread,
        "day_level_unread": report.day_level_unread,
        "tree_too_large": report.tree_too_large,
        "sessions_not_compared": report.sessions_not_compared,
        "session_verb_failed": report.session_verb_failed,
        "verb_skew": report.verb_skew,
        "agree": _ratios(agree),
        "window_agree": f"{report.window_agree[0]}/{report.window_agree[1]}",
        "orphan_days": report.orphan_days,
        "orphan_quiescent": f"{sum(report.orphan_quiescent.values())}/{sum(report.orphan_child_rows.values())}",
        "orphan_pending": report.orphan_pending,
        "orphan_undercount": f"{sum(report.orphan_undercount.values())}/{sum(report.orphan_independent_rows.values())}",
        "rootless_trees": report.rootless_trees,
        "conflicted_keys": report.conflicted_keys,
        "silent": _ratios(report.silent),
        "reader_calls": report.reader_calls,
        "run_level_s": report.run_level_s,
        "elapsed_s": report.elapsed_s,
        "max_call_s": report.max_call_s,
        "max_response_bytes": report.max_response_bytes,
        "alarms": _alarms(findings),
    }
    return "MONITOR " + _fmt(pairs)


def completeness_line(result: completeness.CompletenessResult, status: str, findings: Mapping[str, int]) -> str:
    return "COMPLETENESS " + _fmt(
        {
            "judged_day": result.judged_day,
            "status": status,
            "listed": result.listed,
            "listed_and_captured": result.listed_and_captured,
            "listed_not_captured": result.listed_not_captured,
            "unexplained_not_captured": result.unexplained_not_captured,
            "captured_not_listed": result.captured_not_listed,
            "listed_unjoinable": result.listed_unjoinable,
            "listed_scope_unknown": result.listed_scope_unknown,
            "captured_without_key": result.captured_without_key,
            "alarms": _alarms(findings),
        }
    )


def _file(
    findings: Mapping[str, int],
    *,
    scope: str,
    now: datetime,
    portal: Callable[..., Any] | None,
    file: str,
    reader: Any,
) -> bool:
    """File or update one rec per positive class; a failure prints its class and HTTP status only and returns False."""
    try:
        rows = find_recs(ALARM_SOURCE, reader=reader)
        raise_alarms(
            dict(findings),
            scope=scope,
            alarm_at=None,
            now=now,
            portal=portal,
            file=file,
            acceptance_for=acceptance_for,
            declared=REPORT_ONLY_CHECKS,
            rows=rows,
            reader=reader,
        )
    except Exception as exc:  # noqa: BLE001 -- never the message (a reader body or a URL host)
        status = getattr(exc, "status", None)
        print(f"ALARMS_FAILED class={type(exc).__name__} status={status if isinstance(status, int) else 'none'}")
        return False
    return True


def _verdict(
    args: argparse.Namespace, completed: bool, findings: Mapping[str, int], denominators: Mapping[str, int]
) -> int | None:
    if args.assert_zero is None:
        return None
    ok = completed and denominators.get(args.assert_zero, 0) > 0 and findings.get(args.assert_zero, 0) == 0
    return 0 if ok else 2


def run_monitor_command(
    args: argparse.Namespace,
    *,
    reader: Any,
    now: datetime,
    budget: monitor.Budget | None = None,
    portal: Callable[..., Any] | None = None,
) -> int:
    today = now.date()
    quiet_day = args.quiet_day or (today - timedelta(days=1)).isoformat()
    if not today - timedelta(days=monitor.WINDOW_DAYS) <= date.fromisoformat(quiet_day) < today:
        print("MONITOR_REFUSED reason=quiet_day_outside_window")
        return 2 if args.assert_zero else 1
    try:
        # The routed reader resolves its catalog here, so an unresolved one stops by its own class: run_monitor reports a
        # failing describe as MonitorIncomplete.
        getattr(reader, "catalog", None)
        report = monitor.run_monitor(
            reader.named,
            reader.describe,
            quiet_day=quiet_day,
            as_of=now,
            budget=budget or monitor.Budget(),
            skip_trees=args.skip_trees,
        )
    except Exception as exc:  # noqa: BLE001 -- the class alone is printed
        print(f"MONITOR_STOP class={type(exc).__name__}")
        return 2 if args.assert_zero else 1
    findings = report.findings()
    print(monitor_line(report, findings))
    verdict = _verdict(args, True, findings, report.denominators())
    if verdict is not None:
        return verdict
    if args.no_alarms:
        return 0
    scope = f"{report.window[0]}..{report.window[1]}"
    return 0 if _file(findings, scope=scope, now=now, portal=portal, file=MONITOR_FILE, reader=reader) else 1


def run_completeness_command(
    args: argparse.Namespace, *, reader: Any, now: datetime, portal: Callable[..., Any] | None = None
) -> int:
    judged = args.judged_day or (now.date() - timedelta(days=2)).isoformat()
    empty = completeness.CompletenessResult(judged)
    try:
        catalog = getattr(reader, "catalog", None) or read_route.resolve_catalog()
        first = completeness.first_judged_day(catalog)
        if first is None or judged < first:
            print(completeness_line(empty, "before_v4" if catalog == read_route.DEFAULT_CATALOG else "before_catalog", {}))
            return 2
        lists = completeness.load_lists(Path(args.lists_dir))
        if not completeness.lists_cover(lists, judged):
            print(completeness_line(empty, "lists_short", {}))
            return 2 if args.assert_zero else 1
        center = date.fromisoformat(judged)
        roots = reader.named(
            "telemetry_root_sessions",
            day_from=(center - timedelta(days=1)).isoformat(),
            day_to=(center + timedelta(days=1)).isoformat(),
        )
        result = completeness.compare_sessions(roots, lists.rows, judged_day=judged)
    except Exception as exc:  # noqa: BLE001 -- the class alone is printed
        print(f"COMPLETENESS_STOP class={type(exc).__name__}")
        return 2 if args.assert_zero else 1
    findings = result.findings()
    print(completeness_line(result, "completed", findings))
    verdict = _verdict(args, True, findings, result.denominators())
    if verdict is not None:
        return verdict
    if args.no_alarms:
        return 0
    return 0 if _file(findings, scope=judged, now=now, portal=portal, file=COMPLETENESS_FILE, reader=reader) else 1


def main(
    argv: list[str] | None = None,
    *,
    reader: Any = None,
    now: datetime | None = None,
    budget: monitor.Budget | None = None,
    portal: Callable[..., Any] | None = None,
) -> int:
    args = parser().parse_args(argv)
    logging.disable(logging.CRITICAL)
    when = now or datetime.now(timezone.utc)
    if reader is None:
        reader = read_route.make_routed_reader()
    if args.command == "monitor":
        return run_monitor_command(args, reader=reader, now=when, budget=budget, portal=portal)
    return run_completeness_command(args, reader=reader, now=when, portal=portal)


if __name__ == "__main__":
    sys.exit(main())
