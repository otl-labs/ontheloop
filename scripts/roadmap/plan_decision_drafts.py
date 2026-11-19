"""Planning-time report on drafted Decisions (PLAN-decision-serves-kind-grammar, Decision 243 clause 8).

Finds drafted "## Decision <placeholder|n>: <title>" blocks in a plan's string values, in the plan's approved-text
sidecar (docs/plans/approved/<slug>.yaml, blocks on docs/DECISIONS.md) and in a draft file, parses each through the
shared parser's placeholder grammar (scripts.decisions_md) and applies the same rules the conformance gate applies
(scripts.checks.decisions.envelope_rules), so a missing kind is caught before the wording is approved. Advisory:
every entry point is total (a finding, never a raise). The report is advisory; validate_approval_routing's leg B
reads drafted_entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from scripts.checks.decisions import consolidation, decisions_size_measure, envelope_rules
from scripts.decisions_md import (
    PLACEHOLDER_NUMBER,
    _extract_related_decisions,
    _extract_title_borne_supersedes,
    extract_amends_edges,
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
_EXEMPTIONS = "config/decision_options_exemptions.yaml"


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


def _resolves_at_head(path: str, dotted: str) -> bool:
    try:
        parsed = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return envelope_rules.dotted_value(parsed, dotted)[0]


def _delegation_findings(env: dict, bound_files: set[str], meta: dict) -> list[Finding]:
    """delegates and plan findings; a key that does not resolve but whose file the plan's sidecar binds is a WARN."""
    rules = [str(r) for r in ((meta.get("field_shapes") or {}).get("delegates") or {}).get("change_rules") or []]
    found = [("FAIL", "delegates", i) for i in envelope_rules.delegates_issues(env, rules, lambda _k: True)] if rules else []
    delegates = env.get("delegates")
    for item in delegates if isinstance(delegates, list) and rules else []:
        split = envelope_rules.split_delegated_key(item.get("key")) if isinstance(item, dict) else None
        if split and not _resolves_at_head(*split):
            severity = "WARN" if split[0] in bound_files else "FAIL"
            found.append((severity, "delegates", f"delegates key {item['key']!r} does not resolve at head"))
    found += [("FAIL", "plan", i) for i in envelope_rules.plan_issues(env)]
    found += [
        ("WARN", "plan", i)
        for i in envelope_rules.plan_warnings(env, lambda s: (ROOT / "docs/plans" / f"PLAN-{s}.yaml").exists())
    ]
    return [Finding("", "", sev, field, detail) for sev, field, detail in found]


def _size_findings(block: str, slug: str | None) -> list[Finding]:
    """The options rule and the measured cap, read from the live contract and skipped when its keys are absent."""
    try:
        text = (ROOT / _ENTRY_CONTRACT).read_text(encoding="utf-8")
    except OSError:
        return []
    gov = decisions_size_measure.size_governance(text)
    if isinstance(gov, str):
        return []
    try:
        rows = decisions_size_measure.exemption_rows((ROOT / _EXEMPTIONS).read_text(encoding="utf-8"))
    except OSError:
        rows = []
    exempt = {title for row_slug, title in rows if row_slug == slug}
    issues = decisions_size_measure.options_issues(block, gov, exempt) + decisions_size_measure.measured_cap_issues(block, gov)
    return [Finding("", "", "FAIL", "size", issue) for issue in issues]


def _rule_findings(
    number: str, title: str, block: str, slug: str | None = None, bound_files: set[str] | None = None
) -> list[Finding]:
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
    extra = _delegation_findings(env, bound_files or set(), meta) + _size_findings(block, slug)
    return out + [Finding(number, title, f.severity, f.field, f.detail) for f in extra]


def _int_list(value: Any) -> list[int]:
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)] if isinstance(value, list) else []


def _info_lines(number: str, title: str, block: str) -> list[str]:
    """Advisory context for each resolving serves id (Decision 66): never a finding, total."""
    env = extract_entry_envelope(block, placeholder=True)
    if env is None or not isinstance(env.get("serves"), list):
        return []
    serves = env["serves"]
    try:
        entries = {p.id: p for p in load_north_star(ROOT / "docs/contracts/north-star.yaml").principles}
    except ValueError:
        return []
    edges = (
        set(extract_amends_edges(title)) | set(_extract_title_borne_supersedes(title)) | set(_extract_related_decisions(block))
    )
    edges |= set(_int_list(env.get("supersedes")))
    out: list[str] = []
    for ns_id in serves:
        entry = entries.get(ns_id) if isinstance(ns_id, str) else None
        if entry is None:
            continue
        links = ", ".join(str(n) for n in sorted(edges & set(entry.carried_by))) or "none"
        out.append(
            f'  INFO {number} serves {ns_id}: principle "{entry.principle}"; '
            f'failure_signal "{" ".join(entry.failure_signal.split())}"; '
            f"carried_by [{', '.join(str(n) for n in entry.carried_by)}]; links {links} (corroborate, never substitute)"
        )
    return out


def _nomination_lines(number: str, title: str, block: str) -> list[str]:
    """Decision 278 cl.2: one NOMINATION line per amended root at the consolidation threshold; never a finding, total."""
    env = extract_entry_envelope(block, placeholder=True) or {}
    amends = sorted(set(extract_amends_edges(title)) | set(_int_list(env.get("amends"))))
    threshold = consolidation.load_threshold(ROOT)
    if not amends or threshold is None:
        return []
    return [
        "  " + line
        for line in consolidation.nomination_info(
            number, amends, consolidation.load_rows(ROOT), threshold, consolidation.entry_sizes(ROOT)
        )
    ]


def _drafted_blocks(content: str, root: Path, skip_quoted: bool) -> Iterator[tuple[str, str, str]]:
    """(number, title, block) of each drafted entry: the skip-and-count loop the report and drafted_titles share."""
    concrete = any(n != PLACEHOLDER_NUMBER for n, _, _ in _blocks(content))
    existing = _existing_headings(root) if skip_quoted and concrete else set()
    for number, title, block in _blocks(content):
        if number != PLACEHOLDER_NUMBER and (int(number), title) in existing:
            continue
        yield number, title, block


def _draft_findings(
    content: str, root: Path, skip_quoted: bool, slug: str | None = None
) -> tuple[int, list[Finding], list[str]]:
    drafted = 0
    findings: list[Finding] = []
    infos: list[str] = []
    for number, title, block in _drafted_blocks(content, root, skip_quoted):
        drafted += 1
        findings += _rule_findings(number, title, block, slug)
        infos += _info_lines(number, title, block) + _nomination_lines(number, title, block)
    return drafted, findings, infos


def _sidecar_bound_files(plan: dict, root: Path) -> set[str]:
    slug = plan.get("slug")
    try:
        path = root / approved_text.sidecar_rel(str(slug))
        return {b.file for b in approved_text.parse_sidecar(path.read_text(encoding="utf-8")).blocks}
    except (OSError, approved_text.ApprovedTextError, UnicodeDecodeError):
        return set()


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


def _scan_draft(path: str | Path, root: Path) -> tuple[int, list[Finding], list[str]]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, [Finding("-", "", "FAIL", "path", f"cannot read {path}: {exc}")], []
    return _draft_findings(content, root, skip_quoted=False)


def _scan_plan(path: str | Path, root: Path) -> tuple[int, list[Finding], list[str]]:
    try:
        plan = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return 0, [Finding("-", "", "FAIL", "path", f"cannot read {path}: {exc}")], []
    if not isinstance(plan, dict):
        return 0, [], []
    drafted = 0
    findings: list[Finding] = []
    infos: list[str] = []
    for text in _strings(plan):
        d, f, i = _draft_findings(text, root, skip_quoted=True, slug=str(plan.get("slug")))
        drafted, findings, infos = drafted + d, findings + f, infos + i
    sidecar_texts, findings_extra = _sidecar_contents(plan, root)
    findings += findings_extra
    bound = _sidecar_bound_files(plan, root)
    for text in sidecar_texts:
        drafted += 1
        head = next(iter(_blocks(text)))
        findings += _rule_findings(*head, str(plan.get("slug")), bound)
        infos += _info_lines(head[0], head[1], text) + _nomination_lines(head[0], head[1], text)
    return drafted, findings, infos


def findings_for_draft(path: str | Path, root: Path | None = None) -> list[Finding]:
    return _scan_draft(path, root or ROOT)[1]


def findings_for_plan(path: str | Path, root: Path | None = None) -> list[Finding]:
    """Findings for the drafted Decisions in a plan's text and its approved-text sidecar; total, never raises."""
    return _scan_plan(path, root or ROOT)[1]


_KIND_MAPPING = re.compile(r"^kind:[^\n]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n)*", re.MULTILINE)


def blind_text(block: str) -> str:
    """The block with the envelope's kind mapping (key, value and justification lines) removed, nothing else changed."""
    fence_end = block.find("\n```\n", block.find("```yaml") + 1) if "```yaml" in block else -1
    if fence_end < 0:
        return block
    head, tail = block[: fence_end + 1], block[fence_end + 1 :]
    return _KIND_MAPPING.sub("", head) + tail


def drafted_entries(plan_path: str | Path, root: Path | None = None) -> list[dict]:
    """One dict per drafted Decision in the plan's sidecar blocks on DECISIONS.md: title, kind, amends, block."""
    root = root or ROOT
    try:
        plan = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if not isinstance(plan, dict):
        return []
    entries: list[dict] = []
    for text in _sidecar_contents(plan, root)[0]:
        found = list(_blocks(text))
        title = found[0][1] if found else ""
        env = extract_entry_envelope(text, placeholder=True) or {}
        kind = env.get("kind")
        amends = _int_list(env.get("amends")) or extract_amends_edges(title)
        amends = sorted({*amends, *_int_list(env.get("supersedes")), *_extract_title_borne_supersedes(title)})
        entries.append(
            {"title": title, "kind": kind.get("value") if isinstance(kind, dict) else None, "amends": amends, "block": text}
        )
    return entries


def drafted_titles(plan_path: str | Path, root: Path | None = None) -> list[str]:
    """The title of every drafted entry _scan_plan counts, in its order; total ([] on an unreadable plan).

    A title drafted in both plan text and sidecar appears twice: consumers compare it as a set.
    """
    root = root or ROOT
    try:
        plan = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if not isinstance(plan, dict):
        return []
    titles = [title for text in _strings(plan) for _, title, _ in _drafted_blocks(text, root, True)]
    for text in _sidecar_contents(plan, root)[0]:
        titles.append(next(iter(_blocks(text)))[1])
    return titles


def _report(drafted: int, findings: list[Finding], infos: list[str]) -> list[str]:
    if not drafted and not findings:
        return ["decision_drafts: none"]
    lines = [f"decision_drafts: {drafted} drafted, {len(findings)} findings"]
    return lines + [f"  {f.severity} {f.number} {f.field}: {f.detail}" for f in findings] + infos


def report_for_plan(path: str | Path, root: Path | None = None) -> list[str]:
    return _report(*_scan_plan(path, root or ROOT))


def report_for_draft(path: str | Path, root: Path | None = None) -> list[str]:
    return _report(*_scan_draft(path, root or ROOT))
