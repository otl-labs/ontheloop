"""Planning-time report on drafted Decisions (PLAN-decision-serves-kind-grammar, Decision 243 clause 8).

Finds drafted "## Decision <placeholder|n>: <title>" blocks in a plan's string values, in the plan's approved-text
sidecar (docs/plans/approved/<slug>.yaml, blocks on docs/DECISIONS.md) and in a draft file, parses each through the
shared parser's placeholder grammar (scripts.decisions_md) and applies the same rules the conformance gate applies
(scripts.checks.decisions.envelope_rules), so a missing kind is caught before the wording is approved. Advisory:
every entry point is total (a finding, never a raise) and nothing here feeds a gate verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from scripts.checks.decisions import envelope_rules
from scripts.decisions_md import (
    PLACEHOLDER_NUMBER,
    extract_entry_envelope,
    iter_decision_headings,
    iter_placeholder_headings,
)
from scripts.north_star_models import load_north_star
from scripts.roadmap import approved_text

ROOT = Path(__file__).resolve().parents[2]
_ENTRY_CONTRACT = "docs/contracts/decision-entry.yaml"
_KIND_CONTRACT = "docs/contracts/decision-kind.yaml"
_DECISIONS = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")
_DECISIONS_MD = "docs/DECISIONS.md"
_CONCRETE_NUMBER = "assign at implementation: Decision 217 cl.5"


@dataclass(frozen=True)
class Finding:
    number: str
    title: str
    severity: str
    field: str
    detail: str


def _read_yaml(rel: str) -> dict:
    try:
        data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _blocks(content: str) -> Iterator[tuple[str, str, str]]:
    """(heading number, title, heading-inclusive block) for each heading in content, in order."""
    headings = iter_placeholder_headings(content)
    for i, m in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        yield m.group(1), m.group(2).strip(), content[m.start() : end]


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _existing_headings(root: Path) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for rel in _DECISIONS:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.update((int(m.group(1)), m.group(2).strip()) for m in iter_decision_headings(content))
    return found


def _rule_findings(number: str, title: str, block: str) -> list[Finding]:
    def fnd(severity: str, field: str, detail: str) -> Finding:
        return Finding(number, title, severity, field, detail)

    env = extract_entry_envelope(block, placeholder=True)
    if env is None:
        return [fnd("FAIL", "envelope", envelope_rules.malformed_envelope_detail(block, placeholder=True))]
    meta = _read_yaml(_ENTRY_CONTRACT).get("metadata_envelope") or {}
    required = [str(f) for f in meta.get("required_fields") or []]
    optional = [str(f) for f in meta.get("optional_fields") or []]
    routing = (_read_yaml(_ENTRY_CONTRACT).get("significance") or {}).get("routing") or {}
    kind_doc = _read_yaml(_KIND_CONTRACT)
    kind_values = [str(v) for v in kind_doc.get("values") or []]
    try:
        ns_ids = load_north_star(ROOT / "docs/contracts/north-star.yaml").ids()
    except ValueError:
        ns_ids = set()
    out: list[Finding] = []
    out += [fnd("FAIL", "required_fields", i) for i in envelope_rules.required_field_issues(env, required)]
    out += [
        fnd("FAIL", "significance", i)
        for i in envelope_rules.significance_issues(env, {str(k): str(v) for k, v in routing.items()})
    ]
    quoted_test = " ".join(str(kind_doc.get("test") or "").split())
    out += [fnd("FAIL", "kind", f"{i}. test: {quoted_test}") for i in envelope_rules.kind_issues(env, kind_values)]
    out += [fnd("FAIL", "serves", i) for i in envelope_rules.serves_issues(env, ns_ids)]
    if meta.get("closed_key_set"):
        out += [fnd("FAIL", "keys", i) for i in envelope_rules.unknown_key_issues(env, required + optional)]
    out += [fnd("FAIL", "number", i) for i in envelope_rules.number_issues(env.get("number"), None, draft=True)]
    if number != PLACEHOLDER_NUMBER or env.get("number") != PLACEHOLDER_NUMBER:
        out.append(fnd("WARN", "number", _CONCRETE_NUMBER))
    return out


def _draft_findings(content: str, root: Path, skip_quoted: bool) -> tuple[int, list[Finding]]:
    concrete = any(n != PLACEHOLDER_NUMBER for n, _, _ in _blocks(content))
    existing = _existing_headings(root) if skip_quoted and concrete else set()
    drafted = 0
    findings: list[Finding] = []
    for number, title, block in _blocks(content):
        if number != PLACEHOLDER_NUMBER and (int(number), title) in existing:
            continue
        drafted += 1
        findings += _rule_findings(number, title, block)
    return drafted, findings


def _sidecar_contents(plan: dict, root: Path) -> tuple[list[str], list[Finding]]:
    slug = plan.get("slug")
    path = root / approved_text.sidecar_rel(str(slug)) if isinstance(slug, str) else None
    if path is None or not path.exists():
        return [], []
    try:
        sidecar = approved_text.parse_sidecar(path.read_text(encoding="utf-8"))
    except (OSError, approved_text.ApprovedTextError) as exc:
        return [], [Finding("-", "", "FAIL", "sidecar", f"cannot read {path.name}: {exc}")]
    texts: list[str] = []
    for block in sidecar.blocks:
        if block.file != _DECISIONS_MD:
            continue
        before = {(n, t) for n, t, _ in _blocks(block.before)}
        texts += [b for n, t, b in _blocks(block.after) if (n, t) not in before]
    return texts, []


def _scan_draft(path: str | Path, root: Path) -> tuple[int, list[Finding]]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, [Finding("-", "", "FAIL", "path", f"cannot read {path}: {exc}")]
    return _draft_findings(content, root, skip_quoted=False)


def _scan_plan(path: str | Path, root: Path) -> tuple[int, list[Finding]]:
    try:
        plan = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return 0, [Finding("-", "", "FAIL", "path", f"cannot read {path}: {exc}")]
    if not isinstance(plan, dict):
        return 0, []
    drafted, findings = 0, []
    for text in _strings(plan):
        d, f = _draft_findings(text, root, skip_quoted=True)
        drafted, findings = drafted + d, findings + f
    sidecar_texts, findings_extra = _sidecar_contents(plan, root)
    findings += findings_extra
    for text in sidecar_texts:
        drafted += 1
        findings += _rule_findings(*next(iter(_blocks(text))))
    return drafted, findings


def findings_for_draft(path: str | Path, root: Path | None = None) -> list[Finding]:
    return _scan_draft(path, root or ROOT)[1]


def findings_for_plan(path: str | Path, root: Path | None = None) -> list[Finding]:
    """Findings for the drafted Decisions in a plan's text and its approved-text sidecar; total, never raises."""
    return _scan_plan(path, root or ROOT)[1]


def _report(drafted: int, findings: list[Finding]) -> list[str]:
    if not drafted and not findings:
        return ["decision_drafts: none"]
    lines = [f"decision_drafts: {drafted} drafted, {len(findings)} findings"]
    return lines + [f"  {f.severity} {f.number} {f.field}: {f.detail}" for f in findings]


def report_for_plan(path: str | Path, root: Path | None = None) -> list[str]:
    return _report(*_scan_plan(path, root or ROOT))


def report_for_draft(path: str | Path, root: Path | None = None) -> list[str]:
    return _report(*_scan_draft(path, root or ROOT))
