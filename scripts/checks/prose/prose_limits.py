"""Ambient agent-instruction prose byte-budget ratchet (Decision 43/127; realizes rec-432/433).

Gates the permanent prose surface classes over Decision 127's taxonomy: S1 (root ambient
load-set aggregate: CLAUDE.md + transitive @-imports), S2 (each non-root **/CLAUDE.md), S4 (each
.claude/skills/*/SKILL.md entry file), and S8 (docs/PROJECT_CONTEXT.md). S3
(.claude/commands/*.md) is measured-only (Decision 127 strongest-layer surface) and carries no
budget entry here.

Byte measurement is entirely delegated to scripts.preflight.prose_context.measure_prose_context()
-- this module never re-implements the read/measure step, so this gate and the preflight advisory
report can never silently diverge on what "current bytes" means for a surface.

Unlike the SLOC gate's relief valve (decompose into a facade package), fragmenting ambient prose
across more @-imported files does NOT reduce the ambient load an agent must read -- it spreads the
same bytes across more files (Decision 114/110 anti-fragmentation). FAIL messages here therefore
never suggest that; they name three different relief valves instead: relocate the content to L2
(docs/PROJECT_CONTEXT.md) or a docs/contracts/*.yaml contract (Decision 86 routing), defer detail
to an uncapped auxiliary file the surface points at rather than inlines, or take a loud,
Decision-cited budget raise (Decision 128's marker mechanism, reused here for the raise-gate
mechanism only -- never for its decompose-by-default relief valve, which is SLOC-specific). Every
OVER-budget and unregistered message carries them; an under-budget message instead names the exact
reseed line, since the ratchet only goes down.
"""

from __future__ import annotations

from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.preflight.prose_context import measure_prose_context

_BUDGETS_REL_PATH = "config/prose_budgets.yaml"

# Deliberately never names "split" / "decompose" -- see module docstring. Every OVER-budget and
# unregistered FAIL message below is built with this text so all three relief valves are always present together.
_RELIEF_VALVE_TEXT = (
    "Relief valves: relocate the content to docs/PROJECT_CONTEXT.md (L2) or a "
    "docs/contracts/*.yaml contract (Decision 86); defer the detail to an uncapped auxiliary "
    "file this surface points at instead of inlining it; or take a loud, Decision-cited budget "
    "raise (add `# raise-approved: dec-NNN <reason>` on the entry, Decision 128 marker "
    "mechanism)."
)


def _load_prose_budgets() -> dict[str, dict[str, int]]:
    """Load config/prose_budgets.yaml; return {} if absent or empty.

    Returns the nested {surface: {key: budget}} shape as-is (S1's single "root_ambient_load_set"
    key plus S2/S4/S8's per-path keys) -- unlike the flat SLOC registry, this one stays nested.
    """
    budget_path = _common.ROOT / _BUDGETS_REL_PATH
    if not budget_path.exists():
        return {}
    data = yaml.safe_load(budget_path.read_text(encoding="utf-8")) or {}
    return {k: (v or {}) for k, v in data.items()}


def _check_entry(
    surface: str,
    path: str,
    current_bytes: int,
    budget: int | None,
    errors: list[str],
) -> None:
    """Compare one measured (surface, path) entry against its registered budget.

    Shared by the S1 aggregate call site and every S2/S4/S8 per-file call site so the
    unregistered / over-budget / under-budget branch logic and relief-valve wording live in
    exactly one place.
    """
    if budget is None:
        errors.append(
            f"{surface} {path}: {current_bytes} bytes has no entry in {_BUDGETS_REL_PATH}. "
            f"Register it at its current size, or address the surface first. {_RELIEF_VALVE_TEXT}"
        )
    elif current_bytes > budget:
        errors.append(f"{surface} {path}: {current_bytes} bytes exceeds budget {budget}. {_RELIEF_VALVE_TEXT}")
    elif current_bytes < budget:
        errors.append(
            f"{surface} {path}: {current_bytes} bytes below budget {budget}; lower the entry to {current_bytes} in "
            f"{_BUDGETS_REL_PATH} in this PR (the ratchet only goes down; implement-scope-boundary's "
            "prose_budgets_ratchet_down row sanctions the edit)."
        )


@registry.register("validate_prose_limits", owner="platform")
def validate_prose_limits(failed: list[str]) -> None:
    """Enforce the Decision 43/127 ambient-prose byte-budget ratchet (S1/S2/S4/S8)."""
    print("\n=== Prose size budgets (Decision 43/127) ===")
    budgets = _load_prose_budgets()
    report: dict[str, Any] = measure_prose_context()
    errors: list[str] = []
    examined = 1

    s1_data = report.get("S1") or {}
    s1_budgets = budgets.get("S1") or {}
    _check_entry(
        "S1",
        "root_ambient_load_set",
        s1_data.get("prose_bytes", 0),
        s1_budgets.get("root_ambient_load_set"),
        errors,
    )
    # Split-proof dedup (Decision 114/110): a file counted in the S1 aggregate is never also
    # gated per-file in S2 -- keeps the two passes from ever contradicting each other.
    s1_member_paths = {f.get("path") for f in s1_data.get("files", [])}

    for surface in ("S2", "S4"):
        surface_budgets = budgets.get(surface) or {}
        surface_data = report.get(surface) or {}
        for f in surface_data.get("files", []):
            path = f.get("path")
            if path in s1_member_paths:
                continue
            examined += 1
            _check_entry(surface, path, f.get("prose_bytes", 0), surface_budgets.get(path), errors)

    s8_budgets = budgets.get("S8") or {}
    s8_data = report.get("S8") or {}
    for f in s8_data.get("files", []):
        path = f.get("path")
        examined += 1
        _check_entry("S8", path, f.get("prose_bytes", 0), s8_budgets.get(path), errors)

    registry.examined(examined, unit="prose_surfaces")
    if errors:
        print("Prose budget violations:")
        for e in errors:
            print(f"  - {e}")
        failed.append("Prose size budgets (Decision 43/127)")
    else:
        print("All gated prose surfaces within budget.")
