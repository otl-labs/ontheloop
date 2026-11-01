"""Decision 226 cl.3-4 evaluator: does an open red block a plan or an /implement dispatch.

Pure evaluate() over (reds, work entries, bundled ids, now, slo_hours, selector) plus a CLI that prints one
JSON verdict line on stdout. Exit 0 clear, 1 blocked, 2 unavailable. Rules live in
docs/contracts/red-main-recovery.yaml (recovery_policy.halt.rules); slo_hours is read from the same file.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from scripts.ops_portal.ci_rca_schema import is_track_a
from scripts.ops_portal.closure_gate import parse_context_json

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "docs" / "contracts" / "red-main-recovery.yaml"
REPORT_PATH = ROOT / "logs" / ".preflight-report.json"
_OPEN_STATES = ("open", "in_progress")
_SHARED_CAP = 20
_ACTIONS = {"Create": "A", "Modify": "M", "Delete": "D"}


def _parse_ts(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _red_files(red: dict, ctx: dict) -> set[str]:
    files = {red.get("file") or ""}
    files |= {str(n).split("::")[0] for n in ctx.get("affected_nodeids") or []}
    return {f for f in files if f}


def _affected(manifest: dict, entries: list[tuple[str, str]]) -> set[str]:
    return {p for _, p in entries} | set(manifest.get("selected") or []) | set(manifest.get("deferred") or [])


def _failed_closed(manifest: dict) -> bool:
    return bool(manifest.get("fallback") or manifest.get("full_suite_forced"))


def evaluate(
    reds: list[dict],
    work_entries: list[tuple[str, str]],
    bundled_ids: list[str],
    now: datetime,
    slo_hours: int,
    selector: Optional[Callable[..., dict]] = None,
) -> dict:
    if selector is None:
        from scripts.checks.deps.affected_tests import derive_affected_tests  # noqa: PLC0415

        selector = derive_affected_tests
    bundled = set(bundled_ids)
    bundles_open_red = any(r.get("id") in bundled for r in reds)
    results: list[dict] = []
    work_side: Optional[dict] = None
    for red in reds:
        ctx = parse_context_json(red.get("context_v2_json"))
        created = _parse_ts(red.get("created_timestamp"))
        age = round((now - created).total_seconds() / 3600, 2) if created else None
        track = "test_defect" if is_track_a(ctx) else "product_regression"
        item: dict = {"id": red.get("id"), "rule": "", "track": track, "age_hours": age, "shared": []}
        results.append(item)
        if red.get("id") in bundled:
            item["rule"] = "repairs_this_red"
            continue
        if (age is None or age >= slo_hours) and not bundles_open_red:
            item["rule"] = "slo_breached"
            continue
        if is_track_a(ctx):
            item["rule"] = "track_a_in_slo"
            continue
        red_files = _red_files(red, ctx)
        if not red_files:
            item["rule"] = "undeterminable"
            continue
        if not work_entries:
            item["rule"] = "disjoint"
            continue
        if work_side is None:
            work_side = selector(work_entries)["manifest"]
        red_manifest = selector([("M", f) for f in sorted(red_files)])["manifest"]
        if _failed_closed(work_side) or _failed_closed(red_manifest):
            item["rule"] = "selector_fail_closed"
            continue
        shared = sorted(_affected(work_side, work_entries) & _affected(red_manifest, [("M", f) for f in red_files]))
        if shared:
            item["rule"] = "overlap"
            item["shared"] = shared[:_SHARED_CAP]
        else:
            item["rule"] = "disjoint"
    blocking = {"slo_breached", "undeterminable", "selector_fail_closed", "overlap"}
    verdict = "blocked" if any(i["rule"] in blocking for i in results) else "clear"
    return {"verdict": verdict, "slo_hours": slo_hours, "reds": results}


def _load_slo_hours(contract_path: Path) -> int:
    value = ((yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}).get("recovery_policy") or {}).get("slo_hours")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"recovery_policy.slo_hours in {contract_path} is not a positive int: {value!r}")
    return value


def _likely_resolved_ids(report_path: Path) -> set[str]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(report, dict) or report.get("recs_read_status") != "ok":
        return set()
    return {str(r.get("id") if isinstance(r, dict) else r) for r in report.get("ci_rca_likely_resolved_recs") or []}


def _work_from_plan(plan_path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    entries = [(_ACTIONS.get(s.get("action"), "M"), s["file"]) for s in plan.get("scope") or [] if s.get("file")]
    return entries, list(plan.get("bundled_recommendations") or [])


def _fetch_reds(reader: Any) -> list[dict]:
    rows = reader.named("preflight_signal_rows", followon_ids="")
    return [dict(r) for r in rows if r.get("source") == "ci_rca" and r.get("status") in _OPEN_STATES]


def run(
    plan: Optional[str],
    files: list[str],
    *,
    report: Optional[Path] = None,
    contract: Optional[Path] = None,
    reader: Any = None,
    selector: Optional[Callable[..., dict]] = None,
    now: Optional[datetime] = None,
) -> tuple[dict, int]:
    try:
        slo_hours = _load_slo_hours(contract or CONTRACT_PATH)
        if reader is None:
            from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

            reader = make_reader()
        reds = _fetch_reds(reader)
        if plan:
            entries, bundled = _work_from_plan(Path(plan))
        else:
            entries, bundled = [("M", f) for f in files], []
    except Exception as exc:  # noqa: BLE001 -- every unreadable input fails closed
        return {"verdict": "unavailable", "reason": f"{type(exc).__name__}: {exc}"[:300]}, 2
    dropped = _likely_resolved_ids(report or REPORT_PATH)
    reds = [r for r in reds if r.get("id") not in dropped]
    with contextlib.redirect_stdout(sys.stderr):
        verdict = evaluate(reds, entries, bundled, now or datetime.now(timezone.utc), slo_hours, selector)
    return verdict, 0 if verdict["verdict"] == "clear" else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Decision 226 red-main overlap/SLO verdict")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan")
    group.add_argument("--files", nargs="+", default=[])
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    verdict, code = run(args.plan, args.files, report=Path(args.report) if args.report else None)
    print(json.dumps(verdict))
    return code


if __name__ == "__main__":
    sys.exit(main())
