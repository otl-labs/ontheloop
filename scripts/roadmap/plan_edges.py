"""THE single plan-edge extractor (T4.32 c4): a plan's decision edges, introduced names and roadmap edges.

Reads typed fields only (cites, amends, introduces, closes_criteria and the id-shaped first token of phase); it never
reads context, purposes, steps, constraints, acceptance, the verification plan or any other free text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.roadmap import platform_roadmap
from scripts.roadmap.plan_document import _DECISION_TOKEN_RE

_ITEM_ID_RE = re.compile(r"^T-?[0-9]+(\.[0-9]+)+[a-z]?$")
_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"


class PlanEdgesError(Exception):
    """A plan or roadmap input the extractor cannot read; the message names the field or file."""


@dataclass(frozen=True)
class PlanEdges:
    cites: tuple[str, ...]
    amends: tuple[str, ...]
    decision_numbers: tuple[int, ...]
    introduces: tuple[str, ...]
    items: tuple[str, ...]
    edges: Mapping[str, Mapping[str, Any] | None]


def _strings(plan: Mapping[str, Any], field: str) -> list[str]:
    value = plan.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise PlanEdgesError(f"{field} must be a list of strings")
    return list(value)


def _decision_tokens(field: str, tokens: Iterable[str]) -> list[str]:
    for token in tokens:
        if not _DECISION_TOKEN_RE.fullmatch(token):
            raise PlanEdgesError(f"{field} entry {token!r} is not a valid 'dec:<number>[#<clause-id>]' token")
    return sorted(set(tokens))


def _decision_number(token: str) -> int:
    return int(token[len("dec:") :].split("#", 1)[0])


def _item_ids(plan: Mapping[str, Any]) -> list[str]:
    ids: set[str] = set()
    for token in _strings(plan, "closes_criteria"):
        if token.count(":") != 1:
            raise PlanEdgesError(f"closes_criteria entry {token!r} must hold exactly one ':'")
        ids.add(token.split(":", 1)[0])
    phase = plan.get("phase")
    if isinstance(phase, str) and phase.split():
        first = phase.split()[0].rstrip(",;:")
        if _ITEM_ID_RE.fullmatch(first):
            ids.add(first)
    return sorted(ids)


def extract_edges(
    plan: Mapping[str, Any], roadmap: Mapping[str, Mapping[str, Any]], extra_cites: Iterable[str] = ()
) -> PlanEdges:
    cites = _decision_tokens("cites", [*_strings(plan, "cites"), *extra_cites])
    amends = _decision_tokens("amends", _strings(plan, "amends"))
    items = _item_ids(plan)
    edges: dict[str, Mapping[str, Any] | None] = {}
    for item in items:
        row = roadmap.get(item)
        edges[item] = (
            None
            if row is None
            else {
                "depends_on": sorted(set(row.get("depends_on", []))),
                "start_edges": sorted([ref, once] for ref, once in row.get("start_edges", [])),
            }
        )
    return PlanEdges(
        cites=tuple(cites),
        amends=tuple(amends),
        decision_numbers=tuple(sorted({_decision_number(t) for t in (*cites, *amends)})),
        introduces=tuple(sorted(set(_strings(plan, "introduces")))),
        items=tuple(items),
        edges=edges,
    )


def load_roadmap(root: Path) -> Any:
    path = Path(root) / _ROADMAP_REL
    try:
        return platform_roadmap.load(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise PlanEdgesError(f"cannot read roadmap {path}: {exc}") from exc


def roadmap_edges(doc: Any) -> dict[str, dict[str, Any]]:
    return {
        item.id: {"depends_on": list(item.depends_on), "start_edges": [[e.ref, e.once] for e in item.start_edges]}
        for item in doc.tier_items
    }
