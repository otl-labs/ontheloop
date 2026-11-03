"""Rule-source check (rec-4158; Decision 210 cl.3, cl.4): every contract-backed writer table keeps its rule declarations
well-formed, one-way and loosening-proof.

Scope is derived, never listed: each ops_tables key of config/lambda/ducklake/field_semantics.yaml that has
docs/contracts/<table>.yaml (the grammar and the ratchet read base and head together, so a PR removing a table and its
contract cannot escape the ratchet), plus the raw $ref baselines those contracts reach; the retired `enforced` key is
banned in every contract under docs/contracts/. Orchestration only: the grammar is _rule_grammar, the merge-base ratchet
is _rule_ratchet, and the ops_recommendations-only parity legs (ops.yaml write_time tests and the status vocabularies)
are _ops_rule_bindings -- the portal row-rule gate plan retires that last leg. Writer coverage is the conformance check's
(validate_write_time_enforcement_conformance). The git seam stays here so the helpers are git-free.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from scripts import contract_rules
from scripts.checks import _common, registry
from scripts.checks.contracts import _ops_rule_bindings, _rule_grammar, _rule_ratchet

CHECK_REL = "scripts/checks/contracts/validate_ops_rule_source.py"
_CONTRACTS_REL = "docs/contracts"
_REGISTRY_REL = _rule_ratchet.REGISTRY_REL
_FIELD_SEMANTICS_REL = _rule_ratchet.FIELD_SEMANTICS_REL


def _git(root: Path, *args: str) -> Any:
    return _common.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)


def resolve_base(root: Path) -> tuple[str | None, str]:
    result = _git(root, "merge-base", "HEAD", "origin/main")
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        return None, "no merge-base with origin/main resolves"
    return sha, ""


def _write_base(root: Path, base: str, rel: str, dest: Path) -> str | None:
    shown = _git(root, "show", f"{base}:{rel}")
    if shown.returncode != 0:
        return None
    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
    (dest / rel).write_text(shown.stdout, encoding="utf-8")
    return shown.stdout


def _base_snapshot(root: Path, base: str, dest: Path) -> None:
    """Materialise the merge-base's field_semantics, source registry, every in-scope contract and its $ref targets."""
    _write_base(root, base, _REGISTRY_REL, dest)
    text = _write_base(root, base, _FIELD_SEMANTICS_REL, dest)
    doc = contract_rules.parse_yaml(text) if text else None
    for table in ((doc or {}).get("ops_tables") or {}) if isinstance(doc, dict) else ():
        body = _write_base(root, base, f"{_CONTRACTS_REL}/{table}.yaml", dest)
        contract = contract_rules.parse_yaml(body) if body else None
        for spec in ((contract or {}).get("fields") or {}).values() if isinstance(contract, dict) else ():
            ref = spec.get("$ref") if isinstance(spec, dict) else None
            if ref:
                _write_base(root, base, f"{_CONTRACTS_REL}/{ref.partition('#')[0]}", dest)


def _base_has_check(root: Path, base: str) -> bool:
    return _git(root, "cat-file", "-e", f"{base}:{CHECK_REL}").returncode == 0


def _ratchet(root: Path, base: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        _base_snapshot(root, base, Path(tmp))
        return _rule_ratchet.ratchet_errors(Path(tmp), root)


def _declaration_errors(root: Path) -> tuple[list[str], int, int, dict[str, Any]]:
    errors: list[str] = []
    examined = 0
    recs: dict[str, Any] = {}
    tables = _rule_ratchet.in_scope_tables(root)
    for table in tables:
        try:
            fields, intents, table_class = _rule_grammar.read_table(table, root / _CONTRACTS_REL, root)
        except (ValueError, OSError) as exc:
            errors.append(f"{table}: contract unreadable: {exc}")
            continue
        errors.extend(_rule_grammar.grammar_errors(table, table_class, fields, intents, root))
        examined += sum(len(i) for i in intents.values())
        if table == "ops_recommendations":
            recs = {"fields": fields, "intents": intents}
    return errors, examined, len(tables), recs


@registry.register("validate_ops_rule_source", owner="platform")
def validate_ops_rule_source(failed: list[str], *, root: Path | None = None) -> None:
    """Fail on a malformed or loosened rule declaration, a retired `enforced` key, or ops.yaml/status vocabulary drift."""
    print("\n=== Ops rule source ===")
    root = root if root is not None else _common.ROOT
    errors, examined, table_count, recs = _declaration_errors(root)
    ban, scanned = _rule_grammar.enforced_ban_errors(root / _CONTRACTS_REL)
    errors.extend(ban)
    if recs:
        try:
            errors.extend(_ops_rule_bindings.binding_errors(root, recs["fields"], recs["intents"]))
        except (OSError, SyntaxError, yaml.YAMLError) as exc:
            errors.append(f"parity inputs unreadable: {exc}")
    base, why = resolve_base(root)
    if base is None:
        print(f"  ratchet leg skipped: {why}")
    elif not _base_has_check(root, base):
        print("  ratchet dormant: the merge-base predates this check (installing PR, Decision 208 cl.1 absent arm)")
    else:
        try:
            errors.extend(_ratchet(root, base))
        except ValueError as exc:
            errors.append(f"ratchet: could not read the rule legs: {exc}")
    registry.examined(examined, unit="declared_rules")
    for err in errors:
        print(f"  FAIL: {err}")
        failed.append(f"Ops rule source: {err}")
    if not errors:
        print(
            f"  PASS: {examined} declared rule legs well-formed across {table_count} tables; no `enforced` key in {scanned} "
            "contracts; ops.yaml and status vocabularies agree."
        )
