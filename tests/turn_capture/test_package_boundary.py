"""Plane-neutral boundary: AST scan of src/turn_capture, empty package marker, importlinter contract declared."""

from __future__ import annotations

import ast
import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "turn_capture"
ALLOWED_FIRST_PARTY = ("src.turn_capture", "src.telemetry.timestamps", "src.sensitive_values")
FORBIDDEN = {
    "scripts",
    "src.common",
    "src.data",
    "src.lambdas",
    "src.telemetry.append",
    "src.telemetry.gate",
    "src.telemetry.identity",
}


def _imports(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(("." * node.level) + (node.module or ""))
    return found


def test_runtime_imports_are_stdlib_or_timestamps() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 11
    for path in files:
        for name in _imports(path):
            assert not name.startswith("."), (path.name, name)
            root = name.split(".")[0]
            if root in sys.stdlib_module_names:
                continue
            assert any(name == ok or name.startswith(ok + ".") for ok in ALLOWED_FIRST_PARTY), (path.name, name)
            assert "duckdb" not in name


def test_importlinter_contract_declared() -> None:
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = parser["importlinter:contract:src-turn-capture-is-plane-neutral"]
    assert section["type"] == "forbidden"
    assert section["source_modules"].split() == ["src.turn_capture"]
    assert FORBIDDEN <= set(section["forbidden_modules"].split())


def test_init_defines_nothing() -> None:
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    assert len(tree.body) == 1
    node = tree.body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
