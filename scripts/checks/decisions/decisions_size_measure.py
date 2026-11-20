"""Size legs of Decision 276 as pure functions over injected texts (no file or git reads, never raise on text).

validate_decisions_size wires these to the repository; their mirror test is tests/checks/decisions/
test_decisions_size_measure.py. The contract keys read are docs/contracts/decision-entry.yaml size_governance
.measured_size_norm and .options_considered (Decision 276 clauses 4-6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

import yaml

from scripts.checks.decisions import entry_measure
from scripts.checks.decisions.envelope_rules import amended_numbers
from scripts.decisions_md import extract_entry_envelope

DECISION = 276
CONTRACT = "docs/contracts/decision-entry.yaml"
MEASURED_CAP_KEY = f"{CONTRACT}#size_governance.measured_size_norm.cap_bytes"
_STATUS_SUFFIX = re.compile(r"\s*\((?:Decided|Accepted|Proposed|Superseded[^)]*)\)\s*$")
_HEADING = re.compile(r"^#{2,3}\s+Decision\s+(?:\d+|\{\{N\}\}):\s*(.*?)\s*$")
_NONE_FORM = re.compile(r"none considered -- \S", re.IGNORECASE)
_MEASURED_EXCLUDED = (entry_measure.METADATA_ENVELOPE, entry_measure.REVERSAL_CONDITIONS, entry_measure.OPTIONS_CONSIDERED)
_REMEDIES = 'a "> **Label:**" annotation, a compacted stub (Decision 149), or archival (Decision 146)'


@dataclass(frozen=True)
class Governance:
    per_entry_cap: int
    measured_cap: int | None = None
    options_cap: int | None = None
    options_marker: str = entry_measure.OPTIONS_MARKER


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def size_governance(contract_text: str) -> Governance | str:
    """The parsed size_governance, or the "unreadable" finding text."""
    try:
        data = yaml.safe_load(contract_text)
        section = data["size_governance"]
        per_entry = section["per_entry_size_norm"]["cap_bytes"]
    except (yaml.YAMLError, KeyError, TypeError, AttributeError):
        return "decision-entry.yaml unreadable: unparseable, or no size_governance.per_entry_size_norm.cap_bytes"
    if not _positive_int(per_entry):
        return "decision-entry.yaml unreadable: per_entry_size_norm.cap_bytes is not a positive integer"
    measured = options = None
    marker = entry_measure.OPTIONS_MARKER
    for name in ("measured_size_norm", "options_considered"):
        if name not in section:
            continue
        cap = section[name].get("cap_bytes") if isinstance(section[name], dict) else None
        if not _positive_int(cap):
            return f"decision-entry.yaml unreadable: size_governance.{name} is present without a positive integer cap_bytes"
        if name == "measured_size_norm":
            measured = cap
        else:
            options = cap
            marker = str(section[name].get("marker") or marker)
    return Governance(per_entry, measured, options, marker)


def title_key(heading: str) -> str:
    """The title after "## Decision <N or {{N}}>: ", one trailing status parenthetical removed."""
    match = _HEADING.match(heading.strip())
    return _STATUS_SUFFIX.sub("", match.group(1)) if match else ""


def exemption_rows(text: str | None) -> list[tuple[str, str]]:
    """The (slug, title) rows of config/decision_options_exemptions.yaml; unparseable or absent reads as none."""
    try:
        rows = (yaml.safe_load(text or "") or {}).get("exempt_sidecars") or []
        return [(str(r["slug"]), str(r["title"])) for r in rows]
    except (yaml.YAMLError, KeyError, TypeError, AttributeError):
        return []


def options_issues(entry: str, gov: Governance, exempt_titles: set[str]) -> list[str]:
    """Clause 5 for one new-in-diff entry: the marker, a non-empty block, the none form with a reason, the byte cap."""
    if gov.options_cap is None or title_key(entry.split("\n", 1)[0]) in exempt_titles:
        return []
    block = entry_measure.options_block(entry, gov.options_marker)
    where = f"docs/contracts/decision-entry.yaml size_governance.options_considered (Decision {DECISION} clause 5)"
    if block is None:
        return [f"carries no {gov.options_marker} block ({where})"]
    body = block.removeprefix(gov.options_marker).strip()
    if not body:
        return [f"{gov.options_marker} block is empty ({where})"]
    if body.lower().startswith("none considered") and not _NONE_FORM.match(body):
        return [f'{gov.options_marker} "none considered" needs " -- " and a reason ({where})']
    size = len(block.strip().encode("utf-8"))
    if size > gov.options_cap:
        return [f"{gov.options_marker} block is {size} bytes, over its {gov.options_cap}-byte cap ({where})"]
    return []


def measured_cap_issues(entry: str, gov: Governance) -> list[str]:
    if gov.measured_cap is None:
        return []
    size = entry_measure.measured_bytes(entry, _MEASURED_EXCLUDED, gov.options_marker)
    if size <= gov.measured_cap:
        return []
    return [
        (
            f"is {size} bytes measured without its envelope, reversal stanza and options block, over the "
            f"{gov.measured_cap}-byte cap (docs/contracts/decision-entry.yaml size_governance.measured_size_norm, "
            f"Decision {DECISION} clause 4)"
        )
    ]


def exemption_findings(
    head_rows: list[tuple[str, str]],
    base_rows: list[tuple[str, str]] | None,
    base_has_options: bool,
    approved_slugs: set[str],
) -> tuple[list[str], list[str]]:
    """(fails, warns) for the exemption list: shrink-only (Decision 170 cl.3, Decision 244 cl.2 precedent)."""
    fails: list[str] = []
    warns = [
        f"exempt slug {slug!r} has no approved sidecar here: expected while that slug's PR is open; remove the row "
        "if the PR closed unmerged"
        for slug, _ in head_rows
        if slug not in approved_slugs
    ]
    if base_rows is not None:
        fails += [
            f"exemption row ({slug}, {title}) is not in origin/main's list: the list only shrinks (clause 5)"
            for slug, title in head_rows
            if (slug, title) not in base_rows
        ]
    elif head_rows and base_has_options:
        fails.append(
            "config/decision_options_exemptions.yaml appeared after the options rule landed: only the build PR creates it"
        )
    return fails, warns


def _delegation_numbers(entries: Mapping[int, str]) -> list[int]:
    found = []
    for number, block in entries.items():
        delegates = (extract_entry_envelope(block) or {}).get("delegates")
        if isinstance(delegates, list) and any(isinstance(d, dict) and d.get("key") == MEASURED_CAP_KEY for d in delegates):
            found.append(number)
    return found


def carried_keys(text: str | None) -> set[str]:
    try:
        section = yaml.safe_load(text or "")["size_governance"]
        return {k for k in ("measured_size_norm", "options_considered") if k in section}
    except (yaml.YAMLError, KeyError, TypeError, AttributeError):
        return set()


def fail_closed_issues(
    base_contract: str | None, head_contract: str, base_entries: Mapping[int, str], new_entries: Mapping[int, str]
) -> list[str]:
    """FAIL when head's contract lost a size_governance key base carried, unless a new direction entry amends or
    supersedes the Decision whose delegates carry measured_size_norm.cap_bytes (found by key, never by number)."""
    dropped = sorted(carried_keys(base_contract) - carried_keys(head_contract))
    if not dropped:
        return []
    delegators = set(_delegation_numbers(base_entries))
    for block in new_entries.values():
        envelope = extract_entry_envelope(block) or {}
        if (envelope.get("kind") or {}).get("value") != "direction":
            continue
        if delegators & amended_numbers(envelope):
            return []
    return [
        f"{CONTRACT} dropped size_governance.{key} without a new direction entry amending the Decision that "
        f"delegates its cap (Decision {DECISION} clause 4, Decision 128)"
        for key in dropped
    ]


def in_place_growth(
    number: int, rel: str, head_block: str, base_block: str | None, gov: Governance
) -> tuple[list[str], list[str]]:
    """Clause 6 for a baseline entry present in this file at base: (fails, warns)."""
    if gov.measured_cap is None or base_block is None:
        return [], []
    marker = gov.options_marker
    cap = gov.measured_cap
    excluded = (*_MEASURED_EXCLUDED, entry_measure.DATED_ANNOTATIONS)
    head = entry_measure.measured_bytes(head_block, excluded, marker)
    base = entry_measure.measured_bytes(base_block, excluded, marker)
    if head > base and head > cap:
        grown = (
            f"Decision {number} ({rel}) body grew from {base} to {head} bytes measured, over the {cap}-byte cap "
            f"(size_governance.measured_size_norm.in_place_growth, Decision {DECISION} clause 6); lawful remedies: {_REMEDIES}"
        )
        return [grown], []
    head_dated = entry_measure.measured_bytes(head_block, excluded, marker, dated_only=True)
    base_dated = entry_measure.measured_bytes(base_block, excluded, marker, dated_only=True)
    if head_dated > base_dated and head_dated > cap:
        firsts = [head_block[a:b].strip().splitlines()[0] for a, b in entry_measure.dated_annotation_spans(head_block)]
        labels = [first for first in firsts if not re.search(r"\d{4}-\d{2}", first)]
        return [], [f"Decision {number} ({rel}) grew only through undated annotation paragraph(s): {labels} (clause 6)"]
    return [], []
