"""Byte leg of the structural-size engine (Decision 275, amends Decision 166).

Pure functions over the parsed registry and file sizes, called branch-free from size_limits.py so that
test__byte_budget.py is the mapped test for every line here. A class row carrying `max_bytes` has a per-file
limit in raw UTF-8 bytes; the `byte_budgets:` roster grandfathers named files (keyed by bare file name) above
it. Field semantics live in config/structural_size_budgets.yaml; the permitted maximum below is a reviewed code
edit, never a config drop-in (the _PERMITTED_MAXIMA shape).
"""

from __future__ import annotations

from pathlib import Path

_PERMITTED_MAX_BYTES: dict[str, int] = {"contracts": 49152}
_ROSTER_DIR = "docs/contracts/"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def byte_limit(rel: str, class_row: dict, reg: dict) -> int | None:
    """The file's byte limit: its roster entry (exact depth-1 docs/contracts path), else the row's max_bytes."""
    row_limit = class_row.get("max_bytes")
    if not isinstance(row_limit, int) or isinstance(row_limit, bool):
        return None
    roster = reg.get("byte_budgets") or {}
    if rel.startswith(_ROSTER_DIR) and "/" not in rel[len(_ROSTER_DIR) :]:
        entry = roster.get(rel[len(_ROSTER_DIR) :])
        if isinstance(entry, int) and not isinstance(entry, bool):
            return entry
    return row_limit


def file_violations(rel: str, size: int, class_row: dict, reg: dict) -> list[str]:
    """One message when `size` bytes exceed the file's byte limit, naming the class relief valves."""
    limit = byte_limit(rel, class_row, reg)
    if limit is None or size <= limit:
        return []
    slug, valves = class_row.get("slug"), class_row.get("relief_valve", "")
    return [f"{rel}: {size} bytes exceeds byte limit {limit} (class: {slug}). Relief valves: {valves}"]


def e2_violations(rows: list[dict]) -> list[str]:
    """E2 byte leg: a row's max_bytes must be an integer, on a slug the code map permits, within the maximum."""
    violations: list[str] = []
    for row in rows:
        if "max_bytes" not in row:
            continue
        slug = str(row.get("slug"))
        value = row.get("max_bytes")
        ceiling = _PERMITTED_MAX_BYTES.get(slug)
        if not _is_int(value):
            violations.append(f"E2 (max_bytes): class row `{slug}` declares a non-integer max_bytes {value!r}.")
        elif ceiling is None:
            violations.append(
                f"E2 (max_bytes): class row `{slug}` declares max_bytes {value}, but no byte limit is permitted for "
                "that slug. A byte limit is granted per-slug in scripts/checks/structural/_byte_budget.py's "
                "_PERMITTED_MAX_BYTES, never by editing this config."
            )
        elif isinstance(value, int) and value > ceiling:
            violations.append(
                f"E2 (max_bytes): class row `{slug}` declares max_bytes {value}, above the permitted maximum "
                f"{ceiling}. A higher limit is a code edit in _PERMITTED_MAX_BYTES and a new Decision."
            )
    return violations


def roster_violations(reg: dict, root: Path) -> list[str]:
    """Stale-key leg: every byte_budgets key must name an existing depth-1 docs/contracts file."""
    roster = reg.get("byte_budgets") or {}
    if not isinstance(roster, dict):
        return [f"E2 (byte_budgets): byte_budgets must be a mapping of file name -> bytes, got {type(roster).__name__}."]
    violations: list[str] = []
    for key, value in sorted(roster.items()):
        if "/" in str(key) or not (root / _ROSTER_DIR / str(key)).is_file():
            violations.append(
                f"E2 (byte_budgets): `{key}` names no depth-1 {_ROSTER_DIR} file -- remove the stale roster entry."
            )
        if not _is_int(value):
            violations.append(f"E2 (byte_budgets): `{key}` carries a non-integer budget {value!r}.")
    return violations
