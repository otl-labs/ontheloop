"""Compact old full amendment_log rows to terse rows, losslessly (Decision 275 cl.4, rec-5452).

    bin/venv-python -m scripts.checks.contracts.amendment_compact [--write | --check] [--rows N] [FILES...]

Default FILES: every docs/contracts/*.yaml. Dry run by default. Not a registered check (precedent
scripts/checks/decisions/standards_census.py). Each file is proved before it is written (see _prove): refusing the
whole file on any failure. Judging hand-made compactions against a base ref belongs to rec-5453.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common
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

_LINES = re.compile(r"[^\r\n\x85\u2028\u2029]*(?:\r\n|[\r\n\x85\u2028\u2029])|[^\r\n\x85\u2028\u2029]+")
_EOL = re.compile(r"(?:\r\n|[\r\n\x85\u2028\u2029])$")
_KEYS = ("date", "semantic_break", "change_class")


@dataclass(frozen=True)
class RowSpan:
    first: int
    last: int
    indent: str
    texts: tuple[str, str, str] | None
    refusal: str | None


def split_lines(text: str) -> list[str]:
    """Lines with their endings, split exactly where the YAML reader counts a line."""
    return _LINES.findall(text)


def _scalar_ranges(node: yaml.Node, starts: list[int], out: list[tuple[int, int]]) -> None:
    if isinstance(node, yaml.ScalarNode):
        begin = node.start_mark.index
        if node.style in ("|", ">"):
            nxt = node.start_mark.line + 1
            begin = starts[nxt] if nxt < len(starts) else node.end_mark.index
        out.append((begin, node.end_mark.index))
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _scalar_ranges(child, starts, out)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            _scalar_ranges(key, starts, out)
            _scalar_ranges(value, starts, out)


def _end_lines(node: yaml.Node, lines: list[str], out: list[int]) -> None:
    if isinstance(node, yaml.ScalarNode):
        end = node.end_mark
        last = end.line
        if node.style in ("|", ">"):
            head = lines[end.line][: end.column] if end.line < len(lines) else ""
            last = end.line - 1 if not head.strip() else end.line
            while last > node.start_mark.line and not lines[last].strip():
                last -= 1
        out.append(last)
        return
    if node.flow_style:
        out.append(node.end_mark.line)
    if isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _end_lines(child, lines, out)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            _end_lines(key, lines, out)
            _end_lines(value, lines, out)


def _has_comment(lines: list[str], starts: list[int], span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    for number in range(span[0], span[1] + 1):
        for match in re.finditer("#", lines[number]):
            at = match.start()
            if (at == 0 or lines[number][at - 1].isspace()) and not any(a <= starts[number] + at < b for a, b in ranges):
                return True
    return False


def _anchor_lines(text: str) -> set[int]:
    found: set[int] = set()
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            found.add(event.start_mark.line)
    return found


def _texts(node: yaml.MappingNode, text: str) -> tuple[str, str, str] | None:
    found: dict[str, str] = {}
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value in _KEYS and isinstance(value, yaml.ScalarNode):
            raw = text[value.start_mark.index : value.end_mark.index]
            if raw and "\n" not in raw and "\r" not in raw:
                found[key.value] = raw
    return (found["date"], found["semantic_break"], found["change_class"]) if len(found) == len(_KEYS) else None


def _measure(
    item: yaml.Node,
    node_ids: list[int],
    lines: list[str],
    starts: list[int],
    ranges: list[tuple[int, int]],
    anchors: set[int],
) -> tuple[int, int, str, str | None]:
    """(first, last, indent, refusal) of one item: whole lines, refused when the splice could not be exact."""
    first = item.start_mark.line
    ends: list[int] = []
    _end_lines(item, lines, ends)
    last = max([first, *ends])
    head = lines[first][: item.start_mark.column] if first < len(lines) else ""
    indent = head[: len(head) - len(head.lstrip())]
    refusal = None
    if not isinstance(item, yaml.MappingNode) or node_ids.count(id(item)) > 1:
        refusal = "malformed"
    elif head.strip() != "-" or last >= len(lines):
        refusal = "span_comment"
    elif any(first <= line <= last for line in anchors):
        refusal = "malformed"
    elif _has_comment(lines, starts, (first, last), ranges):
        refusal = "span_comment"
    return first, last, indent, refusal


def locate_log(text: str) -> list[RowSpan] | None:
    """One RowSpan per top-level amendment_log item, or None when the text has no such sequence."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        anchors = _anchor_lines(text)
    except yaml.YAMLError:
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    seq = None
    for key, value in root.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "amendment_log":
            seq = value
    if not isinstance(seq, yaml.SequenceNode):
        return None
    lines = split_lines(text)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    ranges: list[tuple[int, int]] = []
    _scalar_ranges(root, starts, ranges)
    node_ids = [id(item) for item in seq.value]
    raw = [_measure(item, node_ids, lines, starts, ranges, anchors) for item in seq.value]
    spans: list[RowSpan] = []
    for i, (first, last, indent, refusal) in enumerate(raw):
        item = seq.value[i]
        texts = _texts(item, text) if isinstance(item, yaml.MappingNode) else None
        if refusal is None and texts is None:
            refusal = "malformed"
        spans.append(RowSpan(first, last, indent, texts, refusal))
    return spans


def replacement_line(span: RowSpan, summary: str, eol: str) -> str:
    date, broke, change = span.texts or ("", "", "")
    escaped = summary.replace("\\", "\\\\").replace('"', '\\"')
    return f'{span.indent}- {{date: {date}, semantic_break: {broke}, change_class: {change}, summary: "{escaped}"}}{eol}'


def splice_rows(text: str, spans: dict[int, RowSpan], summaries: dict[int, str]) -> str:
    """text with each chosen row's line range replaced by one flow-mapping line; everything else is untouched."""
    lines = split_lines(text)
    for index in sorted(summaries, key=lambda i: spans[i].first, reverse=True):
        span = spans[index]
        match = _EOL.search(lines[span.last])
        lines[span.first : span.last + 1] = [replacement_line(span, summaries[index], match.group() if match else "")]
    return "".join(lines)


def lines_match(old: str, new: str, chosen: list[RowSpan]) -> bool:
    """Proof (a): new equals old with each chosen range replaced by exactly one line, byte for byte elsewhere."""
    before, after = split_lines(old), split_lines(new)
    ranges = {span.first: span for span in chosen}
    i = j = 0
    while i < len(before):
        span = ranges.get(i)
        if span is not None:
            if j >= len(after) or not after[j].startswith(f"{span.indent}- {{"):
                return False
            i, j = span.last + 1, j + 1
        else:
            if j >= len(after) or after[j] != before[i]:
                return False
            i, j = i + 1, j + 1
    return j == len(after) and len(after) == len(before) - sum(s.last - s.first for s in chosen)


@dataclass
class _Plan:
    log: list[Any]
    spans: list[RowSpan]
    selected: list[int]
    refused: dict[int, str] = field(default_factory=dict)


def _plan(text: str, cfg: dict[str, Any]) -> _Plan | None:
    """The row plan for one file, or None when it has no top-level amendment_log list. YAMLError propagates."""
    data = yaml.safe_load(text)
    log = data.get("amendment_log") if isinstance(data, dict) else None
    if not isinstance(log, list):
        return None
    spans = locate_log(text)
    if spans is None or len(spans) != len(log):
        spans = [RowSpan(0, 0, "", None, "malformed")] * len(log)
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
    if not lines_match(old, new, [plan.spans[i] for i in chosen]):
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
    detail: list[str] = field(default_factory=list)


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
    for i, reason in sorted(plan.refused.items()):
        row = plan.log[i]
        result.detail.append(f"  refused row {i} date={row.get('date') if isinstance(row, dict) else None}: {reason}")
    if not chosen:
        return result
    summaries = {i: str(derive_terse(plan.log[i], cfg)) for i in chosen}
    new = splice_rows(text, {i: plan.spans[i] for i in chosen}, summaries)
    problem = _prove(text, new, plan, chosen, cfg, capped=len(chosen) < len(plan.selected))
    if problem:
        result.line = f"{base} proof_failed rows={chosen}: {problem}"
        result.failed = True
        return result
    result.lines_freed = effective_lines(text) - effective_lines(new)
    result.bytes_freed = len(text.encode("utf-8")) - len(new.encode("utf-8"))
    result.line = f"{base} lines_freed={result.lines_freed} bytes_freed={result.bytes_freed}"
    if write:
        scratch = path.with_name(path.name + ".compact.tmp")
        scratch.write_bytes(new.encode("utf-8"))
        os.replace(scratch, path)
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
        if check:
            for detail in result.detail:
                print(detail)
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
