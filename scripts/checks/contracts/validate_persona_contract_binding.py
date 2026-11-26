"""Persona contract binding check (T4.10a c4): a persona's code never lands unbound to its contract.

Each personas row of docs/contracts/executor-personas.yaml is {status: pending, owning_item} until the persona's
code lands, then {status: implemented, location}. Never imports jsonschema (fast tier).

Fixture rows (Decision 235 cl.1 planned change, rec-5561) bind through the kind, never by naming:
(a) a fixture row's location is an existing .py module under implementation_binding.landing_dirs holding a
non-docstring string literal "fixture_kind" (it reads fixture_kind.personas): green;
(b) "fixture_kind" only in a docstring or comment, or a location that does not parse: "does not bind through
fixture_kind"; (c) a location outside landing_dirs: "is outside the landing dirs"; (d) a location naming the contract
path or persona id fails under (f); (e) a missing location: "does not exist"; (f) for every fixture row, pending or
implemented, a landing-dir module whose literals name the persona id or its contract path fails "names fixture
persona"; (g) verdict and compute_plane rows keep the contract-path rule; (h) landing-dir membership is decided on
(root / location).resolve() being relative to a resolved landing dir, so an absolute location, an escaping segment or a
symlink out of every landing dir is outside. Named residuals of (f), not screened: a registry_key literal
(scripts/personas/keyed_run.py AGENT, rec-5675), an id computed, concatenated or read from the index (how execute.py
reaches a fixture persona), and non-.py files under the landing dirs.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.persona_contracts.schema import load_index, open_criteria

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


def _check_implemented(row: Any, location_text: str, root: Path, resolved_landing: list[Path], failed: list[str]) -> None:
    location = root / location_text
    if not location.is_file():
        failed.append(f"{_LABEL}: {row.persona}: implemented location '{location_text}' does not exist")
    elif row.kind == "fixture":
        resolved = location.resolve()
        if not any(resolved.is_relative_to(d) for d in resolved_landing):
            failed.append(f"{_LABEL}: {row.persona}: implemented location '{location_text}' is outside the landing dirs")
        elif resolved.suffix != ".py" or "fixture_kind" not in _string_literals(resolved):
            failed.append(f"{_LABEL}: {row.persona}: implemented location does not bind through fixture_kind")
    elif row.contract not in _string_literals(location):
        failed.append(f"{_LABEL}: {row.persona}: implemented location does not name {row.contract}")


def _scan_landing_dirs(
    index: Any, root: Path, pending: dict[str, str], fixture_rows: dict[str, str], failed: list[str]
) -> int:
    scanned = 0
    for landing in index.implementation_binding.get("landing_dirs", []):
        for module in sorted((root / landing).rglob("*.py")):
            scanned += 1
            names = _string_literals(module)
            rel = module.relative_to(root).as_posix()
            for persona, contract_path in pending.items():
                if persona not in fixture_rows and (persona in names or contract_path in names):
                    failed.append(f"{_LABEL}: {rel} names pending persona '{persona}'")
            for persona, contract_path in fixture_rows.items():
                if persona in names or contract_path in names:
                    failed.append(f"{_LABEL}: {rel} names fixture persona '{persona}'")
    return scanned


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
    landing_dirs = [str(d) for d in index.implementation_binding.get("landing_dirs", [])]
    resolved_landing = [(root / d).resolve() for d in landing_dirs]
    pending: dict[str, str] = {}
    fixture_rows: dict[str, str] = {}
    for row in index.personas:
        if row.kind == "fixture":
            fixture_rows[row.persona] = row.contract
        impl = row.implementation
        status = impl.get("status")
        if status == "pending" and set(impl) == {"status", "owning_item"}:
            pending[row.persona] = row.contract
            if impl["owning_item"] not in open_set:
                failed.append(
                    f"{_LABEL}: {row.persona}: pending owning_item '{impl['owning_item']}' is not an open roadmap criterion"
                )
        elif status == "implemented" and set(impl) == {"status", "location"}:
            _check_implemented(row, str(impl["location"]), root, resolved_landing, failed)
        else:
            failed.append(f"{_LABEL}: {row.persona}: implementation must be pending (owning_item) or implemented (location)")
    scanned = _scan_landing_dirs(index, root, pending, fixture_rows, failed)
    registry.examined(len(index.personas) + scanned, unit="persona_bindings")
