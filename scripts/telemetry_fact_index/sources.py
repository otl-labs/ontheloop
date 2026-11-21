"""Non-contract fact sources: joins, ref links, lexicon facts, metadata keys and verb serving."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.telemetry_fact_index.model import FactRow, Serving, condition_of, parse_address, tokens

_SERVED_KINDS = ("stored", "unproduced", "payload_key", "metadata_key")
_REGISTRY_REL = "docs/contracts/telemetry-metadata-keys.yaml"
_LEXICON_REL = "docs/contracts/telemetry-lexicon.yaml"
_JOINS_REL = "docs/contracts/_joins.yaml"
_PARSER_VERSIONS_REL = "config/telemetry/parser_versions.yaml"


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _line_of(path: Path, needle: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith(needle):
            return number
    return 0


def add_joins(root: Path, rows: list[FactRow]) -> None:
    """Attach every telemetry join id to the rows of its two end columns; add one row per ref_links entry."""
    path = root / _JOINS_REL
    data = _read_yaml(path) or {}
    by_address = {r.address: r for r in rows}
    for join_id, spec in (data.get("joins") or {}).items():
        for end in (spec.get("from"), spec.get("to")):
            row = by_address.get(str(end))
            if row is not None and join_id not in row.joins:
                row.joins.append(join_id)
    for link_id, spec in (data.get("ref_links") or {}).items():
        rows.append(
            FactRow(
                address=f"ref_link:{link_id}",
                kind="ref_link",
                description=" ".join(str(spec.get("description") or "").split()),
                producers=list(spec.get("producers") or []),
                link_from=str(spec.get("from") or ""),
                link_to=str(spec.get("to") or ""),
                owner=str(spec.get("grammar_owner") or ""),
                source=f"{_JOINS_REL}:{_line_of(path, link_id + ':')}",
            )
        )


def _copy_of(base: FactRow, address: str, kind: str, condition: str) -> FactRow:
    row = FactRow(**base.__dict__)
    row.address, row.kind, row.condition = address, kind, condition
    row.joins, row.producers, row.serving, row.concept_words = list(base.joins), list(base.producers), [], []
    return row


def add_lexicon_facts(root: Path, rows: list[FactRow], lexicon: dict[str, Any] | None) -> None:
    """Attach each term's words to the rows its facts name; a facts address with no row of its own gets one."""
    data = lexicon if lexicon is not None else _read_yaml(root / _LEXICON_REL)
    terms = ((data or {}).get("lexicon") or {}).get("terms") or []
    index = {r.address: r for r in rows}
    for term in terms:
        words = tokens(str(term.get("term", ""))) + [w for aka in (term.get("aka") or []) for w in tokens(str(aka))]
        for address in term.get("facts") or []:
            row = index.get(address)
            if row is None:
                table, column, key, disc = parse_address(address)
                base = index.get(f"{table}.{column}")
                if base is None:
                    continue
                row = _copy_of(base, address, "payload_key" if key else base.kind, condition_of(disc))
                row.key = key
                row.description = base.description
                index[address] = row
                rows.append(row)
            row.concept_words = sorted(set(row.concept_words) | set(words))


def _golden_keys(root: Path) -> dict[tuple[str, str, str, str], None]:
    versions = _read_yaml(root / _PARSER_VERSIONS_REL)
    seen: dict[tuple[str, str, str, str], None] = {}
    for producer, spec in (versions.get("producers") or {}).items():
        index_rel = (spec or {}).get("golden_index")
        if not index_rel:
            continue
        index_path = root / index_rel
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for case in index["cases"]:
            body = json.loads((index_path.parent / case).read_text(encoding="utf-8"))
            for row in body["expected_rows"]:
                if row.get("table") != "telemetry_observations" or row.get("metadata") is None:
                    continue
                meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                for key in meta or {}:
                    seen[(producer, str(key), str(row.get("event_kind") or ""), str(row.get("observation_type") or ""))] = None
    return seen


def _metadata_address(key: str, event_kind: str, observation_type: str) -> tuple[str, str]:
    disc = {k: v for k, v in (("event_kind", event_kind), ("observation_type", observation_type)) if v}
    cond = condition_of(disc)
    return f"telemetry_observations.metadata#{key}" + (f"[{cond}]" if cond else ""), cond


def add_metadata_keys(root: Path, rows: list[FactRow]) -> None:
    """Metadata keys from the golden corpora (undeclared) and, when it exists, the key registry (declared)."""
    meta_base = next((r for r in rows if r.address == "telemetry_observations.metadata"), None)
    found: dict[str, FactRow] = {}

    def row_for(key: str, event_kind: str, observation_type: str) -> FactRow:
        address, cond = _metadata_address(key, event_kind, observation_type)
        row = found.get(address)
        if row is None:
            row = FactRow(address=address, kind="metadata_key", table="telemetry_observations", column="metadata", key=key)
            row.condition, row.declared = cond, False
            row.description = "Metadata key emitted in a producer's golden rows; no registry row declares it."
            row.source = meta_base.source if meta_base else ""
            found[address] = row
        return row

    registry_path = root / _REGISTRY_REL
    if registry_path.is_file():
        for entry in (_read_yaml(registry_path) or {}).get("keys") or []:
            row = row_for(str(entry["key"]), str(entry.get("event_kind") or ""), str(entry.get("observation_type") or ""))
            row.declared = True
            row.description = " ".join(str(entry.get("meaning") or "").split()) or row.description
            row.source = _REGISTRY_REL
            if entry.get("producer") and entry["producer"] not in row.producers:
                row.producers.append(str(entry["producer"]))
    for producer, key, event_kind, observation_type in _golden_keys(root):
        row = row_for(key, event_kind, observation_type)
        if producer not in row.producers:
            row.producers.append(producer)
    rows.extend(found.values())


def _serve(row: FactRow, verb: Any, ambiguous: bool) -> None:
    if all(s.verb != verb.verb for s in row.serving):
        row.serving.append(Serving(verb.verb, verb.agent_facing, verb.masked, ambiguous))


def add_serving(rows: list[FactRow], outputs: dict[str, Any]) -> None:
    """Attach the registered verbs that return each stored fact; add a verb-only row for every unmatched output column."""
    holders: dict[str, set[str]] = {}
    for r in rows:
        if r.table and r.column and r.address == f"{r.table}.{r.column}":
            holders.setdefault(r.column, set()).add(r.table)
    for verb in outputs.values():
        mapped = {stored: out for out, stored in verb.content_map.items()}
        for row in rows:
            if row.kind not in _SERVED_KINDS or row.table not in verb.tables:
                continue
            column = row.column
            if column in verb.row_json_columns.get(row.table, ()):
                _serve(row, verb, False)
            if column in mapped and row.table == "telemetry_transcripts":
                _serve(row, verb, False)
            if column in verb.columns and column not in verb.content_map:
                in_verb = [t for t in verb.tables if t in holders.get(column, ())]
                if len(verb.tables) == 1 or len(in_verb) == 1:
                    _serve(row, verb, False)
                else:
                    _serve(row, verb, True)
    known = set(holders)
    for verb in outputs.values():
        for column in verb.columns:
            if column in known or column in verb.content_map:
                continue
            extra = FactRow(
                address=f"verb:{verb.verb}.{column}",
                kind="verb_only",
                column=column,
                description=f"Output column {column} of reader verb {verb.verb}; no stored column carries it.",
            )
            extra.serving.append(Serving(verb.verb, verb.agent_facing, verb.masked))
            rows.append(extra)


def add_sources(root: Path, rows: list[FactRow], lexicon: dict[str, Any] | None, outputs: dict[str, Any]) -> list[FactRow]:
    add_joins(root, rows)
    add_lexicon_facts(root, rows, lexicon)
    add_metadata_keys(root, rows)
    add_serving(rows, outputs)
    return rows
