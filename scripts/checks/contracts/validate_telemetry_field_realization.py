"""Telemetry field-realization check (rec-4589; T3.20 c7; Decision 210 cl.3, Decision 170 accounting).

Every stored column of the four telemetry contracts is credited, in the contract's `realization` block, to the producer (or the
write boundary) that writes it, or to the owner of its absence. The block is compared with the producer's golden corpus, the
producer registry, the generated field semantics and the roadmap. Rules (each error names table and field):
R1 coverage: each stored field is under at least one produced key or exactly one unproduced owner, never both, once per key,
never write_boundary together with a producer; an unknown or read-derived name is an error. R2 a produced key is write_boundary
or a registered producer. R3 produced.write_boundary equals the generated role-derived columns plus the payload uri column.
R4 every produced field of a producer is non-null in its golden rows or in the corpus normalised_out list. R5 no unproduced
field is non-null in any producer's golden rows. R6 every contract column non-null in a producer's golden rows is credited to
it or to write_boundary. R7 owners are well formed and a criterion owner is an open roadmap criterion. R8 an inline field's
populated_by starts with the phrase its entry implies. Pure function realization_errors(inputs); the wrapper loads the inputs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_agents", "telemetry_transcripts")
WRITE_BOUNDARY = "write_boundary"
_OWNER_RE = re.compile(r"^(rec-[0-9]+|T-?[0-9]+(\.[0-9]+)*[a-z]?:c[0-9]+|held:feedback-loop-gate)$")
_CRITERION_RE = re.compile(r"^(T-?[0-9]+(?:\.[0-9]+)*[a-z]?):(c[0-9]+)$")
_PARSER_VERSIONS_REL = "config/telemetry/parser_versions.yaml"
_FS_REL = "config/lambda/ducklake/field_semantics.yaml"
_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"


def stored_fields(contract: dict[str, Any]) -> list[str]:
    """Contract fields written at ingest: every field whose derivation is not read-timed."""
    return [
        name
        for name, spec in (contract.get("fields") or {}).items()
        if ((spec or {}).get("derivation") or {}).get("timing") != "read"
    ]


def criterion_status(roadmap_text: str, owner: str) -> str | None:
    """Status of `T<item>:<criterion>` by targeted text lookup (Decision 208: no full roadmap load); None if absent."""
    match = _CRITERION_RE.match(owner)
    if match is None:
        return None
    item, crit = match.groups()
    start = re.search(rf"^  - id: {re.escape(item)}$", roadmap_text, re.MULTILINE)
    if start is None:
        return None
    rest = roadmap_text[start.end() :]
    nxt = re.search(r"^  - id: ", rest, re.MULTILINE)
    block = rest[: nxt.start()] if nxt else rest
    crit_start = re.search(rf"^\s+- id: {crit}$", block, re.MULTILINE)
    if crit_start is None:
        return None
    tail = block[crit_start.end() :]
    following = re.search(r"^\s+- id: ", tail, re.MULTILINE)
    crit_block = tail[: following.start()] if following else tail
    status = re.search(r"^\s+status: (\S+)\s*$", crit_block, re.MULTILINE)
    return status.group(1) if status else None


def _populated_prefix(produced_keys: list[str], registry_order: list[str]) -> str | None:
    if produced_keys == [WRITE_BOUNDARY]:
        return "the write boundary"
    producers = [p for p in registry_order if p in produced_keys]
    if not producers or WRITE_BOUNDARY in produced_keys:
        return None
    if len(producers) == 1:
        return f"the {producers[0]} producer"
    return f"the {', '.join(producers[:-1])} and {producers[-1]} producers"


def _corpus_columns(rows: list[dict[str, Any]]) -> set[str]:
    return {k for row in rows for k, v in row.items() if v is not None}


def _placements(table: str, block: dict[str, Any], inputs: dict[str, Any], errors: list[str]) -> tuple[dict, dict]:
    produced: dict[str, list[str]] = block.get("produced") or {}
    unproduced: dict[str, list[str]] = block.get("unproduced") or {}
    placed: dict[str, list[str]] = {}
    for key, names in produced.items():
        if key != WRITE_BOUNDARY and key not in inputs["producers"]:
            errors.append(f"{table}: produced key {key!r} is neither write_boundary nor a registered producer (R2)")
        if len(names) != len(set(names)):
            errors.append(f"{table}: produced.{key} lists a field more than once (R1)")
        for name in names:
            placed.setdefault(name, []).append(key)
    owners: dict[str, list[str]] = {}
    for owner, names in unproduced.items():
        if len(names) != len(set(names)):
            errors.append(f"{table}: unproduced.{owner} lists a field more than once (R1)")
        for name in names:
            owners.setdefault(name, []).append(owner)
        if not _OWNER_RE.match(owner):
            errors.append(f"{table}: unproduced owner {owner!r} is malformed (R7)")
        elif _CRITERION_RE.match(owner) and criterion_status(inputs["roadmap_text"], owner) != "open":
            errors.append(
                f"{table}: unproduced owner {owner} is not an open roadmap criterion "
                "(criterion met: move these columns to produced, or name a new owner) (R7)"
            )
    return placed, owners


def _coverage_errors(table: str, contract: dict[str, Any], placed: dict, owners: dict) -> list[str]:
    errors: list[str] = []
    stored = stored_fields(contract)
    all_fields = set(contract.get("fields") or {})
    for name in sorted(set(placed) | set(owners)):
        if name not in all_fields:
            errors.append(f"{table}.{name}: listed in realization but is not a contract field (R1)")
        elif name not in stored:
            errors.append(f"{table}.{name}: read-derived field listed in realization (R1)")
    for name in stored:
        keys, owned_by = placed.get(name, []), owners.get(name, [])
        if not keys and not owned_by:
            errors.append(f"{table}.{name}: stored field is in neither produced nor unproduced (R1)")
        if keys and owned_by:
            errors.append(f"{table}.{name}: stored field is both produced and unproduced (R1)")
        if len(owned_by) > 1:
            errors.append(f"{table}.{name}: stored field has more than one unproduced owner {owned_by} (R1)")
        if WRITE_BOUNDARY in keys and len(keys) > 1:
            errors.append(f"{table}.{name}: write_boundary is credited together with a producer (R1)")
    return errors


def _boundary_errors(table: str, produced: dict[str, list[str]], inputs: dict[str, Any]) -> list[str]:
    fs_entry = inputs["field_semantics"]["ops_tables"][table]
    derived = {c for c, v in fs_entry["columns"].items() if v.get("role") != "input"}
    payload_uri = ((fs_entry.get("table_rules") or {}).get("payload") or {}).get("uri")
    expected = derived | ({payload_uri} if payload_uri else set())
    got = set(produced.get(WRITE_BOUNDARY, []))
    if got == expected:
        return []
    missing, extra = sorted(expected - got), sorted(got - expected)
    return [f"{table}: write_boundary set differs from the writer-derived columns: missing {missing}, extra {extra} (R3)"]


def _corpus_errors(table: str, contract: dict[str, Any], produced: dict, owners: dict, inputs: dict) -> list[str]:
    errors: list[str] = []
    contract_cols = set(contract.get("fields") or {})
    for producer in inputs["producers"]:
        corpus = inputs["golden"].get(producer)
        credited = set(produced.get(producer, []))
        if corpus is None:
            if credited:
                errors.append(f"{table}: producer {producer} credits fields but registers no golden_index (R4)")
            continue
        non_null = _corpus_columns(corpus["rows"].get(table, []))
        for name in sorted(credited - non_null - set(corpus["normalised_out"])):
            errors.append(f"{table}.{name}: credited to {producer} but never non-null in its golden rows (R4)")
        for name in sorted(non_null & set(owners)):
            errors.append(f"{table}.{name}: marked unproduced but {producer}'s golden rows emit it (R5)")
        covered = credited | set(produced.get(WRITE_BOUNDARY, []))
        for name in sorted((non_null & contract_cols) - covered):
            errors.append(f"{table}.{name}: {producer}'s golden rows emit it but the block does not credit it (R6)")
    return errors


def _populated_errors(table: str, contract: dict[str, Any], placed: dict, owners: dict, inputs: dict) -> list[str]:
    errors: list[str] = []
    fields = contract.get("fields") or {}
    for name in stored_fields(contract):
        populated = (fields[name] or {}).get("populated_by")
        if not isinstance(populated, str):
            continue
        want: str | None
        if owners.get(name):
            want = f"unproduced: {owners[name][0]}"
        else:
            want = _populated_prefix(placed.get(name, []), list(inputs["producers"]))
        if want is not None and not " ".join(populated.split()).startswith(want):
            errors.append(f"{table}.{name}: populated_by must start with {want!r} (R8)")
    return errors


def _table_errors(table: str, inputs: dict[str, Any]) -> tuple[list[str], int]:
    contract = inputs["contracts"][table]
    block = contract.get("realization")
    if not block:
        return [f"{table}: no realization block"], 0
    errors: list[str] = []
    placed, owners = _placements(table, block, inputs, errors)
    produced = block.get("produced") or {}
    errors += _coverage_errors(table, contract, placed, owners)
    errors += _boundary_errors(table, produced, inputs)
    errors += _corpus_errors(table, contract, produced, owners, inputs)
    errors += _populated_errors(table, contract, placed, owners, inputs)
    return errors, len(stored_fields(contract))


def realization_errors(inputs: dict[str, Any]) -> tuple[list[str], int]:
    """Errors and the number of stored fields examined, over injected parsed inputs."""
    errors: list[str] = []
    examined = 0
    for table in TABLES:
        table_errors, count = _table_errors(table, inputs)
        errors.extend(table_errors)
        examined += count
    return errors, examined


def load_inputs(root: Path) -> dict[str, Any]:
    contracts = {t: yaml.safe_load((root / "docs" / "contracts" / f"{t}.yaml").read_text(encoding="utf-8")) for t in TABLES}
    versions = yaml.safe_load((root / _PARSER_VERSIONS_REL).read_text(encoding="utf-8"))
    producers = versions["producers"]
    golden: dict[str, Any] = {}
    for producer, spec in producers.items():
        index_rel = (spec or {}).get("golden_index")
        if not index_rel:
            continue
        index_path = root / index_rel
        index = json.loads(index_path.read_text(encoding="utf-8"))
        rows: dict[str, list[dict[str, Any]]] = {}
        for case in index["cases"]:
            body = json.loads((index_path.parent / case).read_text(encoding="utf-8"))
            for row in body["expected_rows"]:
                rows.setdefault(row["table"], []).append(row)
        golden[producer] = {"normalised_out": index.get("normalised_out") or [], "rows": rows}
    return {
        "contracts": contracts,
        "producers": list(producers),
        "golden": golden,
        "field_semantics": yaml.safe_load((root / _FS_REL).read_text(encoding="utf-8")),
        "roadmap_text": (root / _ROADMAP_REL).read_text(encoding="utf-8"),
    }


@registry.register("validate_telemetry_field_realization", owner="platform")
def validate_telemetry_field_realization(failed: list[str], *, root: Path | None = None) -> None:
    """Fail when a telemetry contract's realization block disagrees with the producer corpus, writer or roadmap."""
    print("\n=== Telemetry field realization ===")
    root = root if root is not None else _common.ROOT
    try:
        errors, examined = realization_errors(load_inputs(root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        errors, examined = [f"inputs unreadable: {exc!r}"], 0
    registry.examined(examined, unit="fields")
    for err in errors:
        print(f"  FAIL: {err}")
        failed.append(f"Telemetry field realization: {err}")
    if not errors:
        print(
            f"  PASS: {examined} stored fields across {len(TABLES)} telemetry contracts are credited to a writer or an owner."
        )
