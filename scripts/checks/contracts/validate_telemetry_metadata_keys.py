"""Telemetry metadata-key registry check (rec-4733; Decision 168 evaluator grammar, Decision 170 accounting).

docs/contracts/telemetry-metadata-keys.yaml lists one row per (producer, observation_type, event_kind, key) a producer's
golden rows carry in telemetry_observations.metadata. Rules (each error names the row):
K1 every key in a producer's golden metadata is a registry row. K2 every registry row without until is carried by that
producer's golden rows, and a row with until by none. K3 rows are unique and their producer is registered. K4 since is an
int in 1..current or "<=N" with N in 1..current, and until, when present, is an int with since <= until < current.
K5 no row uses a Class A descriptor key (a deliberate coupling to the contract-population smuggling detector).
Pure function metadata_key_errors(key_rows, corpora, registry_order, currents); the wrapper loads them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.contracts import _population
from scripts.checks.contracts.validate_telemetry_field_realization import load_inputs

_REGISTRY_REL = "docs/contracts/telemetry-metadata-keys.yaml"
_PARSER_VERSIONS_REL = "config/telemetry/parser_versions.yaml"
_SINCE_RE = re.compile(r"^<=([0-9]+)$")
RowKey = tuple[str, str, str, str]


def golden_key_rows(corpora: dict[str, Any]) -> dict[str, set[RowKey]]:
    """Per producer, the (producer, observation_type, event_kind, key) set its golden observations rows carry."""
    out: dict[str, set[RowKey]] = {}
    for producer, corpus in corpora.items():
        found: set[RowKey] = set()
        for row in (corpus.get("rows") or {}).get("telemetry_observations", []):
            raw = row.get("metadata")
            if raw is None:
                continue
            bag = json.loads(raw) if isinstance(raw, str) else raw
            for key in bag or {}:
                found.add((producer, row.get("observation_type"), row.get("event_kind"), key))
        out[producer] = found
    return out


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _since_floor(since: Any, current: int) -> tuple[int | None, str | None]:
    if _is_int(since):
        return (since, None) if 1 <= since <= current else (None, f"since {since} is outside 1..{current}")
    match = _SINCE_RE.match(since) if isinstance(since, str) else None
    if match is None:
        return None, f"since {since!r} is neither an int nor a quoted '<=N'"
    bound = int(match.group(1))
    return (bound, None) if 1 <= bound <= current else (None, f"since {since!r} has N outside 1..{current}")


def _version_errors(label: str, row: dict[str, Any], current: int) -> list[str]:
    floor, problem = _since_floor(row.get("since"), current)
    errors = [f"{label}: {problem} (K4)"] if problem else []
    if "until" in row:
        until = row["until"]
        if not _is_int(until) or until >= current or (floor is not None and until < floor):
            errors.append(f"{label}: until {until!r} must be an int with since <= until < {current} (K4)")
    return errors


def metadata_key_errors(
    key_rows: list[dict[str, Any]],
    corpora: dict[str, Any],
    registry_order: list[str],
    currents: dict[str, int],
) -> tuple[list[str], int]:
    """Errors and the number of key rows examined, over injected parsed inputs."""
    errors: list[str] = []
    golden = golden_key_rows(corpora)
    seen: set[RowKey] = set()
    for row in key_rows:
        ident: RowKey = (row.get("producer"), row.get("observation_type"), row.get("event_kind"), row.get("key"))
        label = " ".join(str(part) for part in ident)
        leaked = sorted(_population._CLASS_A_DESCRIPTOR_KEYS & row.keys())
        if leaked:
            errors.append(f"{label}: uses Class A descriptor key(s) {leaked} (K5)")
        if ident in seen:
            errors.append(f"{label}: duplicate row (K3)")
        seen.add(ident)
        producer = ident[0]
        if producer not in registry_order:
            errors.append(f"{label}: producer is not registered in parser_versions.yaml (K3)")
            continue
        errors.extend(_version_errors(label, row, currents[producer]))
        carried = ident in golden.get(producer, set())
        if "until" in row and carried:
            errors.append(f"{label}: has until but a golden row still carries it (K2)")
        if "until" not in row:
            if not carried:
                errors.append(f"{label}: declared without until but no golden row carries it (K2)")
    for found in golden.values():
        for ident in sorted(found - seen, key=str):
            errors.append(f"{' '.join(str(p) for p in ident)}: golden rows carry it but the registry has no row (K1)")
    return errors, len(key_rows)


def load_registry_inputs(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], dict[str, int]]:
    inputs = load_inputs(root)
    body = yaml.safe_load((root / _REGISTRY_REL).read_text(encoding="utf-8"))
    versions = yaml.safe_load((root / _PARSER_VERSIONS_REL).read_text(encoding="utf-8"))["producers"]
    currents = {name: spec["current"] for name, spec in versions.items()}
    return body["keys"], inputs["golden"], inputs["producers"], currents


@registry.register("validate_telemetry_metadata_keys", owner="platform")
def validate_telemetry_metadata_keys(failed: list[str], *, root: Path | None = None) -> None:
    """Fail when the metadata key registry and a producer's golden rows disagree in either direction."""
    print("\n=== Telemetry metadata keys ===")
    root = root if root is not None else _common.ROOT
    try:
        errors, examined = metadata_key_errors(*load_registry_inputs(root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        errors, examined = [f"inputs unreadable: {exc!r}"], 0
    registry.examined(examined, unit="key rows")
    for err in errors:
        print(f"  FAIL: {err}")
        failed.append(f"Telemetry metadata keys: {err}")
    if not errors:
        print(f"  PASS: {examined} key rows match the producers' golden metadata (telemetry-metadata-keys.yaml).")
