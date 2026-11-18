"""GateRound: one gate round as the planner records it in a plan's gate_rounds (planning SKILL.md Step 9).

Counts and run facts only, never an approval input (Decision 87 as amended by Decision 205).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_GATE_VERDICTS: dict[str, frozenset[str]] = {
    "decision-scout": frozenset({"NO_FLAGS", "FLAGS_FOUND", "BLOCK"}),
    "plan-critique": frozenset({"PROCEED", "REVISE"}),
    "wording-check": frozenset({"PASS", "FAIL"}),
    "kind-blind": frozenset({"mechanism", "direction"}),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_FINGERPRINT_RE = re.compile(r"v[1-9][0-9]*:[0-9a-f]{64}")


class CategoryVerdict(BaseModel):
    """Plan-critique step 12p's verdict for one drafted Decision entry.

    A verdict, not a finding or a directive: findings stay with rec-4434's telemetry_persona_findings and
    directives in check-run output (Decision 205 cl.5). The title is stored raw, as drafted_titles returns it.
    """

    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1)
    verdict: Literal["PASS", "REVISE"]


class GateRound(BaseModel):
    """One gate round as the planner reports it (planning SKILL.md Step 9 convergence rule).

    Counts only: never a status or approval input, and never a per-finding record (Decision 87 as amended by
    Decision 205; T4.29 c2 keeps per-finding rows with rec-4434). A wording-check row also records the run's
    digest, base and selection status (scripts.roadmap.plan_wording_check). A decision-scout row may record
    scout_input_fingerprint (scripts.roadmap.plan_fingerprint): a run fact, never a reason to skip a scout; every
    reader recomputes it. A kind-blind row is the verdict a fresh subagent gave, shown only
    plan_obligations --blind-drafts output (planning SKILL.md 8b); it carries no run facts and no counts.
    """

    model_config = ConfigDict(extra="forbid")

    gate: Literal["decision-scout", "plan-critique", "wording-check", "kind-blind"]
    round: int = Field(ge=1)
    verdict: str
    blocking: int = Field(default=0, ge=0)
    nits: int = Field(default=0, ge=0)
    escalated: bool = False
    escalation_choice: Literal["accept-with-deferral", "re-scope", "split", "abandon", "one more round"] | None = None
    gate_run_id: str | None = None
    digest: str | None = None
    base: str | None = None
    selection: Literal["complete", "degraded"] | None = None
    pending_evaluators: int | None = Field(default=None, ge=1)
    scout_input_fingerprint: str | None = None
    category_consistency: list[CategoryVerdict] | None = None

    @model_validator(mode="after")
    def _verdict_matches_gate(self) -> GateRound:
        if self.verdict not in _GATE_VERDICTS[self.gate]:
            raise ValueError(f"{self.gate} verdict must be one of {sorted(_GATE_VERDICTS[self.gate])}, got {self.verdict!r}")
        if self.gate == "plan-critique" and (self.verdict == "REVISE") != (self.blocking > 0):
            raise ValueError("a plan-critique REVISE carries at least one blocking finding and a PROCEED carries none")
        if self.escalation_choice is not None and not self.escalated:
            raise ValueError("escalation_choice records the human's answer to an escalation; set escalated: true")
        self._check_run_facts()
        self._check_scout_fingerprint()
        self._check_category_consistency()
        return self

    def _check_category_consistency(self) -> None:
        if self.category_consistency is None:
            return
        if self.gate != "plan-critique":
            raise ValueError("category_consistency is allowed only on a plan-critique row")
        titles = [item.entry.strip() for item in self.category_consistency]
        if not all(titles):
            raise ValueError("a category_consistency entry is empty after strip")
        if len(set(titles)) != len(titles):
            raise ValueError("category_consistency names a drafted entry more than once")
        revised = sum(item.verdict == "REVISE" for item in self.category_consistency)
        if revised and (self.verdict != "REVISE" or self.blocking < revised):
            raise ValueError(
                "a REVISE category_consistency entry needs a REVISE row whose blocking count is at least the number of "
                "REVISE entries (12p is blocking on its own)"
            )

    def _check_scout_fingerprint(self) -> None:
        if self.scout_input_fingerprint is None:
            return
        if self.gate != "decision-scout":
            raise ValueError("scout_input_fingerprint is allowed only on a decision-scout row")
        if not _FINGERPRINT_RE.fullmatch(self.scout_input_fingerprint):
            raise ValueError("scout_input_fingerprint is 'v<N>:' followed by 64 lowercase hex characters")

    def _check_run_facts(self) -> None:
        if self.pending_evaluators is not None and (self.gate != "wording-check" or self.verdict != "PASS"):
            raise ValueError("pending_evaluators is allowed only on a wording-check PASS row")
        facts = (self.digest, self.base, self.selection)
        if self.gate == "kind-blind" and (self.blocking or self.nits or self.escalated):
            raise ValueError("a kind-blind row carries no blocking or nits count and is never escalated")
        if self.gate != "wording-check":
            if any(fact is not None for fact in facts):
                raise ValueError("digest, base and selection are wording-check fields only")
            return
        if any(fact is None for fact in facts):
            raise ValueError("a wording-check row needs digest, base and selection")
        if not _SHA256_RE.fullmatch(self.digest or ""):
            raise ValueError("a wording-check digest is 64 lowercase hex characters")
        if not _GIT_SHA_RE.fullmatch(self.base or ""):
            raise ValueError("a wording-check base is a 40 hex character commit sha")
        if self.blocking or self.nits:
            raise ValueError("a wording-check row carries no blocking or nits count")
