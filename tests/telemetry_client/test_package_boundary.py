"""Producer-transport boundary: AST scan of src/telemetry_client, a docstring-only marker, importlinter contract."""

from __future__ import annotations

import ast
import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "telemetry_client"
ALLOWED_FIRST_PARTY = ("src.telemetry_client", "src.telemetry.blobs", "src.telemetry.request", "src.telemetry.timestamps")
FORBIDDEN = {
    "scripts",
    "src.common",
    "src.data",
    "src.lambdas",
    "src.turn_capture",
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


def test_imports_are_stdlib_or_the_shared_protocol() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "blob_delivery.py"}
    for path in files:
        for name in _imports(path):
            assert not name.startswith("."), (path.name, name)
            if name.split(".")[0] in sys.stdlib_module_names:
                continue
            assert any(name == ok or name.startswith(ok + ".") for ok in ALLOWED_FIRST_PARTY), (path.name, name)
            assert not any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN), (path.name, name)
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)
    assert "producer-side transport plane" in ast.get_docstring(tree)  # type: ignore[operator]


def test_importlinter_contract_declared() -> None:
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = parser["importlinter:contract:src-telemetry-client-is-producer-transport"]
    assert section["type"] == "forbidden"
    assert section["source_modules"].split() == ["src.telemetry_client"]
    assert FORBIDDEN <= set(section["forbidden_modules"].split())
    for existing in ("src-turn-capture-is-plane-neutral", "scripts-never-import-telemetry-append"):
        assert f"importlinter:contract:{existing}" in parser
