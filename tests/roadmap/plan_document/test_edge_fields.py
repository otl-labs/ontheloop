"""Tests for PlanDocument.amends / introduces / principal (PLAN-plan-document-edge-fields)."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from scripts.contracts import load_contract
from scripts.contracts_enforcement import check_required_inline_fields
from scripts.roadmap.plan_document import PlanDocument, validate_paths
from tests.fixtures.plan_document_helpers import REPO_ROOT, _base, _mutate

_HANDOFF = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}
_FIELDS = ("amends", "introduces", "principal")
_GRAMMAR = "dec:<number>[#<clause-id>]"


def _v5(**overrides) -> dict:
    return _mutate(schema_version=5, handoff_policy=dict(_HANDOFF), **overrides)


class TestPlanEdgeFields:
    def test_defaults_when_absent(self) -> None:
        doc = PlanDocument.model_validate(_v5())
        assert doc.amends == []
        assert doc.introduces == []
        assert doc.principal is None

    def test_fields_exist_and_are_optional(self) -> None:
        for name in _FIELDS:
            assert name in PlanDocument.model_fields
            assert not PlanDocument.model_fields[name].is_required()

    @pytest.mark.parametrize("token", ["dec:217", "dec:217#6", "dec:151#3(ii)", "dec:1"])
    def test_amends_accepts(self, token: str) -> None:
        assert token in PlanDocument.model_validate(_v5(amends=[token])).amends

    @pytest.mark.parametrize(
        "token",
        [
            "dec-217",
            "Decision 217",
            "217",
            "dec:",
            "dec:0217",
            "dec:2١٧",
            "dec:scale-readiness",
            "dec:217#",
            "dec:217#6#7",
            "dec:217 #6",
            "dec:217#6 x",
        ],
    )
    def test_amends_rejects(self, token: str) -> None:
        with pytest.raises(ValidationError, match="dec:<number>"):
            PlanDocument.model_validate(_v5(amends=[token]))

    @pytest.mark.parametrize("token", ["ready_set", "plan-obligations"])
    def test_introduces_accepts(self, token: str) -> None:
        assert token in PlanDocument.model_validate(_v5(introduces=[token])).introduces

    @pytest.mark.parametrize("token", ["", "   ", "two words"])
    def test_introduces_rejects(self, token: str) -> None:
        with pytest.raises(ValidationError, match="is not a valid name token"):
            PlanDocument.model_validate(_v5(introduces=[token]))

    def test_principal_accepts(self) -> None:
        assert PlanDocument.model_validate(_v5(principal="operator")).principal == "operator"

    def test_principal_explicit_none_accepted(self) -> None:
        assert PlanDocument.model_validate(_v5(principal=None)).principal is None

    @pytest.mark.parametrize("value", ["", "   ", "the operator"])
    def test_principal_rejects_shape(self, value: str) -> None:
        with pytest.raises(ValidationError, match="is not a valid principal token"):
            PlanDocument.model_validate(_v5(principal=value))

    def test_principal_rejects_at_sign(self) -> None:
        with pytest.raises(ValidationError, match="public-repository boundary"):
            PlanDocument.model_validate(_v5(principal="someone@example.com"))

    @pytest.mark.parametrize("name,value", [("amends", []), ("introduces", []), ("principal", "operator")])
    def test_keys_refused_below_v5(self, name: str, value: object) -> None:
        with pytest.raises(ValidationError, match="require schema_version 5 or above"):
            PlanDocument.model_validate(_mutate(**{name: value}))

    def test_v1_without_keys_still_validates(self) -> None:
        assert PlanDocument.model_validate(_base()).amends == []

    def test_v5_accepts_all_three(self) -> None:
        doc = PlanDocument.model_validate(_v5(amends=["dec:217"], introduces=["x"], principal="operator"))
        assert doc.amends == ["dec:217"]

    def test_extra_forbid_still_bites(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            PlanDocument.model_validate(_v5(amend=["dec:217"]))

    def test_loader_path_rejects_bad_token(self, tmp_path) -> None:
        data = _v5(amends=["dec-217"])
        path = tmp_path / f"PLAN-{data['slug']}.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        failures = validate_paths([path])
        assert len(failures) == 1
        assert _GRAMMAR in failures[0][1]


class TestPlanEdgeFieldsContract:
    @pytest.fixture(scope="class")
    def doc(self):
        return load_contract(REPO_ROOT / "docs" / "contracts" / "exit-criteria-ledger.yaml")

    def test_fields_present_and_required_keys(self, doc) -> None:
        assert {"plan_amends", "plan_introduces", "plan_principal"} <= set(doc.fields)
        assert check_required_inline_fields(doc) == []

    def test_descriptions_name_the_model_fields(self, doc) -> None:
        for name in _FIELDS:
            assert f"PlanDocument.{name}" in doc.fields[f"plan_{name}"].description

    def test_amends_semantics_phrases(self, doc) -> None:
        sem = doc.fields["plan_amends"].semantics
        for phrase in (
            _GRAMMAR,
            "DECLARATION",
            "Decision 197 cl.5",
            "Decision 217 cl.5-6",
            "T4.32 c4",
            "current and future-version plans",
        ):
            assert phrase in sem
        assert "rec-4517" in doc.fields["plan_amends"].governance_notes

    def test_principal_semantics_phrase(self, doc) -> None:
        assert "never an email address" in doc.fields["plan_principal"].semantics

    def test_contract_description_names_linkage_fields(self, doc) -> None:
        for phrase in ("followon_recs", "roadmap_alignment_projection", "PlanDocument.amends", "PlanDocument.principal"):
            assert phrase in doc.contract.description

    def test_amendment_log_entries(self, doc) -> None:
        classes = {e.change_class for e in doc.amendment_log if "PLAN-plan-document-edge-fields" in e.summary}
        assert {"field_add", "prose_improvement"} <= classes
