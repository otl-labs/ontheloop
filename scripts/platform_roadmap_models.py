# complexity-waiver: decision-43
"""Pydantic schema for docs/ROADMAP-PLATFORM.yaml: models, constants, and graph validation."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.platform_roadmap_gate_rules import _INACTIVE_FOR_TIER, GateRuleParser

_SUPPORTED_VERSIONS: frozenset[int] = frozenset({1})
_OPS_DECISIONS_RE = re.compile(r"^ops_decisions:dec-\d+$")
_TIER_SHORTCUT_RE = re.compile(r"^T-?\d+$")

# Canonical helper table: name -> expected arity. Populated from document.gate_helpers
# at validation time; this module-level dict is the fallback for test fixtures without
# a gate_helpers section.
_GATE_HELPERS: dict[str, int] = {
    "tier_complete": 1,
    "all_in_tier_with_status": 2,
    "grace_period_elapsed": 2,
    "item_field_eq": 3,
}


class GateHelper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    arity: int
    params: list[dict[str, Any]] = Field(default_factory=list)
    returns: str = "bool"
    semantics: str = ""


class DocumentMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    version: int
    status: str
    filed_via: str
    description: str = ""
    agent_instructions: str = ""
    gate_helpers: list[GateHelper] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v not in _SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported document version {v}. Supported: {sorted(_SUPPORTED_VERSIONS)}")
        return v

    @field_validator("filed_via")
    @classmethod
    def _check_filed_via(cls, v: str) -> str:
        if v == "pending_log_decision_lambda" or _OPS_DECISIONS_RE.match(v):
            return v
        raise ValueError(f"Invalid filed_via '{v}'. Must be 'pending_log_decision_lambda' or 'ops_decisions:dec-<NNN>'")


class CriterionBlocker(BaseModel):
    """One blocking edge on an ExitCriterion: this criterion cannot close until `ref` reaches
    `until`. NOTE: 'disposition' is deliberately NOT a member -- it names a human activity with
    no observable terminus on any schema object, unlike a tier_item/CD's own status/state."""

    model_config = ConfigDict(extra="forbid")
    ref: str
    until: Literal["complete", "ratified"]
    note: str | None = None


class StartEdge(BaseModel):
    """A START order on a tier_item: the item may be planned once `ref` (one of its depends_on
    entries) is in_progress; depends_on still orders completion."""

    model_config = ConfigDict(extra="forbid")
    ref: str
    once: Literal["in_progress"]
    note: str | None = None


class ExitCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    status: Literal["open", "met", "rehomed"] = "open"
    met_by: str | None = None
    blocked_by: list[CriterionBlocker] = Field(default_factory=list)


class TierItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    tier: str
    name: str
    intent: str = ""
    depends_on: list[str] = Field(default_factory=list)
    start_edges: list[StartEdge] = Field(default_factory=list)
    files_in_scope: list[str] = Field(default_factory=list)
    exit_criteria: list[ExitCriterion] = Field(default_factory=list)
    related_candidate_decisions: list[str] = Field(default_factory=list)
    related_intents: list[str] | None = None
    related_decisions: list[int] | None = None
    effort: Literal["XS", "S", "M", "L", "XL"] = "S"

    @field_validator("exit_criteria", mode="before")
    @classmethod
    def _normalize_exit_criteria(cls, v: Any) -> list[Any]:
        if not isinstance(v, list):
            return v
        result: list[Any] = []
        for i, item in enumerate(v):
            if isinstance(item, str):
                result.append({"id": f"c{i + 1}", "text": item, "status": "open", "met_by": None})
            else:
                result.append(item)
        return result

    strategic: bool = False
    status: Literal["not_started", "in_progress", "complete", "reserved", "deferred_post_mvp"] = "not_started"
    completed_at: str | None = None
    note: str | None = None
    progress_note: str | None = None
    bootstrap_completion_exempt: bool = False
    decision_required_before: list[str] | None = None
    decomposition_hints: dict[str, Any] | None = None
    consumer_fixups: list[str] | None = None
    minimum_verbs: list[str] | None = None
    rename_history: dict[str, Any] | None = None
    user_action_required: bool | None = None


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    detail: str = ""
    gates: list[str] = Field(default_factory=list)
    affects: list[str] = Field(default_factory=list)
    state: Literal["pending", "ratified", "superseded"] = "pending"
    ratified_as: str | None = None
    realization_evidence: str | None = Field(
        default=None,
        description=(
            "str|None; TRUTHY => ratifiable/realized-evidenced, None or empty-string => not. "
            "ratifiable_cds/realization_candidates filter on truthiness (not 'is not None'), so "
            "'' is excluded."
        ),
    )
    decision_required_before: list[str] | str | None = None
    bootstrap_allowance: bool = False
    filed_via: str | None = None
    narrowly_supersedes: dict[str, Any] | None = None
    supersedes_intents: list[str] | None = None
    supersedes_decisions: list[Any] | None = None
    retires_intents: list[str] | None = None
    demotes_intents: list[str] | None = None
    discipline_points: list[Any] | None = None
    enforcement_mechanism: str | None = None


class CrossTierGate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    rule: str
    rationale: str = ""


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    question: str
    resolution_tier: str = ""
    notes: str = ""
    status: Literal["open", "resolved", "closed", "promoted"] = "open"
    resolution_ref: str | None = None


class KnownGap(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    gap: str
    notes: str = ""
    status: Literal["open", "resolved", "closed", "promoted"] = "open"
    resolution_ref: str | None = None


class NorthStar(BaseModel):
    """The roadmap's north_star key is a pointer to the Tier A contract (Decision 243 clause 1)."""

    model_config = ConfigDict(extra="forbid")
    source: str = "docs/contracts/north-star.yaml"


class FoundationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    notes: str = ""


class RoadmapDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document: DocumentMeta
    north_star: NorthStar = Field(default_factory=NorthStar)
    cost_projection: dict[str, Any] = Field(default_factory=dict)
    rebuild_vs_refactor: dict[str, Any] = Field(default_factory=dict)
    foundation_already_shipped: list[FoundationItem] = Field(default_factory=list)
    candidate_decisions: list[CandidateDecision] = Field(default_factory=list)
    tier_items: list[TierItem] = Field(default_factory=list)
    cross_tier_gates: list[CrossTierGate] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    known_gaps: list[KnownGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_graph(self) -> RoadmapDocument:
        item_ids: set[str] = {item.id for item in self.tier_items}
        helpers: dict[str, int] = (
            {gh.name: gh.arity for gh in self.document.gate_helpers} if self.document.gate_helpers else _GATE_HELPERS.copy()
        )

        # (a) id uniqueness
        seen: set[str] = set()
        for item in self.tier_items:
            if item.id in seen:
                raise ValueError(f"Duplicate tier_item id: '{item.id}'")
            seen.add(item.id)

        # (b) depends_on resolution
        for item in self.tier_items:
            for dep in item.depends_on:
                if not (_TIER_SHORTCUT_RE.match(dep) or dep in item_ids):
                    raise ValueError(
                        f"tier_item '{item.id}': depends_on '{dep}' does not resolve to a known id or tier shortcut"
                    )

        # (b2) start_edges: concrete refs inside depends_on, no duplicates, and an item carrying
        # start edges may not complete ahead of its dependencies.
        by_id = {item.id: item for item in self.tier_items}
        for item in self.tier_items:
            edge_refs: set[str] = set()
            for edge in item.start_edges:
                if _TIER_SHORTCUT_RE.match(edge.ref):
                    raise ValueError(f"tier_item '{item.id}': start_edges ref '{edge.ref}' must not be a tier shortcut")
                if edge.ref not in item.depends_on:
                    raise ValueError(f"tier_item '{item.id}': start_edges ref '{edge.ref}' is not in its own depends_on")
                if edge.ref in edge_refs:
                    raise ValueError(f"tier_item '{item.id}': duplicate start_edges ref '{edge.ref}'")
                edge_refs.add(edge.ref)
            if item.start_edges and item.status == "complete":
                for dep in item.depends_on:
                    if _TIER_SHORTCUT_RE.match(dep):
                        unfinished = any(
                            i.tier == dep and i.status not in _INACTIVE_FOR_TIER and i.status != "complete"
                            for i in self.tier_items
                        )
                    else:
                        unfinished = by_id[dep].status != "complete"
                    if unfinished:
                        raise ValueError(
                            f"tier_item '{item.id}' carries start_edges and is complete while depends_on '{dep}' "
                            "is not complete"
                        )

        # (c) cycle detection (DFS, gray-set algorithm)
        adj: dict[str, list[str]] = {item.id: [] for item in self.tier_items}
        for item in self.tier_items:
            for dep in item.depends_on:
                if dep in item_ids:
                    adj[item.id].append(dep)
                elif _TIER_SHORTCUT_RE.match(dep):
                    adj[item.id].extend(i.id for i in self.tier_items if i.tier == dep)

        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node: str) -> None:
            in_stack.add(node)
            visited.add(node)
            for neighbor in adj.get(node, []):
                if neighbor in in_stack:
                    raise ValueError(f"Dependency cycle detected: '{node}' -> '{neighbor}'")
                if neighbor not in visited:
                    _dfs(neighbor)
            in_stack.discard(node)

        for node in item_ids:
            if node not in visited:
                _dfs(node)

        # (d) candidate_decisions.gates resolution
        for cd in self.candidate_decisions:
            for ref in cd.gates:
                if not (_TIER_SHORTCUT_RE.match(ref) or ref in item_ids):
                    raise ValueError(f"CandidateDecision '{cd.id}': gate ref '{ref}' does not resolve")

        # (d2) candidate_decisions.affects resolution -- same tier-only resolution as gates
        # (affects is in-scope-of, not completion-blocking; it never targets a CD).
        for cd in self.candidate_decisions:
            for ref in cd.affects:
                if not (_TIER_SHORTCUT_RE.match(ref) or ref in item_ids):
                    raise ValueError(f"CandidateDecision '{cd.id}': affects ref '{ref}' does not resolve")

        # (d3) exit_criteria[].blocked_by resolution + until/kind agreement. Deliberately
        # EXCLUDED from the step-(c) depends_on adjacency: these edges close real rings (e.g. a
        # criterion blocked_by the very item that rehomed part of its scope), so folding them
        # into the load-time cycle rejector would make the roadmap unloadable rather than
        # reporting the deadlock (see docs/contracts/exit-criteria-ledger.yaml governance_notes).
        cd_ids: set[str] = {cd.id for cd in self.candidate_decisions}
        for item in self.tier_items:
            for crit in item.exit_criteria:
                for blocker in crit.blocked_by:
                    ref = blocker.ref
                    if _TIER_SHORTCUT_RE.match(ref) or ref in item_ids:
                        if blocker.until != "complete":
                            raise ValueError(
                                f"tier_item '{item.id}' criterion '{crit.id}': blocked_by ref '{ref}' "
                                f"resolves to a tier_item/tier-shortcut but until='{blocker.until}' "
                                "(must be 'complete')"
                            )
                    elif ref in cd_ids:
                        if blocker.until != "ratified":
                            raise ValueError(
                                f"tier_item '{item.id}' criterion '{crit.id}': blocked_by ref '{ref}' "
                                f"resolves to a CandidateDecision but until='{blocker.until}' "
                                "(must be 'ratified')"
                            )
                    else:
                        raise ValueError(
                            f"tier_item '{item.id}' criterion '{crit.id}': blocked_by ref '{ref}' "
                            "does not resolve to a known tier_item id, CD id, or tier shortcut"
                        )

        # (e) gate-rule grammar validation
        for gate in self.cross_tier_gates:
            try:
                GateRuleParser.validate(gate.rule, helpers)
            except ValueError as exc:
                raise ValueError(f"CrossTierGate '{gate.id}': {exc}") from exc

        for cd in self.candidate_decisions:
            drb = cd.decision_required_before
            if drb:
                # Widened to list[str] | str | None (T-1.12). Each grammar-shaped string is
                # validated; non-grammar prose entries (e.g. "T0.13 may start") have no
                # function calls and are no-ops under GateRuleParser. List form is iterated.
                # The field's own type (list[str] | str | None) already guarantees every entry
                # is a str -- no isinstance guard needed here.
                entries = drb if isinstance(drb, list) else [drb]
                for entry in entries:
                    try:
                        GateRuleParser.validate(entry, helpers)
                    except ValueError as exc:
                        raise ValueError(f"CandidateDecision '{cd.id}'.decision_required_before: {exc}") from exc

        # (f) no not_started/in_progress item may depend directly on a deferred_post_mvp item
        deferred_ids: set[str] = {i.id for i in self.tier_items if i.status == "deferred_post_mvp"}
        if deferred_ids:
            for item in self.tier_items:
                if item.status in {"not_started", "in_progress"}:
                    for dep in item.depends_on:
                        if dep in deferred_ids:
                            raise ValueError(
                                f"tier_item '{item.id}' (status={item.status}) depends_on "
                                f"'{dep}' which is deferred_post_mvp -- no live item may depend on a parked item"
                            )

        # (g) met/rehomed criteria require a non-empty met_by
        for item in self.tier_items:
            for crit in item.exit_criteria:
                if crit.status in {"met", "rehomed"} and not crit.met_by:
                    raise ValueError(
                        f"tier_item '{item.id}' criterion '{crit.id}': status='{crit.status}' requires a non-empty met_by"
                    )

        # (h) rehomed criterion's met_by must resolve to a known tier_item id
        for item in self.tier_items:
            for crit in item.exit_criteria:
                if crit.status == "rehomed" and crit.met_by and crit.met_by not in item_ids:
                    raise ValueError(
                        f"tier_item '{item.id}' criterion '{crit.id}': "
                        f"rehomed met_by='{crit.met_by}' does not resolve to a known tier_item id"
                    )

        # (i) non-open open_questions require a non-empty resolution_ref
        for oq in self.open_questions:
            if oq.status != "open" and not oq.resolution_ref:
                raise ValueError(f"open_question '{oq.id}': status='{oq.status}' requires a non-empty resolution_ref")

        # (j) non-open known_gaps require a non-empty resolution_ref
        for kg in self.known_gaps:
            if kg.status != "open" and not kg.resolution_ref:
                raise ValueError(f"known_gap '{kg.id}': status='{kg.status}' requires a non-empty resolution_ref")

        return self
