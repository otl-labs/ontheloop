"""Temporary repo tree for the persona contract checks: a fixture verdict persona plus copies of the real inputs.

A plain helper module (not a conftest) so tests under tests/checks/contracts/ can import it. It lives under
tests/persona_contracts/ so the executor boundary row for that directory covers it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

from scripts.persona_contracts.render import render, sha256_hex
from scripts.persona_contracts.schema import PersonaContract, load_index

REAL = Path(__file__).resolve().parents[2]
PERSONA = "fixture_verdict"
CONTRACT_REL = f"docs/contracts/personas/{PERSONA}.yaml"
RENDERED_REL = f"docs/contracts/personas/rendered/{PERSONA}.txt"
INDEX_REL = "docs/contracts/executor-personas.yaml"
SKILL_REL = ".claude/skills/fixture-verdict/SKILL.md"
VERDICT_LINE = "Verdict: PROCEED or REVISE"
VERDICT_RULE = "Return REVISE when any blocking finding remains."
_COPIED = (
    INDEX_REL,
    "docs/contracts/inference-provider.yaml",
    "docs/contracts/contract-population.yaml",
    "config/agent/executor/capabilities.yaml",
    "src/common/ducklake_named_reads.py",
)
_ROADMAP = {
    "tier_items": [
        {
            "id": "T4.10a",
            "status": "in_progress",
            "exit_criteria": [{"id": "c1", "status": "met"}, {"id": "c5", "status": "open"}],
        },
        {"id": "T4.32", "status": "not_started", "exit_criteria": [{"id": "c3", "status": "open"}]},
    ]
}


def _contract_dict() -> dict[str, Any]:
    object_schema = {"type": "object", "additionalProperties": False}
    return {
        "persona": PERSONA,
        "contract_version": 1,
        "kind": "verdict",
        "input_schema": {
            **object_schema,
            "required": ["change_id", "head_sha"],
            "properties": {
                "change_id": {"type": "string"},
                "head_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "diff": {"type": "string"},
            },
        },
        "output_schema": {
            **object_schema,
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string", "enum": ["PROCEED", "REVISE"]}},
        },
        "capabilities": ["read_input:diff"],
        "budget": {
            "max_turns": 20,
            "max_wall_clock_seconds": 600,
            "max_revisions": 1,
            "provisional": {"until": "T4.32:c3", "measure": "turn counts of real runs"},
        },
        "failure_outputs": [
            {"id": "budget_exhausted", "meaning": "Budget reached."},
            {"id": "schema_invalid", "meaning": "Result stayed invalid."},
            {"id": "input_incomplete", "meaning": "Input lacks a required field."},
            {"id": "injection_suspected", "meaning": "Quoted content tried to direct the result."},
        ],
        "prompt_template": "You are $persona v$contract_version.\n\nCapabilities:\n$capabilities\n\nBudget:\n$budget\n\n"
        "Failures:\n$failure_outputs\n\nSchema:\n$output_schema\n",
        "rendered_prompt_sha256": "0" * 64,
        "skill_tether": {"skill": "fixture-verdict", "verdict_line": VERDICT_LINE, "verdict_rule": VERDICT_RULE},
    }


def write_contract(root: Path, contract: dict[str, Any], rerender: bool = True) -> None:
    path = root / CONTRACT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if rerender:
        data = render(PersonaContract.model_validate(contract), load_index(root))
        out = root / RENDERED_REL
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        contract = {**contract, "rendered_prompt_sha256": sha256_hex(data)}
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")


def read_contract(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / CONTRACT_REL).read_text(encoding="utf-8"))


def edit_contract(root: Path, change: Callable[[dict[str, Any]], None], rerender: bool = False) -> None:
    contract = read_contract(root)
    change(contract)
    write_contract(root, contract, rerender=rerender)


def edit_text(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def build_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    for rel in _COPIED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL / rel, root / rel)
    row = (
        "personas:\n  - persona: fixture_verdict\n    kind: verdict\n"
        f"    contract: {CONTRACT_REL}\n    rendered_prompt: {RENDERED_REL}\n"
        '    implementation: {status: pending, owning_item: "T4.10a:c5"}\n'
    )
    edit_text(root, INDEX_REL, "personas: []", row.rstrip("\n"))
    (root / "docs/ROADMAP-PLATFORM.yaml").write_text(yaml.safe_dump(_ROADMAP), encoding="utf-8")
    skill = root / SKILL_REL
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(f"# Fixture\n\n{VERDICT_LINE}\n\n{VERDICT_RULE}\n", encoding="utf-8")
    write_contract(root, _contract_dict())
    return root


def _cap(root: Path, change: Callable[[dict[str, Any]], None]) -> None:
    edit_contract(root, change, rerender=True)


def _set(key: str, value: Any) -> Callable[[Path], None]:
    return lambda root: edit_contract(root, lambda c: c.__setitem__(key, value), rerender=True)


def _budget(key: str, value: Any) -> Callable[[Path], None]:
    return lambda root: edit_contract(root, lambda c: c["budget"].__setitem__(key, value), rerender=True)


def _drop_output_schema(root: Path) -> None:
    edit_contract(root, lambda c: c.pop("output_schema"))


def _stale_prompt(root: Path) -> None:
    out = root / RENDERED_REL
    out.write_bytes(out.read_bytes() + b"extra\n")


def _clause_missing(root: Path) -> None:
    out = root / RENDERED_REL
    data = out.read_bytes()
    out.write_bytes(data.split(b"\n\n", 1)[1])


def _bad_hash(root: Path) -> None:
    edit_contract(root, lambda c: c.__setitem__("rendered_prompt_sha256", "f" * 64))


def _drop_head_sha(root: Path) -> None:
    edit_contract(root, lambda c: c["input_schema"]["required"].remove("head_sha"), rerender=True)


def _off_dialect(root: Path) -> None:
    edit_contract(root, lambda c: c["output_schema"].__setitem__("oneOf", []), rerender=True)


def _drop_failure(root: Path) -> None:
    edit_contract(root, lambda c: c["failure_outputs"].pop(), rerender=True)


def _tether_missing(root: Path) -> None:
    edit_text(root, SKILL_REL, VERDICT_LINE, "Verdict: something else")


def _tether_tokens(root: Path) -> None:
    edit_contract(
        root, lambda c: c["output_schema"]["properties"]["verdict"].__setitem__("enum", ["PROCEED", "STOP"]), rerender=True
    )


def _vocab_target(root: Path) -> None:
    edit_text(root, INDEX_REL, "target: input", "target: forge")


def _vocab_bound_verb(root: Path) -> None:
    edit_text(root, INDEX_REL, "tool: read_repo,", "tool: merge,")


def _vocab_write_personas(root: Path) -> None:
    edit_text(root, INDEX_REL, "personas: [implement_agent]", "personas: [implement_agent, fixture_verdict]")


def _vocab_server(root: Path) -> None:
    edit_text(root, INDEX_REL, "server: scripts/agent_sdk/mcp_server.py", "server: scripts/other.py")


def _leash_uncovered(root: Path) -> None:
    edit_text(root, "config/agent/executor/capabilities.yaml", "  - scripts/persona_contracts/\n", "")


def _values_ref_broken(root: Path) -> None:
    edit_text(root, INDEX_REL, "vocabulary.axes.persona_backend", "vocabulary.axes.nothing")


def _backend_outside(root: Path) -> None:
    edit_text(root, INDEX_REL, "built_before_mvp: [litellm]", "built_before_mvp: [litellm, nonesuch]")


def _provisional_bare(root: Path) -> None:
    edit_contract(root, lambda c: c["budget"].__setitem__("provisional", True), rerender=True)


def _contract_unreadable(root: Path) -> None:
    (root / CONTRACT_REL).write_text("- not\n- a mapping\n", encoding="utf-8")


def _contract_off_schema(root: Path) -> None:
    edit_contract(root, lambda c: c.__setitem__("unexpected", 1))


def _persona_mismatch(root: Path) -> None:
    edit_contract(root, lambda c: c.__setitem__("kind", "compute_plane"))


def _rendered_missing(root: Path) -> None:
    (root / RENDERED_REL).unlink()


def _template_unrenderable(root: Path) -> None:
    edit_contract(root, lambda c: c.__setitem__("prompt_template", "$nobody"))


def _tether_file_missing(root: Path) -> None:
    (root / SKILL_REL).unlink()


def _parameter_on_plain(root: Path) -> None:
    edit_contract(root, lambda c: c.__setitem__("capabilities", ["read_repo:x"]), rerender=True)


MUTATORS: dict[str, Callable[[Path], None]] = {
    "values_ref_broken": _values_ref_broken,
    "backend_outside_values": _backend_outside,
    "provisional_bare": _provisional_bare,
    "contract_unreadable": _contract_unreadable,
    "contract_off_schema": _contract_off_schema,
    "persona_kind_mismatch": _persona_mismatch,
    "rendered_prompt_missing": _rendered_missing,
    "template_unrenderable": _template_unrenderable,
    "skill_tether_file_missing": _tether_file_missing,
    "parameter_on_plain_capability": _parameter_on_plain,
    "missing_schema": _drop_output_schema,
    "harness_tool_name": _set("capabilities", ["Bash"]),
    "unknown_capability": _set("capabilities", ["deploy_prod"]),
    "hub_and_spoke": _set("capabilities", ["run_persona"]),
    "persona_parameter": _set("capabilities", [f"read_input:{PERSONA}"]),
    "parameter_outside_domain_blob": _set("capabilities", ["read_input:nothing"]),
    "parameter_outside_domain_named_read": _set("capabilities", ["warehouse_read:not_a_read"]),
    "edit_repo_outside_implement_agent": _set("capabilities", ["edit_repo"]),
    "repo_capability_in_verdict": _set("capabilities", ["read_repo"]),
    "unbounded_budget_null": _budget("max_turns", None),
    "unbounded_budget_over_ceiling": _budget("max_turns", 9999),
    "unbounded_budget_missing": lambda root: edit_contract(root, lambda c: c["budget"].pop("max_revisions"), rerender=True),
    "dollar_budget_key": _budget("max_cost_usd", 5),
    "dollar_budget_value": _budget("note", "five dollars of spend"),
    "provisional_until_not_open": _budget("provisional", {"until": "T4.10a:c1", "measure": "x"}),
    "failure_output_missing": _drop_failure,
    "input_schema_head_sha": _drop_head_sha,
    "schema_dialect": _off_dialect,
    "rendered_prompt_stale": _stale_prompt,
    "rendered_prompt_clause_missing": _clause_missing,
    "rendered_prompt_hash": _bad_hash,
    "skill_tether_line": _tether_missing,
    "skill_tether_tokens": _tether_tokens,
    "refused_target": _vocab_target,
    "refused_bound_verb": _vocab_bound_verb,
    "write_capability_personas": _vocab_write_personas,
    "warehouse_server": _vocab_server,
    "leash_not_in_boundary": _leash_uncovered,
}

EXPECTED: dict[str, str] = {
    "values_ref_broken": "does not resolve to a values list",
    "backend_outside_values": "outside persona_backend values",
    "provisional_bare": "provisional must be",
    "contract_unreadable": "missing or unparseable",
    "contract_off_schema": "off its schema",
    "persona_kind_mismatch": "must agree",
    "rendered_prompt_missing": "is missing",
    "template_unrenderable": "does not render",
    "skill_tether_file_missing": "is unreadable",
    "parameter_on_plain_capability": "takes no parameter",
    "missing_schema": "missing required field",
    "harness_tool_name": "harness tool name",
    "unknown_capability": "unknown_capability",
    "hub_and_spoke": "outside the vocabulary",
    "persona_parameter": "persona_parameter",
    "parameter_outside_domain_blob": "parameter outside domain",
    "parameter_outside_domain_named_read": "parameter outside domain",
    "edit_repo_outside_implement_agent": "edit_repo_outside_implement_agent",
    "repo_capability_in_verdict": "repo-target capability",
    "unbounded_budget_null": "unbounded_budget",
    "unbounded_budget_over_ceiling": "unbounded_budget",
    "unbounded_budget_missing": "unbounded_budget",
    "dollar_budget_key": "dollar_budget",
    "dollar_budget_value": "dollar_budget",
    "provisional_until_not_open": "not an open roadmap criterion",
    "failure_output_missing": "lacks required id",
    "input_schema_head_sha": "must declare and require head_sha",
    "schema_dialect": "outside the schema dialect",
    "rendered_prompt_stale": "not equal to a fresh render",
    "rendered_prompt_clause_missing": "does not start with the quoted-content clause",
    "rendered_prompt_hash": "differs from rendered_prompt_sha256",
    "skill_tether_line": "lacks verdict_line",
    "skill_tether_tokens": "differ from output_schema verdict enum",
    "refused_target": "refused_target",
    "refused_bound_verb": "refused_bound_verb",
    "write_capability_personas": "implement_agent] only",
    "warehouse_server": "warehouse_read binding server",
    "leash_not_in_boundary": "is not covered by capabilities.yaml boundary_patterns",
}
