"""Entry literals for the decisions domain's registered checks (Decision 169, amends Decision 104).

Bare string-literal module=/attr= pairs only -- see docs/contracts/check-manifest.yaml. Aggregated
by scripts/checks/registry.py; never imported by scripts/validate.py directly.

Every gated Entry's pre_globs must cover the check's whole transitive first-party import closure,
which is why each one carries scripts/checks/_common.py and scripts/checks/registry.py: both are
module-scope imports of every check here AND are consumed at call time (_common.ROOT / diff
helpers, registry.examined()/skipped() accounting), so a semantic change to either can redden a
check body without any domain file in the diff. The rest of the scripts/checks/ spine
(_schema.py, sibling domains' _manifest.py, the package __init__) is deliberately NOT globbed: a
break there fails registry -> scripts/validate.py at IMPORT time, so the whole --pre run crashes
red before any gate is consulted.
"""

from __future__ import annotations

from scripts.checks._schema import Entry

ENTRIES: tuple[Entry, ...] = (
    Entry(
        name="validate_decisions_size",
        module="scripts.checks.decisions.validate_decisions_size",
        attr="validate_decisions_size",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_decisions_index_freshness",
        module="scripts.checks.decisions.validate_decisions_index_freshness",
        attr="validate_decisions_index_freshness",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_decision_entry_conformance",
        module="scripts.checks.decisions.validate_decision_entry_conformance",
        attr="validate_decision_entry_conformance",
        pre=True,
        pre_globs=(
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "docs/contracts/decision-entry.yaml",
            "scripts/decisions_md.py",
            "scripts/checks/decisions/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_supersession_annotations",
        module="scripts.checks.decisions.validate_supersession_annotations",
        attr="validate_supersession_annotations",
        # Promoted into --pre; this Entry is the authority.
        pre=True,
        pre_globs=(
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "config/decision_supersession_waivers.yaml",
            "scripts/decisions_md.py",
            "scripts/checks/decisions/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_decision_currency",
        module="scripts.checks.decisions.validate_decision_currency",
        attr="validate_decision_currency",
        pre=True,
        pre_globs=(
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "docs/decisions-index.json",
            "docs/contracts/decision-entry.yaml",
            ".claude/skills/decision-scout/SKILL.md",
            "scripts/decisions_md.py",
            "scripts/checks/decisions/validate_decision_currency.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
)
