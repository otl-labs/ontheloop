"""Exact structured surfaces of the coupling inventory: one kind per surface, each read by key."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts import decisions_md
from scripts.checks._decision_chain import resolve_live
from scripts.checks.decisions.consolidation_inventory._model import (
    BAD,
    MISSING,
    Evidence,
    Gate,
    Hit,
    load_yaml,
    make_hit,
    note,
    read_text,
)
from scripts.checks.decisions.consolidation_inventory._pytest_scan import SOURCE_BASENAMES

RECORDS_REL = "config/agent/verification_registry/entries"
NORTH_STAR_REL = "docs/contracts/north-star.yaml"
WAIVERS_REL = "config/decision_supersession_waivers.yaml"
CENSUS_REL = "audits/standards-cleanup-census.yaml"
MANIFESTS_REL = "docs/decision-migration/consolidations"
INDEX_REL = "docs/decisions-index.json"
DECISIONS_REL = "docs/DECISIONS.md"
ARCHIVE_REL = "docs/DECISIONS_ARCHIVE.md"
ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
GLOSSARY_REL = "docs/contracts/glossary.yaml"
BUDGET_RELS = ("config/prose_budgets.yaml", "config/structural_size_budgets.yaml")
MARKER_DIRS = ("config", "scripts", ".github", "src", "terraform", "docs/contracts")
MARKER_SUFFIXES = frozenset({".yaml", ".yml", ".py", ".sh", ".tf", ".toml", ".json"})
_ROADMAP_DONE = frozenset({"complete", "retired"})
_MARKER = re.compile(r"#\s*([A-Za-z0-9_-]+):\s*dec-0*(\d+)")
_SKIP = frozenset({"__pycache__", ".venv", "node_modules", ".git"})
_KEY_DECISION_SKIPPED = frozenset({"noted_decisions"})
_HELD_DECISION = re.compile(r"Decision (\d+)")


def _ints(value: Any) -> set[int]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, list):
        return {v for v in value if isinstance(v, int) and not isinstance(v, bool)}
    if isinstance(value, str):
        return {int(n) for n in re.findall(r"\d+", value)}
    return set()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _mentions(text: str, victim: int) -> bool:
    return bool(re.search(rf"(?:Decisions?[\s-]+(?:[\d,\s/&+]|and)*?|dec-0*)(?<!\d){victim}(?!\d)", text))


def _yaml(root: Path, rel: str, notes: list[dict[str, str]], must_exist: bool = True) -> Any:
    data = load_yaml(root, rel)
    if data is MISSING:
        if must_exist:
            note(notes, rel, "surface file is missing")
        return None
    if data is BAD:
        note(notes, rel, "surface file does not parse")
        return None
    return data


def _hit(kind: str, victim: int, rel: str, key: str, gate: Gate, evidence: str = "structured_key", detail: str = "") -> Hit:
    return make_hit(kind, victim, f"{rel}:{key}", [Evidence(evidence, detail or key)], gate.is_gated(rel, key.split(".")[0]))


def graduated_records(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    hits: list[Hit] = []
    base = root / RECORDS_REL
    if not base.is_dir():
        return hits
    for path in sorted(base.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        row = _yaml(root, rel, notes)
        if not isinstance(row, dict):
            continue
        spec_text = _strings(row.get("check_spec"))
        target = row.get("guard_target")
        reads_log = any(b in s for s in spec_text + ([target] if isinstance(target, str) else []) for b in SOURCE_BASENAMES)
        if not reads_log:
            continue
        check_id = str(row.get("check_id", path.stem))
        for victim in victims:
            bare = re.compile(rf"(?<![\w.-]){victim}(?![\w.-])")
            if any(bare.search(s) or _mentions(s, victim) for s in spec_text):
                hits.append(
                    make_hit(
                        "graduated_record",
                        victim,
                        f"{rel}:{check_id}",
                        [Evidence("cooccurrence", "check_spec reads a decisions source and holds the victim")],
                        gate.is_gated(rel),
                    )
                )
    return hits


def north_star(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    data = _yaml(root, NORTH_STAR_REL, notes)
    if not isinstance(data, dict):
        return []
    hits: list[Hit] = []
    owners: list[tuple[str, Any]] = []
    mission = data.get("mission")
    if isinstance(mission, dict):
        owners.append(("mission", mission))
    for item in data.get("principles") or []:
        if isinstance(item, dict):
            owners.append((str(item.get("id", "?")), item))
    for name, owner in owners:
        for victim in victims:
            if victim in _ints(owner.get("carried_by")):
                hits.append(_hit("north_star_carrier", victim, NORTH_STAR_REL, f"{name}.carried_by", gate))
            if _mentions(" ".join(_strings(owner.get("ratified_via"))), victim):
                hits.append(_hit("north_star_carrier", victim, NORTH_STAR_REL, f"{name}.ratified_via", gate))
    contract: dict[str, Any] = data["contract"] if isinstance(data.get("contract"), dict) else {}
    for victim in victims:
        if _mentions(" ".join(_strings(contract.get("ratified_via"))), victim):
            hits.append(_hit("north_star_carrier", victim, NORTH_STAR_REL, "contract.ratified_via", gate))
    return hits


def waivers(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    data = _yaml(root, WAIVERS_REL, notes, must_exist=False)
    hits: list[Hit] = []
    rows = data.get("waivers") if isinstance(data, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        for victim in victims:
            if victim in (row.get("superseder"), row.get("victim")):
                hits.append(
                    _hit("supersession_waiver", victim, WAIVERS_REL, f"{row.get('superseder')}->{row.get('victim')}", gate)
                )
    return hits


def census(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    data = _yaml(root, CENSUS_REL, notes, must_exist=False)
    if not isinstance(data, dict):
        return []
    hits: list[Hit] = []
    for group in ("rows", "exceptions"):
        for row in data.get(group) or []:
            if not isinstance(row, dict):
                continue
            named = _ints(row.get("root")) | _ints(row.get("victim"))
            for victim in victims:
                if victim in named:
                    hits.append(_hit("census_row", victim, CENSUS_REL, str(row.get("id", "?")), gate))
    return hits


def _chain(root: Path) -> tuple[dict[int, str], dict[int, dict]]:
    entries: dict[int, str] = {}
    for rel in (DECISIONS_REL, ARCHIVE_REL):
        for match, block in decisions_md.iter_decision_sections(read_text(root, rel) or ""):
            entries.setdefault(int(match.group(1)), block)
    rows = {r["number"]: r for r in _index_rows(root, []) if isinstance(r.get("number"), int)}
    return entries, rows


def _reaching(number: int, victim: int, chain: tuple[dict[int, str], dict[int, dict]]) -> str | None:
    """The resolution path text when number is the victim or its Superseded by chain reaches it, else None."""
    path = resolve_live(chain[0], number, chain[1])[1]
    if victim not in path:
        return None
    return f" (-> {' -> '.join(map(str, path[1:]))})" if len(path) > 1 else ""


def manifests(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    base = root / MANIFESTS_REL
    hits: list[Hit] = []
    if not base.is_dir():
        return hits
    chain = _chain(root)
    for path in sorted(base.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        data = _yaml(root, rel, notes)
        if not isinstance(data, dict):
            continue
        for victim in victims:
            if victim in _ints(data.get("supersedes")):
                hits.append(_hit("manifest_reference", victim, rel, "supersedes", gate))
            for number in sorted(_ints(data.get("carried_forward"))):
                if (via := _reaching(number, victim, chain)) is not None:
                    detail = f"carried_forward Decision {number}{via}"
                    hits.append(_hit("manifest_reference", victim, rel, "carried_forward", gate, detail=detail))
            for unit in data.get("units") or []:
                hits.extend(_held_hits(unit, victim, rel, gate, chain))
    return hits


def _held_hits(unit: Any, victim: int, rel: str, gate: Gate, chain: tuple[dict[int, str], dict[int, dict]]) -> list[Hit]:
    if not isinstance(unit, dict):
        return []
    found: list[Hit] = []
    for held in _strings(unit.get("held")):
        named = _HELD_DECISION.fullmatch(held.strip())
        via = _reaching(int(named.group(1)), victim, chain) if named else ("" if _mentions(held, victim) else None)
        if via is not None:
            key, detail = f"units.{unit.get('id', '?')}.held", f"held {held}{via} token {unit.get('token')!r}"
            found.append(_hit("manifest_held_token", victim, rel, key, gate, detail=detail))
    return found


def chain_edges(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    rows = _index_rows(root, notes)
    hits: list[Hit] = []
    for row in rows:
        number = row.get("number")
        if not row.get("live") or number in victims:
            continue
        for key in ("amends", "supersedes"):
            for victim in victims:
                if victim in _ints(row.get(key)):
                    hits.append(
                        _hit(
                            "chain_edge",
                            victim,
                            INDEX_REL,
                            f"{number}.{key}",
                            gate,
                            detail=f"Decision {number} {key} {victim}",
                        )
                    )
    return hits


def _index_rows(root: Path, notes: list[dict[str, str]]) -> list[dict]:
    text = read_text(root, INDEX_REL)
    if text is None:
        note(notes, INDEX_REL, "surface file is missing")
        return []
    try:
        rows = json.loads(text)["decisions"]
    except (ValueError, KeyError, TypeError):
        note(notes, INDEX_REL, "surface file does not parse")
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def delegated_keys(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    text = read_text(root, DECISIONS_REL)
    if text is None:
        note(notes, DECISIONS_REL, "surface file is missing")
        return []
    hits: list[Hit] = []
    for match, block in decisions_md.iter_decision_sections(text):
        number = int(match.group(1))
        if number not in victims:
            continue
        envelope = decisions_md.extract_entry_envelope(block)
        for item in (envelope or {}).get("delegates") or []:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                hits.append(_hit("delegated_key", number, DECISIONS_REL, f"{number}.delegates:{item['key']}", gate))
    return hits


def markers(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    hits: list[Hit] = []
    for directory in MARKER_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in MARKER_SUFFIXES or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if set(path.relative_to(root).parts) & _SKIP:
                continue
            text = read_text(root, rel) or ""
            for lineno, line in enumerate(text.splitlines(), 1):
                found = _MARKER.search(line)
                if found and int(found.group(2)) in victims:
                    hits.append(
                        make_hit(
                            "authorizing_marker",
                            int(found.group(2)),
                            f"{rel}:{lineno}",
                            [Evidence("structured_key", f"{found.group(1)}: dec-{found.group(2)}")],
                            gate.is_gated(rel, None, lineno),
                        )
                    )
    return hits


def roadmap(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    data = _yaml(root, ROADMAP_REL, notes, must_exist=False)
    if not isinstance(data, dict):
        return []
    hits: list[Hit] = []
    for item in data.get("tier_items") or []:
        if not isinstance(item, dict) or item.get("status") in _ROADMAP_DONE:
            continue
        name = str(item.get("id", "?"))
        criteria = " ".join(_strings(item.get("exit_criteria")))
        for victim in victims:
            if _mentions(criteria, victim):
                hits.append(_hit("roadmap_citation", victim, ROADMAP_REL, f"{name}.exit_criteria", gate))
            if victim in _ints(item.get("related_decisions")):
                hits.append(_hit("roadmap_related", victim, ROADMAP_REL, f"{name}.related_decisions", gate))
    return hits


def glossary(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    data = _yaml(root, GLOSSARY_REL, notes, must_exist=False)
    read_as = data.get("read_as") if isinstance(data, dict) else None
    noted = _ints(read_as.get("noted_decisions")) if isinstance(read_as, dict) else set()
    return [_hit("glossary_noted", v, GLOSSARY_REL, "read_as.noted_decisions", gate) for v in victims if v in noted]


def _walk(value: Any, path: str):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key), f"{path}.{key}" if path else str(key), inner
            yield from _walk(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk(inner, f"{path}[{index}]")


def decision_lists(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    hits: list[Hit] = []
    files = sorted((root / "docs/contracts").glob("*.yaml")) + sorted((root / "config").rglob("*.yaml"))
    for path in files:
        rel = path.relative_to(root).as_posix()
        if set(path.relative_to(root).parts) & _SKIP or "verification_registry" in rel:
            continue
        data = _yaml(root, rel, notes)
        if data is None:
            continue
        for key, dotted, value in _walk(data, ""):
            if "decision" not in key.lower() or key in _KEY_DECISION_SKIPPED:
                continue
            if not isinstance(value, (int, list)) or isinstance(value, bool):
                continue
            for victim in victims:
                if victim in _ints(value):
                    hits.append(_hit("decision_list", victim, rel, dotted, gate))
    return hits


def size_budgets(root: Path, hits: list[Hit], gate: Gate) -> list[Hit]:
    """size_budget rows for each file a hit's effect edits (no DECISIONS.md line: Decision 179 retired its ceilings)."""
    edited: dict[int, set[str]] = {}
    for hit in hits:
        if hit.kind in {"size_budget", "chain_edge"}:
            continue
        rel = hit.locator.split(":")[0]
        edited.setdefault(hit.victim, set()).add(rel)
    found: list[Hit] = []
    for budget in BUDGET_RELS:
        text = read_text(root, budget)
        if text is None:
            continue
        lines = text.splitlines()
        for victim, files in sorted(edited.items()):
            for rel in sorted(files):
                for lineno, line in enumerate(lines, 1):
                    if line.strip().strip('"').startswith(f"{rel}:") or line.strip().startswith(f'"{rel}":'):
                        found.append(_hit("size_budget", victim, budget, rel, gate, detail=f"{rel} budget row, line {lineno}"))
                        break
    return found


SURFACES = (
    graduated_records,
    north_star,
    waivers,
    census,
    manifests,
    chain_edges,
    delegated_keys,
    markers,
    roadmap,
    glossary,
    decision_lists,
)
