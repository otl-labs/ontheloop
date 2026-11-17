"""Persona code stays GitHub-agnostic (T4.2 c13, first half).

Persona code is every .py file the persona image pin's build.copy entries hold. It runs unchanged on any host, so it
may not shell out to gh, import a GitHub client, name a GitHub token variable, or depend on a module the image does
not copy. The image's lock must not pin a GitHub client either.

Decision 170: calls registry.examined() on every reachable exit path.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.checks import _common, registry
from src.persona_hosts.image_pin import ImagePin, load_pin

_PREFIX = "persona-github-agnosticism"
_ENTRYPOINT = "scripts/run_persona.py"
_LOCK = "docker/persona/requirements-persona.txt"
_CLIENTS = {"github", "ghapi", "githubkit", "github3", "octokit"}
_LOCK_CLIENTS = {"pygithub", "ghapi", "githubkit", "github3.py"}
_GH_STRING = re.compile(r"(^|[\s;&|`(])gh\s")
_TOKEN = re.compile(r"\b(GITHUB_TOKEN|GH_TOKEN)\b")
_FIRST_PARTY = ("scripts", "src")


def _persona_files(root: Path, pin: ImagePin) -> list[Path]:
    files: list[Path] = []
    for entry in pin.copy:
        path = root / entry
        if entry.endswith("/"):
            files += sorted(path.rglob("*.py"))
        elif path.suffix == ".py":
            files.append(path)
    return [f for f in files if "__pycache__" not in f.parts]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    return {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}


def _import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
        return [node.module]
    if isinstance(node, ast.Call):
        fn = node.func
        called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if called in {"import_module", "__import__"} and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return [arg.value]
    return []


def _node_findings(node: ast.AST, rel: str, skip: set[int]) -> list[str]:
    out = [f"{rel}:{node.lineno}: imports GitHub client {n}" for n in _import_names(node) if n.split(".")[0] in _CLIENTS]  # type: ignore[attr-defined]
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value == "gh":
            out.append(f"{rel}:{node.lineno}: gh command (argv starts with 'gh')")
    elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
        if _GH_STRING.search(node.value):
            out.append(f"{rel}:{node.lineno}: gh command string")
        if _TOKEN.search(node.value):
            out.append(f"{rel}:{node.lineno}: names a GitHub token variable")
    return out


def _violations(path: Path, rel: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [f"{rel}: unreadable ({exc})"]
    skip = _docstring_nodes(tree)
    return [line for node in ast.walk(tree) for line in _node_findings(node, rel, skip)]


def _module_file(root: Path, module: str) -> Path | None:
    base = root / module.replace(".", "/")
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    init = base / "__init__.py"
    return init if init.is_file() else None


def _closure(root: Path) -> set[str]:
    seen: set[str] = set()

    def walk(path: Path) -> None:
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            for mod in mods:
                if mod.split(".")[0] not in _FIRST_PARTY:
                    continue
                parts = mod.split(".")
                for i in range(1, len(parts) + 1):
                    target = _module_file(root, ".".join(parts[:i]))
                    if target is not None:
                        walk(target)

    entry = root / _ENTRYPOINT
    if entry.is_file():
        walk(entry)
    return seen


def _lock_pins(root: Path) -> set[str]:
    try:
        text = (root / _LOCK).read_text(encoding="utf-8")
    except OSError:
        return set()
    pins = (m.group(1).lower().replace("_", "-") for m in re.finditer(r"^([A-Za-z0-9._-]+)==", text, re.M))
    return {p for p in pins if p in _LOCK_CLIENTS}


@registry.register("validate_persona_github_agnosticism", owner="platform")
def validate_persona_github_agnosticism(failed: list[str]) -> None:
    """Persona code carries no GitHub coupling and its import closure is fully copied. See module docstring."""
    print("\n=== persona GitHub-agnosticism guard ===")
    root = _common.ROOT
    try:
        pin = load_pin(root)
    except ValueError as exc:
        print(f"  FAIL: {exc}")
        failed.append(f"{_PREFIX}: persona image pin unreadable: {exc}")
        registry.examined(0, unit="persona_files")
        return
    files = _persona_files(root, pin)
    found: list[str] = []
    for path in files:
        found += _violations(path, path.relative_to(root).as_posix())
    copied = {p.relative_to(root).as_posix() for p in files}
    found += [f"{m}: in the import closure of {_ENTRYPOINT} but not in build.copy" for m in sorted(_closure(root) - copied)]
    found += [f"{_LOCK}: pins GitHub client {name}" for name in sorted(_lock_pins(root))]
    for line in found:
        print(f"  FAIL: {line}")
        failed.append(f"{_PREFIX}: {line}")
    if not found:
        print(f"  PASS: {len(files)} persona files carry no GitHub coupling")
    registry.examined(len(files), unit="persona_files")
