"""The single output schema of scripts.recs_related (Decision 215 cl.2: completeness is disclosed, never implied).

The CLI emits Envelope.to_json(); rec-4280's typed tool outputSchema is to be generated from these dataclasses, so
the field set here is the one place the shape is defined. Completeness is computed against the POST-exclusion totals
the recs_related verb returns: a page that follows a next_exclude hand-off is judged against what is still unseen.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

TIER_DIRECT = "direct"
TIER_NEAR = "near"
TIER_TERM = "term"
TIERS = (TIER_DIRECT, TIER_NEAR, TIER_TERM)

CHANNEL_OK = "ok"
UNAVAILABLE_PREFIX = "unavailable: "

FILE_TRACKED = "tracked"
FILE_UNTRACKED = "untracked"
FILE_OUTSIDE_REPO = "outside_repo"
FILE_INVALID = "invalid"


def unavailable(reason: str) -> str:
    """The channel-status string for a channel that could not run (never a silent zero)."""
    return f"{UNAVAILABLE_PREFIX}{reason}"


@dataclass(frozen=True)
class Hit:
    """One open recommendation, ranked, with the reasons it was returned (`why` is never empty)."""

    id: str
    title: str
    priority: str | None
    status: str
    effort: str | None
    source: str | None
    file: str | None
    tier: str
    score: float
    why: list[str]
    snippet: str | None


@dataclass
class Envelope:
    """A bounded, ranked page of hits plus everything needed to judge how much of the answer it is."""

    query: dict[str, Any]
    query_files: list[dict[str, str]]
    hits: list[Hit]
    total: int
    direct_total: int
    channels: dict[str, str]
    dir_anchor_hits: int = 0
    near_truncated: bool = False
    terms_truncated: bool = False
    next_exclude: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def returned(self) -> int:
        return len(self.hits)

    @property
    def complete(self) -> bool:
        return self.returned >= self.total

    @property
    def direct_complete(self) -> bool:
        return sum(1 for h in self.hits if h.tier == TIER_DIRECT) >= self.direct_total

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["returned"] = self.returned
        out["complete"] = self.complete
        out["direct_complete"] = self.direct_complete
        return out

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)
