"""Telemetry-lexicon canonical-tables resolution check (rec-3059 first-wave enforcer build).

Asserts docs/contracts/telemetry-lexicon.yaml's lexicon.model.canonical_tables each resolve to
an existing Class A contract (docs/contracts/<table>.yaml, contract.class == "A", contract.id ==
<table>), and that every lexicon.model.collapses target names one of those canonical tables.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common, registry
from scripts.contracts import ContractValidationError, load_contract, resolve_refs
from scripts.telemetry_fact_index.model import parse_address

_CONTRACT_NAME = "telemetry-lexicon.yaml"


def _dig(data: object, *keys: str) -> object:
    """Walk nested mappings by `keys`; returns None the moment any level is not a mapping."""
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _resolves_to_class_a(target_dir: Path, table: str) -> bool:
    contract_path = target_dir / f"{table}.yaml"
    if not contract_path.is_file():
        return False
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    contract = data.get("contract") if isinstance(data, dict) else None
    if not isinstance(contract, dict):
        return False
    return contract.get("class") == "A" and contract.get("id") == table


def _resolved_fields(target_dir: Path, table: str, cache: dict[str, object]) -> dict | None:
    if table not in cache:
        path = target_dir / f"{table}.yaml"
        try:
            cache[table] = resolve_refs(load_contract(path), target_dir) if path.is_file() else None
        except ContractValidationError:
            cache[table] = None
    return cache[table]  # type: ignore[return-value]


def _facts_errors(target_dir: Path, terms: object) -> tuple[list[str], int]:
    """Errors and the number of facts entries examined: bare resolvable addresses, one discriminator, lower-case unique aka."""
    errors: list[str] = []
    examined = 0
    cache: dict[str, object] = {}
    for term in terms if isinstance(terms, list) else []:
        name = term.get("term") if isinstance(term, dict) else None
        aka = term.get("aka") if isinstance(term, dict) and isinstance(term.get("aka"), list) else []
        if any(not isinstance(a, str) or a != a.lower() for a in aka) or len(set(aka)) != len(aka):
            errors.append(f"term {name!r}: aka words must be lower case and unique")
        for entry in (term.get("facts") or []) if isinstance(term, dict) else []:
            examined += 1
            if not isinstance(entry, str):
                errors.append(f"term {name!r}: facts entry {entry!r} is not a bare address string (Decision 95)")
                continue
            try:
                table, column, _key, disc = parse_address(entry)
            except ValueError:
                errors.append(f"term {name!r}: facts entry {entry!r} is not a fact address")
                continue
            fields = _resolved_fields(target_dir, table, cache)
            if fields is None or column not in fields:
                errors.append(f"term {name!r}: facts entry {entry!r} names no resolved field {table}.{column}")
                continue
            if len(disc) > 1:
                errors.append(f"term {name!r}: facts entry {entry!r} carries more than one discriminator")
            for dname, value in disc.items():
                spec = fields.get(dname)
                accepted = ((getattr(spec, "dq_intent", None) or {}).get("accepted_values") or {}).get("values") or []
                if spec is None or value not in [str(v) for v in accepted]:
                    errors.append(
                        f"term {name!r}: facts entry {entry!r}: {dname}={value} is not an accepted value of {table}.{dname}"
                    )
    return errors, examined


@registry.register("validate_telemetry_lexicon_tables", owner="platform")
def validate_telemetry_lexicon_tables(failed: list[str], *, contracts_dir: Path | None = None) -> None:
    """Fail if a canonical_tables entry does not resolve to a Class A contract, or a collapses
    target does not name a canonical table."""
    print("\n=== Telemetry-lexicon canonical-tables resolution ===")
    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"
    path = target_dir / _CONTRACT_NAME

    if not path.is_file():
        failed.append(f"Telemetry-lexicon canonical-tables: {path} not found")
        registry.skipped(f"{_CONTRACT_NAME} not found")
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"Telemetry-lexicon canonical-tables: could not read/parse {path}: {exc}")
        registry.skipped(f"{_CONTRACT_NAME} not parseable")
        return

    canonical = _dig(data, "lexicon", "model", "canonical_tables")
    if not isinstance(canonical, list) or not canonical:
        failed.append(f"Telemetry-lexicon canonical-tables: {path} missing or empty 'lexicon.model.canonical_tables'")
        registry.skipped(f"{_CONTRACT_NAME} missing or empty 'lexicon.model.canonical_tables'")
        return

    facts_errors, facts_examined = _facts_errors(target_dir, _dig(data, "lexicon", "terms"))
    registry.examined(len(canonical) + facts_examined, unit="canonical_tables and facts entries")
    failed.extend(f"Telemetry-lexicon facts: {err}" for err in facts_errors)
    canonical_set = {t for t in canonical if isinstance(t, str)}
    unresolved = sorted(t for t in canonical if not (isinstance(t, str) and _resolves_to_class_a(target_dir, t)))
    if unresolved:
        failed.append(
            f"Telemetry-lexicon canonical-tables: {_CONTRACT_NAME} canonical_tables entr(y/ies) do not "
            f"resolve to an existing Class A contract: {unresolved}"
        )

    collapses = _dig(data, "lexicon", "model", "collapses")
    bad_collapses: list[tuple[str, str]] = []
    if isinstance(collapses, dict):
        for source, target_desc in collapses.items():
            target_table = str(target_desc).split(" ", 1)[0]
            if target_table not in canonical_set:
                bad_collapses.append((source, target_table))
    if bad_collapses:
        failed.append(
            f"Telemetry-lexicon canonical-tables: {_CONTRACT_NAME} collapses target(s) do not name a "
            f"canonical table: {bad_collapses}"
        )

    if not unresolved and not bad_collapses and not facts_errors:
        print(
            f"  PASS: {_CONTRACT_NAME} -- {len(canonical)} canonical_tables resolve to Class A contracts, "
            f"all collapses targets are canonical."
        )
