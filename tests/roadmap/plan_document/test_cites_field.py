"""Tests for PlanDocument.cites, schema_version 6 and the ledger's plan_cites / plan_scout_input_fingerprint entries
(PLAN-scout-input-fingerprint)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.contracts import load_contract
from scripts.contracts_enforcement import check_required_inline_fields
from scripts.roadmap import plan_fingerprint
from scripts.roadmap.plan_document import PlanDocument
from tests.fixtures.plan_document_helpers import REPO_ROOT, _mutate

_HANDOFF = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}
_GRAMMAR = "dec:<number>[#<clause-id>]"


def _v6(**overrides) -> dict:
    return _mutate(schema_version=6, handoff_policy=dict(_HANDOFF), **overrides)


def _step(**overrides) -> dict:
    step = {
        "step": 1,
        "phase": "pre-deploy",
        "action": "Run the example check",
        "command": "echo ok",
        "expected": "prints ok",
        "fix_if": "never fails in practice",
        "hermetic": True,
    }
    step.update(overrides)
    return step


class TestCitesField:
    def test_v6_accepted_without_cites(self) -> None:
        doc = PlanDocument.model_validate(_v6())
        assert doc.schema_version == 6
        assert doc.cites == []

    @pytest.mark.parametrize("token", ["dec:217", "dec:217#6", "dec:151#3(ii)"])
    def test_v6_accepts_cites(self, token: str) -> None:
        assert PlanDocument.model_validate(_v6(cites=[token])).cites == [token]

    @pytest.mark.parametrize("token", ["dec-217", "Decision 217", "dec:", "dec:0217", "dec:217#", "dec:217#6#7"])
    def test_malformed_cites_refused(self, token: str) -> None:
        with pytest.raises(ValidationError, match="cites entry"):
            PlanDocument.model_validate(_v6(cites=[token]))

    def test_cites_refused_below_6_naming_6(self) -> None:
        data = _mutate(schema_version=5, handoff_policy=dict(_HANDOFF), cites=["dec:217"])
        with pytest.raises(ValidationError, match="cites requires schema_version 6"):
            PlanDocument.model_validate(data)

    def test_v6_implementation_plan_still_requires_handoff_policy(self) -> None:
        with pytest.raises(ValidationError, match="IMPLEMENTATION plans require handoff_policy"):
            PlanDocument.model_validate(_mutate(schema_version=6))

    def test_v6_hermetic_step_still_refuses_backtick_literal(self) -> None:
        data = _v6(verification_plan=[_step(expected="prints `ok`")])
        with pytest.raises(ValidationError, match="must not carry a backtick literal"):
            PlanDocument.model_validate(data)


class TestCitesLedgerContract:
    @pytest.fixture(scope="class")
    def doc(self):
        return load_contract(REPO_ROOT / "docs" / "contracts" / "exit-criteria-ledger.yaml")

    def test_fields_present_with_required_inline_keys(self, doc) -> None:
        assert {"plan_cites", "plan_scout_input_fingerprint"} <= set(doc.fields)
        assert check_required_inline_fields(doc) == []

    def test_descriptions_name_the_model_fields(self, doc) -> None:
        assert "PlanDocument.cites" in doc.fields["plan_cites"].description
        assert "GateRound.scout_input_fingerprint" in doc.fields["plan_scout_input_fingerprint"].description

    def test_fingerprint_semantics_name_every_payload_key(self, doc) -> None:
        sem = doc.fields["plan_scout_input_fingerprint"].semantics
        for key in plan_fingerprint.PAYLOAD_KEYS:
            assert key in sem

    def test_cites_semantics_use_the_amends_grammar(self, doc) -> None:
        assert _GRAMMAR in doc.fields["plan_cites"].semantics

    def test_contract_description_names_both_fields(self, doc) -> None:
        for phrase in ("PlanDocument.cites", "GateRound.scout_input_fingerprint"):
            assert phrase in doc.contract.description

    def test_amends_consumer_note_names_the_built_extractor(self, doc) -> None:
        sem = doc.fields["plan_amends"].semantics
        assert "not built" not in sem
        assert "T4.32 c4" in sem

    def test_audit_invariants_still_hold_exactly_one_entry(self, doc) -> None:
        assert len(doc.audit_invariants) == 1
