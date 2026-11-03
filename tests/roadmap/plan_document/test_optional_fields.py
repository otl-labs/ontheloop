"""Tests for scripts/roadmap/plan_document.py -- additive optional-field schema extensions
(closes_criteria, graduation disposition, fallback_reevaluation), covering the T1.11 exit
criteria.

Split from the former tests/test_plan_document.py monolith (PLAN-decompose-test-plan-document,
Decision 128 decompose-by-default): TestClosesCriteria, TestGraduationDisposition,
TestFallbackReevaluation relocated, with two permitted edits inside
TestFallbackReevaluation::test_all_historical_plans_still_valid_with_field_absent -- re-rooted its
docs/plans glob on REPO_ROOT (was two parent-dir hops off __file__, which silently resolved wrong
two directories deeper and globbed vacuously empty) and added an in-test floor assertion so the
non-vacuity guard is durable rather than external. This is deliberately the smallest of the three
multi-class modules -- PLAN-plan-resolution-content-keyed extends it next.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from scripts.roadmap.plan_document import PlanDocument, WeakeningDisposition, load, validate_paths
from tests.fixtures.plan_document_helpers import REPO_ROOT, _base, _mutate


class TestClosesCriteria:
    """T-1.23: closes_criteria is an additive optional field (Decision 85)."""

    def test_closes_criteria_defaults_to_empty_list(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.closes_criteria == []

    def test_closes_criteria_accepts_list_of_strings(self) -> None:
        d = _mutate(closes_criteria=["T-1.23:c1", "T-1.23:c2"])
        doc = PlanDocument.model_validate(d)
        assert doc.closes_criteria == ["T-1.23:c1", "T-1.23:c2"]

    def test_closes_criteria_empty_list_explicit(self) -> None:
        d = _mutate(closes_criteria=[])
        doc = PlanDocument.model_validate(d)
        assert doc.closes_criteria == []

    def test_unknown_field_still_rejected(self) -> None:
        d = _mutate(some_unknown_field="oops")
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_closes_criteria_present_does_not_break_extra_forbid(self) -> None:
        d = _mutate(closes_criteria=["T0.4:c1"], some_bad="x")
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_closes_criteria_rejects_prose_with_whitespace(self) -> None:
        """T-1.23 field_validator: narrative/prose entries belong in context:, not closes_criteria."""
        d = _mutate(closes_criteria=["APPLY-PATH SPLIT (critical -- some narrative caveat)."])
        with pytest.raises(ValidationError, match="contains whitespace"):
            PlanDocument.model_validate(d)

    def test_closes_criteria_rejects_missing_colon(self) -> None:
        d = _mutate(closes_criteria=["T2.18c1"])
        with pytest.raises(ValidationError, match="exactly one ':'"):
            PlanDocument.model_validate(d)

    def test_closes_criteria_rejects_multiple_colons(self) -> None:
        d = _mutate(closes_criteria=["T2.18:c1:extra"])
        with pytest.raises(ValidationError, match="exactly one ':'"):
            PlanDocument.model_validate(d)

    def test_closes_criteria_rejects_empty_item_id(self) -> None:
        d = _mutate(closes_criteria=[":c1"])
        with pytest.raises(ValidationError, match="must both be non-empty"):
            PlanDocument.model_validate(d)

    def test_closes_criteria_rejects_empty_crit_id(self) -> None:
        d = _mutate(closes_criteria=["T2.18:"])
        with pytest.raises(ValidationError, match="must both be non-empty"):
            PlanDocument.model_validate(d)

    def test_closes_criteria_accepts_real_world_token_shapes(self) -> None:
        """Loose grammar: lettered criteria and hyphenated/triple-dotted/lettered-suffix item ids."""
        tokens = ["T2.18:c1", "T4.12:cA", "T-1.20:c3", "T3.15.1:c4", "T2.25a:c2"]
        d = _mutate(closes_criteria=tokens)
        doc = PlanDocument.model_validate(d)
        assert doc.closes_criteria == tokens


class TestFollowonRecs:
    """PLAN-plan-followon-recs-field (PDB-01, B1-R5): followon_recs is an additive optional
    field naming a plan's deferred halves by rec id, token-shape validated like its sibling
    closes_criteria. Deliberately NOT shared with bundled_recommendations (Fork 2, consistency
    with Decision 85's whole-corpus revalidation): exactly one merged legacy plan populates that
    field with prose, and a shared validator would invalidate it."""

    def test_followon_recs_defaults_to_empty_list(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.followon_recs == []

    def test_followon_recs_accepts_rec_id_tokens(self) -> None:
        d = _mutate(followon_recs=["rec-3091", "rec-3078"])
        doc = PlanDocument.model_validate(d)
        assert doc.followon_recs == ["rec-3091", "rec-3078"]

    def test_followon_recs_rejects_prose_with_whitespace(self) -> None:
        d = _mutate(followon_recs=["file the caching half later"])
        with pytest.raises(ValidationError, match="not a valid 'rec-NNNN' token"):
            PlanDocument.model_validate(d)

    def test_followon_recs_rejects_bare_number(self) -> None:
        d = _mutate(followon_recs=["3091"])
        with pytest.raises(ValidationError, match="not a valid 'rec-NNNN' token"):
            PlanDocument.model_validate(d)

    def test_followon_recs_rejects_uppercase_prefix(self) -> None:
        d = _mutate(followon_recs=["REC-3091"])
        with pytest.raises(ValidationError, match="not a valid 'rec-NNNN' token"):
            PlanDocument.model_validate(d)

    def test_followon_recs_rejects_trailing_suffix(self) -> None:
        d = _mutate(followon_recs=["rec-3091x"])
        with pytest.raises(ValidationError, match="not a valid 'rec-NNNN' token"):
            PlanDocument.model_validate(d)

    def test_followon_recs_rejected_at_loader_level(self, tmp_path) -> None:
        """The token-shape validator also fires through the load()/validate_paths() CLI path,
        not just model_validate() -- a plan authored to disk with a bad entry must fail the
        same way validate_plan_documents would catch it in CI."""
        d = _mutate(followon_recs=["not-a-rec-id"])
        plan_path = tmp_path / "PLAN-zz-valid-demo.yaml"
        plan_path.write_text(yaml.safe_dump(d), encoding="utf-8")
        failures = validate_paths([plan_path])
        assert len(failures) == 1
        assert "not a valid 'rec-NNNN' token" in failures[0][1]

    def test_bundled_recommendations_stays_unvalidated(self) -> None:
        """Pinned asymmetry (Fork 2): the rec-id token validator applies to followon_recs only.
        PLAN-t1-11-plan-yaml-migration.yaml carries two prose bundled_recommendations entries and
        must still load -- a shared validator would invalidate this merged, historical plan."""
        doc = load(REPO_ROOT / "docs" / "plans" / "PLAN-t1-11-plan-yaml-migration.yaml")
        assert len(doc.bundled_recommendations) == 2
        assert any(" " in entry for entry in doc.bundled_recommendations)


class TestGraduationDisposition:
    """T3.21 (VF-05 enforcement): per-VP-step graduation disposition, backward-compatible."""

    def test_graduate_with_check_id_accepted(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "graduate"
        d["verification_plan"][0]["graduation_check_id"] = "some-check-id"
        doc = PlanDocument.model_validate(d)
        assert doc.verification_plan[0].graduation == "graduate"
        assert doc.verification_plan[0].graduation_check_id == "some-check-id"

    def test_waive_with_reason_accepted(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "waive"
        d["verification_plan"][0]["graduation_waiver_reason"] = "requires live infra, not kernel-expressible"
        doc = PlanDocument.model_validate(d)
        assert doc.verification_plan[0].graduation == "waive"
        assert doc.verification_plan[0].graduation_waiver_reason == "requires live infra, not kernel-expressible"

    def test_not_applicable_accepted(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "not-applicable"
        doc = PlanDocument.model_validate(d)
        assert doc.verification_plan[0].graduation == "not-applicable"

    def test_absent_field_backward_compatible(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.verification_plan[0].graduation is None
        assert doc.verification_plan[0].graduation_check_id is None
        assert doc.verification_plan[0].graduation_waiver_reason is None

    def test_graduate_without_check_id_rejected(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "graduate"
        with pytest.raises(ValidationError, match="graduation='graduate' requires a non-empty graduation_check_id"):
            PlanDocument.model_validate(d)

    def test_waive_without_reason_rejected(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "waive"
        with pytest.raises(ValidationError, match="graduation='waive' requires a non-empty graduation_waiver_reason"):
            PlanDocument.model_validate(d)

    def test_unknown_disposition_value_rejected(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation"] = "bogus"
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_check_id_without_graduate_rejected(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation_check_id"] = "orphaned-check-id"
        with pytest.raises(ValidationError, match="graduation_check_id requires graduation='graduate'"):
            PlanDocument.model_validate(d)

    def test_reason_without_waive_rejected(self) -> None:
        d = _base()
        d["verification_plan"][0]["graduation_waiver_reason"] = "orphaned reason"
        with pytest.raises(ValidationError, match="graduation_waiver_reason requires graduation='waive'"):
            PlanDocument.model_validate(d)

    def test_graduate_with_stray_waiver_reason_rejected(self) -> None:
        """Cross-field leakage: a 'graduate' step must not also carry a waiver reason."""
        d = _base()
        d["verification_plan"][0]["graduation"] = "graduate"
        d["verification_plan"][0]["graduation_check_id"] = "some-check-id"
        d["verification_plan"][0]["graduation_waiver_reason"] = "stray leftover reason"
        with pytest.raises(ValidationError, match="graduation_waiver_reason requires graduation='waive'"):
            PlanDocument.model_validate(d)

    def test_waive_with_stray_check_id_rejected(self) -> None:
        """Cross-field leakage: a 'waive' step must not also carry a check_id."""
        d = _base()
        d["verification_plan"][0]["graduation"] = "waive"
        d["verification_plan"][0]["graduation_waiver_reason"] = "requires live infra"
        d["verification_plan"][0]["graduation_check_id"] = "stray leftover check-id"
        with pytest.raises(ValidationError, match="graduation_check_id requires graduation='graduate'"):
            PlanDocument.model_validate(d)


class TestImplementationDeclared:
    """PLAN-plan-resolution-content-keyed: implementation_declared is an additive optional
    field (Decision 85), defaulted False so the entire existing corpus keeps validating."""

    def test_defaults_to_false(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.implementation_declared is False

    def test_accepts_explicit_true(self) -> None:
        d = _mutate(implementation_declared=True)
        doc = PlanDocument.model_validate(d)
        assert doc.implementation_declared is True

    def test_accepts_explicit_false(self) -> None:
        d = _mutate(implementation_declared=False)
        doc = PlanDocument.model_validate(d)
        assert doc.implementation_declared is False

    def test_absent_field_backward_compatible(self) -> None:
        d = _base()
        assert "implementation_declared" not in d
        doc = PlanDocument.model_validate(d)
        assert doc.implementation_declared is False


class TestFallbackReevaluation:
    """ESB-02 remediation (PLAN-esb-fallback-spec-carrier): the optional fallback_reevaluation
    block a plan carries when it names a CD.27-gated tier item (scripts/checks/roadmap/
    validate_fallback_reevaluation.py). The obligation to attach it lives in that check, not in
    this schema -- so absence must stay valid on every historical plan."""

    def _block(self, **overrides) -> dict:
        base = {
            "reevaluated_on": "2026-08-02",
            "substrate_status": "no API-semantics regression observed at filing",
            "verdict": "continue_on_current_substrate",
            "basis": "reviewed against the CD.27 fallback_spec trigger; substrate semantics unchanged",
        }
        base.update(overrides)
        return base

    def test_well_formed_block_validates(self) -> None:
        d = _mutate(fallback_reevaluation=self._block())
        doc = PlanDocument.model_validate(d)
        assert doc.fallback_reevaluation is not None
        assert doc.fallback_reevaluation.verdict == "continue_on_current_substrate"

    def test_field_absent_is_valid_backward_compatible(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.fallback_reevaluation is None

    def test_unknown_verdict_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(verdict="looks_fine"))
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_empty_basis_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(basis="   "))
        with pytest.raises(ValidationError, match="basis must be non-blank"):
            PlanDocument.model_validate(d)

    def test_whitespace_only_reevaluated_on_rejected(self) -> None:
        """Symmetry with basis (code review round 1): all three free-text fields reject blank."""
        d = _mutate(fallback_reevaluation=self._block(reevaluated_on="   "))
        with pytest.raises(ValidationError, match="reevaluated_on must be non-blank"):
            PlanDocument.model_validate(d)

    def test_whitespace_only_substrate_status_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(substrate_status="   "))
        with pytest.raises(ValidationError, match="substrate_status must be non-blank"):
            PlanDocument.model_validate(d)

    def test_non_iso_reevaluated_on_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(reevaluated_on="August 2, 2026"))
        with pytest.raises(ValidationError, match="must be an ISO date"):
            PlanDocument.model_validate(d)

    def test_iso_reevaluated_on_accepted(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(reevaluated_on="2026-01-05"))
        doc = PlanDocument.model_validate(d)
        assert doc.fallback_reevaluation.reevaluated_on == "2026-01-05"

    def test_basic_format_reevaluated_on_rejected(self) -> None:
        """Code review round 2 (Low): date.fromisoformat() alone accepts the dash-free basic
        ISO format on Python 3.11+ ('20260802'); the validator must reject it -- this is a date
        stamp in a governance document, and the error message promises YYYY-MM-DD specifically."""
        d = _mutate(fallback_reevaluation=self._block(reevaluated_on="20260802"))
        with pytest.raises(ValidationError, match="must be an ISO date"):
            PlanDocument.model_validate(d)

    def test_datetime_with_time_component_reevaluated_on_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(reevaluated_on="2026-08-02T00:00:00"))
        with pytest.raises(ValidationError, match="must be an ISO date"):
            PlanDocument.model_validate(d)

    def test_missing_basis_rejected(self) -> None:
        block = self._block()
        block.pop("basis")
        d = _mutate(fallback_reevaluation=block)
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_extra_key_rejected(self) -> None:
        d = _mutate(fallback_reevaluation=self._block(note="x"))
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_all_historical_plans_still_valid_with_field_absent(self) -> None:
        """Decision 85: validate_plan_documents re-validates the whole plans directory against
        an extra='forbid' model, so an added optional field is a repo-wide event. This is the
        module's one corpus pass: it also covers graduation and implementation_declared, whose
        own corpus passes it replaced (rec-4777)."""
        paths = sorted((REPO_ROOT / "docs" / "plans").glob("PLAN-*.yaml"))
        assert len(paths) >= 300
        failures = validate_paths(paths)
        assert not failures, f"historical plan(s) invalidated by the new field: {[(p.name, e[:80]) for p, e in failures[:3]]}"


class TestWeakeningDispositions:
    """PLAN-test-weakening-surfacing (rec-3357): weakening_dispositions is an additive optional
    field recording validate_weakening_dispositions' implement-PR-leg obligation."""

    def _entry(self, **overrides: object) -> dict:
        base = {
            "candidate": "[assertion-removed] tests/t.py::test_a",
            "disposition": "refactor",
            "cite": "scripts/checks/foo.py::kept_fn",
        }
        base.update(overrides)
        return base

    def test_valid_entry_parses(self) -> None:
        d = _mutate(weakening_dispositions=[self._entry()])
        doc = PlanDocument.model_validate(d)
        assert len(doc.weakening_dispositions) == 1
        assert isinstance(doc.weakening_dispositions[0], WeakeningDisposition)
        assert doc.weakening_dispositions[0].disposition == "refactor"

    def test_field_defaults_to_empty(self) -> None:
        doc = PlanDocument.model_validate(_base())
        assert doc.weakening_dispositions == []

    def test_unknown_disposition_rejected(self) -> None:
        d = _mutate(weakening_dispositions=[self._entry(disposition="bogus")])
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_malformed_candidate_key_rejected(self) -> None:
        d = _mutate(weakening_dispositions=[self._entry(candidate="not-a-valid-key")])
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_empty_cite_rejected(self) -> None:
        d = _mutate(weakening_dispositions=[self._entry(cite="")])
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_extra_key_rejected(self) -> None:
        d = _mutate(weakening_dispositions=[self._entry(note="x")])
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(d)

    def test_weakening_disposition_itself_is_accepted_by_schema(self) -> None:
        """The schema permits recording "weakening" (the check, not the schema, blocks it)."""
        d = _mutate(weakening_dispositions=[self._entry(disposition="weakening")])
        doc = PlanDocument.model_validate(d)
        assert doc.weakening_dispositions[0].disposition == "weakening"
