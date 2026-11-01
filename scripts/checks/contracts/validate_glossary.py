"""Glossary check (Decision 240): shape of docs/contracts/glossary.yaml, its guidance projection, and the added-line lint.

Three legs. SHAPE: the contract's keys, the fourteen terms, the relative words, the read_as rows and the ambient pointer in
docs/CLAUDE.md. GUIDANCE: get_rec_write_guidance() carries the contract's terms and say_instead values. LINT: a governed
line added on this branch (never text already on the base) fails if it uses "platform" or "product" unqualified; the token
rule and span exemptions live in _glossary_words, shared with the rec portal.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.contracts import _glossary_words
from scripts.executor.rec_write_guidance import get_rec_write_guidance

_CONTRACT = "glossary.yaml"
_LABEL = "Glossary"
_TOP_KEYS = frozenset({"contract", "amendment_log", "terms", "relative_words", "read_as", "anti_patterns"})
_TERM_KEYS = (
    "on_the_loop",
    "on_the_loop_labs",
    "core",
    "paid_service",
    "this_repository",
    "release",
    "deployment",
    "operator_deployment",
    "operator",
    "adopter",
    "customer",
    "user",
    "project",
    "adopter_product",
)
_TERM_FIELDS = ("written", "reserved_meaning", "do_not_use_for")
_EXTRA_PATHSPECS = (".claude/agents/**/*.md", ".github/prompts/**/*.md", "docs/audit-prompts/*.md")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _shape(data: dict[str, Any], root: Path) -> list[str]:
    out: list[str] = []
    if set(data) != _TOP_KEYS:
        out.append(f"top-level keys {sorted(data)} differ from {sorted(_TOP_KEYS)}")
    contract = data.get("contract") or {}
    if contract.get("subject") != "glossary":
        out.append("contract.subject is not glossary")
    if (contract.get("evaluator") or {}).get("check") != "validate_glossary":
        out.append("contract.evaluator.check is not validate_glossary")
    out += _ratified(str(contract.get("ratified_via", "")), root)
    out += _terms(data.get("terms") or {})
    out += _relative(data)
    out += _read_as(data.get("read_as") or {})
    pointer = root / "docs" / "CLAUDE.md"
    if not pointer.is_file() or "docs/contracts/glossary.yaml" not in pointer.read_text(encoding="utf-8"):
        out.append("docs/CLAUDE.md does not name docs/contracts/glossary.yaml (the ambient pointer)")
    return out


def _ratified(via: str, root: Path) -> list[str]:
    found = re.search(r"Decision (\d+)", via)
    header = rf"^## Decision {found.group(1)}:" if found else ""
    for name in ("DECISIONS.md", "DECISIONS_ARCHIVE.md"):
        path = root / "docs" / name
        if header and path.is_file() and re.search(header, path.read_text(encoding="utf-8"), re.MULTILINE):
            return []
    return [f"contract.ratified_via {via!r} names no existing Decision header"]


def _terms(terms: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if set(terms) != set(_TERM_KEYS):
        out.append(f"terms keys differ from the fourteen reserved names: {sorted(set(terms) ^ set(_TERM_KEYS))}")
    seen: set[str] = set()
    for key, term in terms.items():
        term = term if isinstance(term, dict) else {}
        out += [f"term {key}: {field} is missing or empty" for field in _TERM_FIELDS if not _text(term.get(field))]
        written = str(term.get("written", "")).lower()
        if written in seen:
            out.append(f"term {key}: written {written!r} duplicates another term ignoring case")
        seen.add(written)
    return out


def _relative(data: dict[str, Any]) -> list[str]:
    block = data.get("relative_words") or {}
    out = [f"relative_words.{k} is missing or empty" for k in ("rule", "identifiers") if not _text(block.get(k))]
    out += [
        f"relative_words.{word}.say_instead is missing or empty"
        for word in ("platform", "product")
        if not _text((block.get(word) or {}).get("say_instead"))
    ]
    known = _glossary_words.identifiers(data)
    out += [
        f"relative_words.identifiers lacks {need}" for need in ("agent-platform-*", "platform-security-*") if need not in known
    ]
    return out


def _read_as(block: dict[str, Any]) -> list[str]:
    out = [] if _text(block.get("rule")) else ["read_as.rule is missing or empty"]
    phrases: set[str] = set()
    for i, row in enumerate(block.get("rows") or []):
        row = row if isinstance(row, dict) else {}
        out += [f"read_as row {i}: {k} is missing or empty" for k in ("phrase", "where", "reads_as") if not _text(row.get(k))]
        phrase = str(row.get("phrase", ""))
        if phrase in phrases:
            out.append(f"read_as row {i}: phrase {phrase!r} is duplicated")
        phrases.add(phrase)
    if not block.get("rows"):
        out.append("read_as.rows is missing or empty")
    return out


def _guidance(data: dict[str, Any], path: Path) -> list[str]:
    entry = get_rec_write_guidance(glossary_path=path).get("glossary")
    if entry is None:
        return ["get_rec_write_guidance() carries no glossary entry"]
    out: list[str] = []
    if set(entry.get("terms", {})) != set(data["terms"]):
        out.append("guidance glossary terms differ from the contract's")
    for word in ("platform", "product"):
        if entry.get("relative_words", {}).get(word) != data["relative_words"][word]["say_instead"]:
            out.append(f"guidance glossary say_instead for {word} differs from the contract's")
    return out


def _git(root: Path, *args: str) -> str:
    result = _common.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
    return result.stdout if result.returncode == 0 else ""


def _pathspecs(root: Path) -> list[str]:
    arch = yaml.safe_load((root / "docs/contracts/instruction-architecture.yaml").read_text(encoding="utf-8"))
    globs = [g for layer in arch["layers"] for g in layer.get("content_locations", [])]
    globs += ["docs/contracts/**/*.yaml", "docs/DECISIONS.md", "docs/plans/PLAN-*.yaml", *_EXTRA_PATHSPECS]
    return [*(f":(glob){g}" for g in dict.fromkeys(globs)), ":(glob,exclude)docs/contracts/glossary.yaml"]


def _added_lines(root: Path, base: str, specs: list[str]) -> tuple[list[tuple[str, int, str]], set[str]]:
    """(path, 1-based line, text) of every added governed line, and the stripped text of every removed one."""
    added: list[tuple[str, int, str]] = []
    removed: set[str] = set()
    path, number = "", 0
    for line in _git(root, "diff", "-U0", "--no-color", "--no-renames", base, "--", *specs).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif (hunk := _HUNK.match(line)) is not None:
            number = int(hunk.group(1))
        elif line.startswith("+") and path:
            added.append((path, number, line[1:]))
            number += 1
        elif line.startswith("-") and not line.startswith("--- "):
            removed.add(line[1:].strip())
    for name in _git(root, "ls-files", "--others", "--exclude-standard", "--", *specs).split():
        for i, text in enumerate((root / name).read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            added.append((name, i, text))
    return added, removed


def _defining_range(root: Path, via: str, added: list[tuple[str, int, str]]) -> range:
    found = re.search(r"Decision (\d+)", via)
    path = root / "docs" / "DECISIONS.md"
    if not found or not path.is_file():
        return range(0)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, t in enumerate(lines, 1) if t.startswith(f"## Decision {found.group(1)}:")), 0)
    if not start or ("docs/DECISIONS.md", start) not in {(p, n) for p, n, _ in added}:
        return range(0)
    end = next((i for i in range(start + 1, len(lines) + 1) if lines[i - 1].startswith("## Decision ")), len(lines) + 1)
    return range(start, end)


def _plan_lines_at(root: Path, base: str) -> set[str]:
    out = _git(root, "grep", "-h", "-I", "-i", "-E", "platform|product", base, "--", ":(glob)docs/plans/PLAN-*.yaml")
    return {line.strip() for line in out.splitlines()}


def _lint(data: dict[str, Any], root: Path, base: str) -> tuple[list[str], int]:
    added, removed = _added_lines(root, base, _pathspecs(root))
    known = _glossary_words.identifiers(data)
    defining = _defining_range(root, str(data["contract"]["ratified_via"]), added)
    plan_lines: set[str] | None = None
    out: list[str] = []
    for path, number, text in added:
        uses = _glossary_words.unqualified_uses(text, yaml_keys=path.endswith(".yaml"), identifiers=known)
        if not uses or text.strip() in removed:
            continue
        if path == "docs/DECISIONS.md" and number in defining:
            continue
        if not path.startswith("docs/plans/"):
            plan_lines = _plan_lines_at(root, base) if plan_lines is None else plan_lines
            if text.strip() in plan_lines:
                continue
        for _, word in uses:
            hint = _glossary_words.say_instead(word, data)
            out.append(
                f'{path}:{number}: unqualified "{word}" -- say instead: {hint}, or quote it if it is not the relative word'
            )
    return out, len(added)


@registry.register("validate_glossary", owner="platform")
def validate_glossary(failed: list[str], root: Path | None = None) -> None:
    """Fail if the glossary is malformed, unprojected into rec guidance, or a changed governed line uses a relative word."""
    print("\n=== Glossary (glossary.yaml) ===")
    base_dir = root if root is not None else _common.ROOT
    path = base_dir / "docs" / "contracts" / _CONTRACT
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        findings = _shape(data, base_dir)
        if not findings:
            findings += _guidance(data, path)
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as exc:
        failed.append(_LABEL)
        print(f"  FAIL: {path}: {exc}")
        registry.skipped(f"{_CONTRACT} not loadable")
        return
    scanned = 0
    base = _common.status_diff_base(root)
    if base is None:
        print("  SKIP: lint leg has no diff base")
    elif not findings:
        lint, scanned = _lint(data, base_dir, base)
        findings += lint
    if findings:
        failed.append(_LABEL)
        for finding in findings:
            print(f"  FAIL: {finding}")
    else:
        print(f"  PASS: {_CONTRACT} satisfies every rule and {scanned} added governed lines use glossary names.")
    registry.examined(len(data["terms"]) + len(data["read_as"]["rows"]) + scanned, unit="glossary_items_and_added_lines")
