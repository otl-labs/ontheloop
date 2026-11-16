"""Models and loaders for docs/contracts/executor-personas.yaml and the persona contracts it indexes (T4.10a).

The fast tier never imports jsonschema: the schema dialect is a keyword subset checked here, and the full 2020-12
metaschema is checked only by tests/persona_contracts/test_schema_metaschema.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[2]
INDEX_REL = "docs/contracts/executor-personas.yaml"
_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
_NAMED_READS_REL = "src/common/ducklake_named_reads.py"
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


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
    registry_key: str


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


def open_criteria(root: Path) -> set[str]:
    """Every "<item>:<criterion>" whose status is open on the roadmap under `root`."""
    data = yaml.load((root / _ROADMAP_REL).read_text(encoding="utf-8"), Loader=_LOADER)
    found: set[str] = set()
    for item in data.get("tier_items", []):
        for crit in item.get("exit_criteria", []) or []:
            if isinstance(crit, dict) and crit.get("status") == "open":
                found.add(f"{item.get('id')}:{crit.get('id')}")
    return found


def named_read_keys(root: Path) -> set[str]:
    """Keys of NAMED_READS whose agent_facing is true, read from the module file under `root`."""
    name = f"_persona_named_reads_{abs(hash(str(root)))}"
    spec = importlib.util.spec_from_file_location(name, root / _NAMED_READS_REL)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_NAMED_READS_REL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return {key for key, nr in module.NAMED_READS.items() if nr.agent_facing}


def resolve_values_ref(root: Path, ref: str) -> list[Any] | None:
    """Resolve "<file>#<dotted.path>.values" style refs: the `values` list of the mapping the path names."""
    rel, _, dotted = ref.partition("#")
    node: Any = yaml.load((root / rel).read_text(encoding="utf-8"), Loader=_LOADER)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    values = node.get("values") if isinstance(node, dict) else None
    return values if isinstance(values, list) else None
