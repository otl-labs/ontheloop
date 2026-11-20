"""Delegated-key change rules (Decision 276 clauses 1 and 3).

A decision entry may delegate values to a contract or config key through its envelope `delegates` field. Delegations
are read from origin/main, never from head, so an in-place envelope edit cannot move them. For every base delegation:
(c) the delegation itself may not change at head, then, when the delegated key's value changed, (a) a new mechanism
entry (without its own delegates, as decision-entry.yaml field_shapes.delegates states) may not cite the key, and (b)
the head value must sit inside the base bounds. A new kind-direction entry that amends or supersedes the delegating
Decision waives all three.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from scripts.checks import _common, registry
from scripts.checks.decisions import consolidation, entry_measure
from scripts.checks.decisions._baseline import BaselineReaderFn, base_file_text, baseline_decision_numbers
from scripts.checks.decisions.envelope_rules import amended_numbers, dotted_value, split_delegated_key
from scripts.decisions_md import extract_entry_envelope, extract_superseded_by, iter_decision_sections

BaseTextReaderFn = Callable[[Path, str], Optional[str]]
_FILES = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")
_UNIT = "base_delegations"
_MANIFEST_UNIT = "consolidation_units"


def _manifest_unit(count: int) -> str:
    return _MANIFEST_UNIT if count else _UNIT


def _entries(texts: dict[str, str | None]) -> dict[int, str]:
    found: dict[int, str] = {}
    for text in texts.values():
        for match, block in iter_decision_sections(text or ""):
            found[int(match.group(1))] = block
    return found


def _delegations(entries: dict[int, str]) -> dict[int, list[Any]]:
    found: dict[int, list[Any]] = {}
    for number, block in entries.items():
        delegates = (extract_entry_envelope(block) or {}).get("delegates")
        if isinstance(delegates, list) and delegates:
            found[number] = delegates
    return found


def _parsed(text: str | None) -> Any:
    try:
        return yaml.safe_load(text) if text is not None else None
    except yaml.YAMLError:
        return None


def _outside_bounds(bounds: Any, value: Any) -> bool:
    keys = set(bounds) if isinstance(bounds, dict) else set()
    if keys == {"min", "max"}:
        numbers = (value, bounds["min"], bounds["max"])
        numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in numbers)
        return not numeric or not bounds["min"] <= value <= bounds["max"]
    if keys == {"one_of"}:
        return value not in bounds["one_of"]
    return False


def _names_key(text: str, path: str, dotted: str) -> bool:
    parts = dotted.split(".")
    tail = ".".join(parts[-2:])
    return Path(path).name in text and (dotted in text or tail in text)


def _outside_envelope(block: str) -> str:
    span = entry_measure.envelope_span(block)
    return block if span is None else block[: span[0]] + block[span[1] :]


def _is_mechanism_without_delegates(block: str) -> bool:
    envelope = extract_entry_envelope(block) or {}
    return (envelope.get("kind") or {}).get("value") == "mechanism" and not envelope.get("delegates")


def _waivers(new: dict[int, str]) -> set[int]:
    waived: set[int] = set()
    for block in new.values():
        envelope = extract_entry_envelope(block) or {}
        if (envelope.get("kind") or {}).get("value") == "direction":
            waived |= amended_numbers(envelope)
    return waived


def _superseder_lists(base: dict[int, str], head_block: str, number: int) -> bool:
    target = extract_superseded_by(head_block).removeprefix("dec-")
    if not target.isdigit() or int(target) not in base:
        return False
    return number in amended_numbers(extract_entry_envelope(base[int(target)]) or {})


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


def _delegation_problems(
    number: int,
    delegates: list[Any],
    base: dict[int, str],
    head: dict[int, str],
    new: dict[int, str],
    root: Path,
    base_text_reader: BaseTextReaderFn,
) -> list[str]:
    """Rules (c), (a) and (b) for one base delegation that no new direction entry amends."""
    head_block = head.get(number)
    head_delegates = (extract_entry_envelope(head_block) or {}).get("delegates") if head_block else None
    problems: list[str] = []
    if head_delegates != delegates and not (head_block and _superseder_lists(base, head_block, number)):
        problems.append(
            f"Decision {number}'s delegates changed or were removed at head; only a new direction entry amending it may"
        )
    for item in delegates:
        split = split_delegated_key(item.get("key")) if isinstance(item, dict) else None
        if split is None:
            continue
        base_found, base_value = dotted_value(_parsed(base_text_reader(root, split[0])), split[1])
        head_found, head_value = dotted_value(_parsed(_read(root / split[0])), split[1])
        if not head_found:
            print(f"  WARN: Decision {number} delegates {item['key']}, which no longer resolves at head.")
        elif not (base_found and base_value == head_value):
            problems += _changed_key_problems(number, item, split, head_value, new)
    return problems


def _changed_key_problems(number: int, item: dict, split: tuple[str, str], head_value: Any, new: dict[int, str]) -> list[str]:
    problems = [
        f"Decision {n} re-sets {item['key']}, which Decision {number} delegates: change the key by its "
        f"{item.get('change_rule')} rule, not a new mechanism entry (Decision 276 clause 3)"
        for n, block in sorted(new.items())
        if _is_mechanism_without_delegates(block) and _names_key(_outside_envelope(block), *split)
    ]
    if _outside_bounds(item.get("bounds"), head_value):
        problems.append(
            f"{item['key']} is {head_value!r} at head, outside Decision {number}'s bounds {item.get('bounds')}: "
            "a value outside them needs a new direction entry amending it (Decision 276 clause 1)"
        )
    return problems


@registry.register("validate_delegated_key_changes", owner="platform")
def validate_delegated_key_changes(
    failed: list[str],
    root: Path | None = None,
    baseline_reader: BaselineReaderFn | None = None,
    base_text_reader: BaseTextReaderFn | None = None,
) -> None:
    """Fail a changed delegated key that a new mechanism entry re-sets, leaves its bounds, or whose delegation moved."""
    print("\n=== Delegated-key change rules (Decision 276) ===")
    using_default_root = root is None
    root = root if root is not None else _common.ROOT
    baseline_reader = baseline_reader or baseline_decision_numbers
    base_text_reader = base_text_reader or base_file_text

    manifest_failures, manifest_count = consolidation.scan(root, base_text_reader)
    for problem in manifest_failures:
        print(f"  FAIL: {problem}")
    if manifest_failures:
        failed.append("Consolidation manifests")
    elif manifest_count:
        print(f"  PASS: {manifest_count} consolidation unit(s) resolve.")

    if using_default_root and not any(f in _FILES or f.endswith((".yaml", ".yml")) for f in _common.get_changed_files(root)):
        registry.examined(manifest_count, unit=_manifest_unit(manifest_count))
        return
    base_texts = {rel: base_text_reader(root, rel) for rel in _FILES}
    if not any("delegates:" in (text or "") for text in base_texts.values()):
        registry.examined(manifest_count, unit=_manifest_unit(manifest_count))
        return
    baseline_numbers = baseline_reader(root)
    if baseline_numbers is None:
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI).")
        registry.skipped("origin/main unreachable")
        return

    base = _entries(base_texts)
    head = _entries({rel: _read(root / rel) for rel in _FILES})
    new = {n: b for n, b in head.items() if n not in baseline_numbers}
    waived = _waivers(new)
    problems: list[str] = []
    compared = 0
    for number, delegates in sorted(_delegations(base).items()):
        compared += len(delegates)
        if number not in waived:
            problems += _delegation_problems(number, delegates, base, head, new, root, base_text_reader)
    for problem in problems:
        print(f"  FAIL: {problem}")
    if problems:
        failed.append("Delegated-key changes")
    registry.examined(compared, unit=_UNIT)
