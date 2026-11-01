"""Unit tests for scripts/contracts_schema.py -- the ritual contract Pydantic schema.

Hermetic: every fixture is constructed in-test (no committed fixture files, no sockets, no
shared module-level state). Compatible with pytest-socket / pytest-randomly (T3.6).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.contracts_schema import (
    AmendmentLogEntry,
    ChangeClass,
    ContractClass,
    ContractDocument,
    ContractGovernance,
    ContractMeta,
    ContractStatus,
    EvaluatorSpec,
    FieldSpec,
    VerbSpec,
)


def _class_a_doc() -> dict:
    return {
        "contract": {
            "id": "ops_recommendations",
            "class": "A",
            "contract_version": 1,
            "status": "ratified",
            "ratified_at": "2026-05-21",
            "ratified_via": "test-decision",
            "description": "Class A contract for ops_recommendations.",
            "projects_to": {"pydantic_model": "src/schemas/rec.py::RecPayload"},
            "governance": {
                "table_class": "SCD2_append_only",
                "partition_by": "day(last_updated_timestamp)",
                "dedup_view": "ops_recommendations_current",
            },
            "amendment_policy": {"default": "forward_compat_only"},
        },
        "fields": {
            "id": {
                "type": "str",
                "iceberg_type": "string",
                "nullable": False,
                "description": "Recommendation id.",
                "semantics": "DynamoDB-allocated; SCD2.",
                "dq_intent": {"not_null": {"enforced": True}},
                "governance_notes": "Immutable once written.",
                "amendment_log": [],
            },
            "source": {
                "$ref": "docs/contracts/source-lineage.yaml#/contract/fields/registry_key",
                "dq_intent_local": {"not_null": {"enforced": True, "write_time": True}},
                "joins": ["lineage_join_source_to_agent_type"],
                "amendment_log": [],
            },
        },
        "previous_versions": [],
    }


def _class_b_doc() -> dict:
    return {
        "contract": {
            "id": "lambda-log-rec",
            "class": "B",
            "contract_version": 1,
            "status": "provisional_v0",
            "ratified_at": "2026-05-21",
            "description": "Verb contract for the log-rec Lambda.",
            "governance": {"auth_type": "AWS_IAM", "principal_classes": ["PlatformDev"]},
        },
        "verbs": {
            "POST": {
                "payload_schema_ref": "docs/contracts/ops_recommendations.yaml::contract",
                "response_codes": {200: "ok", 400: "validation error"},
                "typed_errors": {"VALIDATION_ERROR": "payload failed validation"},
            }
        },
        "audit_invariants": ["Every successful write emits a structured log line."],
        "amendment_log": [],
        "previous_versions": [],
    }


def _class_c_doc() -> dict:
    return {
        "contract": {
            "id": "source-lineage",
            "class": "C",
            "contract_version": 1,
            "status": "ratified",
            "ratified_via": "test-decision",
            "description": "Canonical source lineage key contract.",
        },
        "governance": {
            "registry_path": "config/agent/data_quality/source_registry.yaml",
            "validation_path": "scripts/ops_data_portal.py",
            "human_initiated_value": "manual",
        },
        "joins_using_this_key": ["lineage_join_source_to_agent_type"],
        "governance_notes": "Graduated from routing-enum to lineage-key 2026-05-06.",
        "amendment_log": [
            {
                "date": "2026-05-06",
                "semantic_break": True,
                "change_class": "accepted_values_extend",
                "migration_story": "No backfill; filter on created_timestamp.",
            }
        ],
        "previous_versions": [],
    }


class TestValidContracts:
    def test_class_a_accepted(self) -> None:
        doc = ContractDocument.model_validate(_class_a_doc())
        assert doc.contract.class_ is ContractClass.A
        assert doc.contract.status is ContractStatus.ratified
        assert doc.fields is not None
        assert doc.fields["source"].ref is not None

    def test_class_b_accepted(self) -> None:
        doc = ContractDocument.model_validate(_class_b_doc())
        assert doc.contract.class_ is ContractClass.B
        assert doc.verbs is not None
        assert doc.verbs["POST"].response_codes == {200: "ok", 400: "validation error"}

    def test_class_c_accepted(self) -> None:
        doc = ContractDocument.model_validate(_class_c_doc())
        assert doc.contract.class_ is ContractClass.C
        assert doc.fields is None
        assert doc.verbs is None
        assert doc.governance is not None
        assert doc.amendment_log is not None
        assert doc.amendment_log[0].change_class is ChangeClass.accepted_values_extend

    def test_amendment_log_entry_optional_fields(self) -> None:
        entry = AmendmentLogEntry(date="2026-07-15", semantic_break=False, change_class=ChangeClass.prose_improvement)
        assert entry.summary is None
        assert entry.migration_story is None

    def test_governance_superset_model(self) -> None:
        gov = ContractGovernance(table_class="SCD2_append_only", auth_type="AWS_IAM")
        assert gov.table_class == "SCD2_append_only"
        assert gov.auth_type == "AWS_IAM"

    def test_governance_merge_key_accepted(self) -> None:
        gov = ContractGovernance(table_class="SCD2", merge_key="id")
        assert gov.merge_key == "id"

    def test_governance_merge_key_defaults_none(self) -> None:
        gov = ContractGovernance(table_class="SCD2")
        assert gov.merge_key is None

    def test_governance_merge_key_in_full_contract(self) -> None:
        doc = _class_a_doc()
        doc["governance"] = {"table_class": "SCD2", "merge_key": "id"}
        validated = ContractDocument.model_validate(doc)
        assert validated.governance is not None
        assert validated.governance.merge_key == "id"

    def test_governance_unknown_key_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContractGovernance.model_validate({"merge_key": "id", "unknown_key": "oops"})

    def test_verbspec_defaults(self) -> None:
        verb = VerbSpec()
        assert verb.payload_schema_ref is None
        assert verb.response_codes is None
        assert verb.typed_errors is None

    def test_fieldspec_ref_alias_round_trip(self) -> None:
        spec = FieldSpec.model_validate({"$ref": "docs/contracts/x.yaml#/contract/fields/y"})
        assert spec.ref == "docs/contracts/x.yaml#/contract/fields/y"
        assert spec.model_dump(by_alias=True)["$ref"] == spec.ref

    def test_contractmeta_class_alias(self) -> None:
        meta = ContractMeta.model_validate({"id": "x", "class": "A", "contract_version": 2, "status": "draft"})
        assert meta.class_ is ContractClass.A
        assert meta.contract_version == 2


class TestRejectsMalformed:
    def test_unknown_field_rejected(self) -> None:
        doc = _class_a_doc()
        doc["contract"]["unexpected_key"] = "boom"
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_unknown_top_level_field_rejected(self) -> None:
        doc = _class_a_doc()
        doc["surprise"] = True
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_missing_contract_version_rejected(self) -> None:
        doc = _class_a_doc()
        del doc["contract"]["contract_version"]
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_bad_status_rejected(self) -> None:
        doc = _class_a_doc()
        doc["contract"]["status"] = "retired"
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_bad_change_class_rejected(self) -> None:
        doc = _class_c_doc()
        doc["amendment_log"][0]["change_class"] = "vibe_shift"
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_bad_contract_class_rejected(self) -> None:
        doc = _class_a_doc()
        doc["contract"]["class"] = "D"
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_class_a_without_fields_rejected(self) -> None:
        doc = _class_a_doc()
        del doc["fields"]
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_class_a_with_verbs_rejected(self) -> None:
        doc = _class_a_doc()
        doc["verbs"] = {"POST": {}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_class_b_without_verbs_rejected(self) -> None:
        doc = _class_b_doc()
        del doc["verbs"]
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_class_b_with_fields_rejected(self) -> None:
        doc = _class_b_doc()
        doc["fields"] = {"x": {"type": "str"}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_class_c_may_own_ref_target_fields(self) -> None:
        # Class C owns canonical cross-system field definitions (Invariant 5); fields allowed.
        doc = _class_c_doc()
        doc["fields"] = {"registry_key": {"type": "str", "nullable": False}}
        validated = ContractDocument.model_validate(doc)
        assert validated.fields is not None
        assert validated.fields["registry_key"].type == "str"

    def test_class_c_with_verbs_rejected(self) -> None:
        doc = _class_c_doc()
        doc["verbs"] = {"POST": {}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_amendment_log_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AmendmentLogEntry.model_validate(
                {"date": "2026-07-15", "semantic_break": False, "change_class": "prose_improvement", "extra": 1}
            )

    def test_fieldspec_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FieldSpec.model_validate({"type": "str", "not_a_field": True})


def _class_d_meta(**overrides: object) -> dict:
    meta = {
        "id": "cd-test",
        "class": "D",
        "contract_version": 1,
        "status": "ratified",
        "ratified_via": "test-decision",
        "subject": "cd-test-subject",
        "evaluator": {"check": "validate_placement"},
    }
    meta.update(overrides)
    return meta


class TestClassDEnumWidenings:
    """Coverage for the two enum widenings (contracts-first-class-migration)."""

    def test_contract_class_admits_d(self) -> None:
        assert ContractClass("D") is ContractClass.D
        assert {c.value for c in ContractClass} == {"A", "B", "C", "D"}

    def test_contract_status_admits_active(self) -> None:
        assert ContractStatus("active") is ContractStatus.active


class TestEvaluatorSpec:
    def test_exactly_one_kind_required(self) -> None:
        EvaluatorSpec.model_validate({"check": "validate_placement"})
        EvaluatorSpec.model_validate({"agent_surface": ".claude/skills/foo/SKILL.md"})

    def test_zero_kinds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorSpec.model_validate({})

    def test_two_kinds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorSpec.model_validate({"check": "validate_placement", "agent_surface": "x.md"})

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvaluatorSpec.model_validate({"check": "x", "extra": 1})

    def test_none_grandfathered_is_not_a_declarable_kind(self) -> None:
        """VP step 5's graduated node (rec-3059 wave 2) -- the plan's core unrepresentability
        claim. CHECK_ID HONESTY: none-grandfathered-kind-unrepresentable names the FULL claim, so
        every assertion in the plan's VP step 5 shell leg is asserted HERE, not only the
        model_validate half."""
        import dataclasses

        import scripts.contracts_schema as cs
        from scripts.checks.contracts import _population, _ratchet

        route = {"reason": "x", "mechanism": "x", "blocker": "x", "shape": "check"}
        with pytest.raises(ValidationError):
            EvaluatorSpec.model_validate({"none_grandfathered": route})

        assert not hasattr(cs, "EnforcementRoute"), "EnforcementRoute still exported"
        assert "none_grandfathered" not in cs.EvaluatorSpec.model_fields, cs.EvaluatorSpec.model_fields.keys()
        assert not hasattr(_population, "check_evaluator_kind_change"), "kind-change rule survived"
        census_fields = {f.name for f in dataclasses.fields(_population.Census)}
        assert "evaluator_none_grandfathered" not in census_fields, "census field survived"
        assert _ratchet.PIN_KEYS == ("grandfathered_max", "status_active_max"), _ratchet.PIN_KEYS


class TestContractMetaClassDRequirements:
    def test_class_d_requires_subject_and_evaluator(self) -> None:
        ContractMeta.model_validate(_class_d_meta())

        no_subject = _class_d_meta()
        del no_subject["subject"]
        with pytest.raises(ValidationError, match="subject"):
            ContractMeta.model_validate(no_subject)

        no_evaluator = _class_d_meta()
        del no_evaluator["evaluator"]
        with pytest.raises(ValidationError, match="evaluator"):
            ContractMeta.model_validate(no_evaluator)

    def test_non_d_classes_do_not_require_subject_or_evaluator(self) -> None:
        ContractMeta.model_validate({"id": "x", "class": "A", "contract_version": 1, "status": "draft"})

    @pytest.mark.parametrize("cls", ["A", "B", "C", "D"])
    def test_ratified_status_requires_ratified_via_for_every_class(self, cls: str) -> None:
        if cls == "D":
            meta = _class_d_meta()
            del meta["ratified_via"]
        else:
            meta = {"id": "x", "class": cls, "contract_version": 1, "status": "ratified"}
        with pytest.raises(ValidationError, match="ratified_via"):
            ContractMeta.model_validate(meta)

    def test_ratified_with_ratified_via_passes(self) -> None:
        ContractMeta.model_validate(
            {"id": "x", "class": "A", "contract_version": 1, "status": "ratified", "ratified_via": "test"}
        )


class TestValidateClassShapeRaisesForD:
    def test_full_contract_document_raises_for_class_d(self) -> None:
        # A Class D file must never construct a ContractDocument -- load_contract_meta
        # (envelope-only) is the sole legal loader. _validate_class_shape's explicit D branch
        # raises rather than silently falling through to the Class C else-branch. A genuinely
        # heterogeneous D body would ALSO be rejected earlier by extra="forbid" on the shared
        # top-level fields -- this fixture carries none, isolating the D-branch raise itself.
        doc = {"contract": _class_d_meta()}
        with pytest.raises(ValidationError, match="load_contract_meta"):
            ContractDocument.model_validate(doc)


class TestRealizationBlock:
    def test_well_formed_block_loads(self) -> None:
        doc = _class_a_doc()
        doc["realization"] = {"produced": {"write_boundary": ["source"]}, "unproduced": {"rec-1": ["x"]}}
        loaded = ContractDocument.model_validate(doc)
        assert loaded.realization is not None
        assert loaded.realization.produced == {"write_boundary": ["source"]}
        assert loaded.realization.unproduced == {"rec-1": ["x"]}

    def test_absent_block_is_none(self) -> None:
        assert ContractDocument.model_validate(_class_a_doc()).realization is None

    def test_unknown_inner_key_refused(self) -> None:
        doc = _class_a_doc()
        doc["realization"] = {"produced": {}, "unprodused": {}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_non_list_value_refused(self) -> None:
        doc = _class_a_doc()
        doc["realization"] = {"produced": {"write_boundary": "source"}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_misspelt_top_level_key_refused(self) -> None:
        doc = _class_a_doc()
        doc["realisation"] = {"produced": {}}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)


def _run_shape(**over: object) -> dict:
    shape: dict = {
        "session_id": "root",
        "observation_id": "null",
        "entity_ref": "<root session entity_ref>/run",
        "external_ref": "refs",
        "agent_type": "the registry key",
        "triggers": ["workflow_run"],
        "host_run_ref": "required",
        "rows": "one open and one close row",
    }
    shape.update(over)
    return shape


class TestRunShapes:
    def test_class_a_with_two_run_shapes_parses(self) -> None:
        doc = _class_a_doc()
        doc["run_shapes"] = {
            "sub_agent": _run_shape(session_id="child", observation_id="spawning_tool_call", host_run_ref="null"),
            "top_level": _run_shape(one_run_per="root session", state="derived"),
        }
        loaded = ContractDocument.model_validate(doc)
        assert loaded.run_shapes is not None
        assert set(loaded.run_shapes) == {"sub_agent", "top_level"}
        assert loaded.run_shapes["top_level"].host_run_ref == "required"

    def test_absent_run_shapes_is_none(self) -> None:
        assert ContractDocument.model_validate(_class_a_doc()).run_shapes is None

    def test_unknown_run_shape_key_refused(self) -> None:
        doc = _class_a_doc()
        doc["run_shapes"] = {"top_level": _run_shape(surprise="x")}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_session_id_outside_literal_set_refused(self) -> None:
        doc = _class_a_doc()
        doc["run_shapes"] = {"top_level": _run_shape(session_id="parent")}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_empty_triggers_refused(self) -> None:
        doc = _class_a_doc()
        doc["run_shapes"] = {"top_level": _run_shape(triggers=[])}
        with pytest.raises(ValidationError):
            ContractDocument.model_validate(doc)

    def test_run_shapes_refused_on_class_b(self) -> None:
        doc = _class_b_doc()
        doc["run_shapes"] = {"top_level": _run_shape()}
        with pytest.raises(ValidationError, match="run_shapes"):
            ContractDocument.model_validate(doc)

    def test_run_shapes_refused_on_class_c(self) -> None:
        doc = _class_c_doc()
        doc["run_shapes"] = {"top_level": _run_shape()}
        with pytest.raises(ValidationError, match="run_shapes"):
            ContractDocument.model_validate(doc)
