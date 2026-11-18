"""Evaluator coverage for Class D contracts (rec-5263, CFR-08): liveness of an agent_surface, trigger text,
asserted keys and the per-contract Coverage line.

Stdlib + yaml only; no import of _population (no cycle); nothing here raises at import. Rules live in
docs/contracts/contract-population.yaml evaluator_kinds and evaluator_coverage.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
from pathlib import Path
from typing import Any, Callable, Iterable

ENVELOPE_KEYS = frozenset(
    {"contract", "amendment_log", "version", "contract_version", "status", "ratified_at", "ratified_via"}
)
MAX_CHAIN_DEPTH = 1
_ARCHITECTURE = "instruction-architecture.yaml"
_POPULATION = "contract-population.yaml"
_CONTRACTS = "docs/contracts/"


def body_keys(data: Any) -> list[str]:
    """Sorted top-level keys of a contract file that are not envelope keys."""
    if not isinstance(data, dict):
        return []
    return sorted(str(k) for k in data if k not in ENVELOPE_KEYS)


def _load_yaml(path: Path) -> tuple[Any, str | None]:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f"{path.name}: {exc}"


@dataclasses.dataclass(frozen=True)
class LiveTable:
    globs: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()


def _str_list(value: Any, label: str) -> tuple[list[str], str | None]:
    if value is None:
        return [], None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return [], f"{label} is present and not a list of strings"
    return list(value), None


def load_live_table_from(path: Path) -> LiveTable | str:
    """The live table of an instruction-architecture file; an error string when it is missing or malformed."""
    data, err = _load_yaml(path)
    if err:
        return f"live table unreadable: {err}"
    if not isinstance(data, dict):
        return f"live table unreadable: {path.name} is not a mapping"
    globs: list[str] = []
    layers = data.get("layers")
    if layers is not None and not isinstance(layers, list):
        return "live table: layers is present and not a list"
    for layer in layers or []:
        found, err = _str_list(
            layer.get("content_locations") if isinstance(layer, dict) else None, "layers[].content_locations"
        )
        if err:
            return f"live table: {err}"
        globs.extend(found)
    consumers = data.get("consumers")
    if consumers is not None and not isinstance(consumers, dict):
        return "live table: consumers is present and not a mapping"
    for consumer in (consumers or {}).values():
        found, err = _str_list(consumer.get("loads") if isinstance(consumer, dict) else None, "consumers[].loads")
        if err:
            return f"live table: {err}"
        globs.extend(found)
    retired_block = data.get("retired_surfaces")
    if retired_block is not None and not isinstance(retired_block, dict):
        return "live table: retired_surfaces is present and not a mapping"
    retired, err = _str_list((retired_block or {}).get("paths"), "retired_surfaces.paths")
    if err:
        return f"live table: {err}"
    return LiveTable(tuple(globs), tuple(retired))


def load_live_table(root: Path) -> LiveTable | str:
    return load_live_table_from(root / "docs" / "contracts" / _ARCHITECTURE)


def directly_live(path: str, table: LiveTable) -> bool:
    return path not in table.retired and any(fnmatch.fnmatchcase(path, g) for g in table.globs)


def _class_d_surface(root: Path, rel: str) -> str | None:
    """The agent_surface of the Class D contract at `rel`, or None."""
    data, err = _load_yaml(root / rel)
    contract = data.get("contract") if isinstance(data, dict) else None
    if err or not isinstance(contract, dict) or contract.get("class") != "D":
        return None
    evaluator = contract.get("evaluator")
    surface = evaluator.get("agent_surface") if isinstance(evaluator, dict) else None
    return surface if isinstance(surface, str) else None


def surface_liveness(contract_basename: str, surface: str, root: Path, table: LiveTable) -> tuple[bool, str]:
    if directly_live(surface, table):
        return True, f"{surface} is live"
    if surface.startswith(_CONTRACTS) and surface.endswith(".yaml"):
        via = surface[len(_CONTRACTS) :]
        onward = _class_d_surface(root, surface)
        if onward is not None:
            loop = f"loop: {contract_basename} -> {via} -> {contract_basename}"
            if via == contract_basename or onward == f"{_CONTRACTS}{contract_basename}":
                return False, loop
            if directly_live(onward, table):
                return True, f"live via {via}"
            return False, f"chain deeper than one contract: {contract_basename} -> {via} -> {onward}"
    return False, f"{surface} is not in instruction-architecture.yaml's live table"


def _description(text: str) -> str | None:
    import yaml

    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    desc = meta.get("description") if isinstance(meta, dict) else None
    return desc if isinstance(desc, str) else None


def retired_marker_errors(root: Path, table: LiveTable) -> list[str]:
    errors: list[str] = []
    candidates = [*sorted(root.glob(".claude/skills/*/SKILL.md")), *sorted(root.glob(".claude/commands/*.md"))]
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        try:
            desc = _description(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if desc is not None and desc.strip().startswith("Being retired") and rel not in table.retired:
            errors.append(f"{rel} is marked 'Being retired' but is not in retired_surfaces.paths")
    for rel in table.retired:
        if not (root / rel).exists():
            errors.append(f"retired_surfaces.paths names {rel}, which does not exist")
    return errors


@dataclasses.dataclass(frozen=True)
class CoverageRow:
    triggers: tuple[str, ...] = ()
    asserts: tuple[str, ...] = ()


def load_coverage_from(path: Path) -> dict[str, CoverageRow] | str:
    """evaluator_coverage rows of a contract-population file; {} when absent, an error string when malformed."""
    data, err = _load_yaml(path)
    if err:
        return f"coverage table unreadable: {err}"
    table = data.get("evaluator_coverage") if isinstance(data, dict) else None
    if table is None:
        return {}
    if not isinstance(table, dict):
        return "coverage table: evaluator_coverage is not a mapping"
    rows: dict[str, CoverageRow] = {}
    for name, row in table.items():
        if not isinstance(row, dict) or not set(row) <= {"triggers", "asserts"}:
            return f"coverage table: {name} must be a mapping with only triggers or asserts"
        lists = {}
        for key in ("triggers", "asserts"):
            if key in row:
                value = row[key]
                if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                    return f"coverage table: {name}.{key} must be a non-empty list of strings"
                lists[key] = tuple(value)
        if len(lists) != 1:
            return f"coverage table: {name} must list exactly one of triggers or asserts"
        rows[str(name)] = CoverageRow(**lists)
    return rows


def load_coverage(root: Path) -> dict[str, CoverageRow] | str:
    return load_coverage_from(root / "docs" / "contracts" / _POPULATION)


def coverage_table_errors(rows: dict[str, CoverageRow], kinds: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, row in sorted(rows.items()):
        kind = kinds.get(name)
        if kind is None:
            errors.append(f"evaluator_coverage row {name} names no valid Class D contract")
        elif row.triggers and kind != "agent_surface":
            errors.append(f"evaluator_coverage row {name} lists triggers but its evaluator is a check")
        elif row.asserts and kind != "check":
            errors.append(f"evaluator_coverage row {name} lists asserts but its evaluator is an agent_surface")
    return errors


def _norm(text: str) -> str:
    return " ".join(text.split())


def triggers_resolve(surface_text: str, basename: str, triggers: Iterable[str]) -> tuple[bool, str]:
    text = _norm(surface_text)
    for trigger in triggers:
        norm = _norm(trigger)
        if basename not in norm:
            return False, f"trigger {trigger!r} does not contain {basename!r}"
        if norm not in text:
            return False, f"trigger {trigger!r} does not occur in the surface"
    return True, "every trigger occurs in the surface and names the contract"


def instructed_keys(basename: str, triggers: Iterable[str], body: Iterable[str]) -> tuple[set[str], list[str]]:
    body_set = set(body)
    keys: set[str] = set()
    errors: list[str] = []
    pattern = re.compile(re.escape(basename) + r"(?:#([A-Za-z_][A-Za-z0-9_]*))?")
    for trigger in triggers:
        for match in pattern.finditer(trigger):
            key = match.group(1)
            if key is None:
                keys |= body_set
            elif key in body_set:
                keys.add(key)
            else:
                errors.append(f"anchor names no top-level key: {basename}#{key}")
    return keys, errors


def asserts_resolve(
    basename: str,
    asserts: Iterable[str],
    body: Iterable[str],
    module_paths: Iterable[Path],
    contains_literal: Callable[[Path, str], bool],
) -> tuple[bool, str]:
    body_set = set(body)
    paths = list(module_paths)
    for key in asserts:
        if key not in body_set:
            return False, f"asserted key {key!r} is not a body key of {basename}"
        if not any(contains_literal(p, key) for p in paths):
            return False, f"asserted key {key!r} has no literal in the check or its one-hop imports"
    return True, "every asserted key has a literal in the check"


def coverage_line(basename: str, body: Iterable[str], asserted: Iterable[str], instructed: Iterable[str]) -> str:
    body_set = set(body)
    a = body_set & set(asserted)
    i = body_set & set(instructed)

    def fmt(keys: Iterable[str]) -> str:
        return "[" + ", ".join(sorted(keys)) + "]"

    return f"Coverage: {basename} asserted={fmt(a)} instructed={fmt(i)} neither={fmt(body_set - a - i)}"
