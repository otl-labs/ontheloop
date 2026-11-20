"""AST boundary for scripts/telemetry_verify: the oracle's independence is its own code, not its import closure.

Direct runtime imports only (the allowed data modules themselves import the reader's SQL module), so this is an AST scan and
never an .importlinter forbidden contract, which checks the transitive closure.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[2] / "scripts" / "telemetry_verify"
_ALLOWED_MODULES = {
    "yaml",
    "src.turn_capture.transcript",
    "src.common.ducklake_event_reads",
    "src.common.ducklake_event_read_verbs",
    "src.common.ducklake_reader_client",
    "src.telemetry.identity",
    "scripts.rec_episode",
    "scripts.telemetry_capture.read_route",
}
_FORBIDDEN = (
    "duckdb",
    "src.common.ducklake_event_read_sql",
    "src.turn_capture.streams",
    "src.turn_capture.observations",
    "tests",
)


_VERBS = "src.common.ducklake_event_read_verbs"


def _is_sql_text_name(name: str) -> bool:
    return name.endswith("_SQL") or name == "FRICTION_CTES"


def _sql_attribute_references(tree: ast.Module) -> list[str]:
    return sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and _is_sql_text_name(n.attr)})


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _violations(modules: set[str]) -> list[str]:
    bad = []
    for name in sorted(modules):
        root = name.split(".", 1)[0]
        if any(name == f or name.startswith(f + ".") for f in _FORBIDDEN):
            bad.append(name)
        elif name.startswith(_VERBS + ".") and _is_sql_text_name(name.rsplit(".", 1)[1]):
            bad.append(name)
        elif root in sys.stdlib_module_names or name.startswith("scripts.telemetry_verify"):
            continue
        elif not any(name == ok or ok.startswith(name + ".") or name.startswith(ok + ".") for ok in _ALLOWED_MODULES):
            bad.append(name)
    return bad


def test_oracle_has_no_direct_reader_sql_duckdb_streams_or_tests_import() -> None:
    files = sorted(_PACKAGE.glob("*.py"))
    assert {f.name for f in files} >= {
        "dedupe.py",
        "derive.py",
        "ledger.py",
        "shape.py",
        "joins.py",
        "orphans.py",
        "rows.py",
        "alarms.py",
        "compare.py",
        "stored_ledger.py",
        "__init__.py",
    }
    for path in files:
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert _violations(_imported_modules(parsed)) == [], path.name
        assert _sql_attribute_references(parsed) == [], path.name


def test_the_scan_flags_each_forbidden_and_unlisted_import() -> None:
    sample = ast.parse(
        "import duckdb\nfrom src.common.ducklake_event_read_sql import SESSIONS\nimport src.turn_capture.streams\n"
        "from src.turn_capture import observations\nfrom tests.fixtures import x\nimport requests\n"
        "from src.common.ducklake_event_read_verbs import FRICTION_CTES, FRICTION_SIGNATURE_SQL, FRICTION_RULES\n"
    )
    flagged = _violations(_imported_modules(sample))
    for expected in (
        "duckdb",
        "src.common.ducklake_event_read_sql",
        "src.turn_capture.streams",
        "src.turn_capture.observations",
        "tests.fixtures",
        "requests",
        f"{_VERBS}.FRICTION_CTES",
        f"{_VERBS}.FRICTION_SIGNATURE_SQL",
    ):
        assert expected in flagged
    assert f"{_VERBS}.FRICTION_RULES" not in flagged
    assert _sql_attribute_references(ast.parse("x = verbs.FRICTION_CTES + verbs.FRICTION_RULES")) == ["FRICTION_CTES"]
