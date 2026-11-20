"""Structural-size budget-raise guardrail (Decision 166) -- the SIXTH CONSUMER of
scripts/checks/_marker_guard.py, never a sixth copy.

config/structural_size_budgets.yaml mixes an ordered `classes:` table (carrying scalar
keys like `limit`/`max_line_chars`) with two flat per-file rosters (`budgets:` and
`long_line_budgets:`), so this module declares TWO section-scoped RegistrySpecs over the
SAME rel_path -- THE CRITICAL SEAM: make_section_extractor anchors each spec on its own
top-level section and ends it at the next column-0 non-comment line, so neither
extraction ever captures a `classes:` block scalar as a budget entry (a flat extractor
would). Contains no local Decision-header regex, no local base reader, and no local
entry parser -- scripts.checks._marker_guard owns all of that.

Also carries frozen_base_violations() -- the BASE-AWARE half of the SGE-05 escape legs. It
lives here rather than in size_limits.py because all three of its legs read frozen-ness
from the BASE text, and this module is the one that already owns the base-versus-current
diff and its injection seam. A git-less helper structurally cannot see a DELETION, which is
exactly what the measured escapes exploit.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.structural import _byte_raises

_REGISTRY_REL_PATH = "config/structural_size_budgets.yaml"

_BUDGET_SPEC = _marker_guard.RegistrySpec(
    rel_path=_REGISTRY_REL_PATH,
    token="raise-approved",
    gated_direction="up",
    extractor=_marker_guard.make_section_extractor("budgets", token="raise-approved", value_type=int),
    gates_new_entry=lambda _value: True,
    label="Structural-size budget-raise guardrail (Decision 166, budgets)",
)

_LONG_LINE_SPEC = _marker_guard.RegistrySpec(
    rel_path=_REGISTRY_REL_PATH,
    token="raise-approved",
    gated_direction="up",
    extractor=_marker_guard.make_section_extractor("long_line_budgets", token="raise-approved", value_type=int),
    gates_new_entry=lambda _value: True,
    label="Structural-size budget-raise guardrail (Decision 166, long_line_budgets)",
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sections(text: str) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    """(budgets, budget_notes, deferred paths) parsed from one registry text."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        return {}, {}, set()
    deferred: set[str] = set()
    for row in data.get("classes") or []:
        if isinstance(row, dict):
            deferred |= set(_mapping(row.get("defer_to_incumbent")))
    return _mapping(data.get("budgets")), _mapping(data.get("budget_notes")), deferred


def _frozen(notes: dict[str, Any]) -> set[str]:
    return {key for key, note in notes.items() if isinstance(note, dict) and note.get("frozen") is True}


def _numeric(value: object) -> float | None:
    """A comparable budget value, or None for a missing entry or a non-numeric one -- so the
    direction leg never compares a string (or a bool) against an integer."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def frozen_base_violations(base_text: str, current_text: str) -> list[str]:
    """E4's direction half: three legs, every one reading frozen-ness from BASE_TEXT.

    Leg 1 -- an entry frozen in base whose value INCREASED, regardless of any marker and
    regardless of what current says about frozen-ness (closing both the marker-authorizes-
    every-future-raise defect and the one-commit unfreeze-and-raise). Leg 2 -- a path frozen
    in base and not frozen in current WHILE its budgets entry survives (closing the two-PR
    unfreeze-then-raise, which leg 1 cannot see because by the second PR the base is already
    innocent). Leg 3 -- a path frozen in base that is NEWLY deferred in current (closing the
    measured deferral bypass, which deletes the note in the same commit).

    Direction, never equality: a SHRINK passes, because compaction is this class's own
    first-listed relief valve and the engine carries no --update-sloc-budgets equivalent to
    absorb the cost of re-pinning. Removing the note AND the entry together is the prescribed
    retirement and stays clean.
    """
    try:
        base_budgets, base_notes, base_deferred = _sections(base_text)
        cur_budgets, cur_notes, cur_deferred = _sections(current_text)
    except yaml.YAMLError as exc:
        return [f"E4: {_REGISTRY_REL_PATH} did not parse as YAML ({type(exc).__name__}) -- the frozen legs could not run."]

    violations: list[str] = []
    for key in sorted(_frozen(base_notes)):
        base_value = base_budgets.get(key)
        current_value = cur_budgets.get(key)
        base_number = _numeric(base_value)
        current_number = _numeric(current_value)
        if base_number is not None and current_number is not None and current_number > base_number:
            violations.append(
                f"E4: `{key}` is frozen in the base config -- {base_value} -> {current_value} is an INCREASE. "
                "A committed raise-approved marker does not authorize it; the freeze is a direction rule, and "
                "a shrink or a full retirement are the only movements available."
            )
        if key not in _frozen(cur_notes) and key in cur_budgets:
            violations.append(
                f"E4: `{key}` is frozen in the base config but not in this change, while its `budgets:` entry "
                "survives -- un-freezing is itself the escape. Retire the note and the entry together, or leave "
                "the freeze in place."
            )
        if key in cur_deferred and key not in base_deferred:
            violations.append(
                f"E4: `{key}` is frozen in the base config and is NEWLY deferred to an incumbent -- a deferred "
                "path is dropped before measurement, so this un-governs the file the freeze exists to hold."
            )
    return violations


def _frozen_leg(base_text: Optional[str], current_text: str) -> list[str]:
    """Run the frozen legs on the SAME base text check_diff saw, preserving its
    base-unreachable contract exactly: a None base is a SKIP that evaluates NO frozen leg.
    Never `base_text or ""` -- an empty base text yields an empty base map, which makes
    every frozen leg vacuous and reports a clean pass on an unreachable base, indistinguishable
    in CI from a genuine pass (the masking shape Decision 55 forbids).
    """
    if base_text is None:
        return []
    return frozen_base_violations(base_text, current_text)


@registry.register("validate_structural_size_budget_raises", owner="platform")
def validate_structural_size_budget_raises(
    failed: list[str],
    base_reader: Optional[Callable[[str], Optional[str]]] = None,
) -> None:
    """Fail on an unauthorized config/structural_size_budgets.yaml `budgets:` or
    `long_line_budgets:` increase, new >limit registration, or a currently-committed
    marker that no longer authorizes its entry -- over BOTH sections, each via its own
    section-scoped extractor.

    Declares skipped when the registry file is absent or origin/main is unreachable (the diff
    legs' base is unavailable), else examined over the current entries of both sections (unit
    "entries"), each of which a diff leg judged against its base value.
    """
    print(f"\n=== {_BUDGET_SPEC.label} ===")
    current_path = _common.ROOT / _REGISTRY_REL_PATH
    if not current_path.exists():
        print(f"  {_REGISTRY_REL_PATH} not found -- nothing to check.")
        registry.skipped(f"{_REGISTRY_REL_PATH} not found")
        return

    base_text = (base_reader or _marker_guard.default_base_reader)(_REGISTRY_REL_PATH)
    current_text = current_path.read_text(encoding="utf-8")
    violations = (
        _marker_guard.check_diff(_BUDGET_SPEC, base_reader=lambda _rel: base_text)
        + _marker_guard.check_present_markers(_BUDGET_SPEC)
        + _marker_guard.check_diff(_LONG_LINE_SPEC, base_reader=lambda _rel: base_text)
        + _marker_guard.check_present_markers(_LONG_LINE_SPEC)
        + _frozen_leg(base_text, current_text)
        + _byte_raises.violations(base_text, base_reader)
    )

    if violations:
        print("Structural-size budget-raise violations:")
        for v in violations:
            print(f"  - {v}")
        failed.append(_BUDGET_SPEC.label)
    else:
        print("No unauthorized structural-size budget raises.")

    if base_text is None:
        registry.skipped("origin/main unreachable")
    else:
        examined = (
            len(_BUDGET_SPEC.extractor(current_text))
            + len(_LONG_LINE_SPEC.extractor(current_text))
            + _byte_raises.entry_count(current_text)
        )
        registry.examined(examined, unit="entries")
