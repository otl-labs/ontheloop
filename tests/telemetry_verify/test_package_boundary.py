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
        found = _imported_modules(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        assert _violations(found) == [], path.name


def test_the_scan_flags_each_forbidden_and_unlisted_import() -> None:
    sample = ast.parse(
        "import duckdb\nfrom src.common.ducklake_event_read_sql import SESSIONS\nimport src.turn_capture.streams\n"
        "from src.turn_capture import observations\nfrom tests.fixtures import x\nimport requests\n"
    )
    flagged = _violations(_imported_modules(sample))
    for expected in (
        "duckdb",
        "src.common.ducklake_event_read_sql",
        "src.turn_capture.streams",
        "src.turn_capture.observations",
        "tests.fixtures",
        "requests",
    ):
        assert expected in flagged
