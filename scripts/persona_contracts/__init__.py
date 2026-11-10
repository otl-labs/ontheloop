"""Persona contract mechanism facade (Decision 80 pattern): public surface of schema.py and render.py."""

from scripts.persona_contracts.render import check, render, sha256_hex
from scripts.persona_contracts.schema import (
    INDEX_REL,
    ROOT,
    PersonaContract,
    PersonaIndex,
    check_schema_dialect,
    load_contract,
    load_index,
    parse_capability,
)

__all__ = [
    "INDEX_REL",
    "ROOT",
    "PersonaContract",
    "PersonaIndex",
    "check",
    "check_schema_dialect",
    "load_contract",
    "load_index",
    "parse_capability",
    "render",
    "sha256_hex",
]
