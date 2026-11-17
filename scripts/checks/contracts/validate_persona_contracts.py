"""Persona contract validator (T4.10a c2, c3, c5-mechanism, c6, c7, c8; Decisions 206, 229, 205).

Registered check and the evaluator of docs/contracts/executor-personas.yaml: the closed capability vocabulary, the
refused targets and verbs, every personas row's contract shape, budget, schema dialect, rendered prompt and skill
tether, an optional verdict_consistency rule (ordered cases "verdict V when any element of array A [with field F in S]",
then otherwise; checked against output_schema and the prose verdict_rule), a row's required registry_key (a
registered, unique source_registry.yaml canonical_id equal to the contract's skill_tether.skill; the registry is read
whenever the index has a row), and the leash's own paths being executor boundary paths. Never imports jsonschema (fast
tier).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from scripts.checks import _common, registry
from scripts.persona_contracts.render import render, sha256_hex
from scripts.persona_contracts.schema import (
    PersonaContract,
    PersonaIndex,
    check_schema_dialect,
    load_index,
    named_read_keys,
    open_criteria,
    parse_capability,
    resolve_values_ref,
)

_INDEX_REL = "docs/contracts/executor-personas.yaml"
_CAPS_REL = "config/agent/executor/capabilities.yaml"
_REGISTRY_REL = "config/agent/data_quality/source_registry.yaml"
_MCP_SERVER = "scripts/agent_sdk/mcp_server.py"
_LABEL = "Persona contracts"
_MONEY = re.compile(r"\b(cost|price|spend|currency|dollars?|usd|eur|gbp|money)\b|\$", re.IGNORECASE)
_MONEY_KEY = re.compile(r"cost|price|spend|currency|dollar|usd|money", re.IGNORECASE)
_VERDICT_TOKEN = re.compile(r"[A-Z][A-Z_]+")
_LEASH_PATHS = (
    _INDEX_REL,
    "docs/contracts/personas/",
    "scripts/persona_contracts/",
    "scripts/checks/contracts/validate_persona_contracts.py",
    "scripts/checks/contracts/validate_persona_contract_binding.py",
)


def _load_raw(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _vocabulary_failures(index: PersonaIndex, root: Path) -> list[str]:
    out: list[str] = []
    vocab = index.capability_vocabulary
    backends = index.run_persona.get("backends", {})
    values = resolve_values_ref(root, str(backends.get("values_ref", "")))
    if values is None:
        out.append(f"{_LABEL}: backends.values_ref does not resolve to a values list")
    else:
        for backend in backends.get("built_before_mvp", []):
            if backend not in values:
                out.append(f"{_LABEL}: built_before_mvp backend '{backend}' is outside persona_backend values")
    refused_verbs = {v.verb for v in vocab.refused.bound_verbs}
    for cap in vocab.capabilities:
        if cap.target in vocab.refused.targets:
            out.append(f"{_LABEL}: capability {cap.id} targets refused target '{cap.target}' (refused_target)")
        for backend, bound in cap.binding.items():
            if isinstance(bound, dict) and bound.get("tool") in refused_verbs:
                out.append(
                    f"{_LABEL}: capability {cap.id} binds refused verb '{bound['tool']}' on {backend} (refused_bound_verb)"
                )
        if cap.effect == "write" and cap.personas != ["implement_agent"]:
            out.append(f"{_LABEL}: write capability {cap.id} must list personas [implement_agent] only")
        if cap.id == "warehouse_read":
            server = cap.binding.get("litellm", {}).get("server")
            if server != _MCP_SERVER:
                out.append(f"{_LABEL}: warehouse_read binding server must be {_MCP_SERVER}, got {server}")
    return out


def _capability_failures(persona: str, caps: list[str], raw: dict[str, Any], index: PersonaIndex, root: Path) -> list[str]:
    out: list[str] = []
    vocab = {c.id: c for c in index.capability_vocabulary.capabilities}
    persona_ids = {r.persona for r in index.personas}
    props = (raw.get("input_schema") or {}).get("properties") or {}
    for spec in caps:
        cap_id, parameter = parse_capability(spec)
        if cap_id not in vocab:
            if cap_id in index.capability_vocabulary.harness_tool_names:
                out.append(f"{_LABEL}: {persona}: capability '{cap_id}' is a harness tool name (harness_tool_name)")
            else:
                out.append(f"{_LABEL}: {persona}: capability '{cap_id}' is outside the vocabulary (unknown_capability)")
            continue
        cap = vocab[cap_id]
        if parameter is not None and parameter in persona_ids:
            out.append(f"{_LABEL}: {persona}: capability {cap_id} parameter '{parameter}' is a persona id (persona_parameter)")
            continue
        if cap.parameter is None:
            if parameter is not None:
                out.append(f"{_LABEL}: {persona}: capability {cap_id} takes no parameter (parameter outside domain)")
        elif parameter is None:
            out.append(f"{_LABEL}: {persona}: capability {cap_id} needs a parameter (parameter outside domain)")
        elif cap.parameter.get("domain") == "input_blob" and parameter not in props:
            out.append(
                f"{_LABEL}: {persona}: {cap_id} parameter '{parameter}' is not an input property (parameter outside domain)"
            )
        elif cap.parameter.get("domain") == "named_read" and parameter not in named_read_keys(root):
            out.append(
                f"{_LABEL}: {persona}: {cap_id} parameter '{parameter}' is not a NAMED_READS key (parameter outside domain)"
            )
        if cap.effect == "write" and persona not in (cap.personas or []):
            out.append(
                f"{_LABEL}: {persona}: write capability {cap_id} is not allowed here (edit_repo_outside_implement_agent)"
            )
        if raw.get("kind") == "verdict" and cap.target == "repo":
            out.append(f"{_LABEL}: {persona}: a verdict contract names repo-target capability {cap_id}")
    return out


def _budget_failures(persona: str, budget: dict[str, Any], index: PersonaIndex, root: Path) -> list[str]:
    out: list[str] = []
    for key, ceiling in index.budget_rules.ceilings.items():
        value = budget.get(key)
        floor = 0 if key == "max_revisions" else 1
        if isinstance(value, bool) or not isinstance(value, int):
            out.append(f"{_LABEL}: {persona}: budget {key} is missing, null or not an integer (unbounded_budget)")
        elif value < floor or value > ceiling:
            out.append(f"{_LABEL}: {persona}: budget {key}={value} is outside {floor}..{ceiling} (unbounded_budget)")

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if _MONEY_KEY.search(str(k)):
                    out.append(
                        f"{_LABEL}: {persona}: budget key '{trail}{k}' names a currency, cost, price or spend (dollar_budget)"
                    )
                walk(v, f"{trail}{k}.")
        elif isinstance(node, str) and _MONEY.search(node):
            out.append(
                f"{_LABEL}: {persona}: budget value at '{trail}' names a currency, cost, price or spend (dollar_budget)"
            )

    walk(budget, "")
    prov = budget.get("provisional")
    if isinstance(prov, dict):
        if str(prov.get("until")) not in open_criteria(root):
            out.append(f"{_LABEL}: {persona}: provisional.until '{prov.get('until')}' is not an open roadmap criterion")
    elif prov is not False or not budget.get("basis"):
        out.append(f"{_LABEL}: {persona}: provisional must be {{until, measure}} or false with a basis")
    return out


def _registry_ids(root: Path) -> set[str] | None:
    raw = _load_raw(root / _REGISTRY_REL)
    entries = raw.get("entries") if raw else None
    if not isinstance(entries, list):
        return None
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("canonical_id"), str):
            return None
        ids.add(entry["canonical_id"])
    return ids


def _registry_key_failures(index: PersonaIndex, root: Path) -> list[str]:
    keyed = list(index.personas)
    if not keyed:
        return []
    ids = _registry_ids(root)
    if ids is None:
        return [f"{_LABEL}: {_REGISTRY_REL} is unreadable"]
    out = [
        f"{_LABEL}: {r.persona}: registry_key '{r.registry_key}' is not a registered canonical_id"
        for r in keyed
        if r.registry_key not in ids
    ]
    counts = Counter(r.registry_key for r in keyed)
    out += [f"{_LABEL}: registry_key '{key}' is not unique" for key, n in counts.items() if n > 1]
    return out


def _git_show(root: Path, rel: str) -> str | None:
    mb = _common.run(["git", "merge-base", "origin/main", "HEAD"], capture_output=True, text=True, cwd=root)
    if mb.returncode != 0:
        return None
    shown = _common.run(["git", "show", f"{mb.stdout.strip()}:{rel}"], capture_output=True, text=True, cwd=root)
    return shown.stdout if shown.returncode == 0 else None


def _rendered_failures(
    persona: str, row: Any, raw: dict[str, Any], contract: PersonaContract, index: PersonaIndex, root: Path
) -> list[str]:
    out: list[str] = []
    target = root / row.rendered_prompt
    try:
        fresh = render(contract, index)
    except (ValueError, KeyError) as exc:
        return [f"{_LABEL}: {persona}: prompt_template does not render ({exc})"]
    if not target.is_file():
        return [f"{_LABEL}: {persona}: rendered prompt {row.rendered_prompt} is missing"]
    data = target.read_bytes()
    if data != fresh:
        out.append(f"{_LABEL}: {persona}: rendered prompt is not equal to a fresh render")
    if not data.decode("utf-8", "replace").startswith(index.rendered_prompt.quoted_content_clause.strip()):
        out.append(f"{_LABEL}: {persona}: rendered prompt does not start with the quoted-content clause")
    if sha256_hex(data) != contract.rendered_prompt_sha256:
        out.append(f"{_LABEL}: {persona}: rendered prompt hash differs from rendered_prompt_sha256")
    base_raw = _git_show(root, row.rendered_prompt)
    if base_raw is not None and base_raw.encode("utf-8") != data:
        base_contract = _git_show(root, row.contract)
        base_version = (yaml.safe_load(base_contract) or {}).get("contract_version") if base_contract else None
        if isinstance(base_version, int) and contract.contract_version <= base_version:
            out.append(f"{_LABEL}: {persona}: rendered prompt changed against the merge-base without raising contract_version")
    return out


def _tether_failures(persona: str, contract: PersonaContract, root: Path) -> list[str]:
    tether = contract.skill_tether
    if tether is None:
        return []
    path = root / ".claude" / "skills" / tether.skill / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"{_LABEL}: {persona}: skill_tether file {path.relative_to(root).as_posix()} is unreadable"]
    out = [
        f"{_LABEL}: {persona}: skill file lacks {name} byte for byte"
        for name, value in (("verdict_line", tether.verdict_line), ("verdict_rule", tether.verdict_rule))
        if value not in text
    ]
    tokens = set(_VERDICT_TOKEN.findall(tether.verdict_line.partition(":")[2]))
    enum = ((contract.output_schema.get("properties") or {}).get("verdict") or {}).get("enum")
    if not isinstance(enum, list) or tokens != set(enum):
        out.append(f"{_LABEL}: {persona}: verdict_line tokens {sorted(tokens)} differ from output_schema verdict enum")
    return out


def _malformed_rule(rule: Any) -> bool:
    def condition_ok(cond: Any) -> bool:
        if not isinstance(cond, dict) or not isinstance(cond.get("array"), str):
            return False
        keys = set(cond)
        if keys == {"array"}:
            return True
        values = cond.get("in")
        return (
            keys == {"array", "field", "in"}
            and isinstance(cond["field"], str)
            and isinstance(values, list)
            and bool(values)
            and all(isinstance(v, str) for v in values)
        )

    def case_ok(case: Any) -> bool:
        any_of = case.get("any") if isinstance(case, dict) else None
        return (
            isinstance(case, dict)
            and set(case) == {"verdict", "any"}
            and isinstance(case["verdict"], str)
            and isinstance(any_of, list)
            and bool(any_of)
            and all(condition_ok(c) for c in any_of)
        )

    if not isinstance(rule, dict) or set(rule) != {"cases", "otherwise"} or not isinstance(rule["otherwise"], str):
        return True
    cases = rule["cases"]
    return not (isinstance(cases, list) and cases and all(case_ok(c) for c in cases))


def _walk_array(schema: Any, path: str) -> dict[str, Any] | None:
    node = schema
    for step in path.split("."):
        props = node.get("properties") if isinstance(node, dict) else None
        node = props.get(step) if isinstance(props, dict) else None
        if not isinstance(node, dict):
            return None
    return node if node.get("type") == "array" else None


def _verdict_rule_failures(persona: str, contract: PersonaContract) -> list[str]:
    rule = contract.verdict_consistency
    if rule is None:
        return []
    label = f"{_LABEL}: {persona}: "
    if contract.kind == "compute_plane":
        return [f"{label}verdict_consistency on a compute_plane contract"]
    if _malformed_rule(rule):
        return [f"{label}verdict_consistency is malformed"]
    out: list[str] = []
    for case in rule["cases"]:
        for cond in case["any"]:
            path = cond["array"]
            array = _walk_array(contract.output_schema, path)
            if array is None:
                out.append(f"{label}array {path} is not an array of output_schema")
                continue
            if "field" not in cond:
                continue
            items = array.get("items")
            props = items.get("properties") if isinstance(items, dict) else None
            prop = props.get(cond["field"]) if isinstance(props, dict) else None
            enum = prop.get("enum") if isinstance(prop, dict) else None
            if not isinstance(enum, list):
                out.append(f"{label}field {cond['field']} is not an enum property of {path}'s items")
                continue
            out += [f"{label}in value {v} is outside {path}[].{cond['field']}'s enum" for v in cond["in"] if v not in enum]
    verdict_enum = ((contract.output_schema.get("properties") or {}).get("verdict") or {}).get("enum")
    verdicts = [c["verdict"] for c in rule["cases"]] + [rule["otherwise"]]
    if not isinstance(verdict_enum, list) or sorted(verdicts) != sorted(verdict_enum):
        out.append(f"{label}verdict_consistency verdicts differ from the verdict enum")
    prose = contract.skill_tether.verdict_rule if contract.skill_tether else ""
    names = [*verdicts, *(v for c in rule["cases"] for cond in c["any"] for v in cond.get("in", []))]
    for name in dict.fromkeys(names):
        if not re.search(rf"\b{re.escape(name)}\b", prose):
            out.append(f"{label}verdict_consistency names {name}, absent from skill_tether.verdict_rule")
    return out


def _contract_failures(row: Any, index: PersonaIndex, root: Path) -> list[str]:
    persona = row.persona
    path = root / row.contract
    raw = _load_raw(path)
    if raw is None:
        return [f"{_LABEL}: {persona}: contract {row.contract} is missing or unparseable"]
    required = list(index.persona_contract["required"])
    if row.kind == "verdict":
        required += list(index.persona_contract["required_for_verdict"])
    missing = [f for f in required if raw.get(f) is None]
    if missing:
        return [f"{_LABEL}: {persona}: missing required field(s) {missing} (missing_schema)"]
    out: list[str] = []
    if raw.get("persona") != persona or path.stem != persona or raw.get("kind") != row.kind:
        out.append(f"{_LABEL}: {persona}: persona, file stem, kind and personas row must agree")
    try:
        contract = PersonaContract.model_validate(raw)
    except ValidationError as exc:
        return out + [f"{_LABEL}: {persona}: contract is off its schema ({exc.error_count()} errors)"]
    props = (raw["input_schema"] or {}).get("properties") or {}
    required_inputs = (raw["input_schema"] or {}).get("required") or []
    for field in ("change_id", "head_sha"):
        if field not in props or field not in required_inputs:
            out.append(f"{_LABEL}: {persona}: input_schema must declare and require {field}")
    keywords = index.schema_dialect.keywords
    out += check_schema_dialect(raw["input_schema"], keywords, f"{_LABEL}: {persona}: input_schema")
    out += check_schema_dialect(raw["output_schema"], keywords, f"{_LABEL}: {persona}: output_schema")
    out += _capability_failures(persona, contract.capabilities, raw, index, root)
    out += _budget_failures(persona, raw["budget"], index, root)
    ids = {f.id for f in contract.failure_outputs}
    out += [
        f"{_LABEL}: {persona}: failure_outputs lacks required id {fid}"
        for fid in index.failure_envelope.required_ids
        if fid not in ids
    ]
    out += _rendered_failures(persona, row, raw, contract, index, root)
    out += _tether_failures(persona, contract, root)
    out += _verdict_rule_failures(persona, contract)
    tether = contract.skill_tether
    if tether is not None and row.registry_key != tether.skill:
        out.append(f"{_LABEL}: {persona}: registry_key '{row.registry_key}' differs from skill_tether.skill '{tether.skill}'")
    return out


def _leash_failures(root: Path) -> list[str]:
    caps = _load_raw(root / _CAPS_REL) or {}
    patterns = caps.get("boundary_patterns") or []
    return [
        f"{_LABEL}: leash path {p} is not covered by capabilities.yaml boundary_patterns"
        for p in _LEASH_PATHS
        if not any(pat in p for pat in patterns)
    ]


@registry.register("validate_persona_contracts", owner="platform")
def validate_persona_contracts(failed: list[str], root: Path | None = None) -> None:
    root = root or _common.ROOT
    try:
        index = load_index(root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        failed.append(f"{_LABEL}: {_INDEX_REL} is unreadable or off its schema ({type(exc).__name__})")
        registry.skipped(f"{_INDEX_REL} unreadable")
        return
    try:
        failed.extend(_vocabulary_failures(index, root))
        failed.extend(_registry_key_failures(index, root))
        for row in index.personas:
            failed.extend(_contract_failures(row, index, root))
    except (OSError, yaml.YAMLError, ImportError, AttributeError, KeyError) as exc:
        failed.append(f"{_LABEL}: a source file is missing or unreadable ({type(exc).__name__}: {exc})")
    failed.extend(_leash_failures(root))
    registry.examined(len(index.capability_vocabulary.capabilities) + len(index.personas), unit="persona_contract_items")
