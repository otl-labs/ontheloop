"""Decision-entry authoring-grammar forward conformance check (DAF-03 / Decision 134 cl.3 /
PLAN-daf-authoring-grammar; extended by PLAN-decision-entry-flow-governance / Decision 167 with
the typed metadata envelope, the Significance routing claim, and the envelope/body conflict and
number-match sub-checks).

Enforces docs/contracts/decision-entry.yaml's canonical markers on GENUINELY-NEW decision
entries only -- a decision number absent from the origin/main baseline. Modified, amended, or
promoted historical entries (e.g. this same plan's own DECISIONS_ARCHIVE.md h3->h2 promote of
Decisions 52/53/54) are grandfathered: they carry only a "**Status:** Decided -- April 2026"
line with no separate "**Date:**" marker, and would self-fail this check if the historical band
were retro-enforced. A Decision 149 compacted stub (its body wholly replaced with the fixed
stub_grammar shape, including a literal "**Status:** Superseded" and a "**Superseded by:
Decision N**" pointer) is likewise exempt from the envelope obligation -- a compaction replaces a
body it never authored.

Baseline and current populations are both computed via the SAME shared grammar
(scripts.decisions_md.iter_decision_headings, the '#{2,3}' regex) so a header-level promote
(h3->h2) never manufactures a false "new" entry -- Decisions 52/53/54 are counted as
already-present at origin/main regardless of which heading level either snapshot used. The
origin/main baseline read itself is delegated to scripts.checks.decisions._baseline (Decision
153 fast-tier budget: one shared, memoized git read for both --pre DECISIONS consumers).

Required markers and the significance vocabulary are read from docs/contracts/decision-entry.yaml
at check time (never a hardcoded copy) -- the contract is the single source of truth.

Advisory SKIP (never a failure) when origin/main is unreachable (mirrors the
validate_graduation_completeness precedent): the check cannot resolve new-vs-baseline without
it. The baseline_reader injection seam returns None for exactly this case, so the default
reader's own reachability check and a test's synthetic "unreachable" case share one code path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from scripts.checks import _common, registry
from scripts.checks.decisions import envelope_rules
from scripts.checks.decisions._baseline import BaselineReaderFn
from scripts.checks.decisions._baseline import baseline_decision_numbers as _default_baseline_decision_numbers
from scripts.decisions_md import (
    _BOLD_STATUS_RE,
    _DECISION_MARKER_BODY,
    PLACEHOLDER_NUMBER,
    extract_entry_envelope,
    extract_superseded_by,
    is_compacted_stub,
    iter_decision_headings,
    iter_placeholder_headings,
    status_is_superseded,
)
from scripts.north_star_models import load_north_star

_CONTRACT_REL_PATH = "docs/contracts/decision-entry.yaml"
_KIND_CONTRACT_REL_PATH = "docs/contracts/decision-kind.yaml"
_DECISIONS_REL_PATHS = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")

_BOLD_DATE_RE = re.compile(r"\*\*Date:\*\*\s*(.+)")


def _current_decision_entries(root: Path) -> dict[int, str]:
    """decision number -> heading-inclusive raw block, from the CURRENT working tree.

    First-wins on a duplicate number across the two files, mirroring
    scripts.decisions_md.parse_decisions_md's dedup rule.
    """
    entries: dict[int, str] = {}
    for rel in _DECISIONS_REL_PATHS:
        path = root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        headings = list(iter_decision_headings(content))
        for i, m in enumerate(headings):
            n = int(m.group(1))
            if n in entries:
                continue
            end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
            entries[n] = content[m.start() : end]
    return entries


def _load_required_markers(root: Path, failed: list[str]) -> Optional[list[str]]:
    contract_path = root / _CONTRACT_REL_PATH
    if not contract_path.exists():
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} not found")
        return None
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        failed.append(f"Decision-entry conformance: could not parse {_CONTRACT_REL_PATH}: {exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("required_markers"), list):
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} has no required_markers list")
        return None
    return [str(m) for m in data["required_markers"]]


def _missing_markers(block: str, required_markers: list[str]) -> list[str]:
    """Return the subset of required_markers absent from block.

    The "Decision" marker tolerates the decorated form (e.g. "**Decision (four invariants):**")
    per decision-entry.yaml's decision_marker_tolerance -- reuses decisions_md's own
    _DECISION_MARKER_BODY pattern so this check never diverges from the shared parser's notion
    of what counts as a Decision marker. Every other required marker uses its exact spelling.
    """
    missing: list[str] = []
    for marker in required_markers:
        pattern = rf"\*\*{_DECISION_MARKER_BODY}:\*\*" if marker == "Decision" else rf"\*\*{re.escape(marker)}:\*\*"
        if not re.search(pattern, block):
            missing.append(marker)
    return missing


def _load_block_separator_form(root: Path) -> Optional[str]:
    """Return the declared block_separator form (e.g. '---'), or None when the contract is
    unreadable or declares no block_separator section.

    A missing block_separator key is a TOLERATED absence, not a failure -- unlike
    required_markers (mandatory), this sub-check is additive (CFG-06 / rec-2991) and must not
    fail every conformance test fixture pre-dating the section's existence.
    """
    contract_path = root / _CONTRACT_REL_PATH
    if not contract_path.exists():
        return None
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    block_separator = data.get("block_separator")
    if not isinstance(block_separator, dict):
        return None
    form = block_separator.get("form")
    return str(form) if form else None


def _load_envelope_config(root: Path, failed: list[str]) -> Optional[tuple[list[str], dict[str, str]]]:
    """Return (metadata_envelope.required_fields, significance.routing) from the contract, or
    None (with a FAIL appended) when either section is absent or malformed.

    Read fresh at check time (never a hardcoded copy of the token vocabulary), mirroring
    _load_required_markers -- both required_markers and this pair of sections are mandatory
    contract shape, not a tolerated-absence sub-check like block_separator.
    """
    contract_path = root / _CONTRACT_REL_PATH
    if not contract_path.exists():
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} not found")
        return None
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        failed.append(f"Decision-entry conformance: could not parse {_CONTRACT_REL_PATH}: {exc}")
        return None
    if not isinstance(data, dict):
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} has an unexpected top-level shape")
        return None
    envelope_cfg = data.get("metadata_envelope")
    if not isinstance(envelope_cfg, dict) or not isinstance(envelope_cfg.get("required_fields"), list):
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} has no metadata_envelope.required_fields list")
        return None
    significance = data.get("significance")
    routing = significance.get("routing") if isinstance(significance, dict) else None
    if not isinstance(routing, dict) or not routing:
        failed.append(f"Decision-entry conformance: {_CONTRACT_REL_PATH} has no significance.routing mapping")
        return None
    return [str(f) for f in envelope_cfg["required_fields"]], {str(k): str(v) for k, v in routing.items()}


def _load_grammar_config(root: Path) -> dict:
    """The serves/kind/closed-key/number declarations of the entry contract; each rule fires only when declared
    (the tolerated-absence pattern _load_block_separator_form uses), so an old-style synthetic contract stays valid."""
    try:
        data = yaml.safe_load((root / _CONTRACT_REL_PATH).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    cfg = data.get("metadata_envelope") if isinstance(data, dict) else None
    if not isinstance(cfg, dict):
        return {}
    shapes = cfg.get("field_shapes") if isinstance(cfg.get("field_shapes"), dict) else {}
    serves = shapes.get("serves") if isinstance(shapes.get("serves"), dict) else {}
    required = [str(f) for f in cfg.get("required_fields") or []]
    optional = [str(f) for f in cfg.get("optional_fields") or []]
    return {
        "kind": "kind" in required,
        "closed_keys": required + optional if cfg.get("closed_key_set") else None,
        "serves_target": str(serves["resolves_against"]) if serves.get("resolves_against") else None,
        "number": bool(cfg.get("number_assignment")),
    }


def _load_kind_values(root: Path, failed: list[str]) -> Optional[list[str]]:
    try:
        values = yaml.safe_load((root / _KIND_CONTRACT_REL_PATH).read_text(encoding="utf-8")).get("values")
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        failed.append(f"Decision-entry conformance: could not read {_KIND_CONTRACT_REL_PATH}: {exc}")
        return None
    if not isinstance(values, list) or not values:
        failed.append(f"Decision-entry conformance: {_KIND_CONTRACT_REL_PATH} has no values list")
        return None
    return [str(v) for v in values]


def _load_north_star_doc(root: Path, rel: str, failed: list[str] | None):
    """The north-star document (ids and carried_by through scripts.north_star_models), or None; a FAIL is appended
    only when `failed` is given (a new entry needs it), else the WARN rules that need it are skipped."""
    try:
        return load_north_star(root / rel)
    except ValueError as exc:
        if failed is not None:
            failed.append(f"Decision-entry conformance: could not read {rel}: {exc}")
        return None


def _placeholder_heading_issues(root: Path) -> list[str]:
    issues: list[str] = []
    for rel in _DECISIONS_REL_PATHS:
        path = root / rel
        if path.exists():
            for m in iter_placeholder_headings(path.read_text(encoding="utf-8", errors="replace")):
                if m.group(1) == PLACEHOLDER_NUMBER:
                    issues.append(f"  FAIL: {rel} carries an unfilled placeholder heading {m.group(0)!r} (Decision 217 cl.5).")
    return issues


def _warn_lines(current_entries: dict[int, str], new_number_set: set[int], doc) -> list[str]:
    """Advisory lines (never appended to failed): a baseline serves id that no longer resolves, and a mechanism
    entry carried by a north star."""
    ids = doc.ids()
    warnings: list[str] = []
    envelopes = {n: extract_entry_envelope(b) for n, b in current_entries.items() if "serves:" in b or "kind:" in b}
    for n in sorted(envelopes):
        if n not in new_number_set and envelopes[n]:
            warnings += envelope_rules.dangling_serves_warnings(n, envelopes[n], ids)
    carried = {"mission": doc.mission.carried_by, **{p.id: p.carried_by for p in doc.principles}}
    kinds = {
        n: str(e["kind"]["value"])
        for n, e in envelopes.items()
        if e and isinstance(e.get("kind"), dict) and e["kind"].get("value")
    }
    warnings += envelope_rules.carried_by_mechanism_warnings(carried, kinds)
    return [f"  WARN: {w}." for w in warnings]


def _superseded_stub_grammar_issues(
    current_entries: dict[int, str], new_number_set: set[int], required_markers: list[str]
) -> tuple[list[str], int]:
    """Widened stub_grammar enforcement (Decision 175 / T2.56 migration step 7, closing the hole
    where a malformed compaction of a baseline-present entry passed CI silently): ANY entry whose
    body declares '**Status:** Superseded' must be a conforming compacted stub, independent of
    origin/main baseline presence. Keys on status_is_superseded (the body declaring '**Status:**
    Superseded' exactly) -- never a '**Superseded by:**' substring test, which would falsely catch
    a full-bodied entry that merely mentions the marker in prose (e.g. Decision 149) while missing
    a genuinely malformed compaction whose Status line reads differently. New-in-diff entries get
    this for free from the caller's own envelope-conformance loop (a strictly stronger check), so
    this walks only baseline-present numbers. Returns (issues, count of entries examined)."""
    issues: list[str] = []
    checked = 0
    for n in sorted(current_entries):
        if n in new_number_set:
            continue
        block = current_entries[n]
        if not status_is_superseded(block):
            continue
        checked += 1
        missing = _missing_markers(block, required_markers)
        if missing:
            issues.append(
                f"  FAIL: Decision {n} declares '**Status:** Superseded' but is missing marker(s): {missing} "
                "(docs/contracts/decision-entry.yaml compaction.stub_grammar)."
            )
        if not extract_superseded_by(block):
            issues.append(
                f"  FAIL: Decision {n} declares '**Status:** Superseded' but carries no resolvable "
                "'**Superseded by: Decision N**' pointer -- malformed compacted stub "
                "(docs/contracts/decision-entry.yaml compaction.stub_grammar)."
            )
    return issues, checked


# Module-level alias, not a re-implementation: the compaction-stub predicate is promoted to
# scripts.decisions_md.is_compacted_stub (DAF-03 / PLAN-decision-corpus-currency) so exactly one
# definition exists. Retained under this private name because this module's existing test suite
# (tests/checks/decisions/test_validate_decision_entry_conformance.py) imports it directly -- an
# import-and-rename edit there is unwarranted churn.
_is_compacted_stub = is_compacted_stub


def _envelope_body_conflicts(envelope: dict, block: str) -> list[str]:
    """Field-by-field envelope-vs-bold-marker comparison. Agreement or the bold marker's
    absence both pass; only a genuine value mismatch is a conflict."""
    conflicts: list[str] = []
    status_m = _BOLD_STATUS_RE.search(block)
    if status_m and envelope.get("status") is not None:
        bold_status = status_m.group(1).strip().split("--")[0].strip()
        if str(envelope["status"]).strip() != bold_status:
            conflicts.append(f"status (envelope={envelope['status']!r} vs body={bold_status!r})")
    date_m = _BOLD_DATE_RE.search(block)
    if date_m and envelope.get("decided_date") is not None:
        bold_date = date_m.group(1).strip()
        if str(envelope["decided_date"]).strip() != bold_date:
            conflicts.append(f"decided_date (envelope={envelope['decided_date']!r} vs body={bold_date!r})")
    return conflicts


def _new_entry_conformance_issues(
    new_numbers: list[int],
    current_entries: dict[int, str],
    required_markers: list[str],
    block_separator_form: str | None,
    envelope_required_fields: list[str],
    routing_tokens: dict[str, str],
    grammar: dict | None = None,
    kind_values: list[str] | None = None,
    north_star_ids: set[str] | None = None,
    baseline_max: int = 0,
) -> list[str]:
    """Full envelope-conformance sweep over new-in-diff decision numbers: required markers, the
    block_separator obligation, metadata-envelope well-formedness, the number-match sub-check,
    Significance routing, and envelope/body-marker conflicts. A Decision 149 compacted stub is
    exempt from the envelope obligation (a compaction replaces a body it never authored)."""
    issues: list[str] = []
    for n in new_numbers:
        block = current_entries[n]
        missing = _missing_markers(block, required_markers)
        if missing:
            issues.append(
                f"  FAIL: Decision {n} is new (absent from the origin/main baseline) but missing marker(s): {missing}"
            )
        if block_separator_form and not block.rstrip().endswith(block_separator_form):
            issues.append(
                f"  FAIL: Decision {n} is new (absent from the origin/main baseline) but its block does not "
                f"end with the required separator {block_separator_form!r} (docs/contracts/decision-entry.yaml "
                "block_separator)."
            )

        if _is_compacted_stub(block):
            continue  # Decision 149 compacted stub -- exempt from the envelope obligation

        envelope = extract_entry_envelope(block)
        if envelope is None:
            issues.append(
                f"  FAIL: Decision {n} is new (absent from the origin/main baseline) but carries no well-formed "
                "metadata envelope (a fenced ```yaml block immediately after the heading, per "
                f"docs/contracts/decision-entry.yaml metadata_envelope): {envelope_rules.malformed_envelope_detail(block)}."
            )
            continue

        for field_issue in envelope_rules.required_field_issues(envelope, envelope_required_fields):
            issues.append(f"  FAIL: Decision {n} {field_issue}")

        if "number" in envelope_required_fields and envelope.get("number") is not None and int(envelope["number"]) != n:
            issues.append(
                f"  FAIL: Decision {n} envelope declares number={envelope['number']!r}, which disagrees with its heading."
            )

        if "significance" in envelope_required_fields:
            for sig_issue in envelope_rules.significance_issues(envelope, routing_tokens):
                issues.append(f"  FAIL: Decision {n} {sig_issue}.")

        grammar = grammar or {}
        rule_issues: list[str] = []
        if kind_values is not None:
            rule_issues += envelope_rules.kind_issues(envelope, kind_values)
        if north_star_ids is not None:
            rule_issues += envelope_rules.serves_issues(envelope, north_star_ids)
        if grammar.get("closed_keys") is not None:
            rule_issues += envelope_rules.unknown_key_issues(envelope, grammar["closed_keys"])
        if grammar.get("number"):
            rule_issues += envelope_rules.number_issues(envelope.get("number"), baseline_max)
        issues += [f"  FAIL: Decision {n} {r}." for r in rule_issues]

        for conflict in _envelope_body_conflicts(envelope, block):
            issues.append(f"  FAIL: Decision {n} envelope conflicts with its bold-marker twin on {conflict}.")
    return issues


@registry.register("validate_decision_entry_conformance", owner="platform")
def validate_decision_entry_conformance(
    failed: list[str],
    root: Path | None = None,
    baseline_reader: BaselineReaderFn | None = None,
) -> None:
    """Enforce the decision-entry authoring grammar on new-in-diff decision numbers only.

    root / baseline_reader are test/dogfood injection seams (mirrors the vp_replay /
    graduation_completeness precedents) -- default to _common.ROOT and a real
    `git show origin/main:...` reader respectively.
    """
    print("\n=== Decision-entry authoring-grammar conformance (DAF-03) ===")
    root = root if root is not None else _common.ROOT
    baseline_reader = baseline_reader or _default_baseline_decision_numbers

    baseline_numbers = baseline_reader(root)
    if baseline_numbers is None:
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI).")
        registry.skipped("origin/main unreachable")
        return

    required_markers = _load_required_markers(root, failed)
    if required_markers is None:
        registry.skipped("required_markers unavailable")
        return

    current_entries = _current_decision_entries(root)
    new_numbers = sorted(set(current_entries) - baseline_numbers)
    new_number_set = set(new_numbers)

    issues, superseded_checked = _superseded_stub_grammar_issues(current_entries, new_number_set, required_markers)
    grammar = _load_grammar_config(root)
    if grammar.get("number"):
        issues += _placeholder_heading_issues(root)
    if grammar.get("serves_target"):
        doc = _load_north_star_doc(root, grammar["serves_target"], None)
        for line in _warn_lines(current_entries, new_number_set, doc) if doc else []:
            print(line)

    if not new_numbers:
        if issues:
            for issue in issues:
                print(issue)
            failed.append("Decision-entry conformance")
        else:
            print(
                "  PASS: no genuinely-new decision entries in this diff (origin/main baseline unchanged); "
                "no baseline-present Superseded entry is a malformed stub."
            )
        registry.examined(superseded_checked, unit="decision_entries_checked")
        return

    envelope_config = _load_envelope_config(root, failed)
    if envelope_config is None:
        registry.skipped("envelope_config unavailable")
        return
    envelope_required_fields, routing_tokens = envelope_config

    block_separator_form = _load_block_separator_form(root)
    kind_values = _load_kind_values(root, failed) if grammar.get("kind") else None
    north_star_ids = None
    if grammar.get("serves_target") and any("serves:" in current_entries[n] for n in new_numbers):
        doc = _load_north_star_doc(root, grammar["serves_target"], failed)
        north_star_ids = doc.ids() if doc else set()
    if (grammar.get("kind") and kind_values is None) or (north_star_ids is not None and not north_star_ids):
        registry.skipped("declared contract unavailable")
        return

    issues += _new_entry_conformance_issues(
        new_numbers,
        current_entries,
        required_markers,
        block_separator_form,
        envelope_required_fields,
        routing_tokens,
        grammar,
        kind_values,
        north_star_ids,
        max(baseline_numbers, default=0),
    )

    if issues:
        for issue in issues:
            print(issue)
        failed.append("Decision-entry conformance")
    else:
        plural = "y" if len(new_numbers) == 1 else "ies"
        print(f"  PASS: {len(new_numbers)} new decision entr{plural} conform to the canonical grammar ({new_numbers}).")
    registry.examined(len(new_numbers) + superseded_checked, unit="decision_entries_checked")
