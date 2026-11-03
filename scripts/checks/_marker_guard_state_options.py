"""Private option helpers for scripts/checks/_marker_guard.py (Decision 165, Decision 187 cl.7).

Every opt-in branch of the shared marker mechanism lives here, so scripts/checks/_marker_guard.py gains only
unconditional calls and stays measured at 100% by its own mirror. Measured through
tests/checks/test__marker_guard_state_options.py. Grammar and semantics: docs/contracts/marker-grammar.yaml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.checks._marker_guard import MarkerEntry, RegistrySpec, StateEntry


def effective_marker(spec: RegistrySpec, entry: MarkerEntry | StateEntry) -> str | None:
    """The entry's marker, or None when spec.reason_required and its reason is None or blank (rec-3736),
    so a reasonless marker is no marker on base and head entries alike."""
    if spec.reason_required and not (entry.reason and entry.reason.strip()):
        return None
    return entry.marker
