"""Routing library for docs/contracts/approval-routing.yaml (PLAN-wording-approval-routing).

Total: bad input yields None, a reason or a fail-closed reading, never an exception. Only git show, git log and
git diff-tree under root, and reading a given map file, touch the outside.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

MAP_REL = "docs/contracts/approval-routing.yaml"
_HEADING = re.compile(r"^(#{1,6})\s")
_ITEM = re.compile(r"^\s*\d+[a-z]*\.\s")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLAN = re.compile(r"^docs/plans/PLAN-(.+)\.yaml$")


def _git(root: Path, *args: str) -> str | None:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else None


def text_at(root: Path, ref: str | None, rel: str) -> str | None:
    """The file's text at a git ref, or in the working tree when ref is None; None when absent."""
    if ref is None:
        try:
            return (root / rel).read_text(encoding="utf-8")
        except OSError:
            return None
    return _git(root, "show", f"{ref}:{rel}")


def parse_map(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), dict) for k in ("lane", "gated", "notice")):
        return None
    return data


def load_map(root: Path, ref: str | None) -> dict | None:
    return parse_map(text_at(root, ref, MAP_REL))


def active(m: dict | None) -> bool:
    return m is not None and isinstance(m.get("lane"), dict) and m["lane"].get("rung") == "sampled"


def kind_floor(m: dict | None) -> int | None:
    floor = m["lane"].get("kind_floor") if m and isinstance(m.get("lane"), dict) else None
    return floor if isinstance(floor, int) and not isinstance(floor, bool) else None


def _gated(m: dict) -> dict:
    return m["gated"]


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _row_file(row: Any) -> str | None:
    if isinstance(row, str):
        return row
    if isinstance(row, dict) and isinstance(row.get("file"), str):
        return row["file"]
    return None


def _in_files(m: dict, rel: str) -> bool:
    for entry in _as_list(_gated(m).get("files")):
        if entry == rel or (isinstance(entry, str) and entry.startswith("**/") and PurePosixPath(rel).name == entry[3:]):
            return True
    return False


def _is_gated_path(m: dict, rel: str) -> bool:
    g = _gated(m)
    return _in_files(m, rel) or rel in (g.get("yaml_keys") or {}) or rel in (g.get("md_sections") or {})


def _load_yaml(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _keys_changed(keys: Any, base_text: str | None, head_text: str | None) -> bool:
    base, head = _load_yaml(base_text), _load_yaml(head_text)
    if base is None or head is None:
        return True
    for key in _as_list(keys):
        if (key in base) != (key in head) or base.get(key) != head.get(key):
            return True
    return False


def _section(text: str | None, entry: str) -> str | None:
    if text is None:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if entry.endswith("."):
            if not line.startswith(entry):
                continue
            end = next((j for j in range(i + 1, len(lines)) if _ITEM.match(lines[j]) or _HEADING.match(lines[j])), len(lines))
            return "\n".join(lines[i:end])
        if line.rstrip() != entry:
            continue
        marks = _HEADING.match(line)
        level = len(marks.group(1)) if marks else 0
        end = len(lines)
        for j in range(i + 1, len(lines)):
            found = _HEADING.match(lines[j])
            if found and len(found.group(1)) <= level:
                end = j
                break
        return "\n".join(lines[i:end])
    return None


def _sections_changed(entries: Any, base_text: str | None, head_text: str | None) -> bool:
    for entry in _as_list(entries):
        if not isinstance(entry, str):
            return True
        before, after = _section(base_text, entry), _section(head_text, entry)
        if before is None or after is None or before != after:
            return True
    return False


def _demoted(m: dict) -> tuple[set[str], list[str]]:
    rows = (m.get("demotions") or {}).get("rows", [])
    files: set[str] = set()
    reasons: list[str] = []
    if not isinstance(rows, list):
        return files, ["demotions.rows is not a list"]
    for row in rows:
        name = _row_file(row)
        if name is None:
            reasons.append(f"malformed demotions.rows entry: {row!r}")
        else:
            files.add(name)
    restorations = (m.get("demotions") or {}).get("restorations", [])
    if not isinstance(restorations, list) or any(_row_file(r) is None for r in restorations):
        reasons.append("malformed demotions.restorations")
    return files, reasons


def _restored(m: dict) -> set[str]:
    return {n for n in map(_row_file, _as_list((m.get("demotions") or {}).get("restorations"))) if n}


def reverted_files(m: dict, root: Path, base: str) -> tuple[list[str], str | None]:
    """(non-gated files changed by a revert commit reachable from base since lane.sampled_since, reason on failure)."""
    since = str(m.get("lane", {}).get("sampled_since", ""))
    if not _DATE.match(since):
        return [], f"lane.sampled_since is not a date: {since!r}"
    out = _git(root, "log", f"--since={since}T00:00:00Z", "--format=%H%x1f%s%x1f%b%x1e", base)
    if out is None:
        return [], f"git log failed at {base}"
    found: set[str] = set()
    for record in out.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) < 3 or not (parts[1].lower().startswith("revert") or "This reverts commit" in parts[2]):
            continue
        names = _git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", parts[0].strip())
        if names is None:
            return [], f"git diff-tree failed at {parts[0].strip()}"
        found.update(n for n in names.splitlines() if n)
    return sorted(n for n in found if not _is_gated_path(m, n) and n not in _restored(m)), None


def gated_changes(m: dict, root: Path, base: str, head: str | None, changed: list[str]) -> list[tuple[str, str]]:
    """Sorted (file, reason) pairs for changed files in the gated set (never applies exempt)."""
    g = _gated(m)
    out: set[tuple[str, str]] = set()
    demoted, reasons = _demoted(m)
    out.update((MAP_REL, reason) for reason in reasons)
    reverted = set(reverted_files(m, root, base)[0]) if active(m) else set()
    for rel in changed:
        if _in_files(m, rel):
            out.add((rel, "gated.files"))
        if rel in demoted:
            out.add((rel, "demotions.rows"))
        if rel in reverted:
            out.add((rel, "reverted since sampled_since"))
        yaml_keys = (g.get("yaml_keys") or {}).get(rel)
        md_entries = (g.get("md_sections") or {}).get(rel)
        if yaml_keys is not None or md_entries is not None:
            before, after = text_at(root, base, rel), text_at(root, head, rel)
            if yaml_keys is not None and _keys_changed(yaml_keys, before, after):
                out.add((rel, f"gated.yaml_keys {yaml_keys}"))
            if md_entries is not None and _sections_changed(md_entries, before, after):
                out.add((rel, f"gated.md_sections {md_entries}"))
    return sorted(out)


def _appended(base: Any, head: Any) -> bool:
    return isinstance(base, list) and isinstance(head, list) and len(head) > len(base) and head[: len(base)] == base


def _prefix_or_equal(base: Any, head: Any) -> bool:
    return base == head or _appended(base, head)


def _structure_exempt(file: str, base: dict, head: dict) -> bool:
    if file == MAP_REL:
        b_dem, h_dem = base.get("demotions"), head.get("demotions")
        if not isinstance(b_dem, dict) or not isinstance(h_dem, dict):
            return False
        rest = lambda d, drop: {k: v for k, v in d.items() if k not in drop}  # noqa: E731
        if rest(base, {"demotions", "amendment_log"}) != rest(head, {"demotions", "amendment_log"}):
            return False
        if rest(b_dem, {"rows"}) != rest(h_dem, {"rows"}) or not _appended(b_dem.get("rows"), h_dem.get("rows")):
            return False
        if not all(_row_file(r) for r in h_dem["rows"][len(b_dem["rows"]) :]):
            return False
        return _prefix_or_equal(base.get("amendment_log"), head.get("amendment_log"))
    if file == "config/agent/executor/capabilities.yaml":
        return _appended(base.get("boundary_patterns"), head.get("boundary_patterns"))
    return False


def _comments_untouched(base_text: str, head_text: str) -> bool:
    base_lines, head_lines = base_text.splitlines(), head_text.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base_lines, head_lines, autojunk=False).get_opcodes():
        if tag in ("delete", "replace") and any(line.lstrip().startswith("#") for line in base_lines[i1:i2]):
            return False
        if tag in ("insert", "replace"):
            added = head_lines[j1:j2]
            if any(x.lstrip().startswith("#") for x in added) and all(x.lstrip().startswith("#") for x in added):
                return False
    return True


def exempt(m: dict, file: str, base_text: str | None, head_text: str | None) -> bool:
    """True only for a pure tightening append (a demotions row with amendment_log rows, a boundary_patterns entry)."""
    if base_text is None or head_text is None:
        return False
    base, head = _load_yaml(base_text), _load_yaml(head_text)
    if base is None or head is None or not _structure_exempt(file, base, head):
        return False
    return _comments_untouched(base_text, head_text)


def in_surfaces(m: dict, file: str) -> bool:
    notice = m.get("notice") or {}
    surfaces = tuple(s for s in _as_list(notice.get("surfaces")) if isinstance(s, str))
    suffixes = tuple(s for s in _as_list(notice.get("wording_suffixes")) if isinstance(s, str))
    return bool(surfaces and suffixes) and file.startswith(surfaces) and file.endswith(suffixes)


def notice_changes(m: dict, root: Path, base: str, head: str | None, changed: list[str]) -> list[str]:
    gated = {f for f, _ in gated_changes(m, root, base, head, changed)}
    return sorted(f for f in changed if f not in gated and not _is_gated_path(m, f) and in_surfaces(m, f))


def bound_files(root: Path, ref: str, slug: str) -> set[str]:
    data = _load_yaml(text_at(root, ref, f"docs/plans/approved/{slug}.yaml"))
    blocks = _as_list(data.get("blocks")) if data else []
    return {b["file"] for b in blocks if isinstance(b, dict) and isinstance(b.get("file"), str)}


def declared_plan(root: Path, sha: str) -> str | None:
    names = _git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
    for rel in (names or "").splitlines():
        found = _PLAN.match(rel)
        if not found:
            continue
        after = _load_yaml(text_at(root, sha, rel))
        before = _load_yaml(text_at(root, f"{sha}^", rel))
        if (
            after
            and after.get("implementation_declared") is True
            and not (before and before.get("implementation_declared") is True)
        ):
            return found.group(1)
    return None
