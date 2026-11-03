"""Roadmap blocking-graph liveness ratchet (PLAN-roadmap-blocking-liveness-detector; rec-3395,
rec-3394; docs/contracts/roadmap-liveness.yaml).

Three heterogeneous legs sharing one registered check and one RoadmapDocument load (governance
rationale: docs/contracts/roadmap-liveness.yaml's `governance_note`):

  Leg A -- toxic-SCC shrink-only ratchet against config/roadmap_liveness_baseline.yaml, mirroring
  scripts/checks/hygiene/validate_check_accounting.py's (a)/(b)/(c) structure, plus a fourth
  sub-leg (d) that gives the ratchet teeth in the shrinking direction too.
  Leg B -- every 'rehomed' exit criterion's met_by target must carry at least one open criterion.
  Leg C -- diff-scoped: an added/changed criterion naming a NON_TERMINAL id with a blocking
  phrase (docs/contracts/roadmap-liveness.yaml's sole-source grammar) must carry a matching
  blocked_by entry.

All three legs hard-fail -- there is no warn tier (docs/contracts/check-accounting.yaml's
status_vocabulary has no "warned" member; a warn here would record as "enforced").
"""

from __future__ import annotations

import re

import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.platform_roadmap_liveness import toxic_node_ids
from scripts.platform_roadmap_models import RoadmapDocument, TierItem
from scripts.platform_roadmap_state import load as load_roadmap_document
from scripts.roadmap import roadmap_archive

_BASELINE_REL_PATH = "config/roadmap_liveness_baseline.yaml"
_CONTRACT_REL_PATH = "docs/contracts/roadmap-liveness.yaml"
_ROADMAP_REL_PATH = "docs/ROADMAP-PLATFORM.yaml"

# The ratchet's frozen seed -- the 19 toxic node ids measured on the live roadmap when this check
# landed. Growing this set requires editing this constant in a reviewed code change; a
# config/roadmap_liveness_baseline.yaml edit alone can never admit an id absent from here
# (Decision 165/166 precedent: scripts/checks/hygiene/validate_check_accounting.py's own
# _BASELINE_SEED).
_BASELINE_SEED: frozenset[str] = frozenset(
    {
        "CD.11",
        "CD.27",
        "CD.38",
        "CD.40",
        "CD.43",
        "T2.18",
        "T2.19",
        "T2.26",
        "T2.36",
        "T3.2",
        "T3.20",
        "T4.1",
        "T4.10a",
        "T4.13",
        "T4.2",
        "T4.3",
        "T4.4",
        "T4.9",
        "T4.9a",
    }
)

_ID_MENTION_RE = re.compile(r"\b(T-?\d+\.\d+[a-z]?|CD\.\d+)\b")


def _load_baseline_entries(text: str) -> list[str]:
    data = yaml.safe_load(text) or {}
    entries = data.get("entries") or []
    return [str(e) for e in entries]


def _load_blocking_phrases() -> list[str]:
    """docs/contracts/roadmap-liveness.yaml's leg_c_blocking_phrase_grammar.phrases -- the SOLE
    source; this module holds no second copy. Each entry is a regex pattern (matched via
    re.search against the lowercased criterion text) -- most are plain literals, but several
    (e.g. "requires .* ratified") are genuinely wildcarded, so the caller must never downgrade
    this to a literal substring test."""
    contract_path = _common.ROOT / _CONTRACT_REL_PATH
    if not contract_path.exists():
        return []
    data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    grammar = data.get("leg_c_blocking_phrase_grammar") or {}
    phrases = grammar.get("phrases") or []
    return [str(p).lower() for p in phrases]


def _non_terminal_ids(doc: RoadmapDocument) -> set[str]:
    item_ids = {item.id for item in doc.tier_items if item.status in {"not_started", "in_progress", "deferred_post_mvp"}}
    cd_ids = {cd.id for cd in doc.candidate_decisions if cd.state == "pending"}
    return item_ids | cd_ids


def _leg_b_rehome_coverage(doc: RoadmapDocument) -> list[str]:
    """Every 'rehomed' exit criterion's met_by target must carry >=1 open criterion (necessary
    but not sufficient for "the rehomed scope landed" -- docs/contracts/roadmap-liveness.yaml's
    leg_b_rehome_coverage records the accepted permanent bound)."""
    violations: list[str] = []
    by_id = {item.id: item for item in doc.tier_items}
    for item in doc.tier_items:
        for crit in item.exit_criteria:
            if crit.status != "rehomed":
                continue
            target = by_id.get(crit.met_by) if crit.met_by else None
            has_open = target is not None and any(c.status == "open" for c in target.exit_criteria)
            if not has_open:
                violations.append(
                    f"tier_item '{item.id}' criterion '{crit.id}': rehomed to met_by='{crit.met_by}', but "
                    "that item carries no open criterion -- leg B requires the rehome target to carry at "
                    "least one open criterion (see docs/contracts/roadmap-liveness.yaml's "
                    "leg_b_rehome_coverage)."
                )
    return violations


def _base_item_texts(base_text: str) -> dict[str, set[str]]:
    """{tier_item id: set of its exit_criteria texts} at the diff base -- parsed PER ITEM via
    TierItem.model_validate, never RoadmapDocument.model_validate (whose graph validator can
    raise on an older base's since-changed shape). A stub-bearing base is hydrated leniently from
    its own archive (Decision 227), so an unchanged archived criterion never reads as added."""
    result: dict[str, set[str]] = {}
    data = yaml.safe_load(base_text) or {}
    if roadmap_archive.has_stubs(data):
        data = roadmap_archive.hydrate_base(data, _marker_guard.default_base_reader(roadmap_archive.ARCHIVE_REL_PATH))
    for raw in data.get("tier_items") or []:
        try:
            item = TierItem.model_validate(raw)
        except Exception:  # noqa: BLE001 -- a malformed base-era item is skipped, not fatal to leg C.
            continue
        result[item.id] = {c.text for c in item.exit_criteria}
    return result


def _leg_c_prose_blocking_edges(doc: RoadmapDocument) -> list[str]:
    """Diff-scoped: for each ADDED/CHANGED criterion (keyed on TEXT, so a bare-string-list
    insertion never reports once per downstream sibling whose synthetic id merely shifted), fail
    if its text names a NON_TERMINAL id with a blocking phrase and carries no blocked_by entry
    for that ref."""
    base_text = _marker_guard.default_base_reader(_ROADMAP_REL_PATH)
    if base_text is None:
        print(
            f"  SKIP (leg C only): {_ROADMAP_REL_PATH} unreachable at origin/main (advisory locally, "
            "seeding-safe in CI) -- legs A and B keep enforcing."
        )
        return []

    phrases = _load_blocking_phrases()
    if not phrases:
        return []

    base_texts_by_item = _base_item_texts(base_text)
    non_terminal_ids = _non_terminal_ids(doc)

    violations: list[str] = []
    for item in doc.tier_items:
        base_texts = base_texts_by_item.get(item.id, set())
        for crit in item.exit_criteria:
            if crit.text in base_texts:
                continue  # unchanged text -- out of leg C's diff scope regardless of any id shift
            lowered = crit.text.lower()
            if not any(re.search(phrase, lowered) for phrase in phrases):
                continue
            mentioned = {m for m in _ID_MENTION_RE.findall(crit.text) if m in non_terminal_ids and m != item.id}
            blocked_refs = {b.ref for b in crit.blocked_by}
            for ref in sorted(mentioned - blocked_refs):
                until = "ratified" if ref.startswith("CD.") else "complete"
                violations.append(
                    f"tier_item '{item.id}' criterion '{crit.id}' (added/changed this diff) names '{ref}' "
                    f"with a blocking phrase but carries no blocked_by entry for it -- add "
                    f"{{ref: {ref}, until: {until}}} to this criterion's blocked_by (re-sequence the edge), "
                    "or the criterion is genuinely blocked and this is a true toxic-SCC finding once added."
                )
    return violations


@registry.register("validate_roadmap_liveness", owner="platform")
def validate_roadmap_liveness(failed: list[str]) -> None:
    print(f"\n=== Roadmap blocking-graph liveness ratchet ({_CONTRACT_REL_PATH}) ===")

    roadmap_path = _common.ROOT / _ROADMAP_REL_PATH
    try:
        doc = load_roadmap_document(roadmap_path)
    except Exception as exc:  # noqa: BLE001 -- an unloadable roadmap is a genuine check-wide SKIP.
        registry.skipped(f"{_ROADMAP_REL_PATH} unloadable: {exc}")
        print(f"  SKIP: {_ROADMAP_REL_PATH} unloadable: {exc}")
        return

    violations: list[str] = []

    measured_toxic = set(toxic_node_ids(doc))

    baseline_path = _common.ROOT / _BASELINE_REL_PATH
    current_baseline_text = baseline_path.read_text(encoding="utf-8") if baseline_path.exists() else ""
    current_baseline = set(_load_baseline_entries(current_baseline_text))

    # Leg A(a) -- PRIMARY, unconditional, base-independent: every measured toxic id must be
    # baselined. This is what makes the check a liveness gate rather than a config-file lint.
    uncovered = measured_toxic - current_baseline
    if uncovered:
        violations.append(
            f"Measured toxic node id(s) {sorted(uncovered)} are absent from {_BASELINE_REL_PATH} -- a "
            "roadmap edit that closes a fresh completion-demanding ring must ADD the newly toxic id(s) to "
            f"{_BASELINE_REL_PATH} in this PR (the id must also be present in this check's frozen "
            "_BASELINE_SEED constant -- a config edit alone can never satisfy this leg)."
        )

    # Leg A(b) -- frozen-constant cross-check.
    unrecognized = current_baseline - _BASELINE_SEED
    if unrecognized:
        violations.append(
            f"{_BASELINE_REL_PATH} carries {sorted(unrecognized)}, which is NOT in this check's frozen "
            "_BASELINE_SEED constant -- growing the baseline requires editing _BASELINE_SEED in a "
            "reviewed code change; a config-file edit alone can never admit a new member."
        )

    # Leg A(c) -- shrink-only diff, SKIP scoped to this sub-leg only.
    base_text = _marker_guard.default_base_reader(_BASELINE_REL_PATH)
    if base_text is None:
        print(
            f"  SKIP (leg A sub-leg c only): {_BASELINE_REL_PATH} unreachable at origin/main (advisory "
            "locally, seeding-safe in CI) -- legs A(a)/A(b)/A(d), B and C keep enforcing."
        )
    else:
        base_baseline = set(_load_baseline_entries(base_text))
        grown = current_baseline - base_baseline
        if grown:
            violations.append(
                f"{_BASELINE_REL_PATH} grew by {sorted(grown)} relative to origin/main -- the baseline is "
                "shrink-only (no marker escape, Decision 165); an entry may be removed, never (re-)added."
            )

    # Leg A(d) -- the ratchet has teeth in the shrinking direction too.
    drained = current_baseline - measured_toxic
    if drained:
        violations.append(
            f"{_BASELINE_REL_PATH} carries {sorted(drained)}, which the live roadmap no longer reports "
            f"toxic -- remove the drained id(s) from {_BASELINE_REL_PATH} in this PR (the ratchet "
            "tightens, it does not merely refuse to grow)."
        )

    violations.extend(_leg_b_rehome_coverage(doc))
    violations.extend(_leg_c_prose_blocking_edges(doc))

    registry.examined(len(measured_toxic) + len(current_baseline), unit="toxic_and_baselined_ids")
    if violations:
        print("Roadmap liveness ratchet violations:")
        for violation in violations:
            print(f"  - {violation}")
        failed.append("Roadmap blocking-graph liveness ratchet")
    else:
        print(f"  PASS: {len(measured_toxic)} measured toxic id(s), {len(current_baseline)} baselined.")
