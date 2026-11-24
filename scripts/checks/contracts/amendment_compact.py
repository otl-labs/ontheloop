"""Compact old full amendment_log rows to terse rows, losslessly (Decision 275 cl.4, rec-5452).

    bin/venv-python -m scripts.checks.contracts.amendment_compact [--write | --check] [--rows N] [FILES...]

Default FILES: every docs/contracts/*.yaml. Dry run by default. Not a registered check (precedent
scripts/checks/decisions/standards_census.py). Each file is proved before it is written (see _prove): refusing the
whole file on any failure. Judging hand-made compactions against a base ref belongs to rec-5453.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common
from scripts.checks.contracts import _amendment_splice as _splice
from scripts.checks.contracts._amendment_retention import load_retention
from scripts.checks.contracts._amendment_terse import (
    Refusal,
    compaction_order,
    derive_terse,
    exempt_file,
    lossless,
    rows_past_keep,
)
from scripts.checks.structural._classify import effective_lines


@dataclass
class _Plan:
    log: list[Any]
    spans: list[_splice.RowSpan]
    selected: list[int]
    refused: dict[int, str] = field(default_factory=dict)


def _plan(text: str, cfg: dict[str, Any]) -> _Plan | None:
    """The row plan for one file, or None when it has no top-level amendment_log list. YAMLError propagates."""
    data = yaml.safe_load(text)
    log = data.get("amendment_log") if isinstance(data, dict) else None
    if not isinstance(log, list):
        return None
    spans = _splice.locate_log(text)
    if spans is None or len(spans) != len(log):
        spans = [_splice.RowSpan(0, 0, "", None, "malformed")] * len(log)
    selected = [i for i in compaction_order(log, cfg) if spans[i].refusal is None]
    chosen = set(selected)
    refused: dict[int, str] = {}
    for i in rows_past_keep(log, cfg):
        if i in chosen:
            continue
        verdict = derive_terse(log[i], cfg)
        refused[i] = verdict.reason if isinstance(verdict, Refusal) else (spans[i].refusal or "malformed")
    return _Plan(log, spans, selected, refused)


def selected_rows(text: str, cfg: dict[str, Any]) -> list[int]:
    """Indexes of the rows the tool would compact, oldest first; [] for text it cannot read."""
    try:
        plan = _plan(text, cfg)
    except yaml.YAMLError:
        return []
    return plan.selected if plan else []


def _without_log(text: str) -> object:
    data = yaml.safe_load(text)
    return {k: v for k, v in data.items() if k != "amendment_log"} if isinstance(data, dict) else data


def _prove(old: str, new: str, plan: _Plan, chosen: list[int], cfg: dict[str, Any], capped: bool) -> str | None:
    """Proofs (a) lines, (b) parsed content, (c) a second run selects nothing; the failure or None."""
    if not _splice.lines_match(old, new, [plan.spans[i] for i in chosen]):
        return "proof (a): lines outside the chosen rows changed"
    try:
        new_log = yaml.safe_load(new)["amendment_log"]
        if _without_log(new) != _without_log(old) or len(new_log) != len(plan.log):
            return "proof (b): content outside the log or the row count changed"
        for i, row in enumerate(new_log):
            if i in chosen:
                why = lossless(plan.log[i], row, cfg)
                if why:
                    return f"proof (b): row {i}: {why}"
            elif row != plan.log[i]:
                return f"proof (b): untouched row {i} changed"
    except (yaml.YAMLError, KeyError, TypeError):
        return "proof (b): the output does not parse"
    again = len(selected_rows(new, cfg))
    expected = len(plan.selected) - len(chosen) if capped else 0
    return None if again == expected else f"proof (c): a second run selects {again} rows, expected {expected}"


@dataclass
class _Result:
    line: str
    files: int = 0
    compactable: int = 0
    refused: int = 0
    lines_freed: int = 0
    bytes_freed: int = 0
    failed: bool = False
    skipped: bool = False
    remaining: int = 0


def _one(path: Path, cfg: dict[str, Any], write: bool, rows: int | None) -> _Result:
    if exempt_file(path, cfg):
        return _Result(f"{path}: skipped (exempt)", skipped=True)
    try:
        text = path.read_bytes().decode("utf-8")
        plan = _plan(text, cfg)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return _Result(f"{path}: unreadable ({type(exc).__name__})", failed=True)
    if plan is None:
        return _Result(f"{path}: skipped (no amendment_log list)", skipped=True)
    chosen = plan.selected[:rows] if rows is not None else list(plan.selected)
    reasons = Counter(plan.refused.values())
    refused = f"{sum(reasons.values())}[" + ",".join(f"{k}={v}" for k, v in sorted(reasons.items())) + "]"
    base = f"{path}: compactable={len(plan.selected)} refused={refused}"
    result = _Result(base, files=1, compactable=len(plan.selected), refused=sum(reasons.values()))
    result.remaining = len(plan.selected)
    if not chosen:
        return result
    summaries = {i: str(derive_terse(plan.log[i], cfg)) for i in chosen}
    new = _splice.splice(text, {i: plan.spans[i] for i in chosen}, summaries)
    problem = _prove(text, new, plan, chosen, cfg, capped=len(chosen) < len(plan.selected))
    if problem:
        result.line = f"{base} proof_failed rows={chosen}: {problem}"
        result.failed = True
        return result
    result.lines_freed = effective_lines(text) - effective_lines(new)
    result.bytes_freed = len(text.encode("utf-8")) - len(new.encode("utf-8"))
    result.line = f"{base} lines_freed={result.lines_freed} bytes_freed={result.bytes_freed}"
    if write:
        path.write_bytes(new.encode("utf-8"))
        result.line += " written"
        result.remaining = len(plan.selected) - len(chosen)
    return result


def run(paths: list[Path], cfg: dict[str, Any] | None, write: bool, check: bool, rows: int | None) -> int:
    if cfg is None:
        print("config_unreadable: docs/contracts/contract-population.yaml amendment_log_retention is absent or malformed")
        return 1
    totals = _Result("")
    remaining = 0
    skipped = 0
    for path in paths:
        result = _one(path, cfg, write, rows)
        print(result.line)
        skipped += result.skipped
        remaining += result.remaining
        for name in ("files", "compactable", "refused", "lines_freed", "bytes_freed"):
            setattr(totals, name, getattr(totals, name) + getattr(result, name))
        totals.failed = totals.failed or result.failed
    if skipped:
        print(f"skipped={skipped}")
    print(
        f"TOTAL files={totals.files} compactable={totals.compactable} refused={totals.refused} "
        f"lines_freed={totals.lines_freed} bytes_freed={totals.bytes_freed}"
    )
    return 1 if totals.failed or (check and remaining) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amendment_compact", description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--write", action="store_true", help="apply the compactions")
    parser.add_argument("--check", action="store_true", help="exit 1 while a compactable row remains past keep_full")
    parser.add_argument("--rows", type=int, help="compact at most N oldest selected rows per file")
    args = parser.parse_args(argv)
    if args.write and args.check:
        parser.error("--write and --check are exclusive")
    if args.rows is not None and args.rows < 1:
        parser.error("--rows must be at least 1")
    paths = args.files or sorted((_common.ROOT / "docs" / "contracts").glob("*.yaml"))
    return run(paths, load_retention(), args.write, args.check, args.rows)


if __name__ == "__main__":
    sys.exit(main())
