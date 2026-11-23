"""Telemetry field-realization check (rec-4589; T3.20 c7; Decision 210 cl.3, Decision 170 accounting).

Every stored column of the four telemetry contracts is credited, in the contract's `realization` block, to the producer (or the
write boundary) that writes it, or to the owner of its absence. The block is compared with the producer's golden corpus, the
producer registry, the generated field semantics and the roadmap. Rules (each error names table and field):
R1 coverage: each stored field is under at least one produced key, exactly one unproduced owner or exactly one retired owner
(a third placement for columns no producer ever wrote), never two placements, once per key, never write_boundary together with
a producer; an unknown or read-derived name is an error. R2 a produced key is write_boundary or a registered producer.
R3 produced.write_boundary equals the generated role-derived columns plus the payload uri column. R4 every produced field of a
producer is non-null in its golden rows or in the corpus normalised_out list. R5 no unproduced or retired field is non-null in
any producer's golden rows. R6 every contract column non-null in a producer's golden rows is credited to it or to
write_boundary. R7 owners are well formed (a retired owner is a rec id; the hold label is refused), a criterion owner is an
open roadmap criterion and its text names each field it owns. R8 an inline field's populated_by starts with the phrase its
entry implies (a retired field's starts "retired: <owner>" at a word boundary). Pure function realization_errors(inputs); the
wrapper loads the inputs. Pure owner_recs and owner_errors serve the owners command of scripts.telemetry_verify, which checks
each rec owner is open and names its fields.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_agents", "telemetry_transcripts")
WRITE_BOUNDARY = "write_boundary"
_OWNER_RE = re.compile(r"^(rec-[0-9]+|T-?[0-9]+(\.[0-9]+)*[a-z]?:c[0-9]+)$")
_REC_RE = re.compile(r"^rec-[0-9]+$")
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


def _criterion_block(roadmap_text: str, owner: str) -> str | None:
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
    return tail[: following.start()] if following else tail


def criterion_status(roadmap_text: str, owner: str) -> str | None:
    """Status of `T<item>:<criterion>` by targeted text lookup (Decision 208: no full roadmap load); None if absent."""
    crit_block = _criterion_block(roadmap_text, owner)
    if crit_block is None:
        return None
    status = re.search(r"^\s+status: (\S+)\s*$", crit_block, re.MULTILINE)
    return status.group(1) if status else None


def criterion_text(roadmap_text: str, owner: str) -> str | None:
    """The `text` value of one criterion (its block yaml-loaded, so folded and quoted text both read); None if absent."""
    crit_block = _criterion_block(roadmap_text, owner)
    if crit_block is None:
        return None
    try:
        loaded = yaml.safe_load(textwrap.dedent(crit_block))
    except yaml.YAMLError:
        return None
    text = loaded.get("text") if isinstance(loaded, dict) else None
    return text if isinstance(text, str) else None


def names_field(text: str, name: str) -> bool:
    """Whole-word match: a hyphen or word character on either side defeats it (provider never matches inference-provider)."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def _owner_boundary(populated: str, prefix: str) -> bool:
    text = " ".join(populated.split())
    return text == prefix or text.startswith(prefix + " ")


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


def _produced_placements(table: str, produced: dict[str, list[str]], inputs: dict[str, Any], errors: list[str]) -> dict:
    placed: dict[str, list[str]] = {}
    for key, names in produced.items():
        if key != WRITE_BOUNDARY and key not in inputs["producers"]:
            errors.append(f"{table}: produced key {key!r} is neither write_boundary nor a registered producer (R2)")
        if len(names) != len(set(names)):
            errors.append(f"{table}: produced.{key} lists a field more than once (R1)")
        for name in names:
            placed.setdefault(name, []).append(key)
    return placed


def _criterion_owner_errors(table: str, owner: str, names: list[str], roadmap_text: str) -> list[str]:
    errors: list[str] = []
    if criterion_status(roadmap_text, owner) != "open":
        errors.append(
            f"{table}: unproduced owner {owner} is not an open roadmap criterion "
            "(criterion met: move these columns to produced, or name a new owner) (R7)"
        )
    text = criterion_text(roadmap_text, owner) or ""
    errors.extend(
        f"{table}.{name}: owner {owner}'s criterion text does not name the field (R7)"
        for name in names
        if not names_field(text, name)
    )
    return errors


def _unproduced_placements(table: str, unproduced: dict[str, list[str]], inputs: dict[str, Any], errors: list[str]) -> dict:
    owners: dict[str, list[str]] = {}
    for owner, names in unproduced.items():
        if len(names) != len(set(names)):
            errors.append(f"{table}: unproduced.{owner} lists a field more than once (R1)")
        for name in names:
            owners.setdefault(name, []).append(owner)
        if not _OWNER_RE.match(owner):
            errors.append(f"{table}: unproduced owner {owner!r} is malformed (R7)")
        elif _CRITERION_RE.match(owner):
            errors.extend(_criterion_owner_errors(table, owner, names, inputs["roadmap_text"]))
    return owners


def _retired_placements(table: str, retired: dict[str, list[str]], errors: list[str]) -> dict:
    gone: dict[str, list[str]] = {}
    for owner, names in retired.items():
        if len(names) != len(set(names)):
            errors.append(f"{table}: retired.{owner} lists a field more than once (R1)")
        for name in names:
            gone.setdefault(name, []).append(owner)
        if not _REC_RE.match(owner):
            errors.append(f"{table}: retired owner {owner!r} is malformed (R7)")
    return gone


def _placements(table: str, block: dict[str, Any], inputs: dict[str, Any], errors: list[str]) -> tuple[dict, dict, dict]:
    placed = _produced_placements(table, block.get("produced") or {}, inputs, errors)
    owners = _unproduced_placements(table, block.get("unproduced") or {}, inputs, errors)
    return placed, owners, _retired_placements(table, block.get("retired") or {}, errors)


def _coverage_errors(table: str, contract: dict[str, Any], placed: dict, owners: dict, gone: dict) -> list[str]:
    errors: list[str] = []
    stored = stored_fields(contract)
    all_fields = set(contract.get("fields") or {})
    for name in sorted(set(placed) | set(owners) | set(gone)):
        if name not in all_fields:
            errors.append(f"{table}.{name}: listed in realization but is not a contract field (R1)")
        elif name not in stored:
            errors.append(f"{table}.{name}: read-derived field listed in realization (R1)")
    for name in stored:
        keys, owned_by, retired_by = placed.get(name, []), owners.get(name, []), gone.get(name, [])
        if not keys and not owned_by and not retired_by:
            errors.append(f"{table}.{name}: stored field is in neither produced nor unproduced nor retired (R1)")
        if keys and owned_by:
            errors.append(f"{table}.{name}: stored field is both produced and unproduced (R1)")
        if retired_by and keys:
            errors.append(f"{table}.{name}: stored field is both retired and produced (R1)")
        if retired_by and owned_by:
            errors.append(f"{table}.{name}: stored field is both retired and unproduced (R1)")
        if len(owned_by) > 1:
            errors.append(f"{table}.{name}: stored field has more than one unproduced owner {owned_by} (R1)")
        if len(retired_by) > 1:
            errors.append(f"{table}.{name}: stored field has more than one retired owner {retired_by} (R1)")
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


def _corpus_errors(table: str, contract: dict[str, Any], produced: dict, owners: dict, gone: dict, inputs: dict) -> list[str]:
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
        for name in sorted(non_null & set(gone)):
            errors.append(f"{table}.{name}: marked retired but {producer}'s golden rows emit it (R5)")
        covered = credited | set(produced.get(WRITE_BOUNDARY, []))
        for name in sorted((non_null & contract_cols) - covered):
            errors.append(f"{table}.{name}: {producer}'s golden rows emit it but the block does not credit it (R6)")
    return errors


def _populated_errors(table: str, contract: dict[str, Any], placed: dict, owners: dict, gone: dict, inputs: dict) -> list[str]:
    errors: list[str] = []
    fields = contract.get("fields") or {}
    for name in stored_fields(contract):
        populated = (fields[name] or {}).get("populated_by")
        if not isinstance(populated, str):
            continue
        want: str | None
        if gone.get(name):
            want = f"retired: {gone[name][0]}"
        elif owners.get(name):
            want = f"unproduced: {owners[name][0]}"
        else:
            want = _populated_prefix(placed.get(name, []), list(inputs["producers"]))
        by_owner = bool(gone.get(name) or owners.get(name))
        ok = _owner_boundary(populated, want or "") if by_owner else " ".join(populated.split()).startswith(want or "")
        if want is not None and not ok:
            errors.append(f"{table}.{name}: populated_by must start with {want!r} (R8)")
    return errors


def _table_errors(table: str, inputs: dict[str, Any]) -> tuple[list[str], int]:
    contract = inputs["contracts"][table]
    block = contract.get("realization")
    if not block:
        return [f"{table}: no realization block"], 0
    errors: list[str] = []
    placed, owners, gone = _placements(table, block, inputs, errors)
    produced = block.get("produced") or {}
    errors += _coverage_errors(table, contract, placed, owners, gone)
    errors += _boundary_errors(table, produced, inputs)
    errors += _corpus_errors(table, contract, produced, owners, gone, inputs)
    errors += _populated_errors(table, contract, placed, owners, gone, inputs)
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


def _rec_owned(contracts: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    out: list[tuple[str, str, list[str]]] = []
    for table in TABLES:
        block = (contracts.get(table) or {}).get("realization") or {}
        for placement in ("unproduced", "retired"):
            for owner, names in (block.get(placement) or {}).items():
                if _REC_RE.match(owner):
                    out.append((table, owner, list(names)))
    return out


def owner_recs(contracts: dict[str, Any]) -> list[str]:
    """Sorted rec ids owning any field under unproduced or retired (criterion owners are roadmap items, not recs)."""
    return sorted({owner for _, owner, _ in _rec_owned(contracts)}, key=lambda r: int(r.split("-")[1]))


def owner_errors(contracts: dict[str, Any], rows: list[dict[str, Any]], missing: list[str]) -> list[str]:
    """One error per owned field whose owner rec is missing, has no status, is not open or does not name the field."""
    by_id = {r.get("id"): r for r in rows}
    errors: list[str] = []
    for table, owner, names in _rec_owned(contracts):
        row = by_id.get(owner)
        if owner in missing or row is None:
            errors.extend(f"{table}.{name}: owner {owner} was not found" for name in names)
            continue
        status = row.get("status")
        if not status:
            errors.extend(f"{table}.{name}: owner {owner} has no status" for name in names)
            continue
        if status != "open":
            errors.extend(f"{table}.{name}: owner {owner} is {status}, not open" for name in names)
            continue
        text = " ".join(str(row.get(k) or "") for k in ("title", "context", "acceptance"))
        errors.extend(
            f"{table}.{name}: owner {owner} does not name the field" for name in names if not names_field(text, name)
        )
    return errors


def load_contracts(root: Path) -> dict[str, Any]:
    return {t: yaml.safe_load((root / "docs" / "contracts" / f"{t}.yaml").read_text(encoding="utf-8")) for t in TABLES}


def load_inputs(root: Path) -> dict[str, Any]:
    contracts = load_contracts(root)
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
