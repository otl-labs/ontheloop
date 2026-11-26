"""GateRound: one gate round as the planner records it in a plan's gate_rounds (planning SKILL.md Step 9).

Counts, run facts and a plan-critique row's recorded per-finding gate history; never an approval input (Decision 87
as amended by Decision 205).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

_GATE_VERDICTS: dict[str, frozenset[str]] = {
    "decision-scout": frozenset({"NO_FLAGS", "FLAGS_FOUND", "BLOCK"}),
    "plan-critique": frozenset({"PROCEED", "REVISE"}),
    "wording-check": frozenset({"PASS", "FAIL"}),
    "kind-blind": frozenset({"mechanism", "direction"}),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_FINGERPRINT_RE = re.compile(r"v[1-9][0-9]*:[0-9a-f]{64}")
_Choice = Literal["accept-with-deferral", "re-scope", "split", "abandon", "one more round"]


class CategoryVerdict(BaseModel):
    """Plan-critique step 12p's verdict for one drafted Decision entry.

    A verdict, not a finding or a directive: critique directives stay in check-run output (Decision 205 cl.5) and
    persona per-finding rows with rec-4434. The title is stored raw, as drafted_titles returns it.
    """

    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1)
    verdict: Literal["PASS", "REVISE"]


class CritiqueFinding(BaseModel):
    """One plan-critique finding as the planner records it: tag and disposition, never the critic's directive.

    Maps to docs/contracts/personas/plan_critic.yaml's findings item: subject = title; id is the critic's short id
    (not plan_critic.yaml's derived <check>:<anchor>); check, detail and suggestion are not carried.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    severity: Literal["blocking", "nit"]
    origin: Literal["mechanical", "judgement"]
    anchor: str
    subject: str
    disposition: Literal["fixed", "disputed", "deferred", "applied-wording", "build-note"]

    @model_validator(mode="after")
    def _shape(self) -> CritiqueFinding:
        if not (self.id.strip() and self.anchor.strip() and self.subject.strip()):
            raise ValueError("a finding's id, anchor and subject are non-empty after strip")
        if len(self.subject) > 200 or "\n" in self.subject:
            raise ValueError("a finding's subject is one line of at most 200 characters")
        return self


_FINDINGS_SHAPE = (
    "findings: [{id, severity: blocking|nit, origin: mechanical|judgement, anchor, subject, "
    "disposition: fixed|disputed|deferred|applied-wording|build-note}]"
)


def version_errors(schema_version: int, rows: list[GateRound]) -> list[str]:
    """Version-gated findings rules for a plan's plan-critique rows (empty list when clean)."""
    errors: list[str] = []
    for row in rows:
        if row.gate != "plan-critique":
            continue
        if schema_version < 7 and row.findings is not None:
            errors.append(f"plan-critique round {row.round}: findings requires schema_version 7 or above")
        if schema_version >= 7 and row.findings is None:
            errors.append(f"plan-critique round {row.round}: schema_version 7 rows carry {_FINDINGS_SHAPE}")
        if schema_version >= 7 and row.verdict == "PROCEED" and row.effective_choice == "accept-with-deferral":
            errors.append(
                f"plan-critique round {row.round}: accept-with-deferral on a PROCEED is closed at schema_version 7; "
                "record each nit's disposition (applied-wording or build-note) in findings instead"
            )
    consult_rows = [row for row in rows if row.gate == "plan-critique" and row.escalation_decider == "consult"]
    if len(consult_rows) > 1:
        errors.append("a consult decides once per plan (Decision 287 cl.3)")
    return errors


class GateRound(BaseModel):
    """One gate round as the planner reports it (planning SKILL.md Step 9 convergence rule).

    A plan-critique escalation records its decider (escalation_decider: operator, or consult for one more round) and an
    operator override on the consult's row (overrides_consult, override_choice; effective_choice reads it).

    Counts, plus on a plan-critique row the planner's recorded per-finding gate history (findings): never a status
    or approval input (Decision 87 as amended by Decision 205; persona per-finding rows stay with rec-4434, T4.29
    c2). A wording-check row also records the run's
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
    escalation_choice: _Choice | None = None
    escalation_decider: Literal["operator", "consult"] | None = None
    overrides_consult: StrictBool = False
    override_choice: _Choice | None = None
    gate_run_id: str | None = None
    digest: str | None = None
    base: str | None = None
    selection: Literal["complete", "degraded"] | None = None
    pending_evaluators: int | None = Field(default=None, ge=1)
    scout_input_fingerprint: str | None = None
    category_consistency: list[CategoryVerdict] | None = None
    findings: list[CritiqueFinding] | None = None

    @model_validator(mode="after")
    def _verdict_matches_gate(self) -> GateRound:
        if self.verdict not in _GATE_VERDICTS[self.gate]:
            raise ValueError(f"{self.gate} verdict must be one of {sorted(_GATE_VERDICTS[self.gate])}, got {self.verdict!r}")
        if self.gate == "plan-critique" and (self.verdict == "REVISE") != (self.blocking > 0):
            raise ValueError("a plan-critique REVISE carries at least one blocking finding and a PROCEED carries none")
        if self.escalation_choice is not None and not self.escalated:
            raise ValueError("escalation_choice records the decider's answer to an escalation; set escalated: true")
        self._check_decider()
        self._check_run_facts()
        self._check_scout_fingerprint()
        self._check_category_consistency()
        self._check_findings()
        return self

    @property
    def effective_choice(self) -> str | None:
        return self.override_choice if self.overrides_consult else self.escalation_choice

    def _check_decider(self) -> None:
        if self.gate != "plan-critique" and (
            self.escalation_decider == "consult" or self.overrides_consult or self.override_choice is not None
        ):
            raise ValueError("the consult route is the plan-critique gate's alone (Decision 287)")
        if self.escalation_decider is not None and self.escalation_choice is None:
            raise ValueError("escalation_decider needs an escalation_choice")
        if self.escalation_decider == "consult" and self.escalation_choice != "one more round":
            raise ValueError("a consult decides only one more round; any other choice is the operator's (Decision 287 cl.4)")
        if self.overrides_consult and (self.escalation_decider != "consult" or self.override_choice is None):
            raise ValueError("an override is written on the consult's row with the operator's choice")
        if self.override_choice is not None and not self.overrides_consult:
            raise ValueError("override_choice needs overrides_consult")
        if self.override_choice is not None and self.override_choice == self.escalation_choice:
            raise ValueError("an override names a different choice")

    def _check_findings(self) -> None:
        if self.findings is None:
            return
        if self.gate != "plan-critique":
            raise ValueError("findings is allowed only on a plan-critique row")
        ids = [item.id.strip() for item in self.findings]
        if len(set(ids)) != len(ids):
            raise ValueError("findings ids must be unique within a row")
        blocking = [item for item in self.findings if item.severity == "blocking"]
        if len(blocking) != self.blocking or len(self.findings) - len(blocking) != self.nits:
            raise ValueError("findings blocking and nit entries must match the row's blocking and nits counts")
        deferrable = {"fixed", "disputed"} | ({"deferred"} if self.effective_choice == "accept-with-deferral" else set())
        if any(item.disposition not in deferrable for item in blocking):
            raise ValueError(
                "a blocking finding's disposition is fixed or disputed (deferred only under accept-with-deferral)"
            )
        if any(item.disposition == "deferred" for item in self.findings if item.severity == "nit"):
            raise ValueError("a nit finding's disposition is fixed, disputed, applied-wording or build-note")

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
