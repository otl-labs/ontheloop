"""Recs read-topology guard (T2.19 c10, audit F-004; Decision 84 closed boundary).

Detective defence in depth for the ops-store plane: IAM stays the boundary (Decision 143). The
guard keeps the recs reader path DuckLake-only, so a regression that re-opens a non-DuckLake read
path fails CI. It is table-agnostic by construction -- an engine import names no table -- so it
covers the whole ops-store plane. It is the read-side complement of validate_warehouse_write_sources.

R4b follows module-scope re-exports of allowlisted modules only; a re-export built inside a
function body, or relayed through a non-allowlisted module, is outside its reach.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from scripts.checks import _common, registry

SELF_REL = "scripts/checks/ops_governance/validate_recs_read_topology.py"
READER_CLIENT_REL = "src/common/ducklake_reader_client.py"
SCAN_ROOTS = ("scripts", "src")

DECLARED_READERS: tuple[tuple[str, str], ...] = (("DuckLakeReader", "read-engine.yaml#reader_client; Decision 84 I-1"),)

ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("src/common/ducklake_runtime.py", "the DuckLake connection primitive"),
    ("src/common/ducklake_spike.py", "privileged-host operational tool"),
    ("src/common/ducklake_connect_probe.py", "privileged-host operational tool"),
    ("src/common/ducklake_maintenance.py", "privileged-host operational tool"),
    ("src/lambdas/ducklake_reader/**", "the reader boundary itself (Decision 81 cl.1/cl.7)"),
    ("src/lambdas/ducklake_writer/**", "the writer boundary itself (Decision 81 cl.1/cl.7)"),
    ("src/lambdas/ducklake_maintenance/**", "the maintenance boundary itself (Decision 81 cl.1/cl.7)"),
    ("src/lambdas/ducklake_maintenance_smoke/**", "the maintenance smoke boundary (Decision 81 cl.1/cl.7)"),
    (
        "src/lambdas/ducklake_telemetry_reader/**",
        "the telemetry reader boundary, own scoped catalog login (Decisions 213, 236)",
    ),
    ("src/lambdas/ducklake_telemetry_writer/**", "the telemetry writer boundary, own scoped catalog login (Decision 213)"),
    ("src/lambdas/ducklake_catalog_dr/**", "the catalog DR boundary (Decision 81 cl.1/cl.7)"),
    ("src/common/ducklake_event_reads.py", "connection layer of the reader-boundary handlers only (Decisions 199 cl.1, 236)"),
    ("scripts/ducklake_smoke/**", "break-glass smoke harness, DUCKLAKE_ALLOW_DIRECT_GATE (Decision 81 cl.7)"),
    ("scripts/ducklake_neon_smoke_test.py", "break-glass smoke harness re-export surface (Decision 81 cl.7)"),
    ("src/telemetry/**", "separate plane-neutral telemetry kernel (Decision 184 cl.2)"),
)

RETIRED_TOKENS = ("OPS_STORAGE_BACKEND", "DuckDBIcebergReader")
RETIRED_MODULES = ("pyiceberg", "iceberg_reader")
ENGINE_IMPORTS = ("duckdb", "psycopg2")
RUNTIME_MODULE = "src.common.ducklake_runtime"
ENTRY_POINTS = frozenset({"open_connection", "get_warm_connection", "reset_warm_connection", "fetch_dsn", "libpq_conninfo"})
SPIKE_MODULE = "src.common.ducklake_spike"
SPIKE_ENTRY = "_require_duckdb"
_SEEDS: dict[str, frozenset[str]] = {RUNTIME_MODULE: ENTRY_POINTS, SPIKE_MODULE: frozenset({SPIKE_ENTRY})}
_READER_METHODS = frozenset({"current_state", "latest_snapshot"})


@dataclass(frozen=True)
class Finding:
    leg: str
    path: str  # repo-relative POSIX path
    line: int
    message: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[Finding, ...]
    files_parsed: int


def _module_name(rel: str) -> str:
    dotted = rel[: -len(".py")].replace("/", ".")
    return dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted


def _allowed(rel: str, allowlist: tuple[tuple[str, str], ...]) -> bool:
    return any(fnmatch(rel, glob) for glob, _ in allowlist)


def _walk(root: Path) -> list[str]:
    rels: set[str] = set()
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if base.exists():
            rels.update(p.relative_to(root).as_posix() for p in base.glob("**/*.py"))
    rels.discard(SELF_REL)
    return sorted(rels)


def _parse_all(root: Path) -> tuple[dict[str, ast.Module], list[Finding]]:
    trees: dict[str, ast.Module] = {}
    findings: list[Finding] = []
    for rel in _walk(root):
        try:
            trees[rel] = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(Finding("IO", rel, 0, f"unreadable file ({type(exc).__name__})"))
        except SyntaxError as exc:
            findings.append(Finding("IO", rel, exc.lineno or 0, "unparseable file (SyntaxError)"))
    return trees, findings


def _segments(dotted: str | None) -> set[str]:
    return set(dotted.split(".")) if dotted else set()


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _type_checking_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        ):
            for stmt in node.body:
                ids.update(id(sub) for sub in ast.walk(stmt))
    return ids


def _bindings(nodes: list[ast.stmt] | ast.Module) -> tuple[dict[str, str], set[str]]:
    """Map each local name bound to a module (asname, else the bound name) to that module's dotted
    name, plus the dotted chains a bare `import a.b.c` makes reachable as an Attribute chain."""
    walk = ast.walk(nodes) if isinstance(nodes, ast.Module) else iter(nodes)
    names: dict[str, str] = {}
    chains: set[str] = set()
    for node in walk:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    names[alias.name.split(".")[0]] = alias.name.split(".")[0]
                    chains.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if alias.name != "*":
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names, chains


def _attribute_module(node: ast.Attribute, names: dict[str, str], chains: set[str]) -> str | None:
    value = node.value
    if isinstance(value, ast.Name):
        return names.get(value.id)
    if isinstance(value, ast.Attribute):
        dotted = ast.unparse(value)
        return dotted if dotted in chains else None
    return None


def _derive(trees: dict[str, ast.Module], allowed: list[str]) -> dict[str, frozenset[str]]:
    exports: dict[str, set[str]] = {module: set(names) for module, names in _SEEDS.items()}
    changed = True
    while changed:
        changed = False
        for rel in allowed:
            tree = trees[rel]
            module = _module_name(rel)
            names, _ = _bindings(list(tree.body))
            added: set[str] = set()
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module in exports and stmt.level == 0:
                    source = exports[stmt.module]
                    for alias in stmt.names:
                        if alias.name == "*":
                            added.update(source)
                        elif alias.name in source:
                            added.add(alias.asname or alias.name)
                elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Attribute):
                    origin = _attribute_module(stmt.value, names, set())
                    if origin in exports and stmt.value.attr in exports[origin]:
                        added.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
            target = exports.setdefault(module, set())
            if not added <= target:
                target.update(added)
                changed = True
    return {module: frozenset(found) for module, found in exports.items() if module not in _SEEDS and found}


def derive_exports(root: Path, allowlist: tuple[tuple[str, str], ...] | None = None) -> dict[str, frozenset[str]]:
    """The R4b re-export map: allowlisted module -> names it binds at module scope to an entry
    point. The two seed modules are excluded (R4 owns them)."""
    rows = ALLOWLIST if allowlist is None else allowlist
    trees, _ = _parse_all(root)
    return _derive(trees, [rel for rel in trees if _allowed(rel, rows)])


def _check_rows(rows: tuple[tuple[str, str], ...], label: str) -> list[Finding]:
    return [
        Finding("R0", SELF_REL, 0, f"{label} row {key!r} carries no reason; every row must say why it exists")
        for key, reason in rows
        if not reason.strip()
    ]


def _check_factory(trees: dict[str, ast.Module], declared: set[str], root: Path) -> list[Finding]:
    tree = trees.get(READER_CLIENT_REL)
    if tree is None:
        if (root / READER_CLIENT_REL).exists():
            return []  # unparseable: the IO finding already names it
        return [Finding("R1", READER_CLIENT_REL, 0, "reader client is missing; the recs read path cannot be verified")]
    func = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_reader"), None)
    if func is None:
        return [Finding("R1", READER_CLIENT_REL, 0, "module-level make_reader is missing")]
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    if not returns:
        return [Finding("R1", READER_CLIENT_REL, func.lineno, "make_reader has no return")]
    findings = []
    for ret in returns:
        value = ret.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in declared):
            findings.append(Finding("R1", READER_CLIENT_REL, ret.lineno, "make_reader returns an undeclared reader"))
    return findings


def _is_protocol(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
        if name.endswith("Protocol"):
            return True
    return False


def _check_readers(rel: str, tree: ast.Module, declared: set[str]) -> list[Finding]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or _is_protocol(node) or node.name in declared:
            continue
        methods = {b.name for b in node.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if _READER_METHODS <= methods:
            findings.append(Finding("R2", rel, node.lineno, f"class {node.name} implements Reader but is not declared"))
    return findings


def _check_retired(rel: str, tree: ast.Module) -> list[Finding]:
    docstrings = _docstring_ids(tree)
    retired_modules = set(RETIRED_MODULES)
    findings = []
    for node in ast.walk(tree):
        hit = ""
        if isinstance(node, ast.Name) and node.id in RETIRED_TOKENS:
            hit = node.id
        elif isinstance(node, ast.Attribute) and node.attr in RETIRED_TOKENS:
            hit = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if "ops_storage_backend" in node.value.lower():
                hit = "OPS_STORAGE_BACKEND string"
        elif isinstance(node, ast.Import):
            if any(_segments(a.name) & retired_modules for a in node.names):
                hit = "retired engine import"
        elif isinstance(node, ast.ImportFrom):
            alias_names = {a.name for a in node.names}
            if _segments(node.module) & retired_modules or any(_segments(n) & retired_modules for n in alias_names):
                hit = "retired engine import"
            elif alias_names & set(RETIRED_TOKENS):
                hit = "retired token import"
        if hit:
            findings.append(Finding("R3", rel, getattr(node, "lineno", 0), f"retired legacy-engine token ({hit})"))
    return findings


def _check_engine(rel: str, tree: ast.Module, derived: dict[str, frozenset[str]]) -> list[Finding]:
    guarded = _type_checking_ids(tree)
    names, chains = _bindings(tree)
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and id(node) not in guarded:
            for alias in node.names:
                if alias.name.split(".")[0] in ENGINE_IMPORTS:
                    findings.append(Finding("R4", rel, node.lineno, f"runtime engine import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in ENGINE_IMPORTS and id(node) not in guarded:
                findings.append(Finding("R4", rel, node.lineno, f"runtime engine import {node.module}"))
            for leg, table in (("R4", _SEEDS), ("R4b", derived)):
                exported = table.get(node.module)
                if exported is not None:
                    for alias in node.names:
                        if alias.name == "*" or alias.name in exported:
                            findings.append(Finding(leg, rel, node.lineno, f"imports entry point {node.module}.{alias.name}"))
        elif isinstance(node, ast.Attribute):
            module = _attribute_module(node, names, chains)
            for leg, table in (("R4", _SEEDS), ("R4b", derived)):
                if module is not None and node.attr in table.get(module, frozenset()):
                    findings.append(Finding(leg, rel, node.lineno, f"uses entry point {module}.{node.attr}"))
    return findings


def scan(
    root: Path,
    *,
    allowlist: tuple[tuple[str, str], ...] | None = None,
    declared_readers: tuple[tuple[str, str], ...] | None = None,
) -> ScanResult:
    """Run legs R0-R4b over every non-test .py under scripts/ and src/. None reads the module
    constant at call time."""
    rows = ALLOWLIST if allowlist is None else allowlist
    readers = DECLARED_READERS if declared_readers is None else declared_readers
    declared = {name for name, _ in readers}
    trees, findings = _parse_all(root)
    findings += _check_rows(readers, "DECLARED_READERS") + _check_rows(rows, "ALLOWLIST")
    findings += _check_factory(trees, declared, root)
    derived = _derive(trees, [rel for rel in trees if _allowed(rel, rows)])
    for rel, tree in trees.items():
        findings += _check_readers(rel, tree, declared)
        findings += _check_retired(rel, tree)
        if not _allowed(rel, rows):
            findings += _check_engine(rel, tree, derived)
    return ScanResult(tuple(findings), len(trees))


@registry.register("validate_recs_read_topology", owner="platform")
def validate_recs_read_topology(failed: list[str]) -> None:
    """Fail if the recs reader path stops being DuckLake-only (T2.19 c10; Decision 84 I-1/I-3)."""
    print("\n=== Recs read topology ===")
    result = scan(_common.ROOT)
    registry.examined(result.files_parsed, unit="files")
    for finding in result.findings:
        failed.append(f"{finding.leg} {finding.path}:{finding.line}: {finding.message} (Decision 84 closed boundary)")
    if result.findings:
        print(f"Recs read topology: {len(result.findings)} finding(s).")
    else:
        print(f"  PASS: recs reader path is DuckLake-only ({result.files_parsed} files).")
