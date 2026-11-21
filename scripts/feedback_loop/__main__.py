"""python -m scripts.feedback_loop friction: the daily friction detector (propose-only; files and stores nothing).

stdout is one counts line (counts, version numbers and class words only, Decision 101). Exit 0 after a completed run whatever
the findings; 1 with one FRICTION_STOP line when a read failed. --json and --sheet carry session-level detail and are refused
under GitHub Actions before any read. --assert-quiet fails closed: 0 only after a completed run in which the subject's
detector is measurable at a version that holds and raises no finding for the subject.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.feedback_loop import friction, proposals, render
from scripts.telemetry_usage import WindowFailed, _error_type

_WORD = re.compile(r"[^A-Za-z0-9_.-]")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.feedback_loop")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("friction")
    cmd.add_argument("--to", type=date.fromisoformat, default=None, help="last UTC day of the window (default: yesterday)")
    cmd.add_argument("--summary", type=Path, default=None, help="append the public job summary to this file")
    cmd.add_argument("--json", type=Path, default=None, help="write findings with their drafts (session runs only)")
    cmd.add_argument("--sheet", type=Path, default=None, help="write the group review sheet (session runs only)")
    cmd.add_argument("--assert-quiet", nargs=2, metavar=("DETECTOR", "SUBJECT"), default=None)
    cmd.add_argument("--classifier-version", type=int, default=None)
    return parser


def _word(value: str) -> str:
    return _WORD.sub("", value) or "unknown"


def _counts(result: friction.Result) -> dict[str, Any]:
    share = "none" if result.unmapped_share is None else f"{result.unmapped_share:.4f}"
    proposed = sum(1 for f in result.findings if f.proposed)
    return {
        "window": f"{result.first}..{result.last}",
        "detector_version": friction.DETECTOR_VERSION,
        "classifier_version": result.classifier_version,
        "calls": result.calls,
        "rows": result.rows,
        "friction_trees": result.friction_trees,
        "process_event_facts": result.process_event_facts,
        "unmapped": share,
        "coverage": result.coverage,
        "findings": len(result.findings),
        "proposed": proposed,
        "decided": len(result.findings) - proposed,
        "unparsed_recs": result.unparsed_recs,
        "other_version_rows": result.other_version_rows,
        "conflicted_rows": result.conflicted_rows,
        "response_bytes": result.response_bytes,
        "elapsed_s": result.elapsed_s,
    }


def _counts_line(counts: dict[str, Any]) -> str:
    return "FRICTION " + " ".join(f"{k}={v}" for k, v in counts.items())


def _stop(stage: str, error_type: str, status: Any, out: Any) -> int:
    print(
        f"FRICTION_STOP stage={stage} error_type={_word(error_type)} status={'none' if status is None else status}", file=out
    )
    return 1


def _quiet(verdict: str, out: Any, reason: str | None = None) -> int:
    print(f"FRICTION_QUIET verdict={verdict}" + (f" reason={reason}" if reason else ""), file=out)
    return 0 if verdict == "quiet" else 2


def _assert_quiet(args: argparse.Namespace, result: friction.Result, out: Any) -> int:
    detector, subject = args.assert_quiet
    params, n, version = friction.DETECTOR_PARAMS, args.classifier_version, result.classifier_version
    held = version == n if detector == friction.RECURRING else version >= n
    if not held:
        return _quiet("unmeasurable", out, "version_mismatch")
    if result.friction_trees < params["min_trees"]:
        return _quiet("unmeasurable", out, "few_trees")
    if detector == friction.COVERAGE and result.process_event_facts < params["min_process_event_facts"]:
        return _quiet("unmeasurable", out, "coverage_unmeasurable")
    if any(f.detector_id == detector and f.subject_key == subject for f in result.findings):
        return _quiet("not_quiet", out)
    return _quiet("quiet", out)


def _write_outputs(args: argparse.Namespace, result: friction.Result) -> None:
    counts = _counts(result)
    if args.json is not None:
        versions = {"detector_version": friction.DETECTOR_VERSION, "classifier_version": result.classifier_version}
        window = (str(result.first), str(result.last))
        payload = {
            "window": {"from": window[0], "to": window[1]},
            "versions": versions,
            "counts": counts,
            "findings": [
                {
                    "detector_id": f.detector_id,
                    "subject_key": f.subject_key,
                    "state": f.state,
                    "rec_id": f.rec_id,
                    "draft": proposals.draft(f, window, versions),
                }
                for f in result.findings
            ],
        }
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if args.sheet is not None:
        args.sheet.write_text(render.sheet_markdown(result), encoding="utf-8")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(render.summary_markdown(result))


def _run(args: argparse.Namespace, reader: Any, last: date, out: Any, err: Any) -> int:
    quiet = args.assert_quiet is not None
    stage = "window"
    started = datetime.now(timezone.utc)
    try:
        if reader is None:
            from scripts.telemetry_capture import read_route  # noqa: PLC0415

            reader = read_route.make_routed_reader()
        first = last - timedelta(days=friction.DETECTOR_PARAMS["window_days"] - 1)
        rows, calls = friction.read_window(reader, first, last)
        result = friction.detect(rows, first, last, calls)
        if not quiet:
            stage = "recs"
            friction.apply_rec_states(result, friction.rec_states(reader))
        result.elapsed_s = int((datetime.now(timezone.utc) - started).total_seconds())
    except WindowFailed as exc:
        if quiet:
            return _quiet("unmeasurable", out, "read_failed")
        match = re.search(r"error_type=(\S+)", str(exc))
        return _stop("window", match.group(1) if match else "unknown", getattr(exc.__context__, "status", None), out)
    except Exception as exc:  # noqa: BLE001 -- the run fails closed naming a stage and a type only
        if quiet:
            return _quiet("unmeasurable", out, "read_failed")
        if stage == "recs":
            return _stop("recs", _error_type(exc), getattr(exc, "status", None), out)
        if os.environ.get("GITHUB_ACTIONS") != "true":
            traceback.print_exc(file=err)
        return _stop("internal", type(exc).__name__, None, out)
    if quiet:
        return _assert_quiet(args, result, out)
    _write_outputs(args, result)
    print(_counts_line(_counts(result)), file=out)
    return 0


def main(
    argv: list[str] | None = None, *, reader: Any = None, today: date | None = None, out: Any = None, err: Any = None
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    args = _parser().parse_args(argv)
    now = today or datetime.now(timezone.utc).date()
    last = args.to if args.to is not None else now - timedelta(days=1)
    if last >= now:
        print("feedback_loop: --to must be before today (UTC)", file=err)
        return 2
    if args.assert_quiet is not None and (
        args.classifier_version is None or args.assert_quiet[0] not in (friction.COVERAGE, friction.RECURRING)
    ):
        print("feedback_loop: --assert-quiet needs a known detector and --classifier-version", file=err)
        return 2
    if (args.json is not None or args.sheet is not None) and os.environ.get("GITHUB_ACTIONS") == "true":
        print("FRICTION_REFUSED reason=private_output_in_ci", file=out)
        return 2
    prior = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _run(args, reader, last, out, err)
    finally:
        logging.disable(prior)


if __name__ == "__main__":
    sys.exit(main())
