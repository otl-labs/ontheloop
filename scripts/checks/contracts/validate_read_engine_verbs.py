"""Read-engine named-verb parity check (rec-3059 first-wave enforcer build, closes rec-3095).

Asserts docs/contracts/read-engine.yaml's read_surface.named_verbs.verbs {table: [verbs]}
grouping equals the grouping derived from the live NAMED_READS registry
(src/common/ducklake_scd2_schema.py) plus the reader-only EVENT_READS registry
(src/common/ducklake_event_read_verbs.py) and the content reader's CONTENT_READS registry
(src/common/ducklake_content_read_verbs.py), each grouped under a verb's first table -- promotes
rec-3095's verb-drift finding from a routed debt record (evaluator.none_grandfathered) to a machine-enforced check.
"""

from __future__ import annotations

import collections
from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_NAME = "read-engine.yaml"


def _dig(data: object, *keys: str) -> object:
    """Walk nested mappings by `keys`; returns None the moment any level is not a mapping."""
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _live_verbs() -> dict[str, list[str]]:
    from src.common.ducklake_content_read_verbs import CONTENT_READS  # noqa: PLC0415
    from src.common.ducklake_event_read_verbs import EVENT_READS  # noqa: PLC0415
    from src.common.ducklake_scd2_schema import NAMED_READS  # noqa: PLC0415

    grouped: dict[str, list[str]] = collections.defaultdict(list)
    for verb, named_read in NAMED_READS.items():
        grouped[named_read.table].append(verb)
    for verb, event_read in EVENT_READS.items():
        grouped[event_read.tables[0]].append(verb)
    for verb, content_read in CONTENT_READS.items():
        grouped[content_read.tables[0]].append(verb)
    return {table: sorted(verbs) for table, verbs in grouped.items()}


@registry.register("validate_read_engine_verbs", owner="platform")
def validate_read_engine_verbs(failed: list[str], *, contracts_dir: Path | None = None) -> None:
    """Fail if read-engine.yaml's declared verb grouping diverges from the live NAMED_READS registry."""
    print("\n=== Read-engine verb parity ===")
    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"
    path = target_dir / _CONTRACT_NAME

    if not path.is_file():
        failed.append(f"Read-engine verb parity: {path} not found")
        registry.skipped(f"{_CONTRACT_NAME} not found")
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"Read-engine verb parity: could not read/parse {path}: {exc}")
        registry.skipped(f"{_CONTRACT_NAME} not parseable")
        return

    verbs = _dig(data, "read_surface", "named_verbs", "verbs")
    if not isinstance(verbs, dict) or not verbs:
        failed.append(f"Read-engine verb parity: {path} missing or empty 'read_surface.named_verbs.verbs'")
        registry.skipped(f"{_CONTRACT_NAME} missing or empty 'read_surface.named_verbs.verbs'")
        return
    if not all(isinstance(v, list) for v in verbs.values()):
        failed.append(f"Read-engine verb parity: {path} 'read_surface.named_verbs.verbs' entries must all be lists")
        registry.skipped(f"{_CONTRACT_NAME} 'read_surface.named_verbs.verbs' entries not all lists")
        return

    declared = {table: sorted(v) for table, v in verbs.items()}
    live = _live_verbs()
    total = sum(len(v) for v in live.values())
    registry.examined(total, unit="verbs")
    if declared != live:
        failed.append(
            f"Read-engine verb parity: {_CONTRACT_NAME} declares {declared} "
            f"but NAMED_READS, EVENT_READS and CONTENT_READS derive {live}"
        )
        return

    print(
        f"  PASS: {_CONTRACT_NAME} matches NAMED_READS, EVENT_READS and CONTENT_READS "
        f"({total} verbs across {len(live)} table(s))."
    )
