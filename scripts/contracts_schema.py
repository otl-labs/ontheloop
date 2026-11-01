"""Canonical Pydantic v2 schema for the pre-codegen contract ritual (T-1.12 subset d).

Models the Class A/B/C `docs/contracts/{name}.yaml` ritual contract shape governed by
Decision 86 / Decision 127 and enforced by scripts/checks/contracts/validate_contract_drift.py.

Split out from scripts/contracts.py (the loader/resolver public API) to keep each module
under the 500-SLOC gate (Decision 43). The loader imports these models; the subset-(e) CI
drift gate imports the ContractStatus / ChangeClass / ContractClass enums from HERE, not
from contracts.py.

Import safety: defining enums and models performs no I/O and raises nothing at import time.
All validation is deferred to explicit model construction inside load_contract / resolve_refs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractClass(str, Enum):
    """The four ritual/mechanism contract classes (INTENT Part 2A; Class D per T2.56/CD.25
    migration step 3, contracts-first-class-migration)."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ContractStatus(str, Enum):
    """Contract lifecycle status enum (INTENT Part 4 Invariant 6).

    `active` (contracts-first-class-migration) is GRANDFATHER-ONLY: in force and consumed but
    never ritually ratified. No transition rule in scripts/contracts.py produces it -- it is
    load-bearing on pre-existing Class D files only, letting the population sweep seed them
    honestly rather than asserting a ratification that never happened.
    """

    draft = "draft"
    ratified = "ratified"
    provisional_v0 = "provisional_v0"
    deprecated = "deprecated"
    superseded = "superseded"
    active = "active"


class ChangeClass(str, Enum):
    """Closed 8-value amendment vocabulary (INTENT Part 4 Invariant 3)."""

    field_add = "field_add"
    not_null_tighten = "not_null_tighten"
    type_widen = "type_widen"
    join_add = "join_add"
    prose_improvement = "prose_improvement"
    governance_note_add = "governance_note_add"
    accepted_values_narrow = "accepted_values_narrow"
    accepted_values_extend = "accepted_values_extend"


class AmendmentLogEntry(BaseModel):
    """One field-level (or contract-level) amendment audit entry (INTENT Invariant 3)."""

    model_config = ConfigDict(extra="forbid")

    date: str
    semantic_break: bool
    change_class: ChangeClass
    summary: str | None = None
    migration_story: str | None = None


class FieldSpec(BaseModel):
    """A single contract field, either defined inline or referenced via `$ref` (Invariant 5).

    Inline (Class A canonical) fields carry type/iceberg_type/nullable/description/semantics
    plus dq_intent. Referencing fields carry `$ref` (aliased to `ref`) plus only the additive
    `*_local` overrides; the resolver (scripts/contracts.py::resolve_refs) rejects a field that
    carries both a `$ref` and an inline definition.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str | None = None
    iceberg_type: str | None = None
    nullable: bool | None = None
    description: str | None = None
    semantics: str | None = None
    populated_by: str | None = None
    write_time_validation: str | None = None
    dq_intent: dict[str, Any] | None = None
    governance_notes: str | None = None
    ref: str | None = Field(default=None, alias="$ref")
    dq_intent_local: dict[str, Any] | None = None
    governance_notes_local: str | None = None
    joins: list[str] | None = None
    amendment_log: list[AmendmentLogEntry] = Field(default_factory=list)
    derivation: dict[str, Any] | None = None


class VerbSpec(BaseModel):
    """A single Class B verb entry (INTENT Part 3D)."""

    model_config = ConfigDict(extra="forbid")

    payload_schema_ref: str | None = None
    response_codes: dict[int, str] | None = None
    typed_errors: dict[str, str] | None = None


class ContractGovernance(BaseModel):
    """Governance block superset across Class A/B/C (INTENT Parts 3C-3E).

    A single closed model rather than a per-class union: each class populates the subset of
    fields relevant to it. extra="forbid" still rejects keys outside this superset.
    """

    model_config = ConfigDict(extra="forbid")

    table_class: str | None = None
    partition_by: str | None = None
    dedup_view: str | None = None
    write_path: str | None = None
    id_allocator: str | None = None
    merge_key: str | None = None
    auth_type: str | None = None
    principal_classes: list[str] | None = None
    admin_only_features: list[str] | None = None
    registry_path: str | None = None
    injection_path: str | None = None
    validation_path: str | None = None
    human_initiated_value: str | None = None


class EvaluatorSpec(BaseModel):
    """A Class D contract's typed evaluator union (contracts-first-class-migration).

    Exactly one of the two kinds: `check` (a registered scripts/checks/* name resolved by
    executable-context evidence of reading the contract) or `agent_surface` (a non-Python
    agent-consumed surface, e.g. a skill or slash command, that names the contract). The
    third, grandfather-only kind that never resolved (migration-step-3-grandfathering) was
    retired at rec-3059 wave 2 -- it is no longer representable at all, so every Class D
    contract's evaluator must genuinely resolve.
    """

    model_config = ConfigDict(extra="forbid")

    check: str | None = None
    agent_surface: str | None = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> EvaluatorSpec:
        kinds = [k for k in ("check", "agent_surface") if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"evaluator must declare exactly one of: check, agent_surface (got {kinds})")
        return self


class ContractMeta(BaseModel):
    """The top-level `contract:` mapping (INTENT Parts 3C-3E).

    `subject` and `evaluator` (contracts-first-class-migration) are optional at the type level
    but REQUIRED together whenever class is D (see the validator below) -- a mechanism contract
    with no declared evaluator is not a rule (CFG-03). Neither field carries a baseline-dependent
    rule: the model cannot see git history, only the current document.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    class_: ContractClass = Field(alias="class")
    contract_version: int
    status: ContractStatus
    ratified_at: str | None = None
    ratified_via: str | None = None
    description: str | None = None
    projects_to: dict[str, Any] | None = None
    governance: ContractGovernance | None = None
    amendment_policy: dict[str, Any] | None = None
    write_payload_projection: dict[str, Any] | None = None
    provisional_v0: dict[str, Any] | None = None
    subject: str | None = None
    evaluator: EvaluatorSpec | None = None

    @model_validator(mode="after")
    def _validate_class_d_and_ratified(self) -> ContractMeta:
        if self.class_ is ContractClass.D and (self.subject is None or self.evaluator is None):
            raise ValueError("Class D contract requires both `subject` and `evaluator`")
        if self.status is ContractStatus.ratified and not self.ratified_via:
            raise ValueError("status: ratified requires `ratified_via`")
        return self


class Realization(BaseModel):
    """Who writes each stored column of a telemetry table (shape only; cross-file rules live in
    scripts/checks/contracts/validate_telemetry_field_realization.py).

    `produced` keys are a registered producer id or the literal `write_boundary`; `unproduced` keys are
    owners. Unrelated to a read-derived field's `derivation.realized`, which says a write-time derivation
    is implemented.
    """

    model_config = ConfigDict(extra="forbid")

    produced: dict[str, list[str]] = Field(default_factory=dict)
    unproduced: dict[str, list[str]] = Field(default_factory=dict)


class RunShape(BaseModel):
    """One declared run shape of a Class A table (telemetry_agents: sub-agent vs top-level run)."""

    model_config = ConfigDict(extra="forbid")

    session_id: Literal["child", "root"]
    observation_id: Literal["spawning_tool_call", "null"]
    entity_ref: str
    external_ref: str
    agent_type: str
    triggers: list[str] = Field(min_length=1)
    host_run_ref: Literal["null", "required"]
    rows: str
    one_run_per: str | None = None
    state: str | None = None


class ContractDocument(BaseModel):
    """A full ritual contract document (INTENT Parts 3C-3E).

    Class A carries `fields`; Class B carries `verbs`; Class C is concept-shaped (neither
    `fields` nor `verbs`, with a top-level `governance` block). The class-conditional shape
    is enforced by the model validator below.
    """

    model_config = ConfigDict(extra="forbid")

    contract: ContractMeta
    fields: dict[str, FieldSpec] | None = None
    realization: Realization | None = None
    run_shapes: dict[str, RunShape] | None = None
    verbs: dict[str, VerbSpec] | None = None
    governance: ContractGovernance | None = None
    allowed_values: Any | None = None
    joins_using_this_key: list[str] | None = None
    governance_notes: str | None = None
    audit_invariants: list[str] | None = None
    amendment_log: list[AmendmentLogEntry] | None = None
    previous_versions: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_class_shape(self) -> ContractDocument:
        cls = self.contract.class_
        if self.run_shapes is not None and cls is not ContractClass.A:
            raise ValueError("run_shapes is allowed on a Class A contract only")
        if cls is ContractClass.A:
            if not self.fields:
                raise ValueError("Class A contract must define a non-empty `fields` mapping")
            if self.verbs is not None:
                raise ValueError("Class A contract must not define `verbs`")
        elif cls is ContractClass.B:
            if not self.verbs:
                raise ValueError("Class B contract must define a non-empty `verbs` mapping")
            if self.fields is not None:
                raise ValueError("Class B contract must not define `fields`")
        elif cls is ContractClass.D:
            # Class D (mechanism contracts) bodies are heterogeneous by design and can never
            # satisfy this model's extra="forbid" (T2.56/CD.25 migration step 3). They must be
            # loaded via scripts.contracts.load_contract_meta (envelope-only, never
            # ContractDocument) -- raise loudly here rather than falling through to the Class C
            # branch below, which would silently accept an arbitrary D body as concept-shaped.
            raise ValueError(
                "Class D contract must be loaded via scripts.contracts.load_contract_meta, "
                "never load_contract/ContractDocument (a D body is heterogeneous and does not "
                "fit the shared extra='forbid' ritual document model)"
            )
        else:  # ContractClass.C -- concept-shaped (not verb-shaped). MAY own the canonical
            # cross-system field definitions that Class A contracts reference via $ref
            # (Invariant 5: a shared field is defined once, in the Class C contract).
            if self.verbs is not None:
                raise ValueError("Class C contract must not define `verbs`")
        return self
