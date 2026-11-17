"""Shared portal-gate assertion for producer rec-template tests (Decision 235 cl.1 repair of the advisory glossary legs)."""

from __future__ import annotations

from typing import Any

from scripts.ops_portal.write_validators import gate_write


def assert_portal_gate_clean(table: str, fields: dict[str, Any], **kwargs: Any) -> None:
    """Run the real gate_write and assert it returns no finding or error result, naming each one."""
    unclean = [r for r in gate_write(table, fields, **kwargs) if r.outcome in ("finding", "error")]
    assert not unclean, [f"{r.column} {r.kind} {r.outcome}: {r.message}" for r in unclean]
