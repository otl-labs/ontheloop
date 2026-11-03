"""src/sensitive_values is a stdlib-only leaf."""

from __future__ import annotations

import ast
import configparser
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "sensitive_values"
FIRST_PARTY = {"scripts", "src.common", "src.data", "src.lambdas", "src.schemas", "src.telemetry", "src.turn_capture"}


def _imports(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(("." * node.level) + (node.module or ""))
    return found


def test_imports_are_stdlib_only() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 4
    for path in files:
        for name in _imports(path):
            assert not name.startswith("."), (path.name, name)
            if name.split(".")[0] in sys.stdlib_module_names:
                continue
            assert name == "src.sensitive_values" or name.startswith("src.sensitive_values."), (path.name, name)


def test_importlinter_contract_declared() -> None:
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = parser["importlinter:contract:src-sensitive-values-is-a-leaf"]
    assert section["type"] == "forbidden"
    assert section["source_modules"].split() == ["src.sensitive_values"]
    assert FIRST_PARTY <= set(section["forbidden_modules"].split())


def test_file_router_routes_package() -> None:
    routes = yaml.safe_load((ROOT / "docs" / "contracts" / "file-router.yaml").read_text(encoding="utf-8"))["routes"]
    topic = next(r for r in routes if r["topic"] == "sensitive-values")
    assert "src/sensitive_values/" in topic["targets"]
