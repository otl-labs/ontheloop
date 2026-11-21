"""Telemetry fact lookup: find a stored or derivable fact by meaning before proposing a new field.

Usage: bin/venv-python -m scripts.session.telemetry_facts <term> [<term> ...]
Facts are projected at call time from the contracts, joins, lexicon, golden corpora and reader verb outputs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.telemetry_fact_index import FactRow, project, tokens

ROOT = Path(__file__).resolve().parents[2]
MAX_ROWS = 10
_KIND_RANK = {
    "stored": 0,
    "unproduced": 1,
    "read_derived": 2,
    "payload_key": 3,
    "metadata_key": 4,
    "ref_link": 5,
    "verb_only": 6,
}
_CONCEPT, _ADDRESS, _DESCRIPTION = 0, 1, 2
_NO_MATCH = "no match is not proof of absence: answer data-modeling-standard.yaml design_time_walk step 0 from its sources"


def _aka_sets(lexicon: dict[str, Any]) -> list[set[str]]:
    sets: list[set[str]] = []
    for term in ((lexicon or {}).get("lexicon") or {}).get("terms") or []:
        if not term.get("aka"):
            continue
        words = set(tokens(str(term.get("term", ""))))
        for aka in term["aka"]:
            words.update(tokens(str(aka)))
        sets.append(words)
    return sets


def expand(query_terms: list[str], lexicon: dict[str, Any]) -> list[set[str]]:
    sets = _aka_sets(lexicon)
    out: list[set[str]] = []
    for word in query_terms:
        group = {word}
        for aka in sets:
            if word in aka:
                group |= aka
        out.append(group)
    return out


def _row_tokens(row: FactRow) -> tuple[set[str], set[str], set[str]]:
    concept = set(row.concept_words)
    address = set(tokens(row.address))
    other = set(tokens(row.description)) | set(tokens(row.populated_by)) | set(tokens(row.condition.replace("|", " ")))
    other |= set(tokens(row.kind))
    return concept, address, other


def _source(row: FactRow, groups: list[set[str]]) -> int | None:
    concept, address, other = _row_tokens(row)
    worst = _CONCEPT
    for group in groups:
        if group & concept:
            rank = _CONCEPT
        elif group & address:
            rank = _ADDRESS
        elif group & other:
            rank = _DESCRIPTION
        else:
            return None
        worst = max(worst, rank)
    return worst


def search(rows: list[FactRow], query_terms: list[str], lexicon: dict[str, Any]) -> tuple[list[FactRow], list[set[str]]]:
    words = [w for term in query_terms for w in tokens(term)]
    groups = expand(words, lexicon)
    ranked: list[tuple[int, int, str, FactRow]] = []
    for row in rows:
        source = _source(row, groups) if groups else None
        if source is not None:
            ranked.append((source, _KIND_RANK.get(row.kind, 9), row.address, row))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked], groups


def _serving_text(row: FactRow) -> str:
    if not row.serving:
        return "unserved"
    parts = []
    for s in row.serving:
        label = f"{s.verb} (registered, never deployed{', masked' if s.masked else ''}{', ambiguous' if s.ambiguous else ''}"
        parts.append(label + f", agent_facing={str(s.agent_facing).lower()})")
    return "; ".join(parts)


def render(row: FactRow) -> str:
    head = f"{row.address}  kind={row.kind}"
    if row.type:
        head += f" type={row.type}"
    lines = [head]
    if row.condition and "[" not in row.address:
        lines.append(f"  when: {row.condition}")
    if row.description:
        lines.append(f"  {row.description}")
    owner = f"unproduced owner {row.owner}" if row.kind == "unproduced" else row.owner
    if row.producers:
        lines.append(f"  producers: {', '.join(row.producers)}")
    if owner:
        lines.append(f"  owner: {owner}")
    if row.kind == "metadata_key":
        lines.append(f"  declared: {'yes' if row.declared else 'no (undeclared)'}")
    if row.kind == "ref_link":
        lines.append(f"  from: {row.link_from}\n  to: {row.link_to}")
    if row.joins:
        lines.append(f"  joins: {', '.join(row.joins)}")
    if row.kind != "ref_link":
        lines.append(f"  served by: {_serving_text(row)}")
    if row.source:
        lines.append(f"  source: {row.source}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = root or ROOT
    lexicon = yaml.safe_load((root / "docs" / "contracts" / "telemetry-lexicon.yaml").read_text(encoding="utf-8"))
    rows = project(root, lexicon=lexicon)
    found, groups = search(rows, args, lexicon)
    if not found:
        shown = " ".join("{" + ",".join(sorted(g)) + "}" for g in groups) or "(no words)"
        print(f"expanded words: {shown}")
        print(_NO_MATCH)
        return 1
    for row in found[:MAX_ROWS]:
        print(render(row))
    print(f"{min(MAX_ROWS, len(found))} of {len(found)} shown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
