"""Public loader API + single-hop `$ref` resolver for ritual contracts (T-1.12 subset d).

Parses and schema-validates `docs/contracts/{name}.yaml` ritual contracts (Class A/B/C),
resolves single-hop `$ref` cross-contract field references, and encodes the contract status
state machine (INTENT Part 4 Invariant 6).

The canonical enums and Pydantic models live in scripts/contracts_schema.py; this module is
the loader/resolver surface (load_contract, load_all_contracts, resolve_refs,
validate_status_transition, ContractValidationError).

Import safety (AGENTS.md): nothing here touches the filesystem or raises at import time. A
docs/contracts/ directory that does not yet exist does not break import or pytest collection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from scripts.contract_rules import not_null_declared, parse_yaml
from scripts.contracts_schema import (
    ContractClass,
    ContractDocument,
    ContractMeta,
    ContractStatus,
    FieldSpec,
)

# Field keys on a FieldSpec that constitute a canonical inline definition. A field carrying a
# `$ref` may ALSO carry only the additive override keys (dq_intent_local, governance_notes_local,
# joins, amendment_log); any of these definition keys alongside a `$ref` is a duplicate (Inv 5).
_INLINE_DEFINITION_KEYS = (
    "type",
    "iceberg_type",
    "nullable",
    "description",
    "semantics",
    "populated_by",
    "write_time_validation",
    "dq_intent",
    "governance_notes",
)

# Allowed status transitions (INTENT Invariant 6). `deprecated` has NO outgoing edge here:
# the default posture is forbidden, NOT permanent terminality -- the ceremonial revival path
# (a fresh contract_version N+1 block at status ratified, INTENT line 560) is intentionally
# implemented elsewhere, not in this default helper.
#
# `active` (contracts-first-class-migration, Class D grandfather-only status) gets three
# OUTBOUND edges -- a grandfathered file can be ratified, deprecated, or superseded like any
# other in-force contract -- but deliberately NO INBOUND edge: nothing transitions INTO active.
# It is seeded only by pre-existing files the population sweep classifies as grandfathered;
# a file that starts elsewhere in the state machine can never arrive at active.
_ALLOWED_TRANSITIONS: frozenset[tuple[ContractStatus, ContractStatus]] = frozenset(
    {
        (ContractStatus.draft, ContractStatus.ratified),
        (ContractStatus.draft, ContractStatus.provisional_v0),
        (ContractStatus.provisional_v0, ContractStatus.ratified),
        (ContractStatus.provisional_v0, ContractStatus.deprecated),
        (ContractStatus.provisional_v0, ContractStatus.superseded),
        (ContractStatus.ratified, ContractStatus.deprecated),
        (ContractStatus.ratified, ContractStatus.superseded),
        (ContractStatus.active, ContractStatus.ratified),
        (ContractStatus.active, ContractStatus.deprecated),
        (ContractStatus.active, ContractStatus.superseded),
    }
)


class ContractValidationError(Exception):
    """Raised for any malformed contract: bad YAML, schema violation, or resolver failure."""


def load_contract(path: str | Path) -> ContractDocument:
    """Parse and schema-validate a single ritual contract YAML.

    Raises ContractValidationError on unreadable/non-mapping YAML, a YAML parse error, or any
    Pydantic schema violation (unknown field, missing contract_version, status/change_class
    outside the enum, wrong class shape).
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractValidationError(f"cannot read contract file {p}: {exc}") from exc

    try:
        data = parse_yaml(raw_text)
    except yaml.YAMLError as exc:
        raise ContractValidationError(f"invalid YAML in {p}: {exc}") from exc

    return _validate_contract(data, p)


def _validate_contract(data: Any, path: Path) -> ContractDocument:
    """Schema-validate an already-parsed contract mapping (the load_contract/load_all_contracts
    shared tail), so load_all_contracts never re-reads or re-parses a file it already parsed."""
    if not isinstance(data, dict):
        raise ContractValidationError(f"contract {path} must be a YAML mapping, got {type(data).__name__}")

    try:
        return ContractDocument.model_validate(data)
    except ValidationError as exc:
        raise ContractValidationError(f"schema validation failed for {path}: {exc}") from exc


def load_all_contracts(contracts_dir: str | Path) -> dict[str, ContractDocument]:
    """Load every Class A/B/C ritual contract under `contracts_dir`, keyed by contract id.

    Skips any *.yaml whose top level is not a mapping carrying a `contract:` mapping with a
    `class:` field -- pre-ritual free-form docs (e.g. read-engine.yaml's top-level `version:`)
    are not Class A/B/C contracts and are silently ignored. A non-existent directory yields an
    empty mapping (import/collection safety).

    Class D (mechanism contracts, contracts-first-class-migration) is EXPLICITLY skipped here
    too -- never passed to load_contract, which would raise (a D body cannot satisfy
    ContractDocument's extra="forbid"). This is load-bearing, not cosmetic: a caller like
    context_docs.py wraps this in a bare `except Exception: pass`, which would silently swallow
    that raise and kill the whole provisional-contract scan for every contract, not just the D
    file that triggered it. Use load_contract_meta for Class D.

    Each file is read and parsed exactly once here -- never via load_contract, which would
    re-read and re-parse a file this function already parsed.
    """
    directory = Path(contracts_dir)
    out: dict[str, ContractDocument] = {}
    if not directory.is_dir():
        return out

    for yaml_path in sorted(directory.glob("*.yaml")):
        try:
            data = parse_yaml(yaml_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not _is_ritual_contract(data):
            continue
        if data["contract"].get("class") == ContractClass.D.value:
            continue
        doc = _validate_contract(data, yaml_path)
        out[doc.contract.id] = doc
    return out


def _is_ritual_contract(data: Any) -> bool:
    """True iff `data` is a mapping with a `contract:` mapping carrying a `class:` field."""
    if not isinstance(data, dict):
        return False
    contract = data.get("contract")
    return isinstance(contract, dict) and "class" in contract


def load_contract_meta(path: str | Path) -> ContractMeta:
    """Parse and schema-validate ONLY a contract's `contract:` envelope (Class D).

    Unlike load_contract, this never constructs a ContractDocument -- it validates
    `data["contract"]` alone as a ContractMeta, so the surrounding body (a Class D mechanism
    contract's heterogeneous, non-ritual keys) is never subject to ContractDocument's
    extra="forbid". This is how Class D escapes that shared model without relaxing it for
    Class A/B/C.

    Raises ContractValidationError on unreadable/non-mapping YAML, a YAML parse error, a missing
    or non-mapping `contract:` block, or any Pydantic schema violation on the envelope itself
    (unknown field, missing subject/evaluator for class D, missing ratified_via for a ratified
    status, etc.).
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractValidationError(f"cannot read contract file {p}: {exc}") from exc

    try:
        data = parse_yaml(raw_text)
    except yaml.YAMLError as exc:
        raise ContractValidationError(f"invalid YAML in {p}: {exc}") from exc

    if not isinstance(data, dict):
        raise ContractValidationError(f"contract {p} must be a YAML mapping, got {type(data).__name__}")

    contract_block = data.get("contract")
    if not isinstance(contract_block, dict):
        raise ContractValidationError(f"contract {p} has no top-level `contract:` mapping")

    try:
        return ContractMeta.model_validate(contract_block)
    except ValidationError as exc:
        raise ContractValidationError(f"schema validation failed for {p} contract envelope: {exc}") from exc


def resolve_refs(doc: ContractDocument, contracts_dir: str | Path) -> dict[str, FieldSpec]:
    """Resolve every `$ref` field in a Class A `doc` against on-disk Class C targets.

    Single-hop only (INTENT Invariant 5). Returns a new mapping {field_name: FieldSpec} where
    each referencing field is replaced by its resolved target field with the local overrides
    (dq_intent_local -> dq_intent, governance_notes_local -> governance_notes, joins,
    amendment_log) layered on top. Inline fields pass through unchanged.

    Raises ContractValidationError on:
      (a) a field carrying both `$ref` and an inline definition,
      (b) a `$ref` whose target is itself a `$ref` (chain depth > 1 / non-leaf Class-C rule),
      (c) a `$ref` whose target file or field pointer does not exist (dangling),
      (d) a `*_local` override that loosens rather than tightens the referenced field.
    """
    directory = Path(contracts_dir)
    resolved: dict[str, FieldSpec] = {}
    fields = doc.fields or {}

    for name, spec in fields.items():
        if spec.ref is None:
            resolved[name] = spec
            continue

        _reject_inline_alongside_ref(name, spec)
        target_file, target_field = _parse_ref(name, spec.ref)
        target_path = directory / target_file
        if not target_path.is_file():
            raise ContractValidationError(f"field {name!r}: $ref target file does not exist: {target_path}")

        target_doc = load_contract(target_path)
        target_spec = (target_doc.fields or {}).get(target_field)
        if target_spec is None:
            raise ContractValidationError(f"field {name!r}: $ref pointer {spec.ref!r} has no field {target_field!r} in target")
        if target_spec.ref is not None:
            raise ContractValidationError(
                f"field {name!r}: $ref target {target_field!r} is itself a $ref (chained Class-C ref forbidden)"
            )

        resolved[name] = _layer_overrides(name, target_spec, spec)
    return resolved


def _reject_inline_alongside_ref(name: str, spec: FieldSpec) -> None:
    """Raise if a `$ref` field also carries any canonical inline-definition key (Inv 5)."""
    dumped = spec.model_dump(exclude_none=True)
    duplicated = [k for k in _INLINE_DEFINITION_KEYS if k in dumped]
    if duplicated:
        raise ContractValidationError(
            f"field {name!r}: carries both $ref and inline definition keys {duplicated} (duplicate forbidden)"
        )


def _parse_ref(name: str, ref: str) -> tuple[str, str]:
    """Split a `docs/contracts/{file}.yaml#/contract/fields/{field}` ref into (file, field).

    Tolerates the documented `/contract/fields/` prefix and a bare `/fields/` prefix; the
    final path segment after `fields/` is the field name. Raises on a malformed pointer.
    """
    if "#" not in ref:
        raise ContractValidationError(f"field {name!r}: malformed $ref (no '#' fragment): {ref!r}")
    file_part, _, fragment = ref.partition("#")
    file_name = Path(file_part).name
    marker = "fields/"
    idx = fragment.rfind(marker)
    if not file_name or idx == -1:
        raise ContractValidationError(f"field {name!r}: malformed $ref pointer: {ref!r}")
    field_name = fragment[idx + len(marker) :].strip("/")
    if not field_name:
        raise ContractValidationError(f"field {name!r}: $ref pointer names no field: {ref!r}")
    return file_name, field_name


def _layer_overrides(name: str, target: FieldSpec, local: FieldSpec) -> FieldSpec:
    """Layer a referencing field's additive `*_local` overrides onto a resolved target.

    Overrides MUST be additive or strictly tighter (Invariant 5); a loosening override raises.
    """
    merged = target.model_dump(by_alias=True)
    merged["$ref"] = None

    if local.dq_intent_local is not None:
        if _override_loosens(target.dq_intent, local.dq_intent_local):
            raise ContractValidationError(
                f"field {name!r}: dq_intent_local loosens the referenced field (overrides must tighten)"
            )
        merged["dq_intent"] = local.dq_intent_local
    if local.governance_notes_local is not None:
        merged["governance_notes"] = local.governance_notes_local
    if local.joins is not None:
        merged["joins"] = local.joins
    if local.amendment_log:
        merged["amendment_log"] = [e.model_dump() for e in local.amendment_log]

    return FieldSpec.model_validate(merged)


def _override_loosens(referenced: dict[str, Any] | None, override: dict[str, Any]) -> bool:
    """True iff `override` weakens a constraint the referenced field already enforces.

    Concrete rule: a referenced not_null (declared by presence) cannot be overridden by a legacy `{enforced: false}`.
    """
    if not referenced:
        return False
    ovr_nn = override.get("not_null")
    if isinstance(ovr_nn, dict) and ovr_nn.get("enforced") is False:
        return not_null_declared(referenced.get("not_null"))
    return False


def validate_status_transition(old: str, new: str) -> bool:
    """Validate a contract status transition against the Invariant 6 state machine.

    Returns True for an allowed transition. Raises ContractValidationError for an unknown
    status value or a forbidden transition. `deprecated` is default-forbidden as a source
    state (no outgoing edge) -- this encodes the DEFAULT posture, not permanent terminality;
    the ceremonial N+1 revival path lives outside this helper (INTENT line 560).
    """
    try:
        old_status = ContractStatus(old)
        new_status = ContractStatus(new)
    except ValueError as exc:
        raise ContractValidationError(f"unknown contract status in transition {old!r}->{new!r}: {exc}") from exc

    if (old_status, new_status) not in _ALLOWED_TRANSITIONS:
        raise ContractValidationError(f"forbidden contract status transition: {old_status.value}->{new_status.value}")
    return True
