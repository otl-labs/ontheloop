"""Tier A amendment guard (Decision 243 cl.6, Decision 196 cl.3; marker-grammar.yaml token tier-a-amendment-approved).

Refuses a change to the mission, an NS.n, partial_order or semantics of docs/contracts/north-star.yaml unless the
key's own lines carry a marker citing a Decision whose text, added in the same change, names it. It binds the shared
marker mechanism (scripts/checks/_marker_guard.py, check_state_diff) and restates none of its mechanics; the
rules it adds (carrying Decision, approval pointer, amendment_log row, ownership) are in the contract's token row.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.misc.sensitive_value_detectors import narrowing_violations, ownership_violations

TOKEN = "tier-a-amendment-approved"
LABEL = "Tier A amendment guard"
_REL = "docs/contracts/north-star.yaml"
_SELF_REL = "scripts/checks/contracts/validate_tier_a_amendment_guard.py"
_CODEOWNERS = ".github/CODEOWNERS"
_CAPS = "config/agent/executor/capabilities.yaml"
_CORPUS = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")
_BLOCK = ("partial_order", "semantics")
_MARKER_RE = re.compile(rf"#\s*{re.escape(TOKEN)}:\s*dec-(\d+)\s*(.*)$")
_POINTER_RE = re.compile(r"approval:\s+\S+\s+\d{4}-\d{2}-\d{2}")

PROTECTED = (
    "scripts/checks/contracts/validate_tier_a_amendment_guard.py",
    "tests/checks/contracts/test_validate_tier_a_amendment_guard.py",
    "scripts/checks/_marker_guard.py",
    "scripts/checks/_marker_guard_state_options.py",
    "tests/checks/test__marker_guard.py",
    "tests/checks/test__marker_guard_state_options.py",
    "docs/contracts/north-star.yaml",
)


class _Entry(NamedTuple):
    state: str
    markers: list[tuple[str, str]]
    carried: list[Any]


class _Parsed(NamedTuple):
    entries: dict[str, _Entry]
    data: dict[str, Any]
    log: list[str]


def _loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.YAMLError(f"duplicate key {key!r}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    return Loader


_LOADER = _loader()


def _scalar_ranges(node: yaml.Node, starts: list[int], out: list[tuple[int, int]]) -> None:
    if isinstance(node, yaml.ScalarNode):
        begin = node.start_mark.index
        if node.style in ("|", ">"):
            begin = starts[node.start_mark.line + 1] if node.start_mark.line + 1 < len(starts) else node.end_mark.index
        out.append((begin, node.end_mark.index))
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _scalar_ranges(child, starts, out)
    else:
        for key, value in node.value:
            _scalar_ranges(key, starts, out)
            _scalar_ranges(value, starts, out)


def _comment_columns(lines: list[str], starts: list[int], ranges: list[tuple[int, int]]) -> list[int | None]:
    cols: list[int | None] = []
    for number, line in enumerate(lines):
        col = None
        for match in re.finditer("#", line):
            at = match.start()
            if (at == 0 or line[at - 1].isspace()) and not any(a <= starts[number] + at < b for a, b in ranges):
                col = at
                break
        cols.append(col)
    return cols


class _Scan(NamedTuple):
    lines: list[str]
    cols: list[int | None]
    markers: list[tuple[str, str] | None]


def _load(text: str) -> tuple[dict[str, Any], yaml.MappingNode]:
    try:
        for event in yaml.parse(text, Loader=_LOADER):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) or getattr(event, "tag", None):
                raise ValueError("anchors, aliases and explicit tags are refused")
        data = yaml.load(text, Loader=_LOADER)
        root = yaml.compose(text, Loader=_LOADER)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML error: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(root, yaml.MappingNode):
        raise ValueError("the file is not a mapping")
    return data, root


def _scan(text: str, root: yaml.MappingNode) -> _Scan:
    lines = text.split("\n")
    starts = [0]
    for match in re.finditer("\n", text):
        starts.append(match.end())
    ranges: list[tuple[int, int]] = []
    _scalar_ranges(root, starts, ranges)
    cols = _comment_columns(lines, starts, ranges)
    markers: list[tuple[str, str] | None] = []
    for number, col in enumerate(cols):
        match = _MARKER_RE.match(lines[number][col:]) if col is not None else None
        markers.append((f"dec-{match.group(1)}", match.group(2).strip()) if match and match.group(2).strip() else None)
    return _Scan(lines, cols, markers)


def _entry(scan: _Scan, value: object, text_from: int, mark_from: int, end: int) -> _Entry:
    found = [m for m in scan.markers[mark_from : end + 1] if m is not None]
    kept: list[str] = []
    for number in range(text_from, end + 1):
        line = scan.lines[number]
        if scan.markers[number] is not None and number >= mark_from:
            line = line[: scan.cols[number]]
        if line.strip():
            kept.append(line.strip())
    try:
        state = json.dumps({"value": value, "text": " ".join(kept)}, sort_keys=True, default=str)
    except TypeError as exc:
        raise ValueError(f"unorderable keys: {exc}") from exc
    carried = value.get("carried_by") if isinstance(value, dict) else None
    return _Entry(state, found, carried if isinstance(carried, list) else [])


def _principles(scan: _Scan, data: dict[str, Any], node: yaml.Node, line: int, end: int) -> dict[str, _Entry]:
    items = data["principles"]
    if not isinstance(items, list) or not isinstance(node, yaml.SequenceNode):
        raise ValueError("principles is not a list")
    entries: dict[str, _Entry] = {}
    for position, item in enumerate(items):
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(ident, str) or ident in entries:
            raise ValueError(f"principles item {position} is not a mapping with a unique string id")
        first = node.value[position].start_mark.line
        last = node.value[position + 1].start_mark.line - 1 if position + 1 < len(items) else end
        entries[ident] = _entry(scan, item, line if position == 0 else first, first, last)
    return entries


def _parse(text: str) -> _Parsed:
    """Entries per Tier A key; ValueError on any defect (a duplicate key, anchor, alias, tag or shape defect)."""
    data, root = _load(text)
    scan = _scan(text, root)
    tops = [(key.value, key.start_mark.line, value) for key, value in root.value]
    entries: dict[str, _Entry] = {}
    for index, (name, line, node) in enumerate(tops):
        end = tops[index + 1][1] - 1 if index + 1 < len(tops) else len(scan.lines) - 1
        if name in ("mission", *_BLOCK):
            entries[name] = _entry(scan, data[name], line, line, end)
        elif name == "principles":
            entries.update(_principles(scan, data, node, line, end))
    log = data.get("amendment_log")
    rows = [json.dumps(row, sort_keys=True, default=str) for row in log] if isinstance(log, list) else []
    return _Parsed(entries, data, rows)


def _entries(text: str) -> dict[str, _Entry]:
    return _parse(text).entries if text.strip() else {}


def _cat_batch(root: Path, ref: str, rels: list[str]) -> dict[str, str] | None:
    payload = "".join(f"{ref}:{rel}\n" for rel in rels).encode("utf-8")
    result = _common.run(["git", "cat-file", "--batch"], input=payload, capture_output=True, cwd=root)
    if result.returncode != 0:
        return None
    out = result.stdout if isinstance(result.stdout, bytes) else b""
    files: dict[str, str] = {}
    pos = 0
    for rel in rels:
        newline = out.find(b"\n", pos)
        if newline == -1:
            return None
        parts = out[pos:newline].decode("utf-8", errors="replace").split()
        pos = newline + 1
        files[rel] = ""
        if len(parts) == 3 and parts[2].isdigit():
            size = int(parts[2])
            if parts[1] == "blob":
                files[rel] = out[pos : pos + size].decode("utf-8", errors="replace")
            pos += size + 1
    return files


class _BaseReader:
    """Blob text at the diff base: "" for a missing path, None for the corpus when its batch fails."""

    def __init__(self, root: Path, ref: str) -> None:
        self.root, self.ref = root, ref
        first = _cat_batch(root, ref, [_REL, _SELF_REL, _CODEOWNERS, _CAPS])
        self.ok = first is not None
        self.files = first or {}
        self.corpus_read = False
        self.corpus_failed = False

    def __call__(self, rel: str) -> str | None:
        if rel in _CORPUS and not self.corpus_read:
            self.corpus_read = True
            got = _cat_batch(self.root, self.ref, list(_CORPUS))
            self.corpus_failed = got is None
            self.files.update(got or {})
        if rel in _CORPUS and self.corpus_failed:
            return None
        return self.files.get(rel, "")


def _spec(keys: list[str]) -> _marker_guard.RegistrySpec:
    def states(text: str) -> dict[str, _marker_guard.StateEntry]:
        found = _entries(text)
        out = {}
        for key in keys:
            entry = found.get(key)
            marker, reason = entry.markers[0] if entry and entry.markers else (None, None)
            out[key] = _marker_guard.StateEntry(entry.state if entry else None, marker, reason)
        return out

    def view(text: str) -> dict[str, _marker_guard.MarkerEntry]:
        return {k: _marker_guard.MarkerEntry(0.0, e.marker, e.reason) for k, e in states(text).items()}

    return _marker_guard.RegistrySpec(
        rel_path=_REL,
        token=TOKEN,
        gated_direction="down",
        extractor=view,
        gates_new_entry=lambda _value: False,
        label=LABEL,
        mention_candidates=lambda key: [key],
        reason_required=True,
        state_extractor=states,
        weakened=lambda base, head: base != head,
        gates_deletion=lambda _base: True,
        fresh_mention=True,
        boundary_match=True,
        transition_label="change",
    )


def _boundary(text: str) -> list[str]:
    try:
        data = yaml.safe_load(text) if text else {}
    except yaml.YAMLError:
        return []
    return list(data.get("boundary_patterns") or []) if isinstance(data, dict) else []


def _head_legs(head: _Parsed, spec: _marker_guard.RegistrySpec) -> list[str]:
    out = list(_marker_guard.check_present_markers(spec))
    for key, entry in head.entries.items():
        if len(entry.markers) > 1:
            out.append(f"{key}: more than one marker on one span (replace the old marker, never add a second).")
        out.extend(
            f"{key}: marker reason carries no `approval: <ref> <YYYY-MM-DD>` pointer."
            for _dec, reason in entry.markers[:1]
            if not _POINTER_RE.search(reason)
        )
    return out


def _diff_legs(head: _Parsed, base: _Parsed, spec: _marker_guard.RegistrySpec, reader: _BaseReader) -> list[str]:
    out: list[str] = []
    changed = [k for k, e in head.entries.items() if getattr(base.entries.get(k), "state", None) != e.state]
    new_rows = Counter(head.log) - Counter(base.log) if not Counter(base.log) - Counter(head.log) else Counter()
    ratified = re.search(r"Decision (\d+)", str(base.data.get("contract", {}).get("ratified_via", "")))
    for key, entry in head.entries.items():
        old = base.entries.get(key)
        marker = entry.markers[0] if entry.markers else None
        if key not in changed:
            if marker is not None and (old is None or marker not in old.markers[:1]):
                out.append(f"{key}: a marker records a change in the same PR; it is never added or reworded alone.")
            continue
        number = marker[0].removeprefix("dec-") if marker else None
        if marker and key in _BLOCK and (ratified is None or number != ratified.group(1)):
            out.append(
                f"{key}: marker cites {marker[0]}, not the ratifying Decision; record the change as a dated note on it."
            )
        elif marker and key not in _BLOCK and number not in {str(c) for c in entry.carried}:
            out.append(f"{key}: marker cites {marker[0]}, which is not in its carried_by; add it in the same change.")
        if not any(_marker_guard._mentions_any(row, [key], True) for row in new_rows.elements()):
            out.append(f"{key}: changed with no new amendment_log row naming it.")
    out.extend(
        f"{key}: removed -- removal has no marker escape (retire an NS.n by status: retired with a marker)."
        for key in base.entries
        if key not in head.entries
    )
    for violation in _marker_guard.check_state_diff(spec, base_reader=reader):
        key = violation.split(":", 1)[0]
        again = key in _BLOCK and "never re-authorizes" in violation
        out.append(
            violation
            + (" A second change takes two PRs: remove the old marker, then change with a fresh note." if again else "")
        )
    if reader.corpus_failed:
        out.append(f"Decision corpus unreadable at the base {reader.ref}, so no change can be shown fresh; re-run.")
    return out


def _base_state(root: Path, ref: str | None) -> tuple[_BaseReader | None, _Parsed | None, str, str | None]:
    """(reader, parsed base, base text, skip reason)."""
    reader = _BaseReader(root, ref) if ref else None
    if reader is None:
        return None, None, "", "no diff base"
    if not reader.ok:
        return reader, None, "", "git cat-file --batch failed"
    base_text = reader(_REL) or ""
    try:
        return reader, (_parse(base_text) if base_text.strip() else _Parsed({}, {}, [])), base_text, None
    except ValueError:
        return reader, None, base_text, "base north-star.yaml unparseable"


@registry.register("validate_tier_a_amendment_guard", owner="platform")
def validate_tier_a_amendment_guard(failed: list[str]) -> None:
    """Fail on an unauthorized change to a Tier A entry of docs/contracts/north-star.yaml, or on missing ownership."""
    print("\n=== Tier A amendment guard ===")
    root = _common.ROOT
    path = root / _REL
    head_text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
    violations: list[str] = []
    head: _Parsed | None = None
    if head_text is not None:
        try:
            head = _parse(head_text)
        except ValueError as exc:
            violations.append(f"PARSE: {_REL}: {exc} (fail closed).")
    reader, base, base_text, skip = _base_state(root, _common.status_diff_base(root))
    if skip:
        print(f"  SKIP: {skip} -- the diff legs did not run (advisory locally, authoritative in CI).")
    spec = _spec(list(head.entries) if head else [])
    if head is not None:
        violations += _head_legs(head, spec)
        if base is not None and reader is not None and head_text != base_text:
            violations += _diff_legs(head, base, spec, reader)
    elif head_text is None and base is not None:
        violations += [f"{key}: removed -- removal has no marker escape." for key in base.entries]
    codeowners = (root / _CODEOWNERS).read_text(encoding="utf-8") if (root / _CODEOWNERS).exists() else ""
    caps = (root / _CAPS).read_text(encoding="utf-8") if (root / _CAPS).exists() else ""
    violations += ownership_violations(PROTECTED, codeowners, _boundary(caps))
    if reader is not None and reader.ok:
        violations += narrowing_violations(
            reader(_SELF_REL) or "",
            reader(_CODEOWNERS) or "",
            _boundary(reader(_CAPS) or ""),
            PROTECTED,
            codeowners,
            _boundary(caps),
        )
    for violation in violations:
        print(f"  - {violation}")
    if violations:
        failed.append(LABEL)
    else:
        print(f"  PASS: {len(head.entries) if head else 0} Tier A entries clean.")
    if skip:
        registry.skipped(skip)
    else:
        registry.examined(len(head.entries) if head else 0, unit="tier_a_entries")
