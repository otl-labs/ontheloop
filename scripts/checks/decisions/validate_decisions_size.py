"""DECISIONS.md / DECISIONS_ARCHIVE.md per-entry authoring-size governance (Decision 167 clause 3;
the module's three mechanical stock ceilings -- the live '## Decision' header count, the
live-byte-only ceiling, and the live+archive combined byte ceiling -- are RETIRED by Decision 179).

Bounded decision-scout retrieval (PLAN-decision-scout-bounded-retrieval) means no consumer reads
the live corpus wholesale anymore, so the stock guards that sized that read no longer have a
referent; Decision 150's significance bar is the retained lever on entry count and triage quality.
This check survives as the per-NEW-entry authoring size norm enforcer
(docs/contracts/decision-entry.yaml size_governance.per_entry_size_norm, Decision 167 clause 3,
rec-2934): a new-in-diff decision entry over the cap HARD-FAILS in the --pre tier. Historical
entries are never measured by that sub-check (forward-only, mirrors
validate_decision_entry_conformance's own new-vs-baseline scope). Since this check is registered
UNGATED in the --pre tier (unlike the glob-gated conformance check), the per-entry sub-check's own
(git-cost) baseline read is skipped whenever a non-default `root` is injected for testing --
production calls (the default root) pay it only when a DECISIONS file changed in this diff.

rec-3243 adds a WARN-tier-only accretion signal alongside the per-entry cap, meeting the
in-place-amendment channel the per-entry cap's forward-only scope cannot reach: an amendment-delta
leg (a BASELINE entry whose body byte-span changed and whose new span now exceeds the cap) and a
standing-pressure leg (one aggregate line naming how many live entries sit over 1.2x the cap).
Both legs are telemetry -- they NEVER append to `failed` -- and sit downstream of this module's
existing hard-fail cap and cost gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from scripts.checks import _common, registry
from scripts.checks.decisions import decisions_size_measure as measure
from scripts.checks.decisions._baseline import (
    BaselineBodies,
    BaselineBodyReaderFn,
    BaselineReaderFn,
    base_file_text,
    baseline_decision_bodies,
    baseline_decision_numbers,
)
from scripts.decisions_md import iter_decision_sections

BaseTextReaderFn = Callable[[Path, str], Optional[str]]
_DECISION_FILES = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")
_EXEMPT_REL = "config/decision_options_exemptions.yaml"
_COST_GATE_FILES = {*_DECISION_FILES, _EXEMPT_REL, measure.CONTRACT}

_PER_ENTRY_CAP_BYTES = 6_144
_STANDING_PRESSURE_MULTIPLIER = 1.2


_PER_ENTRY_CAP_HARD_FAIL_CITATION = (
    'Decision 167 clause 3: "A new entry over 6,144 bytes ... fails validate_decisions_size in '
    "the --pre tier -- Decision 160 point 11 named a per-entry norm as the only lever that bends "
    "the corpus's growth RATE, and this clause installs it.\""
)


def _new_entries_examined_count(root: Path, baseline_numbers: set[int]) -> int:
    """Count of NEW (non-baseline) decision entries the per-entry cap sub-check evaluated --
    mirrors `_per_entry_cap_failures`'s own new-vs-baseline scope, for the Decision 170
    declaration on the fall-through exit path."""
    count = 0
    for rel in ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"):
        path = root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for m, _block in iter_decision_sections(content):
            if int(m.group(1)) not in baseline_numbers:
                count += 1
    return count


def _per_entry_cap_failures(root, baseline_numbers: set[int]) -> list[str]:
    """HARD-FAIL (Decision 167 clause 3's dated pre-commitment, fired by migration step 3) for
    each NEW entry (absent from baseline_numbers) over the per-entry authoring cap. Historical
    entries are still never measured -- forward-only, mirrors
    validate_decision_entry_conformance's own new-vs-baseline scope. Renamed from
    `_per_entry_cap_warnings` (WARN-tier): the old name is actively misleading once this appends
    to `failed` instead of only printing."""
    failures: list[str] = []
    for rel in ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"):
        path = root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for m, block in iter_decision_sections(content):
            n = int(m.group(1))
            if n in baseline_numbers:
                continue
            size = len(block.encode("utf-8"))
            if size > _PER_ENTRY_CAP_BYTES:
                failures.append(
                    f"  FAIL: Decision {n} ({rel}) is {size} bytes, exceeding the {_PER_ENTRY_CAP_BYTES}-byte "
                    f"per-entry authoring cap (docs/contracts/decision-entry.yaml "
                    f"size_governance.per_entry_size_norm) -- {_PER_ENTRY_CAP_HARD_FAIL_CITATION}"
                )
    return failures


def _amendment_delta_warnings(root: Path, baseline_bodies: BaselineBodies) -> list[str]:
    """WARN-tier (rec-3243, never appends to `failed`) accretion signal: a BASELINE entry
    (present in both the origin/main body baseline and the current head) whose body byte-span
    CHANGED -- delta != 0, not merely grew, since Decision 178 retired
    validate_live_entry_immutability and a baseline body can now be rewritten in place, holding
    span flat or shrinking it, not only appended to -- and whose NEW span exceeds the per-entry
    cap. A new-in-diff entry (absent from the baseline) is outside this leg's domain -- it is
    already governed by the existing hard-fail per-entry cap above."""
    warnings: list[str] = []
    for rel in ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"):
        path = root / rel
        if not path.exists():
            continue
        base_by_number = baseline_bodies.get(rel) or {}
        if not base_by_number:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for m, block in iter_decision_sections(content):
            n = int(m.group(1))
            base_block = base_by_number.get(n)
            if base_block is None:
                continue
            head_span = len(block.encode("utf-8"))
            base_span = len(base_block.encode("utf-8"))
            delta = head_span - base_span
            if delta != 0 and head_span > _PER_ENTRY_CAP_BYTES:
                sign = "+" if delta > 0 else ""
                warnings.append(
                    f"  WARN: Decision {n} ({rel}) body changed size by {sign}{delta} bytes, new span "
                    f"{head_span} bytes, over the {_PER_ENTRY_CAP_BYTES}-byte per-entry cap "
                    "(rec-3243 amendment-delta accretion signal; WARN tier only, never fails)."
                )
    return warnings


def _standing_pressure_warning(root: Path) -> list[str]:
    """WARN-tier aggregate (rec-3243): ONE line naming the count of live entries -- new or
    historical, this leg is not baseline-scoped like the delta leg above -- over 1.2x the
    per-entry cap, and the largest offender, so the operator brief's >20%-over-cap ask lands as
    telemetry rather than a per-entry noise floor."""
    threshold = _PER_ENTRY_CAP_BYTES * _STANDING_PRESSURE_MULTIPLIER
    over: list[tuple[int, str, int]] = []
    for rel in ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"):
        path = root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for m, block in iter_decision_sections(content):
            size = len(block.encode("utf-8"))
            if size > threshold:
                over.append((size, rel, int(m.group(1))))
    if not over:
        return []
    over.sort(reverse=True)
    largest_size, largest_rel, largest_n = over[0]
    plural = "y" if len(over) == 1 else "ies"
    return [
        (
            f"  WARN: {len(over)} decision entr{plural} exceed {threshold:.0f} bytes (1.2x the "
            f"{_PER_ENTRY_CAP_BYTES}-byte per-entry cap) -- largest: Decision {largest_n} ({largest_rel}) "
            f"at {largest_size} bytes (rec-3243 standing-pressure accretion signal; WARN tier only, never fails)."
        )
    ]


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


def _measure_findings(
    root: Path,
    gov: measure.Governance,
    baseline_numbers: set[int],
    baseline_bodies: BaselineBodies | None,
    base_text_reader: BaseTextReaderFn,
) -> tuple[list[str], list[str]]:
    """Decision 272's size legs over the repository: (fails, warns), each line printed as is."""
    fails: list[str] = []
    warns: list[str] = []
    head_rows = measure.exemption_rows(_read(root / _EXEMPT_REL))
    exempt_titles = {title for _, title in head_rows}
    corpus = {rel: {int(m.group(1)): b for m, b in iter_decision_sections(_read(root / rel) or "")} for rel in _DECISION_FILES}
    new_entries = {n: b for rel in _DECISION_FILES for n, b in corpus[rel].items() if n not in baseline_numbers}
    for rel in _DECISION_FILES:
        for n, block in corpus[rel].items():
            if n not in baseline_numbers:
                issues = measure.measured_cap_issues(block, gov) + measure.options_issues(block, gov, exempt_titles)
                fails += [f"  FAIL: Decision {n} ({rel}) {issue}" for issue in issues]
            elif baseline_bodies is not None:
                grown, noted = measure.in_place_growth(n, rel, block, baseline_bodies.get(rel, {}).get(n), gov)
                fails += [f"  FAIL: {m}" for m in grown]
                warns += [f"  WARN: {m}" for m in noted]
    base_contract = base_text_reader(root, measure.CONTRACT)
    if base_contract is not None:
        base_gov = measure.size_governance(base_contract)
        base_rows = base_text_reader(root, _EXEMPT_REL)
        approved = {p.stem for p in (root / "docs" / "plans" / "approved").glob("*.yaml")}
        row_fails, row_warns = measure.exemption_findings(
            head_rows,
            None if base_rows is None else measure.exemption_rows(base_rows),
            isinstance(base_gov, measure.Governance) and base_gov.options_cap is not None,
            approved,
        )
        base_entries = {n: b for per_file in (baseline_bodies or {}).values() for n, b in per_file.items()}
        row_fails += measure.fail_closed_issues(base_contract, _read(root / measure.CONTRACT) or "", base_entries, new_entries)
        fails += [f"  FAIL: {m}" for m in row_fails]
        warns += [f"  WARN: {m}" for m in row_warns]
    return fails, warns


@registry.register("validate_decisions_size", owner="platform")
def validate_decisions_size(
    failed: list[str],
    root=None,
    baseline_reader: BaselineReaderFn | None = None,
    baseline_body_reader: BaselineBodyReaderFn | None = None,
    base_text_reader: BaseTextReaderFn | None = None,
) -> None:
    """Enforce the per-NEW-entry authoring size cap (Decision 167 clause 3) on
    docs/DECISIONS.md and docs/DECISIONS_ARCHIVE.md. The stock ceilings this check formerly
    also enforced -- the live header count and the live+archive combined byte ceiling -- are
    retired by Decision 179: bounded decision-scout retrieval means no consumer reads the live
    corpus wholesale anymore, so the guards that sized that read no longer have a referent;
    Decision 150's significance bar is the retained lever on entry count and triage quality.

    root / baseline_reader are test injection seams (mirrors validate_decision_entry_conformance)
    for the hard-fail per-entry cap sub-check below (Decision 167 clause 3, fired by migration
    step 3). baseline_body_reader is the companion seam (rec-3243) for the two WARN-tier
    accretion legs -- amendment delta and standing pressure -- that run alongside the per-entry
    cap on the fall-through path. A non-default root always exercises the per-entry sub-check
    (deterministic for tests); the default (production) root skips it whenever this check's own
    root equals _common.ROOT AND neither DECISIONS file changed in this diff, since this check
    runs UNGATED on every --pre invocation and the git baseline read is not free.

    Composes exactly ONE terminal Decision 170 declaration on each of its five reachable exit
    paths: a missing live or archive file `skipped()`s (could not examine); the cost-gated
    early return `examined(0, ...)`s (definitively zero new entries to check, not an unmet
    precondition); an unreachable diff base for the per-entry cap `skipped()`s; the
    fall-through `examined(N, ...)`s the new entries actually checked against the cap.
    """
    print("\n=== DECISIONS size governance ===")

    using_default_root = root is None
    root = root if root is not None else _common.ROOT
    baseline_reader = baseline_reader or baseline_decision_numbers
    baseline_body_reader = baseline_body_reader or baseline_decision_bodies
    base_text_reader = base_text_reader or base_file_text

    live_path = root / "docs" / "DECISIONS.md"
    archive_path = root / "docs" / "DECISIONS_ARCHIVE.md"

    if not live_path.exists():
        print("  FAIL: docs/DECISIONS.md not found")
        failed.append("DECISIONS size governance")
        registry.skipped("docs/DECISIONS.md not found")
        return
    if not archive_path.exists():
        print("  FAIL: docs/DECISIONS_ARCHIVE.md not found")
        failed.append("DECISIONS size governance")
        registry.skipped("docs/DECISIONS_ARCHIVE.md not found")
        return

    if using_default_root and not (set(_common.get_changed_files(root)) & _COST_GATE_FILES):
        registry.examined(0, unit="new_decision_entries")
        return

    baseline_numbers = baseline_reader(root)
    if baseline_numbers is None:
        print("  (per-entry cap: SKIP, origin/main unreachable.)")
        registry.skipped("origin/main unreachable")
        return
    contract_text = _read(root / measure.CONTRACT)
    gov = measure.size_governance(contract_text) if contract_text is not None else None
    if isinstance(gov, str):
        print(f"  FAIL: {gov}")
        failed.append("DECISIONS size governance")
        gov = None
    baseline_bodies = baseline_body_reader(root)
    per_entry_failures = (
        [] if gov is not None and gov.measured_cap is not None else _per_entry_cap_failures(root, baseline_numbers)
    )
    if gov is not None:
        measured_fails, measured_warns = _measure_findings(root, gov, baseline_numbers, baseline_bodies, base_text_reader)
        per_entry_failures += measured_fails
        for warning in measured_warns:
            print(warning)
    for failure in per_entry_failures:
        print(failure)
    if per_entry_failures:
        failed.append("DECISIONS size governance")

    if baseline_bodies is not None:
        for warning in _amendment_delta_warnings(root, baseline_bodies):
            print(warning)
        for warning in _standing_pressure_warning(root):
            print(warning)

    registry.examined(_new_entries_examined_count(root, baseline_numbers), unit="new_decision_entries")
