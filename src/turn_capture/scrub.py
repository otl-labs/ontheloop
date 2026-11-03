"""CD.40 pre-write credential scrubber: an adapter over the write_time profile of src.sensitive_values.

Replacement is '[REDACTED:<CLASS>]', classes applied in the order of CLASS_ORDER, each once. Deterministic,
idempotent, never raises on a str and never reports matched text -- counts only. The patterns live in
src/sensitive_values/classes.py; a write_time pattern change is a parser_version bump by construction
(config/telemetry/parser_versions.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.sensitive_values import PROFILES, mask

CLASS_ORDER = PROFILES["write_time"]


@dataclass(frozen=True)
class ScrubResult:
    text: str
    counts: dict[str, int]


def scrub_text(text: str) -> ScrubResult:
    result = mask(text, "write_time")
    return ScrubResult(result.text, result.counts)
