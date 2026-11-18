"""Agent CLI for production usage questions: per-model tokens, tool use and startup baselines over a date range.

    bin/venv-python -m scripts.telemetry_usage {models|tools|startup|trees} --from YYYY-MM-DD --to YYYY-MM-DD
        [--by-root] [--by-agent] [--max-windows N]
    bin/venv-python -m scripts.telemetry_usage plans [--last N] [--as-of REF] [--lookback-days D]

Each command reads one telemetry reader window verb (model_usage_rollup, tool_use_rollup, session_startup_baseline,
session_tree_rollup), which
answers at most seven UTC days of per-tree rows and never sums across trees (Decision 199 cl.1). The range is split into
consecutive windows of at most seven days and the rows are summed here: exact, because every tree row carries its root's
partition pin and so lands in exactly one window. A window the reader refuses as too large (413) or too slow (504) is halved
down to one day. Any other failed window prints no total and exits 1 naming the window and error_type (Decision 215); the
call count is bounded and printed (Decision 88). One JSON object goes to stdout; windows and calls go to stderr.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO

from scripts.aws_profile import resolve_aws_profile
from scripts.telemetry_verify import session_presence
from src.common.ducklake_reader_client import ReadDeadlineExceededError, ResponseTooLargeError

WINDOW_DAYS = 7
DEFAULT_MAX_WINDOWS = 9
MAX_CALLS = 32
PROFILE = "agent_platform"
VERBS = {
    "models": "model_usage_rollup",
    "tools": "tool_use_rollup",
    "startup": "session_startup_baseline",
    "trees": "session_tree_rollup",
}
THREAD_PREFIX = "claude/project-thread-"
PLANNING_WORKFLOWS = frozenset({"plan", "decision-scout", "plan-critique"})
OTHER_WORKFLOWS = frozenset({"implement", "audit", "orient"})
DEFAULT_PLANS = 8
DEFAULT_LOOKBACK_DAYS = 56
_CLASSES = ("tokens_input_total", "tokens_output_total", "tokens_cache_read_total", "tokens_cache_creation_total")
_TOKEN_TOTALS = (
    "tokens_input_total",
    "tokens_output_total",
    "tokens_cache_read_total",
    "tokens_cache_creation_total",
    "cache_creation_1h_total",
    "cache_creation_5m_total",
)


class WindowFailed(RuntimeError):
    """A window could not be read; the CLI prints no total."""

    def __init__(self, first: date, last: date, error_type: str) -> None:
        super().__init__(f"window={first}..{last} error_type={error_type}")


def windows(first: date, last: date) -> list[tuple[date, date]]:
    out, start = [], first
    while start <= last:
        end = min(start + timedelta(days=WINDOW_DAYS - 1), last)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def _error_type(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    kind = body.get("error_type") if isinstance(body, dict) else None
    return kind if isinstance(kind, str) and kind else type(exc).__name__


class _Reads:
    """The windowed reads of one invocation: halving on 413/504 and a fixed call cap."""

    def __init__(self, reader: Any, verb: str, err: TextIO) -> None:
        self.reader, self.verb, self.err, self.calls = reader, verb, err, 0
        self.rows: list[dict[str, Any]] = []

    def read(self, first: date, last: date) -> None:
        if self.calls >= MAX_CALLS:
            raise WindowFailed(first, last, "call_cap_exceeded")
        self.calls += 1
        print(f"window {first}..{last}", file=self.err)
        try:
            self.rows += self.reader.named(self.verb, day_from=first.isoformat(), day_to=last.isoformat())
        except (ResponseTooLargeError, ReadDeadlineExceededError) as exc:
            days = (last - first).days + 1
            if days == 1:
                raise WindowFailed(first, last, _error_type(exc)) from None
            middle = first + timedelta(days=(days + 1) // 2 - 1)
            self.read(first, middle)
            self.read(middle + timedelta(days=1), last)
        except Exception as exc:  # noqa: BLE001 -- any failed window fails closed, naming its type only
            raise WindowFailed(first, last, _error_type(exc)) from None


def _sum(values: Iterable[Any]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _tree(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("root_session_id"), row.get("harness_session_key"), row.get("session_started_at"))


def _group(
    rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], tuple[Any, ...]]
) -> list[tuple[Any, list[dict[str, Any]]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return sorted(groups.items(), key=lambda kv: tuple((v is None, "" if v is None else str(v)) for v in kv[0]))


def _widen(args: argparse.Namespace, names: tuple[str, ...]) -> tuple[str, ...]:
    extra = ("harness_session_key", "root_session_id") if args.by_root else ()
    return extra + names


def cache_read_ratio(read: int | None, uncached: int | None, creation: int | None) -> float | None:
    if read is None or uncached is None or creation is None:
        return None
    denominator = uncached + read + creation
    return None if denominator == 0 else read / denominator


def models(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    names = _widen(args, ("model",))
    out = []
    for key, group in _group(rows, lambda r: tuple(r.get(n) for n in names)):
        totals = {name: _sum(r[name] for r in group) for name in _TOKEN_TOTALS}
        out.append(
            {
                **dict(zip(names, key)),
                "trees": len({_tree(r) for r in group}),
                "sessions": _sum(r["session_count"] for r in group),
                "model_call_count": _sum(r["model_call_count"] for r in group),
                **totals,
                "usage_missing_count": _sum(r["usage_missing_count"] for r in group),
                "cache_read_ratio": cache_read_ratio(
                    totals["tokens_cache_read_total"], totals["tokens_input_total"], totals["tokens_cache_creation_total"]
                ),
            }
        )
    return out


def tools(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    names = _widen(args, (("is_root", "agent_name") if args.by_agent else ()) + ("tool_name", "mcp_server"))
    out = []
    for key, group in _group(rows, lambda r: tuple(r.get(n) for n in names)):
        out.append(
            {
                **dict(zip(names, key)),
                "call_count": _sum(r["call_count"] for r in group),
                "error_count": _sum(r["error_count"] for r in group),
                "blocked_count": _sum(r["blocked_count"] for r in group),
                "interrupted_count": _sum(r["interrupted_count"] for r in group),
                "session_row_count": _sum(r["session_count"] for r in group),
                "trees": len({_tree(r) for r in group}),
            }
        )
    return out


def branch_prefix(branch: str | None) -> str:
    if branch and branch.startswith(THREAD_PREFIX):
        return THREAD_PREFIX.rstrip("-")
    return branch.split("/", 1)[0] if branch and "/" in branch else "(none)"


def _day(value: Any) -> str:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _nearest_rank(values: list[int], percent: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * percent // 100) - 1)]


def _stats(values: list[int]) -> dict[str, Any]:
    return {
        "median": statistics.median(values) if values else None,
        "p90": _nearest_rank(values, 90) if values else None,
        "min": min(values, default=None),
        "max": max(values, default=None),
    }


def startup(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    extra = ("agent_name",) if args.by_agent else ()
    names = _widen(args, ("day", "branch_prefix", "is_root") + extra)
    shaped = [{**r, "day": _day(r["session_started_at"]), "branch_prefix": branch_prefix(r.get("branch"))} for r in rows]
    out = []
    for key, group in _group(shaped, lambda r: tuple(r.get(n) for n in names)):
        called = [r for r in group if r["model_call_count"] > 0]
        classes = ("tokens_input", "tokens_cache_read", "tokens_cache_creation")
        totals = [t for r in called if (t := _sum(r[c] for c in classes)) is not None]
        out.append(
            {
                **dict(zip(names, key)),
                "count": len(called),
                "sessions_without_call": len(group) - len(called),
                **{f"first_call_input_{k}": v for k, v in _stats(totals).items()},
                **{
                    f"median_{c}": statistics.median(v) if (v := [r[c] for r in called if r[c] is not None]) else None
                    for c in classes
                },
            }
        )
    return out


def trees(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.by_root:
        return [
            {
                "root_session_id": r["root_session_id"],
                "harness_session_key": r["harness_session_key"],
                "day": _day(r["session_started_at"]),
                "session_count": r["session_count"],
                "compact_count": r["compact_count"],
                "compacted_session_count": r["compacted_session_count"],
                "workflows": r["workflows"],
                "merge_links": len(r["merge_links"]),
            }
            for r in sorted(rows, key=lambda r: (str(r["session_started_at"]), str(r["root_session_id"])))
        ]
    out = []
    for (day,), group in _group([{**r, "day": _day(r["session_started_at"])} for r in rows], lambda r: (r["day"],)):
        out.append(
            {
                "day": day,
                "trees": len(group),
                "compact_count": _sum(r["compact_count"] for r in group),
                "compacted_trees": sum(1 for r in group if r["compact_count"] > 0),
                "planning_trees": sum(1 for r in group if PLANNING_WORKFLOWS & set(r["workflows"])),
            }
        )
    return out


SUMMARIES = {"models": models, "tools": tools, "startup": startup, "trees": trees}


def _merge_entry(
    commit: session_presence.Commit, linked: list[dict[str, Any]], usage: dict[tuple[Any, ...], dict[str, Any]]
) -> dict[str, Any]:
    labels = set().union(*(set(r["workflows"]) for r in linked)) if linked else set()
    classes = {c: _sum(usage.get(_tree(r), {}).get(c) for r in linked) for c in _CLASSES}
    return {
        "merge_sha": commit.sha,
        "merged_at": commit.committed_at.isoformat(),
        "subject": commit.subject,
        "trees": len(linked),
        **classes,
        "model_call_count": _sum(usage.get(_tree(r), {}).get("model_call_count") for r in linked),
        "compact_count": _sum(r["compact_count"] for r in linked),
        "workflows": sorted(labels),
        "planning": bool(linked) and all(PLANNING_WORKFLOWS & set(r["workflows"]) for r in linked),
        "mixed": any(OTHER_WORKFLOWS & set(r["workflows"]) for r in linked),
    }


def plans(args: argparse.Namespace, reader: Any, err: TextIO, found: list[Any]) -> dict[str, Any]:
    """The newest plan merges with the linked trees' cost by class and whether the workflow label finds every tree."""
    merges = found[: args.merges]
    newest, oldest = merges[0].committed_at.date(), merges[-1].committed_at.date()
    floor = oldest - timedelta(days=args.lookback_days)
    chunks = windows(floor, newest)
    print(f"windows={len(chunks)} floor={floor}", file=err)
    tree_reads = _Reads(reader, VERBS["trees"], err)
    for first, last in chunks:
        tree_reads.read(first, last)
    shas = {c.sha for c in merges}
    by_sha: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {}
    for row in tree_reads.rows:
        for link in row["merge_links"]:
            if link["merge_sha"] in shas:
                by_sha.setdefault(link["merge_sha"], {})[_tree(row)] = row
    linked_days = {_day(r["session_started_at"]) for found in by_sha.values() for r in found.values()}
    usage_reads = _Reads(reader, VERBS["models"], err)
    for first, last in chunks:
        if any(first.isoformat() <= d <= last.isoformat() for d in linked_days):
            usage_reads.read(first, last)
    usage: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in usage_reads.rows:
        mine = usage.setdefault(_tree(row), dict.fromkeys((*_CLASSES, "model_call_count")))
        for name in mine:
            mine[name] = _sum([mine[name], row[name]])
    entries = [_merge_entry(c, list(by_sha.get(c.sha, {}).values()), usage) for c in merges]
    linked = [e for e in entries if e["trees"]]
    uncaught = [e["merge_sha"] for e in linked if not e["planning"]]
    unlinked = sum(1 for r in tree_reads.rows if PLANNING_WORKFLOWS & set(r["workflows"]) and not r["merge_links"])
    verdict = "PASS" if linked and not uncaught else "FAIL"
    return {
        "verb": "plans",
        "floor": floor.isoformat(),
        "calls": {"trees": tree_reads.calls, "models": usage_reads.calls},
        "merges": entries,
        "summary": {
            "linked": len(linked),
            "caught": len(linked) - len(uncaught),
            "uncaught": uncaught,
            "planning_trees_unlinked": unlinked,
            "verdict": verdict,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.telemetry_usage", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=[*sorted(VERBS), "plans"])
    parser.add_argument("--from", dest="first", type=date.fromisoformat, help="first UTC day, YYYY-MM-DD (window commands)")
    parser.add_argument("--to", dest="last", type=date.fromisoformat, help="last UTC day, YYYY-MM-DD (window commands)")
    parser.add_argument(
        "--last-merges", "--last", dest="merges", type=int, default=DEFAULT_PLANS, help="plans: newest N, 1..20"
    )
    parser.add_argument("--as-of", default="origin/main", help="plans: git ref whose first-parent plan( commits are read")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="plans: days before the oldest merge")
    parser.add_argument("--by-root", action="store_true", help="keep one row per thread tree (keyed by harness_session_key)")
    parser.add_argument("--by-agent", action="store_true", help="split root from sub-agent rows (tools, startup)")
    parser.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS, help="refuse a longer range before any call")
    parser.epilog = (
        "tools: session_row_count sums the verb's per-row session_count, so a session whose calls of one tool differ in "
        "attribution_mcp_server counts once per value. models: sessions sums per (tree, model) rows the same way."
    )
    return parser


def _reader(reader: Any) -> Any:
    if reader is not None:
        return reader
    from scripts.telemetry_capture.read_route import TelemetryReader  # noqa: PLC0415

    return TelemetryReader(profile=resolve_aws_profile(PROFILE))


def _run_plans(args: argparse.Namespace, reader: Any, out: TextIO, err: TextIO, commits: Callable[..., list[Any]]) -> int:
    if not 1 <= args.merges <= 20 or args.lookback_days < 0:
        print("telemetry_usage: --last must be 1..20 and --lookback-days >= 0", file=err)
        return 2
    try:
        found = [c for c in commits(Path(__file__).resolve().parents[1], args.as_of) if c.subject.startswith("plan(")]
        if not found:
            print("telemetry_usage FAIL no plan( merge on the ref", file=err)
            return 1
        result = plans(args, _reader(reader), err, found)
    except WindowFailed as exc:
        print(f"telemetry_usage FAIL {exc}", file=err)
        return 1
    print(json.dumps(result, sort_keys=True, default=str), file=out)
    if result["summary"]["verdict"] != "PASS":
        print(f"telemetry_usage FAIL verdict uncaught={result['summary']['uncaught']}", file=err)
        return 1
    return 0


def run(
    argv: list[str],
    reader: Any = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    commits: Callable[..., list[Any]] = session_presence.git_commits,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    args = _parser().parse_args(argv)
    if args.command == "plans":
        return _run_plans(args, reader, out, err, commits)
    if args.first is None or args.last is None or args.last < args.first or (args.command == "models" and args.by_agent):
        print("telemetry_usage: invalid arguments (range missing or reversed, or --by-agent with models)", file=err)
        return 2
    chunks = windows(args.first, args.last)
    print(f"windows={len(chunks)}", file=err)
    if len(chunks) > args.max_windows:
        print(f"telemetry_usage: {len(chunks)} windows exceed --max-windows {args.max_windows}", file=err)
        return 2
    reads = _Reads(_reader(reader), VERBS[args.command], err)
    try:
        for first, last in chunks:
            reads.read(first, last)
    except WindowFailed as exc:
        print(f"telemetry_usage FAIL {exc}", file=err)
        return 1
    print(f"calls={reads.calls}", file=err)
    result = {
        "verb": VERBS[args.command],
        "from": args.first.isoformat(),
        "to": args.last.isoformat(),
        "calls": reads.calls,
        "conflicted_rows": sum(1 for r in reads.rows if r.get("derivation_conflicted")),
        "rows": SUMMARIES[args.command](reads.rows, args),
    }
    print(json.dumps(result, sort_keys=True, default=str), file=out)
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
