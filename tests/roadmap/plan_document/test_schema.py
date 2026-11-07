"""Tests for scripts/roadmap/plan_document.py -- PlanDocument schema validation (schema_version
1/2/3 rules), covering the T1.11 exit criteria.

Split from the former tests/test_plan_document.py monolith (PLAN-decompose-test-plan-document,
Decision 128 decompose-by-default): TestPlanDocumentSchema, TestSchemaVersion2, TestSchemaVersion3
relocated verbatim.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.roadmap.plan_document import PlanDocument
from tests.fixtures.plan_document_helpers import _base, _base_v2, _mutate, _mutate_v2


class TestPlanDocumentSchema:
    def test_valid_document_validates(self):
        doc = PlanDocument.model_validate(_base())
        assert doc.slug == "zz-valid-demo"
        assert doc.plan_type == "IMPLEMENTATION"
        assert doc.verification_plan[0].command == "echo ok"

    def test_bad_plan_type_enum_fails(self):
        with pytest.raises(ValidationError, match="plan_type"):
            PlanDocument.model_validate(_mutate(plan_type="WRONG"))

    def test_bad_verification_tier_enum_fails(self):
        with pytest.raises(ValidationError, match="verification_tier"):
            PlanDocument.model_validate(_mutate(verification_tier="V9"))

    def test_unsupported_schema_version_fails(self):
        with pytest.raises(ValidationError, match="Unsupported schema_version"):
            PlanDocument.model_validate(_mutate(schema_version=99))

    def test_unknown_top_level_key_fails(self):
        with pytest.raises(ValidationError, match="acceptance_critera"):
            PlanDocument.model_validate(_mutate(acceptance_critera=["typo key is schema drift"]))

    def test_empty_scope_fails(self):
        with pytest.raises(ValidationError, match="scope"):
            PlanDocument.model_validate(_mutate(scope=[]))

    def test_whitespace_command_fails(self):
        base = _base()
        base["verification_plan"][0]["command"] = "   "
        with pytest.raises(ValidationError, match="non-empty executable command"):
            PlanDocument.model_validate(base)

    def test_duplicate_vp_step_ids_fail(self):
        base = _base()
        base["verification_plan"].append(dict(base["verification_plan"][0]))
        with pytest.raises(ValidationError, match="duplicates"):
            PlanDocument.model_validate(base)

    def test_plan_path_slug_mismatch_fails(self):
        with pytest.raises(ValidationError, match="slug consistency"):
            PlanDocument.model_validate(_mutate(plan_path="docs/plans/PLAN-other-slug.yaml"))

    def test_work_areas_on_implementation_fail(self):
        wa = [{"area": "A", "scope": "files", "rationale": "why", "complexity": "S"}]
        with pytest.raises(ValidationError, match="STRATEGIC plan type is retired"):
            PlanDocument.model_validate(_mutate(work_areas=wa))

    def test_strategic_plan_type_refused(self):
        with pytest.raises(ValidationError, match="plan_type"):
            PlanDocument.model_validate(_mutate(plan_type="STRATEGIC"))

    @pytest.mark.parametrize("plan_type", ["IMPLEMENTATION", "REPORT-ONLY"])
    def test_work_areas_refused_on_every_plan_type(self, plan_type: str):
        wa = [{"area": "A", "scope": "files", "rationale": "why", "complexity": "S"}]
        with pytest.raises(ValidationError, match="STRATEGIC plan type is retired"):
            PlanDocument.model_validate(_mutate(plan_type=plan_type, execution_steps=[], work_areas=wa))

    def test_implementation_requires_execution_steps(self):
        with pytest.raises(ValidationError, match="non-empty execution_steps"):
            PlanDocument.model_validate(_mutate(execution_steps=[]))

    def test_report_only_without_execution_steps_validates(self):
        doc = PlanDocument.model_validate(_mutate(plan_type="REPORT-ONLY", execution_steps=[]))
        assert doc.plan_type == "REPORT-ONLY"


class TestSchemaVersion2:
    """T3.17 (VF-04/VF-13): schema_version-2 phase enum, hermetic default, tier_waiver."""

    def test_v2_valid_document_validates(self) -> None:
        doc = PlanDocument.model_validate(_base_v2())
        assert doc.schema_version == 2
        assert doc.verification_plan[0].phase == "pre-deploy"
        assert doc.verification_plan[1].phase == "post-deploy"

    def test_v2_phase_pre_merge_rejected(self) -> None:
        d = _base_v2()
        d["verification_plan"][0]["phase"] = "pre-merge"
        with pytest.raises(ValidationError, match="schema_version 2 verification_plan"):
            PlanDocument.model_validate(d)

    def test_v1_free_text_phase_still_accepted(self) -> None:
        d = _mutate()
        d["verification_plan"][0]["phase"] = "pre-merge"
        doc = PlanDocument.model_validate(d)
        assert doc.verification_plan[0].phase == "pre-merge"

    def test_hermetic_defaults_false(self) -> None:
        d = _base_v2()
        d["verification_plan"][1]["hermetic"] = False
        doc = PlanDocument.model_validate(d)
        assert doc.verification_plan[1].hermetic is False

    def test_hermetic_true_accepted(self) -> None:
        doc = PlanDocument.model_validate(_base_v2())
        assert doc.verification_plan[0].hermetic is True

    def test_tier_waiver_optional_defaults_none(self) -> None:
        doc = PlanDocument.model_validate(_base_v2())
        assert doc.tier_waiver is None

    def test_tier_waiver_accepted_as_string(self) -> None:
        d = _mutate_v2(tier_waiver="conscious V2: comment-only .tf change")
        doc = PlanDocument.model_validate(d)
        assert doc.tier_waiver == "conscious V2: comment-only .tf change"

    def test_v2_unsupported_version_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="require handoff_policy"):
            PlanDocument.model_validate(_mutate_v2(schema_version=3))


class TestSchemaVersion3:
    def test_historical_version_forbids_handoff_policy(self) -> None:
        data = _mutate_v2(handoff_policy={"full_validation_required_before_commit": True, "timeout_disposition": "blocked"})
        with pytest.raises(ValidationError, match="only valid with schema_version 3"):
            PlanDocument.model_validate(data)

    def test_implementation_requires_blocking_handoff_policy(self) -> None:
        data = _mutate_v2(
            schema_version=3,
            handoff_policy={"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        )
        assert PlanDocument.model_validate(data).schema_version == 3

    @pytest.mark.parametrize(
        "policy",
        [
            None,
            {"full_validation_required_before_commit": False, "timeout_disposition": "blocked"},
            {"full_validation_required_before_commit": True, "timeout_disposition": "warning"},
        ],
    )
    def test_invalid_implementation_policy_fails_closed(self, policy: object) -> None:
        data = _mutate_v2(schema_version=3)
        if policy is not None:
            data["handoff_policy"] = policy
        with pytest.raises(ValidationError):
            PlanDocument.model_validate(data)

    def test_non_implementation_forbids_policy(self) -> None:
        data = _mutate_v2(
            schema_version=3,
            plan_type="REPORT-ONLY",
            execution_steps=[],
            handoff_policy={"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        )
        with pytest.raises(ValidationError, match="only valid"):
            PlanDocument.model_validate(data)
