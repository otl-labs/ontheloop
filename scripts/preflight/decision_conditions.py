"""Reversal-conditions stanza monitor (audit SEQ-02, follow-on to Decision 133).

Parses the frozen ``yaml reversal-conditions`` fenced stanza carried by ratified Decisions in
docs/DECISIONS.md / docs/DECISIONS_ARCHIVE.md, evaluates each decision's per-condition state, and
exposes:

- ``evaluate()`` -- the parser + state-machine core (``paths=``, ``predicates=``, ``today=``
  overrides, all injectable for tests).
- ``preflight_bucket()`` -- a resilient wrapper for scripts/session/preflight.py. Never raises;
  returns a degraded bucket carrying an ``"error"`` key on any failure (surfacing, not gating).
- ``print_decision_conditions()`` -- stdout renderer mirroring the existing
  "--- Provisional contracts due ---" preflight block.
- A ``__main__`` CLI: prints one line per monitored decision, exits nonzero iff any stanza is
  MALFORMED.

Generalizes over ANY decision carrying the fence (Decision 133 is the first row, not the schema);
a decision with no fenced stanza (prose-only "**Reversal conditions:**", e.g. Decision 40) is
opt-out by absence -- not monitored, never malformed.

Fail-loud contract (Decision 55 / AGENTS.md import-safety): parsing happens only inside explicit
function calls, never at import time. A malformed stanza produces an explicit MALFORMED entry
(and, via the CLI, a nonzero exit) -- never a silent skip, never an import-time exception.

Surfacing, not gating: a passed review_by or a fired condition never blocks anything from this
module. Only the separate ``scripts.checks.ops_governance.validate_reversal_stanzas`` check gates
a merge, and only on stanza WELL-FORMEDNESS.

Predicate registry: a module-level dict. Decision 133's two repo_state conditions both ship
``predicate: null`` (manual/context; no predicate to register there). ``roadmap_items_complete``
(Decision 194) is the first registered entry, ``ports_open_coupling_findings_exceed`` (Decision 212)
the second. ``register_predicate()`` remains the extension point for any future predicate.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from scripts.decisions_md import _DECISION_HEADING_RE, _DECISIONS_MD_PATHS

_FENCE_OPEN_RE = re.compile(r"^```yaml reversal-conditions[ \t]*$", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"^```[ \t]*$", re.MULTILINE)

_REQUIRED_CONDITION_KEYS = ("id", "kind", "description")
_VALID_KINDS = ("manual", "repo_state")
_REQUIRED_TOP_KEYS = ("decision", "review_by", "on_trigger", "conditions")

PredicateFn = Callable[..., bool]

# Predicate registry: module-level dict (Decision 133's repo_state conditions both ship
# predicate: null -- docs/DECISIONS.md Decision 133 stanza; roadmap_items_complete, registered
# below for Decision 194, is the first entry). register_predicate() is the extension point for
# any future predicate (e.g. an alpha-readiness or sustained-slip signal).
_PREDICATE_REGISTRY: dict[str, PredicateFn] = {}


def register_predicate(name: str) -> Callable[[PredicateFn], PredicateFn]:
    """Decorator registering a repo-state predicate under `name` for kind: repo_state conditions."""

    def _decorate(fn: PredicateFn) -> PredicateFn:
        _PREDICATE_REGISTRY[name] = fn
        return fn

    return _decorate


@register_predicate("roadmap_items_complete")
def roadmap_items_complete(items: list[str], roadmap_path: str | Path = "docs/ROADMAP-PLATFORM.yaml") -> bool:
    """True iff every tier item id in `items` is status 'complete' with every exit criterion 'met'.

    Validates every id exists in the roadmap FIRST, raising KeyError naming every missing id --
    before evaluating completeness of any item. This ordering matters: checking completeness
    first would mask a missing id behind an earlier item's incompleteness (fail-loud, not
    fail-partial). A 'rehomed' exit criterion status does NOT satisfy this predicate -- only
    'met' does; TierItem._normalize_exit_criteria also coerces a bare-string legacy criterion to
    status 'open', so it does not satisfy it either.
    """
    from scripts.platform_roadmap_state import load  # function-local: AGENTS.md import-safety

    doc = load(Path(roadmap_path))
    by_id = {item.id: item for item in doc.tier_items}

    missing = [item_id for item_id in items if item_id not in by_id]
    if missing:
        raise KeyError(f"unknown tier item id(s): {missing}")

    return all(
        by_id[item_id].status == "complete" and all(c.status == "met" for c in by_id[item_id].exit_criteria)
        for item_id in items
    )


@register_predicate("ports_open_coupling_findings_exceed")
def ports_open_coupling_findings_exceed(threshold: int, contract_path: str | Path = "docs/contracts/ports.yaml") -> bool:
    """True iff docs/contracts/ports.yaml carries strictly more than `threshold` coupling_findings rows with status 'open'.

    Raises (never returns False) when the contract is missing or coupling_findings is not a list, so
    evaluate() reports the condition MALFORMED instead of a silent not-fired.
    """
    import yaml as _yaml  # function-local: AGENTS.md import-safety

    data = _yaml.safe_load(Path(contract_path).read_text(encoding="utf-8"))
    findings = data.get("coupling_findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        raise ValueError(f"{contract_path}: coupling_findings is not a list")
    return sum(1 for row in findings if isinstance(row, dict) and row.get("status") == "open") > threshold


@dataclass
class ConditionResult:
    id: str
    kind: str
    description: str
    predicate: Optional[str] = None
    fired: bool = False


@dataclass
class DecisionConditionState:
    decision_id: int
    state: str  # "fired" | "manual-review-due" | "not-due" | "MALFORMED"
    review_by: Optional[str] = None
    conditions: list[ConditionResult] = field(default_factory=list)
    fired_condition_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _find_decision_blocks(content: str) -> list[tuple[int, str, str]]:
    """Split `content` into (decision_id, title, block_text) per '## Decision N: ...' heading.

    Mirrors scripts.decisions_md.parse_decisions_md's own splitter (same _DECISION_HEADING_RE,
    same block-boundary logic) without pulling in its ops_decisions-shaped extraction fields.
    """
    headings = list(_DECISION_HEADING_RE.finditer(content))
    out: list[tuple[int, str, str]] = []
    for i, m in enumerate(headings):
        decision_id = int(m.group(1))
        title = m.group(2).strip()
        block_start = m.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        out.append((decision_id, title, content[block_start:block_end]))
    return out


def _extract_stanza(block: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Locate the column-0 'yaml reversal-conditions' fence within a decision block.

    Returns (found, stanza_text, error). found=False means no fence at all (prose-only decision,
    not monitored). found=True with error set means the fence opened but never closed before the
    next decision heading / EOF (unclosed fence -- malformed).
    """
    open_m = _FENCE_OPEN_RE.search(block)
    if not open_m:
        return False, None, None
    close_m = _FENCE_CLOSE_RE.search(block, open_m.end())
    if not close_m:
        error = "unclosed 'yaml reversal-conditions' fence (no closing ``` found before the next decision heading/EOF)"
        return True, None, error
    return True, block[open_m.end() : close_m.start()], None


def _normalize_review_by(value: Any) -> Optional[date]:
    """Normalize yaml.safe_load's date coercion: unquoted -> datetime.date, quoted -> str."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _evaluate_stanza(
    decision_id: int,
    stanza_text: str,
    predicates: dict[str, PredicateFn],
    today: date,
) -> DecisionConditionState:
    try:
        data = yaml.safe_load(stanza_text)
    except yaml.YAMLError as exc:
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=f"unparseable YAML: {exc}")

    if not isinstance(data, dict):
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error="stanza did not parse to a mapping")

    missing_top = [k for k in _REQUIRED_TOP_KEYS if k not in data]
    if missing_top:
        return DecisionConditionState(
            decision_id=decision_id, state="MALFORMED", error=f"stanza missing top-level key(s): {missing_top}"
        )

    stanza_decision = data.get("decision")
    if stanza_decision != decision_id:
        return DecisionConditionState(
            decision_id=decision_id,
            state="MALFORMED",
            error=f"stanza 'decision: {stanza_decision!r}' does not match header 'Decision {decision_id}'",
        )

    review_by = _normalize_review_by(data.get("review_by"))
    if review_by is None:
        return DecisionConditionState(
            decision_id=decision_id, state="MALFORMED", error=f"unparseable review_by value: {data.get('review_by')!r}"
        )

    conditions_raw = data.get("conditions")
    if not isinstance(conditions_raw, list) or not conditions_raw:
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error="missing/empty 'conditions' list")

    conditions: list[ConditionResult] = []
    fired_ids: list[str] = []
    for cond in conditions_raw:
        result_or_error = _evaluate_condition(decision_id, cond, predicates)
        if isinstance(result_or_error, DecisionConditionState):
            return result_or_error
        conditions.append(result_or_error)
        if result_or_error.fired:
            fired_ids.append(result_or_error.id)

    if fired_ids:
        state = "fired"
    elif review_by <= today:
        state = "manual-review-due"
    else:
        state = "not-due"

    return DecisionConditionState(
        decision_id=decision_id,
        state=state,
        review_by=review_by.isoformat(),
        conditions=conditions,
        fired_condition_ids=fired_ids,
    )


def _evaluate_condition(
    decision_id: int,
    cond: Any,
    predicates: dict[str, PredicateFn],
) -> ConditionResult | DecisionConditionState:
    """Validate + evaluate one condition entry. Returns a DecisionConditionState(MALFORMED, ...)
    on any structural error (the caller propagates it verbatim), else a ConditionResult."""
    if not isinstance(cond, dict):
        error = f"condition entry is not a mapping: {cond!r}"
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=error)

    missing = [k for k in _REQUIRED_CONDITION_KEYS if k not in cond]
    if missing:
        error = f"condition {cond.get('id', '?')!r} missing key(s): {missing}"
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=error)

    cond_id, kind, description = cond["id"], cond["kind"], cond["description"]
    if kind not in _VALID_KINDS:
        error = f"condition {cond_id!r} has unknown kind {kind!r}"
        return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=error)

    # predicate/params are present ONLY for kind: repo_state, and are ABSENT/OPTIONAL for
    # kind: manual (Decision 133's platform-mvp-closes ships id/kind/description only -- never
    # require predicate/params on a manual condition; requiring them would flag it malformed).
    predicate_name = cond.get("predicate")
    params = cond.get("params") or {}
    if kind == "repo_state":
        if "predicate" not in cond:
            error = f"condition {cond_id!r} is kind: repo_state but has no 'predicate' key"
            return DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=error)
        if predicate_name is not None and predicate_name not in predicates:
            return DecisionConditionState(
                decision_id=decision_id,
                state="MALFORMED",
                error=f"condition {cond_id!r} references unregistered predicate {predicate_name!r}",
            )

    fired = False
    if kind == "repo_state" and predicate_name is not None:
        fn = predicates[predicate_name]
        try:
            fired = bool(fn(**params))
        except Exception as exc:  # noqa: BLE001 -- fail loud at the call site, never at import
            return DecisionConditionState(
                decision_id=decision_id,
                state="MALFORMED",
                error=f"condition {cond_id!r} predicate {predicate_name!r} raised: {exc}",
            )

    return ConditionResult(id=cond_id, kind=kind, description=description, predicate=predicate_name, fired=fired)


def evaluate(
    paths: Optional[list[Path]] = None,
    predicates: Optional[dict[str, PredicateFn]] = None,
    today: Optional[date] = None,
) -> list[DecisionConditionState]:
    """Parse + evaluate every reversal-conditions stanza across `paths`.

    paths: override _DECISIONS_MD_PATHS (tests point at a single fixture file).
    predicates: override the production _PREDICATE_REGISTRY (tests inject a fixture predicate --
        never mutates the production registry).
    today: override "today" (UTC) for deterministic date-compare tests.
    """
    resolved_paths = paths if paths is not None else _DECISIONS_MD_PATHS
    active_predicates = predicates if predicates is not None else _PREDICATE_REGISTRY
    today_date = today if today is not None else datetime.now(timezone.utc).date()

    results: list[DecisionConditionState] = []
    seen_decision_ids: set[int] = set()

    for md_path in resolved_paths:
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8", errors="replace")
        for decision_id, _title, block in _find_decision_blocks(content):
            if decision_id in seen_decision_ids:
                continue  # first occurrence across paths wins (mirrors decisions_md.py precedent)
            seen_decision_ids.add(decision_id)
            found, stanza_text, fence_error = _extract_stanza(block)
            if not found:
                continue  # prose-only decision (no fence) -- opt-out by absence, not monitored
            if fence_error:
                results.append(DecisionConditionState(decision_id=decision_id, state="MALFORMED", error=fence_error))
                continue
            assert stanza_text is not None  # found=True and no fence_error implies stanza_text is set
            results.append(_evaluate_stanza(decision_id, stanza_text, active_predicates, today_date))

    return results


def preflight_bucket(
    paths: Optional[list[Path]] = None,
    predicates: Optional[dict[str, PredicateFn]] = None,
    today: Optional[date] = None,
) -> dict:
    """Resilient wrapper for scripts/session/preflight.py -- NEVER raises.

    Returns {"monitored": [...], "surfaced": [...], "malformed": [...]} on success, or a degraded
    bucket carrying an "error" key on any failure (surfacing, not gating -- preflight must not
    crash on a malformed stanza; only the separate validate_reversal_stanzas check gates that).
    """
    try:
        results = evaluate(paths=paths, predicates=predicates, today=today)
    except Exception as exc:  # noqa: BLE001 -- preflight must never crash on this bucket
        return {"monitored": [], "surfaced": [], "malformed": [], "error": str(exc)}

    monitored = [r.decision_id for r in results]
    surfaced = [
        {
            "decision": r.decision_id,
            "state": r.state,
            "review_by": r.review_by,
            "fired_condition_ids": r.fired_condition_ids,
        }
        for r in results
        if r.state in ("fired", "manual-review-due")
    ]
    # Rank fired first, then manual-review-due (orient skill Status Digest ranking rule).
    surfaced.sort(key=lambda row: (0 if row["state"] == "fired" else 1, row["decision"]))
    malformed = [{"decision": r.decision_id, "error": r.error} for r in results if r.state == "MALFORMED"]
    return {"monitored": monitored, "surfaced": surfaced, "malformed": malformed}


def print_decision_conditions(bucket: dict) -> None:
    """Stdout renderer mirroring the existing '--- Provisional contracts due ---' preflight block."""
    print("\n--- Decisions past review date / reversal conditions fired ---")
    if bucket.get("error"):
        print(f"  DEGRADED: {bucket['error']}")
        print()
        return
    if not bucket.get("surfaced") and not bucket.get("malformed"):
        print("  (none)")
    for row in bucket.get("surfaced", []):
        if row["state"] == "fired":
            fired_list = ", ".join(row["fired_condition_ids"])
            print(f"  Decision {row['decision']}: FIRED ({fired_list})")
        else:
            print(f"  Decision {row['decision']}: REVIEW DUE (review_by {row['review_by']})")
    for row in bucket.get("malformed", []):
        print(f"  Decision {row['decision']}: MALFORMED -- {row['error']}")
    print()


def _cli(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(p) for p in args] if args else None
    results = evaluate(paths=paths)
    exit_code = 0
    for r in results:
        if r.state == "MALFORMED":
            print(f"Decision {r.decision_id}: MALFORMED -- {r.error}")
            exit_code = 1
        elif r.state == "fired":
            print(f"Decision {r.decision_id}: fired ({', '.join(r.fired_condition_ids)})")
        elif r.state == "manual-review-due":
            print(f"Decision {r.decision_id}: manual-review-due (review_by {r.review_by})")
        else:
            print(f"Decision {r.decision_id}: not-due (review_by {r.review_by})")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
