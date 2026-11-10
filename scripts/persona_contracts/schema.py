"""Models and loaders for docs/contracts/executor-personas.yaml and the persona contracts it indexes (T4.10a).

The fast tier never imports jsonschema: the schema dialect is a keyword subset checked here, and the full 2020-12
metaschema is checked only by tests/persona_contracts/test_schema_metaschema.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[2]
INDEX_REL = "docs/contracts/executor-personas.yaml"
_RESERVED_SCHEMA_VALUE_KEYS = frozenset({"enum", "const", "pattern", "description"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capability(_Strict):
    id: str
    effect: str
    target: str
    parameter: dict[str, str] | None
    binding: dict[str, Any]
    meaning: str
    personas: list[str] | None = None


class BoundVerb(_Strict):
    verb: str
    port: str
    why: str


class Refused(_Strict):
    targets: list[str]
    bound_verbs: list[BoundVerb]
    rule: str


class CapabilityVocabulary(_Strict):
    rule: str
    field_meanings: dict[str, str]
    capabilities: list[Capability]
    harness_tool_names: list[str]
    refused: Refused


class SchemaDialect(_Strict):
    base: str
    keywords: list[str]
    rule: str


class BudgetRules(_Strict):
    ceilings: dict[str, int]
    rule: str
    provisional: str


class FailureEnvelope(_Strict):
    shape: str
    required_ids: dict[str, str]
    rule: str


class RenderedPrompt(_Strict):
    location: str
    format: str
    hash: str
    render: str
    placeholders: dict[str, str]
    quoted_content_clause: str
    rule: str


class PersonaRow(_Strict):
    persona: str
    kind: str
    contract: str
    rendered_prompt: str
    implementation: dict[str, Any]


class PersonaIndex(_Strict):
    contract: dict[str, Any]
    amendment_log: list[dict[str, Any]]
    run_persona: dict[str, Any]
    capability_vocabulary: CapabilityVocabulary
    persona_contract: dict[str, Any]
    schema_dialect: SchemaDialect
    budget_rules: BudgetRules
    failure_envelope: FailureEnvelope
    rendered_prompt: RenderedPrompt
    declared_subtree: dict[str, Any]
    implementation_binding: dict[str, Any]
    personas_row: dict[str, Any]
    personas: list[PersonaRow]


class FailureOutput(_Strict):
    id: str
    meaning: str


class SkillTether(_Strict):
    skill: str
    verdict_line: str
    verdict_rule: str


class PersonaContract(_Strict):
    persona: str
    contract_version: int
    kind: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: list[str]
    budget: dict[str, Any]
    failure_outputs: list[FailureOutput]
    prompt_template: str
    rendered_prompt_sha256: str
    skill_tether: SkillTether | None = None


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_index(root: Path | None = None) -> PersonaIndex:
    return PersonaIndex.model_validate(load_yaml((root or ROOT) / INDEX_REL))


def load_contract(path: Path) -> PersonaContract:
    return PersonaContract.model_validate(load_yaml(path))


def parse_capability(spec: str) -> tuple[str, str | None]:
    """Split "<id>[:<parameter>]" into (id, parameter or None)."""
    cap_id, sep, parameter = spec.partition(":")
    return cap_id, (parameter if sep else None)


def check_schema_dialect(schema: Any, keywords: list[str], where: str = "schema") -> list[str]:
    """Failure strings for a schema using a keyword outside `keywords`, an object schema without
    additionalProperties false, or an object schema without an explicit required list."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{where}: not a mapping"]
    for key in schema:
        if key not in keywords:
            errors.append(f"{where}: keyword '{key}' is outside the schema dialect")
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            errors.append(f"{where}: object schema must set additionalProperties to false")
        if not isinstance(schema.get("required"), list):
            errors.append(f"{where}: object schema must list required explicitly")
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            errors.extend(check_schema_dialect(sub, keywords, f"{where}.properties.{name}"))
    if "items" in schema:
        errors.extend(check_schema_dialect(schema["items"], keywords, f"{where}.items"))
    return errors
