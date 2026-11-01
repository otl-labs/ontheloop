"""The redactors that keep their own pattern copies are pinned to be no stronger than the read_masked profile.

The live pattern sets are DERIVED from each copy's code (never read from named attributes) and must equal the
COPY_PATTERNS table in both directions, so an edited or added pattern fails until its strength is re-reviewed here.
Declared residual: a language: system grep hook, a pattern imported from another module, or a call reached through an
assigned alias or getattr is out of reach.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import re
from pathlib import Path

import pytest
import yaml

from scripts.checks.misc import sensitive_value_corpus as corpus
from src.sensitive_values import mask

ROOT = Path(__file__).resolve().parents[2]
RE_FUNCS = {"compile", "sub", "subn", "search", "match", "fullmatch", "findall", "finditer", "split"}
FLAG_ARG_INDEX = {
    "compile": 1,
    "sub": 4,
    "subn": 4,
    "split": 3,
    "search": 2,
    "match": 2,
    "fullmatch": 2,
    "findall": 2,
    "finditer": 2,
}
HOOK_ID = "block-sensitive-identifiers"

MODULES = {
    "scripts/ci/redaction_check.py": "scripts.ci.redaction_check",
    "scripts/terraform_apply_guard.py": "scripts.terraform_apply_guard",
    "src/common/ducklake_handler_errors.py": "src.common.ducklake_handler_errors",
    "scripts/ci/security_detector_probe.py": "scripts.ci.security_detector_probe",
}

# (copy, exact pattern source, flag names joined by "|")
COPY_PATTERNS = {
    ("scripts/ci/redaction_check.py", r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])", ""),
    ("scripts/ci/redaction_check.py", r"(?<![0-9A-Za-z])A(?:ROA|IDA)[A-Z0-9]{16,17}(?![0-9A-Za-z])", ""),
    ("scripts/terraform_apply_guard.py", r"arn:aws:[a-zA-Z0-9_\-]*:[a-zA-Z0-9_\-]*:[0-9]*:[^\s\"'\\]*", ""),
    ("scripts/terraform_apply_guard.py", r"(?<!\d)\d{12}(?!\d)", ""),
    ("src/common/ducklake_handler_errors.py", r"password\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s&;,]+)", "IGNORECASE"),
    ("src/common/ducklake_handler_errors.py", r"X-Amz-[A-Za-z0-9-]+\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s&;,]+)", "IGNORECASE"),
    ("src/common/ducklake_handler_errors.py", r"arn:[^\s,'\"]+", "IGNORECASE"),
    ("src/common/ducklake_handler_errors.py", r"\d{12,}", ""),
    ("scripts/ci/security_detector_probe.py", r"arn:\S*", ""),
    ("scripts/ci/security_detector_probe.py", r"\d{12}", ""),
}

# The hook branches are pinned by digest so no account-shaped literal is restated here.
HOOK_BRANCH_DIGESTS = {"9ab934b26f43e17e", "43c634a944f2aa40", "207a82eb8419c6e5"}  # pragma: allowlist secret

WHOLE_ARN = "whole-ARN pattern removes the resource path read_masked keeps (public or AWS-generated names)"
LOOSE_DIGITS = "unbounded or digit-only-bounded 12-digit run hits 13-plus-digit runs and hex digests read_masked spares"
DIVERGENCES = {
    ("scripts/terraform_apply_guard.py", r"arn:aws:[a-zA-Z0-9_\-]*:[a-zA-Z0-9_\-]*:[0-9]*:[^\s\"'\\]*", WHOLE_ARN),
    ("scripts/terraform_apply_guard.py", r"(?<!\d)\d{12}(?!\d)", LOOSE_DIGITS),
    ("src/common/ducklake_handler_errors.py", r"arn:[^\s,'\"]+", WHOLE_ARN),
    ("src/common/ducklake_handler_errors.py", r"\d{12,}", LOOSE_DIGITS),
    ("scripts/ci/security_detector_probe.py", r"arn:\S*", WHOLE_ARN),
    ("scripts/ci/security_detector_probe.py", r"\d{12}", LOOSE_DIGITS),
}


class _Aliases:
    def __init__(self, tree: ast.AST) -> None:
        self.modules: set[str] = set()
        self.funcs: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.modules |= {a.asname or a.name for a in node.names if a.name == "re"}
            elif isinstance(node, ast.ImportFrom) and node.module == "re":
                self.funcs |= {a.asname or a.name: a.name for a in node.names}

    def resolve(self, call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in self.modules:
            return func.attr if func.attr in RE_FUNCS else None
        if isinstance(func, ast.Name) and self.funcs.get(func.id) in RE_FUNCS:
            return self.funcs[func.id]
        return None


def _flag_names(node: ast.expr | None, aliases: _Aliases, where: str) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return "|".join(
            sorted(_flag_names(node.left, aliases, where).split("|") + _flag_names(node.right, aliases, where).split("|"))
        )
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases.modules:
        return node.attr
    if isinstance(node, ast.Name) and aliases.funcs.get(node.id, "").isupper():
        return aliases.funcs[node.id]
    pytest.fail(f"{where}: flags are not a re flag attribute or a | of them")


def derive(rel: str) -> list[tuple[str, str, str]]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    aliases = _Aliases(tree)
    found: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = aliases.resolve(node)
        if name is None:
            continue
        where = f"{rel}:{node.lineno} re.{name}"
        if not node.args or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            pytest.fail(f"{where}: pattern is not a string literal")
        index = FLAG_ARG_INDEX[name]
        flag_node = node.args[index] if len(node.args) > index else None
        for kw in node.keywords:
            if kw.arg == "flags":
                flag_node = kw.value
        found.append((rel, node.args[0].value, _flag_names(flag_node, aliases, where)))
    return found


def _top_level_branches(entry: str) -> list[str]:
    assert entry.startswith("(") and entry.endswith(")")
    inner, depth, in_class, cur, i = entry[1:-1], 0, False, "", 0
    branches: list[str] = []
    while i < len(inner):
        ch = inner[i]
        if ch == "\\":
            cur += inner[i : i + 2]
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            branches.append(cur)
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    return branches + [cur]


def _hooks() -> list[dict]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return [h for repo in config["repos"] for h in repo["hooks"] if h.get("language") == "pygrep"]


def _compiled(rel: str, pattern: str, flags: str) -> re.Pattern[str]:
    value = 0
    for name in filter(None, flags.split("|")):
        value |= getattr(re, name)
    return re.compile(pattern, value)


def _planted_removed(regex: re.Pattern[str]) -> list[str]:
    return [
        case.case_id
        for case in corpus.positives()
        if regex.search(case.text) and case.planted_value not in mask(case.text, "read_masked").text
    ]


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_derived_patterns_equal_pinned_rows(rel: str) -> None:
    derived = derive(rel)
    pinned = sorted(row for row in COPY_PATTERNS if row[0] == rel)
    assert sorted(derived) == pinned
    assert len(derived) == len(set(derived))


def test_no_unpinned_module_rows() -> None:
    assert {row[0] for row in COPY_PATTERNS} == set(MODULES)


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_copy_modules_import_by_dotted_name(rel: str) -> None:
    importlib.import_module(MODULES[rel])


def test_hook_branches_args_and_pygrep_set_pinned() -> None:
    hooks = _hooks()
    assert {h["id"] for h in hooks} == {HOOK_ID}
    hook = hooks[0]
    assert not hook.get("args")
    branches = _top_level_branches(hook["entry"])
    assert {hashlib.sha256(b.encode("utf-8")).hexdigest()[:16] for b in branches} == HOOK_BRANCH_DIGESTS
    assert len(branches) == len(HOOK_BRANCH_DIGESTS)


def test_every_row_hits_a_masked_positive() -> None:
    for rel, pattern, flags in sorted(COPY_PATTERNS):
        assert _planted_removed(_compiled(rel, pattern, flags)), (rel, pattern)
    for branch in _top_level_branches(_hooks()[0]["entry"]):
        assert _planted_removed(re.compile(branch)), hashlib.sha256(branch.encode("utf-8")).hexdigest()[:16]


def test_divergences_are_exact() -> None:
    pinned = {(rel, pattern) for rel, pattern, _ in COPY_PATTERNS}
    listed = {(rel, pattern) for rel, pattern, _ in DIVERGENCES}
    assert listed <= pinned
    texts = [case.text for case in corpus.negatives()]
    hitting = set()
    for rel, pattern, flags in COPY_PATTERNS:
        regex = _compiled(rel, pattern, flags)
        if any(regex.search(text) for text in texts):
            hitting.add((rel, pattern))
    assert hitting == listed
    for branch in _top_level_branches(_hooks()[0]["entry"]):
        assert not any(re.search(branch, text) for text in texts)


def test_account_class_matches_redaction_check_and_unique_id_is_a_subset() -> None:
    from scripts.ci import redaction_check
    from src.sensitive_values import CLASSES

    assert CLASSES["ACCOUNT_ID"].pattern == redaction_check.ACCOUNT_ID_SHAPE.pattern
    for case in corpus.positives():
        if redaction_check.UNIQUE_ID_SHAPE.search(case.text):
            assert case.planted_value not in mask(case.text, "read_masked").text, case.case_id
