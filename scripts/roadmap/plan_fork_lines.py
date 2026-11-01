"""Plan fork-line engine (rec-3530): judges context[] fork lines against overseer-dispatch.yaml record_grammar.

Pure: no git and no direct filesystem access. Every file it needs (docs/decisions-index.json, a cited contract, a
referenced plan) is requested by repo-relative path through a caller-supplied `read(rel) -> str | None`, and it never
lists a directory. It never raises on bad input; it returns findings.

The contract's record_grammar stays the one rule source (Decision 210 clause 3): the constants below are asserted equal
to its template by `grammar_mismatches` (Decision 169 derive-and-assert).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

Reader = Callable[[str], "str | None"]

FIELDS = ("fork", "chosen", "class", "precedent", "precedent_kind", "reversible", "persisted")
CLASS = ("consistency-only", "asked")
PRECEDENT_KIND = ("decision", "contract", "answered-fork", "none")
REVERSIBLE = ("yes", "no")

CONTRACT = "docs/contracts/overseer-dispatch.yaml"
DECISIONS_INDEX = "docs/decisions-index.json"
_NONE_SENTENCE = "precedent: none | precedent_kind: none is legal ONLY on a class: asked line"
_ENUMS = {"class": CLASS, "precedent_kind": PRECEDENT_KIND, "reversible": REVERSIBLE}
_TEMPLATE_HINT = (
    "fork: <sentence> | chosen: <X> | class: consistency-only|asked | precedent: <ref> | "
    "precedent_kind: decision|contract|answered-fork | reversible: yes|no | persisted: <where>"
)

_EXTRA_KEY_RE = re.compile(r" \| ([A-Za-z_][A-Za-z0-9_-]*): ")
_TEMPLATE_RE = re.compile(r"'(fork:[^']*)'")
_DECISION_RE = re.compile(r"\bDecision (\d+)\b")
_CONTRACT_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:docs/contracts/)?([A-Za-z0-9_][A-Za-z0-9_.-]*\.yaml)(?![A-Za-z0-9_])"
    r"(?:#([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*))?"
)
_ANSWERED_RE = re.compile(r"\bPLAN-([a-z0-9][a-z0-9-]*) fork (\d+)\b")
_ASKED_RE = re.compile(r"\| class: asked(?![\w-])")
_PLAN_PATH_RE = re.compile(r"(?:^|/)PLAN-([a-z0-9][a-z0-9-]*)\.yaml$")


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    message: str


@dataclass(frozen=True)
class ForkRecord:
    index: int
    label: str
    text: str


def _fail(rule: str, message: str) -> Finding:
    return Finding("fail", rule, message)


def _warn(rule: str, message: str) -> Finding:
    return Finding("warn", rule, message)


def _squash(text: str) -> str:
    return " ".join(text.split())


def template_from_record_grammar(text: str) -> str | None:
    match = _TEMPLATE_RE.search(_squash(text))
    return match.group(1) if match else None


def _template_parts(template: str) -> dict[str, str] | None:
    parts: dict[str, str] = {}
    for chunk in template.split(" | "):
        key, sep, value = chunk.partition(":")
        if not sep:
            return None
        parts[key.strip()] = value.strip()
    return parts


def grammar_mismatches(record_grammar: str) -> list[str]:
    """Names each constant that disagrees with the contract's template; empty when they agree."""
    template = template_from_record_grammar(record_grammar)
    if template is None:
        return ["record_grammar: no extractable single-quoted 'fork: ...' template"]
    parts = _template_parts(template)
    if parts is None:
        return ["record_grammar: the template is not a ' | '-separated key: value list"]
    problems: list[str] = []
    if tuple(parts) != FIELDS:
        problems.append(f"FIELDS: engine {FIELDS} != template {tuple(parts)}")
    for name, key, ours in (("CLASS", "class", CLASS), ("REVERSIBLE", "reversible", REVERSIBLE)):
        theirs = tuple(parts.get(key, "").split("|"))
        if theirs != ours:
            problems.append(f"{name}: engine {ours} != template {theirs}")
    theirs_kind = tuple(parts.get("precedent_kind", "").split("|"))
    ours_kind = tuple(k for k in PRECEDENT_KIND if k != "none")
    if theirs_kind != ours_kind:
        problems.append(f"PRECEDENT_KIND: engine {ours_kind} (none excluded) != template {theirs_kind}")
    if _NONE_SENTENCE not in _squash(record_grammar):
        problems.append(f"PRECEDENT_KIND: record_grammar lacks the sentence {_NONE_SENTENCE!r} that admits none")
    return problems


def load_record_grammar(contract_text: str | None) -> str | None:
    if contract_text is None:
        return None
    try:
        doc: Any = yaml.safe_load(contract_text)
    except yaml.YAMLError:
        return None
    node: Any = doc
    for key in ("autonomy_tiers", "plan_fork_classification", "record_grammar"):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, str) else None


def is_fork_record(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("fork:") or " | class: " in stripped or " | precedent_kind: " in stripped


def fork_records(context: Any) -> list[ForkRecord]:
    """Every fork-record entry of a context[] list; 'fork <n>' counts entries starting 'fork:'."""
    if not isinstance(context, list):
        return []
    records: list[ForkRecord] = []
    ordinal = 0
    for index, entry in enumerate(context):
        if not isinstance(entry, str) or not is_fork_record(entry):
            continue
        if entry.strip().startswith("fork:"):
            ordinal += 1
            records.append(ForkRecord(index, f"fork {ordinal}", entry))
        else:
            records.append(ForkRecord(index, f"context[{index}]", entry))
    return records


def _split_fields(text: str) -> tuple[dict[str, str] | None, list[Finding]]:
    stripped = text.strip()
    if not stripped.startswith("fork:"):
        return None, [_fail("near-miss", f"a fork record must start with 'fork:'; template: {_TEMPLATE_HINT}")]
    spans: list[tuple[str, int, int]] = [("fork", 0, len("fork:"))]
    cursor = len("fork:")
    for key in FIELDS[1:]:
        token = f" | {key}:"
        at = stripped.find(token, cursor)
        if at < 0:
            present = all(f" | {k}:" in stripped for k in FIELDS[1:])
            rule = "key-order" if present else "missing-key"
            what = "keys are out of order" if present else f"key '{key}' is missing"
            return None, [_fail(rule, f"{what}; template: {_TEMPLATE_HINT}")]
        spans.append((key, at, at + len(token)))
        cursor = at + len(token)
    values: dict[str, str] = {}
    for i, (key, _start, value_from) in enumerate(spans):
        value_to = spans[i + 1][1] if i + 1 < len(spans) else len(stripped)
        values[key] = stripped[value_from:value_to].strip()
    extra = _EXTRA_KEY_RE.search(values["persisted"])
    if extra:
        return None, [
            _fail("extra-key", f"unexpected extra key '{extra.group(1)}' after persisted; template: {_TEMPLATE_HINT}")
        ]
    return values, []


def _judge_enum(key: str, value: str) -> Finding | None:
    legal = _ENUMS[key]
    if value in legal:
        return None
    if key == "class" and value.startswith("decided-with-notice"):
        return _fail("enum", "the notice tier, not a class: write class: consistency-only")
    for literal in sorted(legal, key=len, reverse=True):
        if value.startswith(literal) and not value[len(literal) :][:1].isalnum():
            return _fail("enum", f"{key} must be the bare literal '{literal}' with no note after it, found {value!r}")
    return _fail("enum", f"{key} must be one of {'|'.join(legal)}, found {value!r}")


def judge_grammar(text: str) -> tuple[dict[str, str] | None, list[Finding]]:
    values, findings = _split_fields(text)
    if values is None:
        return None, findings
    for key in FIELDS:
        if not values[key]:
            findings.append(_fail("empty-value", f"{key} is empty; template: {_TEMPLATE_HINT}"))
    for key in _ENUMS:
        if values[key]:
            found = _judge_enum(key, values[key])
            if found is not None:
                findings.append(found)
    kind, precedent = values["precedent_kind"], values["precedent"]
    if precedent == "none" and kind in PRECEDENT_KIND and kind != "none":
        findings.append(_fail("none-pairing", f"precedent: none pairs only with precedent_kind: none, found {kind}"))
    if values["class"] == "consistency-only" and (precedent == "none" or kind == "none"):
        findings.append(_fail("none-on-consistency-only", "precedent none is legal only on a class: asked line"))
    return values, findings


def _plan_slug(plan_path: str) -> str | None:
    match = _PLAN_PATH_RE.search(plan_path)
    return match.group(1) if match else None


def _load_yaml(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _decision_rows(read: Reader) -> dict[int, dict[str, Any]] | None:
    raw = read(DECISIONS_INDEX)
    if raw is None:
        return None
    try:
        rows = json.loads(raw).get("decisions", [])
        return {int(r["number"]): r for r in rows if isinstance(r, dict) and "number" in r}
    except (ValueError, AttributeError, TypeError, KeyError):
        return None


def _is_superseded(row: dict[str, Any]) -> bool:
    if row.get("currency") == "superseded_compacted":
        return True
    return row.get("live") is False and row.get("currency") is None and str(row.get("status", "")).startswith("Superseded")


def _successor(number: int, row: dict[str, Any], rows: dict[int, dict[str, Any]]) -> str:
    if row.get("superseded_by"):
        return f"Decision {row['superseded_by']}"
    if row.get("currency") != "superseded_pointer":
        found = re.search(r"Decision (\d+)", str(row.get("status", "")))
        if found:
            return f"Decision {found.group(1)}"
    holders = sorted(n for n, r in rows.items() if number in (r.get("supersedes") or []))
    return f"Decision {holders[0]}" if holders else "its successor"


def _decision_findings(precedent: str, read: Reader) -> list[Finding]:
    numbers = [int(n) for n in _DECISION_RE.findall(precedent)]
    if not numbers:
        return [_fail("pointer-missing", "precedent_kind decision needs a 'Decision NNN' pointer")]
    rows = _decision_rows(read)
    if rows is None:
        return [_fail("pointer-unresolved", f"cannot read {DECISIONS_INDEX} to resolve Decision {numbers[0]}")]
    findings: list[Finding] = []
    for number in dict.fromkeys(numbers):
        row = rows.get(number)
        if row is None:
            findings.append(_fail("pointer-unresolved", f"Decision {number} does not exist in {DECISIONS_INDEX}"))
        elif _is_superseded(row):
            findings.append(
                _fail("pointer-superseded", f"Decision {number} is superseded; cite {_successor(number, row, rows)}")
            )
        elif row.get("currency") == "superseded_pointer":
            findings.append(
                _warn(
                    "pointer-partly-superseded", f"Decision {number} is partly superseded; see {_successor(number, row, rows)}"
                )
            )
    return findings


def _walk(node: Any, anchor: str) -> bool:
    for segment in anchor.split("."):
        if not isinstance(node, dict):
            return False
        match = [k for k in node if str(k) == segment]
        if not match:
            return False
        node = node[match[0]]
    return True


def _contract_findings(precedent: str, read: Reader) -> list[Finding]:
    pointers = list(dict.fromkeys(_CONTRACT_RE.findall(precedent)))
    if not pointers:
        return [_fail("pointer-missing", "precedent_kind contract needs a docs/contracts/<name>.yaml pointer")]
    findings: list[Finding] = []
    for name, anchor in pointers:
        rel = f"docs/contracts/{name}"
        text = read(rel)
        if text is None:
            findings.append(_fail("pointer-unresolved", f"{rel} does not exist"))
            continue
        doc = _load_yaml(text)
        if anchor and not _walk(doc, anchor):
            findings.append(_fail("pointer-unresolved", f"{rel} has no key path '{anchor}'"))
    return findings


def _answered_findings(precedent: str, plan_path: str, read: Reader) -> list[Finding]:
    pointers = list(dict.fromkeys(_ANSWERED_RE.findall(precedent)))
    if not pointers:
        return [_fail("pointer-missing", "precedent_kind answered-fork needs a 'PLAN-<slug> fork <n>' pointer")]
    own = _plan_slug(plan_path)
    findings: list[Finding] = []
    for slug, n_text in pointers:
        n, label = int(n_text), f"PLAN-{slug} fork {n_text}"
        if slug == own:
            findings.append(_fail("pointer-self", f"{label} is the plan being judged; cite another plan's asked fork"))
            continue
        doc = _load_yaml(read(f"docs/plans/PLAN-{slug}.yaml"))
        if not isinstance(doc, dict):
            findings.append(_fail("pointer-unresolved", f"docs/plans/PLAN-{slug}.yaml does not exist or does not parse"))
            continue
        forks = [e for e in doc.get("context") or [] if isinstance(e, str) and e.strip().startswith("fork:")]
        if not 1 <= n <= len(forks):
            findings.append(_fail("pointer-unresolved", f"{label} is out of range: the plan has {len(forks)} fork lines"))
        elif not _ASKED_RE.search(forks[n - 1]):
            findings.append(
                _fail("pointer-not-asked", f"{label} is not class: asked; only a human-answered fork is a precedent")
            )
    return findings


def judge_line(text: str, *, plan_path: str, read: Reader) -> list[Finding]:
    values, findings = judge_grammar(text)
    if values is None or any(f.severity == "fail" for f in findings):
        return findings
    if values["class"] != "consistency-only":
        return findings
    if values["reversible"] != "yes":
        findings.append(_fail("not-reversible", "a consistency-only line needs reversible: yes; otherwise class: asked"))
    kind, precedent = values["precedent_kind"], values["precedent"]
    if kind == "decision":
        findings.extend(_decision_findings(precedent, read))
    elif kind == "contract":
        findings.extend(_contract_findings(precedent, read))
    else:
        findings.extend(_answered_findings(precedent, plan_path, read))
    return findings


def cached(read: Reader) -> Reader:
    """Wrap a reader so each repo-relative path is requested at most once."""
    memo: dict[str, str | None] = {}

    def _read(rel: str) -> str | None:
        if rel not in memo:
            memo[rel] = read(rel)
        return memo[rel]

    return _read


def _worktree_reader(root: Path) -> Reader:
    def _read(rel: str) -> str | None:
        try:
            return (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return _read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge every context[] fork line of the named plans (rec-3530).")
    parser.add_argument("plans", nargs="+", help="repo-relative PLAN-*.yaml paths")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    read = cached(_worktree_reader(root))
    failures = 0
    for plan_path in args.plans:
        doc = _load_yaml(read(plan_path))
        if not isinstance(doc, dict):
            print(f"FAIL {plan_path}: unreadable or not a plan mapping")
            failures += 1
            continue
        context = doc.get("context")
        for record in fork_records(context):
            for finding in judge_line(record.text, plan_path=plan_path, read=read):
                hint = "" if _TEMPLATE_HINT in finding.message else f" [template: {_TEMPLATE_HINT}]"
                print(f"{finding.severity.upper()} {plan_path} {record.label}: {finding.rule}: {finding.message}{hint}")
                failures += finding.severity == "fail"
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
