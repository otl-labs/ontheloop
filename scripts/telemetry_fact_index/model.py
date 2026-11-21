"""Shared row shape and address grammar for the telemetry fact index."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TABLES = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_agents",
    "telemetry_transcripts",
    "telemetry_persona_findings",
)

_ADDRESS_RE = re.compile(
    r"^(?P<table>[a-z_]+)\.(?P<column>[a-z0-9_]+)(?:#(?P<key>[A-Za-z0-9_.-]+))?(?:\[(?P<disc>[^\]]*)\])?$"
)


@dataclass
class Serving:
    verb: str
    agent_facing: bool
    masked: bool = False
    ambiguous: bool = False


@dataclass
class FactRow:
    address: str
    kind: str
    table: str = ""
    column: str = ""
    key: str = ""
    condition: str = ""
    type: str = ""
    description: str = ""
    populated_by: str = ""
    owner: str = ""
    producers: list[str] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)
    serving: list[Serving] = field(default_factory=list)
    source: str = ""
    concept_words: list[str] = field(default_factory=list)
    declared: bool | None = None
    link_from: str = ""
    link_to: str = ""
    derived_by: str = ""


def parse_address(address: str) -> tuple[str, str, str, dict[str, str]]:
    """Split table.column[#key][[disc=value,...]] into (table, column, key, discriminators); ValueError if malformed."""
    match = _ADDRESS_RE.match(address)
    if match is None:
        raise ValueError(f"not a fact address: {address!r}")
    disc: dict[str, str] = {}
    raw = match.group("disc")
    if raw is not None:
        for part in raw.split(","):
            name, sep, value = part.partition("=")
            if not sep or not name or not value:
                raise ValueError(f"bad discriminator in {address!r}")
            disc[name] = value
    return match.group("table"), match.group("column"), match.group("key") or "", disc


def condition_of(disc: dict[str, str]) -> str:
    return ",".join(f"{k}={disc[k]}" for k in sorted(disc))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
