"""Typed outcome of a rec filing, so the CLI can tell an insert from a ci_rca dedup that returned an existing id."""

from __future__ import annotations

from typing import NamedTuple


class FileRecOutcome(NamedTuple):
    rec_id: str
    inserted: bool
    reason: str | None
