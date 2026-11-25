"""Hit model, rule table and gated-set reader of the consolidation coupling inventory (rec-5627).

Pure: nothing here raises on malformed input or touches the inventoried tree beyond reads.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

SCHEMA_VERSION = 1
STUB_TEMPLATE_ID = "compacted-stub-v2"
STUB_TEMPLATE = (
    "{header}\n\n**Status:** Superseded\n**Date:** {date}\n\n"
    "**Decision:** Superseded by Decision {successor}, which restates its rulings{amenders} "
    "(consolidation inventory probe stub).\n\n**Superseded by: Decision {successor}**\n\n"
)

EVIDENCE_TYPES = frozenset(
    {"structured_key", "probe_breaks", "probe_survives", "probe_red_before", "dataflow_literal", "cooccurrence"}
)
PROBE_STATES = frozenset({"breaks", "survives", "red_before", "not_run"})
TIERS = ("possible", "probable", "confirmed")

# kind -> (rule_id, effect, needs_disposition)
RULES: dict[str, tuple[str, str, bool]] = {
    "test_node": ("R-TEST", "retarget_or_retire_test", True),
    "graduated_record": ("R-RECORD", "retire_and_replace", True),
    "north_star_carrier": ("R-NS", "move_carrier", True),
    "supersession_waiver": ("R-WAIVER", "delete_orphan_waiver", True),
    "census_row": ("R-CENSUS", "remove_row", True),
    "manifest_reference": ("R-MANIFEST", "fails_resolve", True),
    "chain_edge": ("R-CHAIN", "victim_or_carry_forward", True),
    "delegated_key": ("R-DELEG", "redelegate", True),
    "authorizing_marker": ("R-MARKER", "successor_must_mention_not_mechanism", True),
    "roadmap_citation": ("R-ROADMAP", "restate_not_retire", True),
    "roadmap_related": ("R-ROADREL", "stale_citation", False),
    "size_budget": ("R-SIZE", "ratchet_after_edit", False),
    "glossary_noted": ("R-GLOSS", "drop_from_noted_with_log_row", True),
    "decision_list": ("R-LIST", "review_list", False),
    "anchored_citation": ("R-ANCHOR", "stale_anchor", True),
    "citation": ("R-CITE", "stale_citation", False),
}

_PROBE_EVIDENCE = {"breaks": "probe_breaks", "survives": "probe_survives", "red_before": "probe_red_before"}
_CONFIRMING = frozenset({"structured_key", "probe_breaks"})
APPROVAL_ROUTING_REL = "docs/contracts/approval-routing.yaml"


@dataclasses.dataclass(frozen=True)
class Evidence:
    type: str
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class Hit:
    kind: str
    victim: int
    locator: str
    evidence: tuple[Evidence, ...]
    effect: str
    rule_id: str
    needs_disposition: bool
    gated: bool = False
    probe: str = "not_run"

    @property
    def tier(self) -> str:
        types = {e.type for e in self.evidence}
        if types & _CONFIRMING:
            return "confirmed"
        if "dataflow_literal" in types:
            return "probable"
        return "possible"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "victim": self.victim,
            "locator": self.locator,
            "evidence": [{"type": e.type, "detail": e.detail} for e in self.evidence],
            "tier": self.tier,
            "effect": self.effect,
            "rule_id": self.rule_id,
            "needs_disposition": self.needs_disposition,
            "gated": self.gated,
            "probe": self.probe,
        }

    def sort_key(self) -> tuple[Any, ...]:
        return (self.kind, self.victim, self.locator)


def make_hit(kind: str, victim: int, locator: str, evidence: list[Evidence], gated: bool = False) -> Hit:
    rule_id, effect, needs = RULES[kind]
    return Hit(kind, victim, locator, tuple(evidence), effect, rule_id, needs, gated)


def with_probe(hit: Hit, result: str) -> Hit:
    """The hit after a probe result: breaks keeps the rule effect, survives sets "survives", not_run keeps it."""
    if result not in PROBE_STATES:
        return hit
    extra = _PROBE_EVIDENCE.get(result)
    evidence = hit.evidence + ((Evidence(extra),) if extra else ())
    effect = "survives" if result == "survives" else RULES[hit.kind][1]
    return dataclasses.replace(hit, probe=result, evidence=evidence, effect=effect)


@dataclasses.dataclass
class Inventory:
    victims: tuple[int, ...]
    hits: list[Hit] = dataclasses.field(default_factory=list)
    notes: list[dict[str, str]] = dataclasses.field(default_factory=list)
    probe_info: dict[str, Any] | None = None
    probe_failed: str = ""

    def sorted_hits(self, min_tier: str = "possible") -> list[Hit]:
        floor = TIERS.index(min_tier) if min_tier in TIERS else 0
        return sorted((h for h in self.hits if TIERS.index(h.tier) >= floor), key=Hit.sort_key)

    def as_dict(self, min_tier: str = "possible") -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "stub_template": STUB_TEMPLATE_ID,
            "victims": list(self.victims),
            "hits": [h.as_dict() for h in self.sorted_hits(min_tier)],
            "notes": sorted(self.notes, key=lambda n: (n["surface"], n["reason"])),
            "probe": self.probe_info,
        }


def note(inv_notes: list[dict[str, str]], surface: str, reason: str) -> None:
    item = {"surface": surface, "reason": reason}
    if item not in inv_notes:
        inv_notes.append(item)


def read_text(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_yaml(root: Path, rel: str) -> Any:
    """Parsed YAML at root/rel, or the sentinel MISSING / BAD (never raises)."""
    text = read_text(root, rel)
    if text is None:
        return MISSING
    try:
        return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))  # noqa: S506 - safe loader
    except yaml.YAMLError:
        return BAD


class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


MISSING = _Sentinel("MISSING")
BAD = _Sentinel("BAD")

_HEADING = re.compile(r"^(#{1,6})\s")
_ITEM = re.compile(r"^\s*\d+[a-z]*\.\s")


def _row_file(row: Any) -> str | None:
    if isinstance(row, str):
        return row
    if isinstance(row, dict) and isinstance(row.get("file"), str):
        return row["file"]
    return None


def _mapping(value: Any) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class Gate:
    """The gated set of docs/contracts/approval-routing.yaml, read at the inventoried root (PCC-N9, PCD-N2, PCE-N5)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        data = _mapping(load_yaml(root, APPROVAL_ROUTING_REL))
        gated = _mapping(data.get("gated"))
        self.files = [f for f in _list(gated.get("files")) if isinstance(f, str)]
        self.yaml_keys = _mapping(gated.get("yaml_keys"))
        self.md_sections = _mapping(gated.get("md_sections"))
        self.demoted = {n for n in map(_row_file, _list(_mapping(data.get("demotions")).get("rows"))) if n}
        self._cache: dict[str, list[str] | None] = {}

    def _file_gated(self, rel: str) -> bool:
        if rel in self.demoted:
            return True
        for entry in self.files:
            if entry == rel or fnmatch.fnmatch(rel, entry):
                return True
            if entry.startswith("**/") and (PurePosixPath(rel).name == entry[3:] or fnmatch.fnmatch(rel, entry[3:])):
                return True
        return False

    def _lines(self, rel: str) -> list[str] | None:
        if rel not in self._cache:
            text = read_text(self.root, rel)
            self._cache[rel] = None if text is None else text.splitlines()
        return self._cache[rel]

    def _line_in_section(self, rel: str, line: int) -> bool:
        lines = self._lines(rel)
        if lines is None:
            return False
        for entry in _list(self.md_sections.get(rel)):
            if not isinstance(entry, str):
                continue
            span = self._span(lines, entry)
            if span and span[0] <= line <= span[1]:
                return True
        return False

    @staticmethod
    def _span(lines: list[str], entry: str) -> tuple[int, int] | None:
        for i, text in enumerate(lines):
            if entry.endswith("."):
                if not text.startswith(entry):
                    continue
                end = next(
                    (j for j in range(i + 1, len(lines)) if _ITEM.match(lines[j]) or _HEADING.match(lines[j])), len(lines)
                )
                return i + 1, end
            if text.rstrip() != entry:
                continue
            marks = _HEADING.match(text)
            level = len(marks.group(1)) if marks else 0
            end = len(lines)
            for j in range(i + 1, len(lines)):
                found = _HEADING.match(lines[j])
                if found and len(found.group(1)) <= level:
                    end = j
                    break
            return i + 1, end
        return None

    def is_gated(self, rel: str, key: str | None = None, line: int | None = None) -> bool:
        if self._file_gated(rel):
            return True
        if key is not None and key.split(".")[0] in _list(self.yaml_keys.get(rel)):
            return True
        return line is not None and self._line_in_section(rel, line)
