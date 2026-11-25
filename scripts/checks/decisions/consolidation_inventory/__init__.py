"""Consolidation coupling inventory (rec-5627): what reads a Decision chain's victims, before the plan is drafted.

Facade (Decision 80/104/124 pattern). Not a registered check; run it as
`bin/venv-python -m scripts.checks.decisions.consolidation_inventory VICTIM [VICTIM ...] [--probe]`.
"""

from __future__ import annotations

from pathlib import Path

from scripts.checks.decisions.consolidation_inventory import _citations, _probe, _pytest_scan, _structured
from scripts.checks.decisions.consolidation_inventory._model import (
    RULES,
    STUB_TEMPLATE,
    STUB_TEMPLATE_ID,
    Evidence,
    Gate,
    Hit,
    Inventory,
)

__all__ = ["RULES", "STUB_TEMPLATE", "STUB_TEMPLATE_ID", "Evidence", "Hit", "Inventory", "inventory", "probe"]


def inventory(root: Path, victims: list[int] | tuple[int, ...], *, include_citations: bool = False) -> Inventory:
    """Every hit of the static scan for victims at root (read-only, deterministic, never raises on malformed input)."""
    root = Path(root)
    ordered = tuple(sorted(set(victims)))
    inv = Inventory(victims=ordered)
    gate = Gate(root)
    inv.hits.extend(_pytest_scan.scan(root, ordered, gate, inv.notes))
    for surface in _structured.SURFACES:
        inv.hits.extend(surface(root, ordered, gate, inv.notes))
    inv.hits.extend(_structured.size_budgets(root, inv.hits, gate))
    inv.hits.extend(_citations.scan(root, ordered, gate, include_citations))
    return inv


def probe(root: Path, inv: Inventory) -> Inventory:
    """Fill the probe state of the test_node and graduated_record hits (see _probe)."""
    return _probe.probe(Path(root), inv)
