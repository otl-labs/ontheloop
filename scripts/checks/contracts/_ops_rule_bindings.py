"""The ops_recommendations-only legs of validate_ops_rule_source (rec-4158; moved from the entry module).

Two surviving copies of the contract's rules stay honest here: config/agent/data_quality/ops.yaml, a DQ backstop with
no write-time role that is never stricter than the contract (one-way), and the status vocabularies the writer, the
executor store and the Pydantic model carry. The status sources are read by ast, never imported.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from scripts import contract_rules

_OPS_YAML_REL = "config/agent/data_quality/ops.yaml"
_STATUS_SOURCES = (
    "src/common/ducklake_scd2_schema.py",
    "scripts/executor/jsonl_store.py",
)


def _leg(intent: dict[str, Any], kind: str) -> dict[str, Any]:
    value = intent.get(kind)
    return value if isinstance(value, dict) else {}


def _since(intent: dict[str, Any], kind: str) -> str | None:
    since = _leg(intent, kind).get("exclude_before")
    return None if since is None else str(since)


def _has_write_time_key(node: Any) -> bool:
    if isinstance(node, dict):
        return "write_time" in node or any(_has_write_time_key(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_write_time_key(v) for v in node)
    return False


def _ops_yaml_tests(doc: dict[str, Any]) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], list[str]]:
    columns = (((doc.get("tables") or {}).get("ops_recommendations") or {}).get("columns")) or {}
    out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    malformed: list[str] = []
    for column, spec in columns.items():
        for test in (spec or {}).get("tests") or []:
            params: Any
            if isinstance(test, str):
                name, params = test, {}
            elif isinstance(test, dict) and len(test) == 1:
                name, params = next(iter(test.items()))
            else:
                malformed.append(f"ops.yaml {column}: test entry {test!r} must be a name or a single-key mapping")
                continue
            params = params if isinstance(params, dict) else {}
            if params.get("enforced", True) is not False:
                out.setdefault(column, []).append((name, params))
    return out, malformed


def _never_stricter(where: str, name: str, params: dict[str, Any], intent: dict[str, Any]) -> list[str]:
    if name == "not_null":
        if not contract_rules.not_null_declared(intent.get("not_null")):
            return [f"{where}: stricter than the contract (no not_null rule declared)"]
        since, dq_since = _since(intent, "not_null"), params.get("exclude_before")
        if since is not None and (dq_since is None or str(dq_since) < since):
            return [f"{where}: exclude_before is earlier than the contract's not_null date {since}"]
    elif name == "accepted_values":
        declared = _leg(intent, "accepted_values").get("values")
        if declared is None or not set(params.get("values") or []) <= set(declared):
            return [f"{where}: values are not a subset of the contract's accepted_values"]
        since, dq_since = _since(intent, "accepted_values"), params.get("exclude_before")
        if since is not None and (dq_since is None or str(dq_since) < since):
            return [f"{where}: exclude_before is earlier than the contract's accepted_values date {since}"]
    return []


def _ops_yaml_errors(root: Path, intents: dict[str, dict[str, Any]]) -> list[str]:
    """ops.yaml has no write-time role and, on ops_recommendations, no DQ-enforced test stricter than the contract."""
    doc = yaml.safe_load((root / _OPS_YAML_REL).read_text(encoding="utf-8")) or {}
    errors = (
        ["ops.yaml: a `write_time` key remains; the portal reads no rule from this file"] if _has_write_time_key(doc) else []
    )
    tests_by_column, malformed = _ops_yaml_tests(doc)
    errors.extend(malformed)
    for column, tests in tests_by_column.items():
        intent = intents.get(column)
        for name, params in tests:
            where = f"ops.yaml {column}.{name}"
            if intent is None:
                if name in ("not_null", "accepted_values"):
                    errors.append(f"{where}: test on a column the contract does not declare")
                continue
            errors.extend(_never_stricter(where, name, params, intent))
    return errors


def _literal_strings(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Call) and node.args:
        node = node.args[0]
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return sorted(str(v) for v in value) if isinstance(value, (set, frozenset, list, tuple)) else None


def _module_assign(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def _scd2_status(root: Path) -> list[str] | None:
    tree = ast.parse((root / _STATUS_SOURCES[0]).read_text(encoding="utf-8"))
    table = _module_assign(tree, "STATUS_TRANSITIONS")
    if not isinstance(table, ast.Dict):
        return None
    for key, value in zip(table.keys, table.values):
        if isinstance(key, ast.Constant) and key.value == "ops_recommendations" and isinstance(value, ast.Dict):
            for inner_key, inner in zip(value.keys, value.values):
                if isinstance(inner_key, ast.Constant) and inner_key.value == "enforced" and isinstance(inner, ast.Name):
                    target = _module_assign(tree, inner.id)
                    return _literal_strings(target) if target is not None else None
    return None


def _store_status(root: Path) -> list[str] | None:
    target = _module_assign(ast.parse((root / _STATUS_SOURCES[1]).read_text(encoding="utf-8")), "_VALID_STATUSES")
    return _literal_strings(target) if target is not None else None


def _model_status(root: Path) -> list[str] | None:
    tree = ast.parse((root / _STATUS_SOURCES[1]).read_text(encoding="utf-8"))
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Recommendation"):
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "status":
                for node in ast.walk(stmt.annotation):
                    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
                        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                        return _literal_strings(ast.List(members))
    return None


def _status_errors(root: Path, intents: dict[str, dict[str, Any]]) -> list[str]:
    declared = sorted(_leg(intents.get("status", {}), "accepted_values").get("values") or [])
    errors = []
    for label, reader in (
        ("STATUS_TRANSITIONS enforced set", _scd2_status),
        ("jsonl_store._VALID_STATUSES", _store_status),
        ("Recommendation status Literal", _model_status),
    ):
        found = reader(root)
        if found is None:
            errors.append(f"status vocabulary: could not read the {label} by ast")
        elif found != declared:
            errors.append(f"status vocabulary: the contract's {declared} differs from the {label} {found}")
    return errors


def binding_errors(root: Path, raw_fields: dict[str, Any], intents: dict[str, dict[str, Any]]) -> list[str]:
    return [*_ops_yaml_errors(root, intents), *_status_errors(root, intents)]
