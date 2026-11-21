"""Telemetry fact index facade (Decision 80/104/124 pattern): facts projected from their sources at call time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.telemetry_fact_index.contracts import FactRow, Serving, parse_address, tokens

__all__ = ["FactRow", "Serving", "parse_address", "project", "tokens"]


def project(root: Path, lexicon: dict[str, Any] | None = None, verb_outputs: dict[str, Any] | None = None) -> list[FactRow]:
    """Every fact row from the contracts, joins, lexicon, golden corpora and verb outputs; nothing is cached or committed."""
    from scripts.telemetry_fact_index import contracts, sources  # noqa: PLC0415
    from scripts.telemetry_fact_index import verb_outputs as vo

    outputs = verb_outputs if verb_outputs is not None else vo.declared_outputs()
    rows = contracts.project_contracts(root, set(outputs))
    return sources.add_sources(root, rows, lexicon, outputs)
