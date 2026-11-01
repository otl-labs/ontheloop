"""Roadmap loader plus eligibility, blocking, and CD-gating computation over RoadmapDocument."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.contract_rules import parse_yaml
from scripts.platform_roadmap_gate_rules import _INACTIVE_FOR_TIER, GateRuleEvaluator
from scripts.platform_roadmap_models import _TIER_SHORTCUT_RE, RoadmapDocument, TierItem
from scripts.roadmap.roadmap_archive import hydrate_from_disk

_CD_REF_RE = re.compile(r"\b(CD\.\d+)\b")
_CANONICAL_TIER_ORDER: list[str] = ["T-1", "T0", "T1", "T2", "T3", "T4", "T5"]

# Mirrors scripts/checks/roadmap/validate_candidate_decision_supersession.py's
# _FULLY_SUPERSEDED_RE exactly (that guard's capture group is unneeded here -- we only need
# match/no-match, not the referenced superseder id). Narrow supersession ("narrowly superseded
# by CD.NN") and self-demotion ("[Amendment ...]" prose) do NOT match this phrase and are not
# excluded by it -- same construction as the guard (audit PCD-03 / PCD-01).
_FULLY_SUPERSEDED_RE = re.compile(r"fully superseded by CD\.\d+")


def _item_dict(item: TierItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "tier": item.tier,
        "name": item.name,
        "effort": item.effort,
        "strategic": item.strategic,
        "user_action_required": item.user_action_required,
    }


def _pending_gating_cds_for_item(
    item: TierItem,
    pending_cds: dict[str, Any],
    cd_gates_index: dict[str, list[str]],
) -> dict[str, str]:
    """Return {cd_id: relationship} for pending CDs gating this item's completion.

    Three sources (same logic as blocked_on_cd but applicable to any item status):
      1. item.related_candidate_decisions -> relationship 'related'
      2. cd.gates contains item id or tier shortcut -> relationship 'gates'
      3. item.decision_required_before entries naming a CD id -> relationship 'decision_required_before'

    First source wins when a CD appears in multiple sources.
    """
    blocking: dict[str, str] = {}

    for cd_id in item.related_candidate_decisions:
        if cd_id in pending_cds and cd_id not in blocking:
            blocking[cd_id] = "related"

    for ref, cd_ids in cd_gates_index.items():
        if _TIER_SHORTCUT_RE.match(ref):
            match = item.tier == ref
        else:
            match = item.id == ref
        if match:
            for cd_id in cd_ids:
                if cd_id not in blocking:
                    blocking[cd_id] = "gates"

    drb = item.decision_required_before or []
    drb_entries: list[str] = drb if isinstance(drb, list) else [drb] if isinstance(drb, str) else []
    for entry in drb_entries:
        for m in _CD_REF_RE.finditer(str(entry)):
            cd_id = m.group(1)
            if cd_id in pending_cds and cd_id not in blocking:
                blocking[cd_id] = "decision_required_before"

    return blocking


def load(path: str | Path) -> RoadmapDocument:
    """Parse the YAML roadmap at path, hydrate its archived stubs (Decision 227), and return a validated RoadmapDocument."""
    data = parse_yaml(Path(path).read_text(encoding="utf-8"))
    return RoadmapDocument.model_validate(hydrate_from_disk(data, Path(path)))


def compute_followon_state(doc: RoadmapDocument, plans_dir: Path) -> dict[str, dict[str, Any]]:
    """Compute follow-on planning state for in_progress items (Decision 93: in_progress only).

    Returns dict keyed by item_id with:
      - open_criteria_count: int
      - all_plans_actioned: bool (no in-flight plan targets an open criterion of this item)
      - needs_followon_plan: bool (True iff open_criteria_count > 0 AND all_plans_actioned)
    """
    in_progress = [i for i in doc.tier_items if i.status == "in_progress"]
    if not in_progress:
        return {}

    open_criteria: dict[str, set[str]] = {}
    for item in in_progress:
        open_criteria[item.id] = {c.id for c in item.exit_criteria if c.status == "open"}

    covered_by_plan: dict[str, set[str]] = {item.id: set() for item in in_progress}
    if plans_dir.is_dir():
        for plan_file in sorted(plans_dir.glob("PLAN-*.yaml")):
            try:
                text = plan_file.read_text(encoding="utf-8")
                # Only `closes_criteria` is read below, so a document whose text never mentions
                # the key cannot contribute one -- skip parsing it. A YAML mapping key must
                # appear literally in its own document (quoting keeps the substring; a merge
                # key can only pull in an anchor defined in that same file, whose own text
                # would carry it), so this is equivalent to parsing and finding nothing.
                # Matters because this loop scans every plan on every call: full-parsing all
                # ~350 of them to read one optional key dominated compute_state_dict's runtime.
                if "closes_criteria" not in text:
                    continue
                plan_data = yaml.safe_load(text)
                if not isinstance(plan_data, dict):
                    continue
                closes = plan_data.get("closes_criteria") or []
                if not isinstance(closes, list):
                    continue
                for ref in closes:
                    if not isinstance(ref, str) or ":" not in ref:
                        continue
                    item_id, crit_id = ref.split(":", 1)
                    if item_id in covered_by_plan:
                        covered_by_plan[item_id].add(crit_id)
            except Exception:  # noqa: BLE001
                continue

    result: dict[str, dict[str, Any]] = {}
    for item in in_progress:
        open_set = open_criteria[item.id]
        open_count = len(open_set)
        has_in_flight = bool(open_set & covered_by_plan[item.id])
        all_actioned = not has_in_flight
        result[item.id] = {
            "open_criteria_count": open_count,
            "all_plans_actioned": all_actioned,
            "needs_followon_plan": open_count > 0 and all_actioned,
        }
    return result


class PlatformRoadmapState:
    """Dependency-graph helpers for T-1.4 (preflight) and T-1.2 (planning skill).

    Encapsulates tier-name shortcut resolution per agent_instructions semantics so
    T-1.4 gets a thin shim instead of re-implementing the graph traversal.
    """

    def __init__(self, doc: RoadmapDocument) -> None:
        self._doc = doc
        self._by_id: dict[str, TierItem] = {item.id: item for item in doc.tier_items}

    def tier_complete(self, tier_name: str) -> bool:
        return all(
            item.status == "complete"
            for item in self._doc.tier_items
            if item.tier == tier_name and item.status not in _INACTIVE_FOR_TIER
        )

    def resolve_depends_on(self, item_id: str) -> list[TierItem]:
        item = self._by_id.get(item_id)
        if item is None:
            return []
        result: list[TierItem] = []
        for dep in item.depends_on:
            if _TIER_SHORTCUT_RE.match(dep):
                result.extend(i for i in self._doc.tier_items if i.tier == dep and i.status not in _INACTIVE_FOR_TIER)
            elif dep in self._by_id:
                result.append(self._by_id[dep])
        return result

    def _dep_satisfied(self, dep: str) -> bool:
        if _TIER_SHORTCUT_RE.match(dep):
            return self.tier_complete(dep)
        item = self._by_id.get(dep)
        return item is not None and item.status == "complete"

    def eligible_items(self) -> list[TierItem]:
        return [
            item
            for item in self._doc.tier_items
            if item.status == "not_started" and all(self._dep_satisfied(dep) for dep in item.depends_on)
        ]

    def compute_blocked(self) -> list[TierItem]:
        return [
            item
            for item in self._doc.tier_items
            if item.status == "not_started" and not all(self._dep_satisfied(dep) for dep in item.depends_on)
        ]

    def in_progress_items(self) -> list[TierItem]:
        return [item for item in self._doc.tier_items if item.status == "in_progress"]

    def strategic_pending_items(self) -> list[TierItem]:
        return [item for item in self.eligible_items() if item.strategic]

    def deferred_post_mvp_items(self) -> list[TierItem]:
        return [item for item in self._doc.tier_items if item.status == "deferred_post_mvp"]

    def active_tier(self) -> str | None:
        for tier_name in _CANONICAL_TIER_ORDER:
            tier_items = [i for i in self._doc.tier_items if i.tier == tier_name and i.status not in _INACTIVE_FOR_TIER]
            if tier_items and not all(i.status == "complete" for i in tier_items):
                return tier_name
        return None

    def _blocked_on(self, item: TierItem) -> list[str]:
        return [dep for dep in item.depends_on if not self._dep_satisfied(dep)]

    def evaluate_gates(self) -> list[dict[str, Any]]:
        """Evaluate all cross_tier_gates and return {id, name, verdict, reason} for each."""
        evaluator = GateRuleEvaluator(self)
        result: list[dict[str, Any]] = []
        for gate in self._doc.cross_tier_gates:
            verdict, reason = evaluator.evaluate(gate.rule)
            result.append({"id": gate.id, "name": gate.name, "verdict": verdict, "reason": reason})
        return result

    def blocked_on_cd(self) -> list[dict[str, Any]]:
        """Return eligible items that reference a pending candidate_decision.

        Three sources (for each eligible item):
          1. item.related_candidate_decisions -> relationship 'related'
          2. cd.gates contains item id or tier shortcut -> relationship 'gates'
          3. item.decision_required_before entries naming a CD id -> relationship 'decision_required_before'

        blocking_cds[] is sorted; relationships{} maps cd_id to the relationship type (first source wins).
        """
        pending_cds: dict[str, Any] = {cd.id: cd for cd in self._doc.candidate_decisions if cd.state == "pending"}
        if not pending_cds:
            return []

        cd_gates_index: dict[str, list[str]] = {}
        for cd_id, cd in pending_cds.items():
            for ref in cd.gates:
                cd_gates_index.setdefault(ref, []).append(cd_id)

        result: list[dict[str, Any]] = []
        for item in self.eligible_items():
            blocking = _pending_gating_cds_for_item(item, pending_cds, cd_gates_index)
            if blocking:
                result.append(
                    {
                        "id": item.id,
                        "name": item.name,
                        "blocking_cds": sorted(blocking.keys()),
                        "relationships": blocking,
                        "bootstrap_completion_exempt": item.bootstrap_completion_exempt,
                    }
                )

        return result

    def ratifiable_cds(self) -> list[dict[str, Any]]:
        """Pending CDs carrying realization_evidence -- surfaced to /orient as ready to ratify.

        rec-2468: realization_evidence is str | None; TRUTHY => evidenced/ratifiable, None or
        empty-string => not. This filters on truthiness (`if cd.realization_evidence`), never
        on `is not None` -- an empty string must not count as evidenced.
        """
        return [
            {"id": cd.id, "title": cd.title, "realization_evidence": cd.realization_evidence, "gates": cd.gates}
            for cd in self._doc.candidate_decisions
            if cd.state == "pending" and cd.realization_evidence
        ]

    def realized_but_pending_cds(self) -> list[dict[str, Any]]:
        """Pending CDs whose detail carries a '[Realized' prose marker but no structured
        realization_evidence -- a lower-confidence corroboration/ratification-review signal,
        kept DISTINCT from ratifiable_cds() (Decision 55: prose is not deliberate corroboration).
        """
        result: list[dict[str, Any]] = []
        for cd in self._doc.candidate_decisions:
            if cd.state != "pending" or cd.realization_evidence:
                continue
            detail = cd.detail or ""
            marker_idx = detail.find("[Realized")
            if marker_idx == -1:
                continue
            hint = detail[marker_idx : marker_idx + 80]
            result.append({"id": cd.id, "title": cd.title, "gates": cd.gates, "realized_hint": hint})
        return result

    def _gate_ref_items(self, ref: str) -> list[TierItem]:
        """Resolve one cd.gates entry to its tier_item(s), reusing the _TIER_SHORTCUT_RE
        convention shared with resolve_depends_on/_pending_gating_cds_for_item: a tier
        shortcut resolves to every item whose tier matches; otherwise by_id.get(ref) with a
        skip-on-None tolerance matching the existing resolver (an unresolvable ref is treated
        as vacuously satisfied rather than raising)."""
        if _TIER_SHORTCUT_RE.match(ref):
            return [i for i in self._doc.tier_items if i.tier == ref]
        item = self._by_id.get(ref)
        return [item] if item is not None else []

    def _all_gates_complete(self, gates: list[str]) -> bool:
        """True iff every tier_item resolved from `gates` is complete or non-blocking
        (_INACTIVE_FOR_TIER: reserved/deferred_post_mvp). Empty gates -- or a ref that resolves
        to zero items -- is vacuously True (all() over an empty iterable)."""
        for ref in gates:
            items = self._gate_ref_items(ref)
            if not all(i.status == "complete" or i.status in _INACTIVE_FOR_TIER for i in items):
                return False
        return True

    def realization_candidates(self) -> list[dict[str, Any]]:
        """Pending CDs whose every gated item is realized-but-unevidenced -- surfaced to /orient
        as the lowest-confidence, derived-plausible tier (audit PCD-01), strictly disjoint from
        both ratifiable_cds() and realized_but_pending_cds() by construction:
          - excludes CDs carrying realization_evidence (those are ratifiable_cds()).
          - excludes CDs whose detail carries a '[Realized' prose marker (those are
            realized_but_pending_cds() -- a lower-but-still-distinct-confidence tier).
          - excludes CDs whose detail matches "fully superseded by CD.NN" (that prose belongs
            to the supersession lane, PCD-03/_FULLY_SUPERSEDED_RE -- a fully-superseded-in-prose
            CD's content is replaced, not realized).
          - includes only CDs whose every gated item (via cd.gates, resolved the same way as
            the existing _TIER_SHORTCUT_RE gate-resolution: tier shortcut -> every item in that
            tier; else by_id[ref]) is complete or non-blocking (_INACTIVE_FOR_TIER); empty gates
            are vacuously included UNLESS the CD carries affects (a CD split into gates=[] +
            affects=[...] has no gate-derived realization evidence at all and must not surface
            here just because it gates nothing).

        rec-2468: realization_evidence is str | None; TRUTHY => evidenced (excluded here), None
        or empty-string => not evidenced (a candidate). This filters on truthiness, never on
        `is not None`.

        This is a derived-plausible signal only (Decision 55: no unilateral judgement calls) --
        a candidate here is not deliberately corroborated and must never be treated as
        ratifiable; writing realization_evidence stays a human-confirmed /plan action.
        """
        result: list[dict[str, Any]] = []
        for cd in self._doc.candidate_decisions:
            if cd.state != "pending" or cd.realization_evidence:
                continue
            detail = cd.detail or ""
            if "[Realized" in detail:
                continue
            if _FULLY_SUPERSEDED_RE.search(detail):
                continue
            if not cd.gates and cd.affects:
                # An empty gates list is vacuously all-complete (_all_gates_complete: all() over
                # an empty iterable). A CD carrying affects but no gates has no gate-derived
                # realization evidence at all -- it must not surface here just because it gates
                # nothing. (CD.32-shaped CDs with empty gates AND empty affects are a separate,
                # out-of-scope gap -- see PLAN-roadmap-blocking-edge-semantics context.)
                continue
            if not self._all_gates_complete(cd.gates):
                continue
            result.append({"id": cd.id, "title": cd.title, "gates": cd.gates})
        return result

    def to_preflight_dict(self, plans_dir: Path | None = None) -> dict[str, Any]:
        if plans_dir is None:
            plans_dir = Path(__file__).parent.parent / "docs" / "plans"
        followon = compute_followon_state(self._doc, plans_dir)

        pending_cds: dict[str, Any] = {cd.id: cd for cd in self._doc.candidate_decisions if cd.state == "pending"}
        cd_gates_index: dict[str, list[str]] = {}
        for cd_id, cd in pending_cds.items():
            for ref in cd.gates:
                cd_gates_index.setdefault(ref, []).append(cd_id)

        def _in_progress_dict(item: TierItem) -> dict[str, Any]:
            d = _item_dict(item)
            state = followon.get(item.id, {})
            d["open_criteria_count"] = state.get("open_criteria_count", 0)
            d["all_plans_actioned"] = state.get("all_plans_actioned", True)
            d["needs_followon_plan"] = state.get("needs_followon_plan", False)
            if item.bootstrap_completion_exempt:
                d["completion_blocked_on_cd"] = []
            else:
                blocking = _pending_gating_cds_for_item(item, pending_cds, cd_gates_index)
                d["completion_blocked_on_cd"] = sorted(blocking.keys())
            return d

        return {
            "next_eligible": [_item_dict(i) for i in self.eligible_items() if not i.strategic],
            "in_progress": [_in_progress_dict(i) for i in self.in_progress_items()],
            "blocked": [{**_item_dict(i), "blocked_on": self._blocked_on(i)} for i in self.compute_blocked()],
            "strategic_pending": [_item_dict(i) for i in self.strategic_pending_items()],
            "deferred_post_mvp": [_item_dict(i) for i in self.deferred_post_mvp_items()],
            "active_tier": self.active_tier(),
            "blocked_on_cd": self.blocked_on_cd(),
            "ratifiable_cds": self.ratifiable_cds(),
            "realized_but_pending_cds": self.realized_but_pending_cds(),
            "realization_candidates": self.realization_candidates(),
            "gate_evaluations": self.evaluate_gates(),
        }


def compute_state_dict(yaml_path: Path, *, latest_decision_ts: str | None = None) -> dict[str, Any]:
    """Compute roadmap state and return a JSON-serialisable dict for session_preflight.

    On parse error or missing file, returns a dict with an 'error' key and empty lists.
    When latest_decision_ts is provided and YAML mtime is strictly newer, a stale_cache_note
    is included in the result.
    """
    try:
        doc = load(yaml_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "next_eligible": [],
            "in_progress": [],
            "blocked": [],
            "strategic_pending": [],
            "deferred_post_mvp": [],
            "active_tier": None,
            "blocked_on_cd": [],
            "ratifiable_cds": [],
            "realized_but_pending_cds": [],
            "realization_candidates": [],
            "gate_evaluations": [],
        }

    plans_dir = Path(yaml_path).parent / "plans"
    state = PlatformRoadmapState(doc)
    result = state.to_preflight_dict(plans_dir=plans_dir)

    if latest_decision_ts is not None:
        try:
            yaml_mtime = datetime.fromtimestamp(Path(yaml_path).stat().st_mtime, tz=timezone.utc)
            decision_dt = datetime.fromisoformat(latest_decision_ts.replace(" ", "T").rstrip("Z"))
            if decision_dt.tzinfo is None:
                decision_dt = decision_dt.replace(tzinfo=timezone.utc)
            if yaml_mtime > decision_dt:
                result["stale_cache_note"] = (
                    f"roadmap edits awaiting ratification: YAML mtime {yaml_mtime.isoformat()} "
                    f"newer than latest decision {decision_dt.isoformat()}"
                )
        except Exception:  # noqa: BLE001
            pass

    return result
