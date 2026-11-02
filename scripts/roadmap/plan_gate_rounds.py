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
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")


class GateRound(BaseModel):
    """One gate round as the planner reports it (planning SKILL.md Step 9 convergence rule).

    Counts only: never a status or approval input, and never a per-finding record (Decision 87 as amended by
    Decision 205; T4.29 c2 keeps per-finding rows with rec-4434). A wording-check row also records the run's
    digest, base and selection status (scripts.roadmap.plan_wording_check).
    """

    model_config = ConfigDict(extra="forbid")

    gate: Literal["decision-scout", "plan-critique", "wording-check"]
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

    @model_validator(mode="after")
    def _verdict_matches_gate(self) -> GateRound:
        if self.verdict not in _GATE_VERDICTS[self.gate]:
            raise ValueError(f"{self.gate} verdict must be one of {sorted(_GATE_VERDICTS[self.gate])}, got {self.verdict!r}")
        if self.gate == "plan-critique" and (self.verdict == "REVISE") != (self.blocking > 0):
            raise ValueError("a plan-critique REVISE carries at least one blocking finding and a PROCEED carries none")
        if self.escalation_choice is not None and not self.escalated:
            raise ValueError("escalation_choice records the human's answer to an escalation; set escalated: true")
        self._check_run_facts()
        return self

    def _check_run_facts(self) -> None:
        facts = (self.digest, self.base, self.selection)
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
