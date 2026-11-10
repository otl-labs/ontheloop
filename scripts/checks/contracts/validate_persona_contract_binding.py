"""Persona contract binding check (T4.10a c4): a persona's code never lands unbound to its contract.

Each personas row of docs/contracts/executor-personas.yaml is {status: pending, owning_item} until the persona's
code lands, then {status: implemented, location}. Never imports jsonschema (fast tier).
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from scripts.checks import _common, registry
from scripts.persona_contracts.schema import load_index
from scripts.persona_contracts.sources import open_criteria

_LABEL = "Persona contract binding"


def _string_literals(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_ids.add(id(body[0].value))
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids
    }


@registry.register("validate_persona_contract_binding", owner="platform")
def validate_persona_contract_binding(failed: list[str], root: Path | None = None) -> None:
    root = root or _common.ROOT
    try:
        index = load_index(root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        failed.append(f"{_LABEL}: executor-personas.yaml is unreadable or off its schema ({type(exc).__name__})")
        registry.skipped("executor-personas.yaml unreadable")
        return
    try:
        open_set = open_criteria(root)
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"{_LABEL}: roadmap is missing or unreadable ({type(exc).__name__})")
        registry.skipped("roadmap unreadable")
        return
    pending: dict[str, str] = {}
    for row in index.personas:
        impl = row.implementation
        status = impl.get("status")
        if status == "pending" and set(impl) == {"status", "owning_item"}:
            pending[row.persona] = row.contract
            if impl["owning_item"] not in open_set:
                failed.append(
                    f"{_LABEL}: {row.persona}: pending owning_item '{impl['owning_item']}' is not an open roadmap criterion"
                )
        elif status == "implemented" and set(impl) == {"status", "location"}:
            location = root / str(impl["location"])
            if not location.is_file():
                failed.append(f"{_LABEL}: {row.persona}: implemented location '{impl['location']}' does not exist")
            elif row.contract not in _string_literals(location):
                failed.append(f"{_LABEL}: {row.persona}: implemented location does not name {row.contract}")
        else:
            failed.append(f"{_LABEL}: {row.persona}: implementation must be pending (owning_item) or implemented (location)")
    scanned = 0
    for landing in index.implementation_binding.get("landing_dirs", []):
        for module in sorted((root / landing).rglob("*.py")):
            scanned += 1
            names = _string_literals(module)
            for persona, contract_path in pending.items():
                if persona in names or contract_path in names:
                    failed.append(f"{_LABEL}: {module.relative_to(root).as_posix()} names pending persona '{persona}'")
    registry.examined(len(index.personas) + scanned, unit="persona_bindings")
