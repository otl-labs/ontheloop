"""Tests for scripts/platform_roadmap_models.py: document/tier_item/candidate_decision schema
shape, filed_via union, CD25 schema amendments, user_action_required threading, and coverage
top-up for _validate_graph raise branches.

Migrated from the retired tests/test_platform_roadmap_models.py monolith (Decision 128
decompose-don't-raise / Decision 131 mirror convention). Shared fixture helpers live in
tests/fixtures/platform_roadmap_models.py -- never import from a sibling test_*.py module.
"""

from __future__ import annotations

import pytest

from scripts.roadmap.platform_roadmap import RoadmapDocument, TierItem, load
from tests.fixtures.platform_roadmap_models import _BASE_DOC, _LIVE_ROADMAP, _doc, _item, _state_from_doc

# ---------------------------------------------------------------------------
# TestStructuralValidation
# ---------------------------------------------------------------------------


class TestStructuralValidation:
    def test_tier_items_wrong_type_raises(self) -> None:
        with pytest.raises(Exception):
            RoadmapDocument.model_validate(_doc(tier_items="not-a-list"))

    def test_missing_document_raises(self) -> None:
        with pytest.raises(Exception):
            RoadmapDocument.model_validate({"tier_items": []})

    def test_valid_minimal_doc_passes(self) -> None:
        doc = RoadmapDocument.model_validate(_BASE_DOC)
        assert doc.document.id == "ROADMAP-TEST"

    def test_unsupported_version_raises(self) -> None:
        d = _doc()
        d["document"]["version"] = 99
        with pytest.raises(Exception, match="Unsupported"):
            RoadmapDocument.model_validate(d)


# ---------------------------------------------------------------------------
# TestFiledViaUnion
# ---------------------------------------------------------------------------


class TestFiledViaUnion:
    def test_pending_log_decision_lambda_accepted(self) -> None:
        d = _doc()
        d["document"]["filed_via"] = "pending_log_decision_lambda"
        doc = RoadmapDocument.model_validate(d)
        assert doc.document.filed_via == "pending_log_decision_lambda"

    def test_ops_decisions_ref_accepted(self) -> None:
        d = _doc()
        d["document"]["filed_via"] = "ops_decisions:dec-042"
        doc = RoadmapDocument.model_validate(d)
        assert doc.document.filed_via == "ops_decisions:dec-042"

    def test_arbitrary_string_raises(self) -> None:
        d = _doc()
        d["document"]["filed_via"] = "something_else"
        with pytest.raises(Exception, match="Invalid filed_via"):
            RoadmapDocument.model_validate(d)

    def test_ops_decisions_without_number_raises(self) -> None:
        d = _doc()
        d["document"]["filed_via"] = "ops_decisions:dec-abc"
        with pytest.raises(Exception, match="Invalid filed_via"):
            RoadmapDocument.model_validate(d)


# ---------------------------------------------------------------------------
# TestCD25SchemaAmendments -- T-1.12 amendments per PLAN-cd25-platform-gap-sequencing
# ---------------------------------------------------------------------------


class TestCD25SchemaAmendments:
    def test_bootstrap_completion_exempt_accepts_true(self) -> None:
        item = {**_item("T-1.11"), "bootstrap_completion_exempt": True}
        doc = RoadmapDocument.model_validate(_doc(tier_items=[item]))
        assert doc.tier_items[0].bootstrap_completion_exempt is True

    def test_bootstrap_completion_exempt_defaults_false(self) -> None:
        doc = RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1")]))
        assert doc.tier_items[0].bootstrap_completion_exempt is False

    def test_tier_item_decision_required_before_accepts_list(self) -> None:
        item = {**_item("T1.12"), "decision_required_before": ["CD.16 ratifies"]}
        doc = RoadmapDocument.model_validate(_doc(tier_items=[item]))
        assert doc.tier_items[0].decision_required_before == ["CD.16 ratifies"]

    def test_tier_item_decision_required_before_accepts_none(self) -> None:
        doc = RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1")]))
        assert doc.tier_items[0].decision_required_before is None

    def test_cd_decision_required_before_accepts_list(self) -> None:
        cd = {"id": "CD.X", "title": "T", "decision_required_before": ["T0.13 may start"]}
        doc = RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))
        assert doc.candidate_decisions[0].decision_required_before == ["T0.13 may start"]

    def test_cd_decision_required_before_accepts_string_still(self) -> None:
        cd = {"id": "CD.X", "title": "T", "decision_required_before": "prose entry"}
        doc = RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))
        assert doc.candidate_decisions[0].decision_required_before == "prose entry"

    def test_cd_decision_required_before_list_with_bad_helper_raises(self) -> None:
        cd = {"id": "CD.X", "title": "T", "decision_required_before": ["bogus_helper(T1.1)"]}
        with pytest.raises(Exception, match="Unknown"):
            RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))

    def test_bootstrap_allowance_accepts_true(self) -> None:
        cd = {"id": "CD.25", "title": "T", "bootstrap_allowance": True}
        doc = RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))
        assert doc.candidate_decisions[0].bootstrap_allowance is True

    def test_bootstrap_allowance_defaults_false(self) -> None:
        cd = {"id": "CD.X", "title": "T"}
        doc = RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))
        assert doc.candidate_decisions[0].bootstrap_allowance is False

    def test_decomposition_hints_dict_accepted(self) -> None:
        item = {**_item("T-1.12"), "decomposition_hints": {"split_by": "subsystem", "atomic_plans": ["a", "b"]}}
        doc = RoadmapDocument.model_validate(_doc(tier_items=[item]))
        assert doc.tier_items[0].decomposition_hints == {"split_by": "subsystem", "atomic_plans": ["a", "b"]}

    def test_decomposition_hints_list_rejected(self) -> None:
        item = {**_item("T4.1"), "decomposition_hints": ["plan_a", "plan_b"]}
        with pytest.raises(Exception):
            RoadmapDocument.model_validate(_doc(tier_items=[item]))

    def test_tier_item_extra_forbid_rejects_bogus_field(self) -> None:
        item = {**_item("T0.1"), "bogus_field": 1}
        with pytest.raises(Exception, match="bogus_field|Extra inputs"):
            RoadmapDocument.model_validate(_doc(tier_items=[item]))

    def test_cd_extra_forbid_rejects_bogus_field(self) -> None:
        cd = {"id": "CD.X", "title": "T", "bogus_field": 1}
        with pytest.raises(Exception, match="bogus_field|Extra inputs"):
            RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))

    def test_cd_state_literal_rejects_unknown_value(self) -> None:
        cd = {"id": "CD.X", "title": "T", "state": "Retired"}
        with pytest.raises(Exception, match="state"):
            RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))

    @pytest.mark.parametrize("state", ["pending", "ratified", "superseded"])
    def test_cd_state_literal_accepts_valid_values(self, state: str) -> None:
        cd = {"id": "CD.X", "title": "T", "state": state}
        doc = RoadmapDocument.model_validate(_doc(candidate_decisions=[cd]))
        assert doc.candidate_decisions[0].state == state

    def test_other_classes_still_extra_ignore(self) -> None:
        # KnownGap uses extra="ignore"; unknown fields are dropped without error.
        d = _doc(known_gaps=[{"id": "KG.X", "gap": "g", "bogus_field": "ignored"}])
        doc = RoadmapDocument.model_validate(d)
        assert doc.known_gaps[0].gap == "g"

    def test_live_platform_yaml_bootstrap_exemption_set(self) -> None:
        """Asserts the per-item bootstrap_completion_exempt set in the live YAML
        matches the canonical expected set verbatim (Part 8C of
        docs/INTENT-pre-codegen-contract-ratification.md).

        close-audit-ulf-02 (2026-07-03): CD.1/CD.2/CD.13/CD.20/CD.21/CD.26 ratified
        (Decisions 108-113). Strips the 16 discharged items whose gating CDs are now
        ALL ratified (43 -> 27): T-1.0, T-1.1, T-1.2, T-1.3, T-1.4, T-1.5, T-1.6, T0.2,
        T0.5, T0.11, T0.14, T2.3, T2.10, T2.12, T2.13, T2.16b. Every other exempt item
        (still gated by a pending CD.25/CD.10/CD.12/CD.4/CD.5/CD.8/CD.15/CD.34 -- e.g.
        T0.3 is ALSO gated by pending CD.10, so it stays despite CD.26 ratifying) is
        retained verbatim.

        cd-ratification-wave (2026-07-17): CD.9/CD.5/CD.4/CD.8 ratified (Decisions
        137/138/139/140) and CD.19 ratified as a resolved-by-events closure (Decision
        141) via the Decision 105 lane. Strips the 4 items whose gating CDs are now
        ALL ratified (17 -> 13): T0.9 (sole gate CD.4), T0.10 (sole gate CD.5), T2.4
        (CD.9 + already-ratified CD.31), T2.2 (CD.6 + CD.19, both now ratified).
        T2.5 (co-gating CD.15 still pending) is the only item touched by CD.8's
        ratification, and it stays exempt per the strip-only-when-ALL-gating-CDs-
        ratified safety rule.
        """
        doc = load(_LIVE_ROADMAP)
        expected = {
            "T0.6",
            "T0.7a",
            "T0.7b",
            "T0.7c",
            "T0.8",
            "T0.12",
            "T0.13",
            "T0.12.5",
            "T0.12.7",
            # Migration-realized items (platform-roadmap-reconciliation 2026-05-31):
            # same circular ratification bind as the items above -- T0.7b not yet built.
            "T0.3",
            "T2.1",
            # Scope (c) realized-ahead-of-ratification additions (2026-06-09 roadmap audit
            # integration, finding F-002): items completed under pending gating CDs that
            # ratify post-hoc via the ops portal vehicle. Exemption ends when the gating
            # CD ratifies (CD.8+CD.15 -- CD.15 still pending -- respectively; CD.2/CD.20/
            # CD.21/CD.26 slices discharged by close-audit-ulf-02; CD.5/CD.9/CD.4/CD.19
            # slices discharged by cd-ratification-wave, see docstring above).
            "T2.5",
            "T2.17",
        }
        # dec-118 (Ratify CD.25, 2026-07-03) discharged the CD.25-scoped exemption for
        # the 10 items gated solely by CD.25 (T-1.11..T-1.19, T0.12.6); they are no
        # longer bootstrap_completion_exempt. T0.12.5 (CD.29) and T0.12.7 (CD.10) remain
        # exempt -- gated by other still-pending CDs.
        actual = {item.id for item in doc.tier_items if item.bootstrap_completion_exempt}
        assert actual == expected, f"missing={expected - actual} extra={actual - expected}"

    def test_live_platform_yaml_cd25_present(self) -> None:
        """Asserts CD.25 is present with correct shape per INTENT v4 Part 7."""
        doc = load(_LIVE_ROADMAP)
        cd25 = next((c for c in doc.candidate_decisions if c.id == "CD.25"), None)
        assert cd25 is not None, "CD.25 missing from candidate_decisions[]"
        assert cd25.state == "ratified"
        assert cd25.bootstrap_allowance is True
        assert isinstance(cd25.decision_required_before, list)
        assert len(cd25.decision_required_before) >= 1

    def test_live_platform_yaml_t112_collision_resolved(self) -> None:
        """Asserts T1.12 is the Class B Lambda ratification wave, T1.13 is CI-RCA."""
        doc = load(_LIVE_ROADMAP)
        by_id = {item.id: item for item in doc.tier_items}
        assert "T1.12" in by_id, "T1.12 (Class B Lambda ratification wave) missing"
        assert "Class B" in by_id["T1.12"].name, by_id["T1.12"].name
        assert "T1.13" in by_id, "T1.13 (CI-RCA methodology contract) missing"
        assert "CI-RCA" in by_id["T1.13"].name, by_id["T1.13"].name


# ---------------------------------------------------------------------------
# TestUserActionRequired -- T-1.20: user_action_required threading
# ---------------------------------------------------------------------------


class TestUserActionRequired:
    def test_user_action_required_true_in_item_dict(self) -> None:
        doc = _doc(tier_items=[{**_item("T0.1"), "user_action_required": True}])
        state = _state_from_doc(doc)
        full = state.to_preflight_dict()
        item = next(i for i in full["next_eligible"] if i["id"] == "T0.1")
        assert item["user_action_required"] is True

    def test_user_action_required_none_default(self) -> None:
        doc = _doc(tier_items=[_item("T0.1")])
        state = _state_from_doc(doc)
        full = state.to_preflight_dict()
        item = next(i for i in full["next_eligible"] if i["id"] == "T0.1")
        assert item["user_action_required"] is None

    def test_user_action_required_false(self) -> None:
        doc = _doc(tier_items=[{**_item("T0.1"), "user_action_required": False}])
        state = _state_from_doc(doc)
        full = state.to_preflight_dict()
        item = next(i for i in full["next_eligible"] if i["id"] == "T0.1")
        assert item["user_action_required"] is False


# ---------------------------------------------------------------------------
# TestModelsCoverageTopUp -- closes per-file coverage gaps identified by code
# review after the platform_roadmap decomposition (coverage partition risk,
# rec-2633). test_cd_bad_gate_ref_raises and test_gate_rule_rejected_in_model
# now live in test_platform_roadmap_gate_rules.py (their natural home post-
# decomposition), which orphaned models.py's own _validate_graph raise-branch
# coverage from THIS suite's run. These are fresh, independently-written cases
# for the same raise sites -- not moved from the gate_rules suite.
# ---------------------------------------------------------------------------


class TestModelsCoverageTopUp:
    def test_cd_gate_ref_does_not_resolve_raises(self) -> None:
        # _validate_graph check (d): candidate_decisions[].gates entries must
        # resolve to a known tier_item id or tier shortcut.
        d = _doc(candidate_decisions=[{"id": "CD.X", "title": "T", "gates": ["T999.0"]}])
        with pytest.raises(Exception, match="does not resolve"):
            RoadmapDocument.model_validate(d)

    def test_cross_tier_gate_bad_rule_raises(self) -> None:
        # _validate_graph check (e): cross_tier_gates[].rule must validate
        # against the gate_helpers grammar; GateRuleParser's ValueError is
        # caught and re-raised with CrossTierGate context.
        d = _doc(cross_tier_gates=[{"id": "G.X", "name": "test", "rule": "bogus_helper(T0.1)", "rationale": "test"}])
        with pytest.raises(Exception, match="CrossTierGate 'G.X'"):
            RoadmapDocument.model_validate(d)

    def test_exit_criteria_non_list_input_short_circuits_normalizer(self) -> None:
        # _normalize_exit_criteria's mode="before" guard ("if not isinstance(v,
        # list): return v") returns non-list input unchanged; pydantic's own
        # list-type check then rejects it downstream -- proving the guard
        # clause itself executed rather than the per-item string-promotion loop.
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="valid list"):
            TierItem(id="X", tier="T0", name="t", exit_criteria=42)  # type: ignore[arg-type]


class TestNorthStarPointer:
    _PATH = "docs/contracts/north-star.yaml"

    def test_default_source_is_contract_path(self) -> None:
        assert RoadmapDocument.model_validate(_doc()).north_star.source == self._PATH

    @pytest.mark.parametrize(
        "block",
        [{"principles": []}, {"source": "docs/contracts/north-star.yaml", "bogus": 1}],
    )
    def test_extra_keys_rejected(self, block: dict) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="principles|bogus"):
            RoadmapDocument.model_validate(_doc(north_star=block))

    def test_live_roadmap_carries_the_pointer(self) -> None:
        assert load(_LIVE_ROADMAP).north_star.source == self._PATH
