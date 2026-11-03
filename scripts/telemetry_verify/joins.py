"""The telemetry referential joins, read from docs/contracts/_joins.yaml (Decision 210 cl.3: the contract is the rule source).

Nothing here restates a join pair; the loader reads the registry and orients each join by its own cardinality.
"""

from __future__ import annotations

from pathlib import Path

import yaml

JOINS_PATH = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "_joins.yaml"
JoinPair = tuple[str, str, str, str, str]


def _endpoint(text: str) -> tuple[str, str]:
    table, column = text.split(".", 1)
    return table, column


def load_telemetry_joins(path: Path = JOINS_PATH) -> list[JoinPair]:
    """(join_id, child_table, child_column, parent_table, parent_column) of every join between two telemetry_* tables.

    The child side is the `to` side of a one_to_many join and the `from` side of a many_to_one join.
    """
    joins = yaml.safe_load(path.read_text(encoding="utf-8"))["joins"]
    out: list[JoinPair] = []
    for join_id, spec in joins.items():
        source, target = _endpoint(spec["from"]), _endpoint(spec["to"])
        if not (source[0].startswith("telemetry_") and target[0].startswith("telemetry_")):
            continue
        child, parent = (target, source) if spec["cardinality"] == "one_to_many" else (source, target)
        out.append((join_id, child[0], child[1], parent[0], parent[1]))
    return out
