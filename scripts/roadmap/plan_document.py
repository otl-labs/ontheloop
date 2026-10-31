"""Pydantic schema for docs/plans/PLAN-*.yaml planning artefacts, loader, and CLI (T1.11 / CD.22)."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_serializer, model_validator

from scripts.contract_rules import parse_yaml
from scripts.roadmap.vp_literals import (
    _BACKTICK_LITERAL_RE,
    SELF_SATISFYING_VERDICT,
    _partition_command,
    find_self_satisfying_literals,
    find_unsatisfiable_literals,
)

_SUPPORTED_VERSIONS: frozenset[int] = frozenset({1, 2, 3, 4, 5})
_V2_PHASE_ENUM: frozenset[str] = frozenset({"pre-deploy", "post-deploy"})
_MIN_WAIVER_CHARS = 20
_REC_ID_RE = re.compile(r"rec-\d+")
_DECISION_TOKEN_RE = re.compile(r"dec:[1-9][0-9]*(#[^\s#:]+)?")
_EDGE_FIELDS = frozenset({"amends", "introduces", "principal"})
_EDGE_FIELDS_MIN_VERSION = 5
_GATE_VERDICTS: dict[str, frozenset[str]] = {
    "decision-scout": frozenset({"NO_FLAGS", "FLAGS_FOUND", "BLOCK"}),
    "plan-critique": frozenset({"PROCEED", "REVISE"}),
}

# _partition_command lives in scripts.roadmap.vp_literals (Decision 104 sole-home for the
# shell-command tokenizer within this package) -- imported back here rather than duplicated.


def _argument_selects(argument: str, evidence: str) -> bool:
    """True if pytest argument `argument` would collect `evidence`.

    Covers the three ways a step legitimately hosts a selector without repeating it verbatim:
    the exact node id, a file hosting a `::node` beneath it, and a directory hosting the file.
    """
    if not argument:
        return False
    if evidence == argument or evidence.startswith(f"{argument}::"):
        return True
    directory = argument if argument.endswith("/") else f"{argument}/"
    return evidence.startswith(directory)


PlanType = Literal["IMPLEMENTATION", "STRATEGIC", "REPORT-ONLY"]
VerificationTier = Literal["V1", "V2", "V3"]
ScopeAction = Literal["Create", "Modify", "Delete"]
Complexity = Literal["XS", "S", "M", "L", "XL"]
GraduationDisposition = Literal["graduate", "waive", "not-applicable"]
FallbackVerdict = Literal["continue_on_current_substrate", "fallback_triggered", "obligation_lapsed"]


class HandoffPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_validation_required_before_commit: Literal[True]
    timeout_disposition: Literal["blocked"]


class ScopeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    action: ScopeAction
    purpose: str = Field(min_length=1)
    new_imports: list[Annotated[str, Field(pattern=r"^(scripts|src|tests)(\.[A-Za-z_][A-Za-z0-9_]*)+$")]] = Field(
        default_factory=list
    )

    @model_serializer(mode="wrap")
    def _omit_empty_new_imports(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not data.get("new_imports"):
            data.pop("new_imports", None)
        return data


class VerificationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    phase: str = Field(min_length=1)
    action: str = Field(min_length=1)
    command: str
    expected: str = Field(min_length=1)
    fix_if: str = Field(min_length=1)
    hermetic: bool = False
    graduation: GraduationDisposition | None = None
    graduation_check_id: str | None = None
    graduation_waiver_reason: str | None = None
    expected_literals: list[str] | None = None

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("verification step requires a non-empty executable command")
        return v

    @model_validator(mode="after")
    def _validate_graduation_disposition(self) -> VerificationStep:
        has_check_id = bool(self.graduation_check_id and self.graduation_check_id.strip())
        has_reason = bool(self.graduation_waiver_reason and self.graduation_waiver_reason.strip())
        if self.graduation == "graduate":
            if not has_check_id:
                raise ValueError(
                    f"verification step {self.step}: graduation='graduate' requires a non-empty graduation_check_id"
                )
            if self.graduation_waiver_reason:
                raise ValueError(f"verification step {self.step}: graduation_waiver_reason requires graduation='waive'")
        elif self.graduation == "waive":
            if not has_reason:
                raise ValueError(
                    f"verification step {self.step}: graduation='waive' requires a non-empty graduation_waiver_reason"
                )
            if self.graduation_check_id:
                raise ValueError(f"verification step {self.step}: graduation_check_id requires graduation='graduate'")
        else:
            if self.graduation_check_id:
                raise ValueError(f"verification step {self.step}: graduation_check_id requires graduation='graduate'")
            if self.graduation_waiver_reason:
                raise ValueError(f"verification step {self.step}: graduation_waiver_reason requires graduation='waive'")
        return self


class TestObligation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    behavior: str = Field(min_length=1)
    verification_step: int
    test_selector: str | None = None
    command: str | None = None
    red_green_expectation: str | None = None
    waiver_reason: str | None = None

    @field_validator("source", "behavior")
    @classmethod
    def _required_text_non_blank(cls, v: str, info: ValidationInfo) -> str:
        if not v.strip():
            raise ValueError(f"test_obligations[].{info.field_name} must be non-blank")
        return v

    @property
    def evidence(self) -> str:
        """The single selector or command this obligation is proven by."""
        return (self.test_selector or self.command or "").strip()

    @model_validator(mode="after")
    def _validate_evidence(self) -> TestObligation:
        selectors = [value for value in (self.test_selector, self.command) if value and value.strip()]
        if len(selectors) != 1:
            raise ValueError("test obligation requires exactly one non-blank test_selector or command")
        outcomes = [value for value in (self.red_green_expectation, self.waiver_reason) if value and value.strip()]
        if len(outcomes) != 1:
            raise ValueError("test obligation requires exactly one red_green_expectation or substantive waiver_reason")
        if self.waiver_reason and len(self.waiver_reason.strip()) < _MIN_WAIVER_CHARS:
            raise ValueError(f"test obligation waiver_reason must be substantive (at least {_MIN_WAIVER_CHARS} characters)")
        return self


class WorkArea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    complexity: Complexity


class FallbackReevaluation(BaseModel):
    """CD.27 fallback_spec re-evaluation record (ESB-02 remediation).

    Carried by a plan naming a CD.27-gated tier item, per
    scripts/checks/roadmap/validate_fallback_reevaluation.py. Shape only -- the
    obligation to attach this block lives in that check, not in this schema.
    """

    model_config = ConfigDict(extra="forbid")

    reevaluated_on: str = Field(min_length=1)
    substrate_status: str = Field(min_length=1)
    verdict: FallbackVerdict
    basis: str = Field(min_length=1)

    # One shared validator for all three free-text fields (code review round 2, Low) -- collapsed
    # ONLY because `info.field_name` reproduces each field's original message byte-for-byte
    # ("fallback_reevaluation.<field> must be non-blank"); a collapse that cost message fidelity
    # would not be worth it and was rejected as an option for exactly that reason. Runs in
    # definition order before `_reevaluated_on_is_iso_date` below, so a blank `reevaluated_on`
    # still raises the non-blank message first, matching the pre-collapse behaviour exactly.
    @field_validator("reevaluated_on", "substrate_status", "basis")
    @classmethod
    def _non_blank(cls, v: str, info: ValidationInfo) -> str:
        if not v.strip():
            raise ValueError(f"fallback_reevaluation.{info.field_name} must be non-blank")
        return v

    @field_validator("reevaluated_on")
    @classmethod
    def _reevaluated_on_is_iso_date(cls, v: str) -> str:
        # Explicit %Y-%m-%d match (code review round 2, Low) -- date.fromisoformat() alone is
        # too permissive on Python 3.11+, which also accepts the basic-format "YYYYMMDD" (no
        # dashes). strptime with an exact format string rejects both that and a datetime-with-
        # time string like "2026-08-02T00:00:00" ("unconverted data remains"), matching what the
        # error message promises: a date stamp, not a timestamp.
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"fallback_reevaluation.reevaluated_on must be an ISO date (YYYY-MM-DD): {v!r}") from None
        return v


_WEAKENING_CANDIDATE_RE = re.compile(r"^\[[a-z-]+\] \S+::\S+$")


class WeakeningDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str = Field(pattern=_WEAKENING_CANDIDATE_RE.pattern)
    disposition: Literal["refactor", "planned-change", "retirement", "growth-safe-pin", "owned-skip", "weakening"]
    cite: str = Field(min_length=1)


class GateRound(BaseModel):
    """One gate round as the planner reports it (planning SKILL.md Step 9 convergence rule).

    Counts only: never a status or approval input, and never a per-finding record (Decision 87 as amended by
    Decision 205; T4.29 c2 keeps per-finding rows with rec-4434).
    """

    model_config = ConfigDict(extra="forbid")

    gate: Literal["decision-scout", "plan-critique"]
    round: int = Field(ge=1)
    verdict: str
    blocking: int = Field(default=0, ge=0)
    nits: int = Field(default=0, ge=0)
    escalated: bool = False
    escalation_choice: Literal["accept-with-deferral", "re-scope", "split", "abandon", "one more round"] | None = None
    gate_run_id: str | None = None

    @model_validator(mode="after")
    def _verdict_matches_gate(self) -> GateRound:
        if self.verdict not in _GATE_VERDICTS[self.gate]:
            raise ValueError(f"{self.gate} verdict must be one of {sorted(_GATE_VERDICTS[self.gate])}, got {self.verdict!r}")
        if self.gate == "plan-critique" and (self.verdict == "REVISE") != (self.blocking > 0):
            raise ValueError("a plan-critique REVISE carries at least one blocking finding and a PROCEED carries none")
        if self.escalation_choice is not None and not self.escalated:
            raise ValueError("escalation_choice records the human's answer to an escalation; set escalated: true")
        return self


class PlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    slug: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    plan_type: PlanType
    verification_tier: VerificationTier
    plan_path: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    scope: list[ScopeEntry] = Field(min_length=1)
    bundled_recommendations: list[str] = Field(default_factory=list)
    followon_recs: list[str] = Field(default_factory=list)
    closes_criteria: list[str] = Field(default_factory=list)
    infrastructure_dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    verification_plan: list[VerificationStep] = Field(min_length=1)
    test_obligations: list[TestObligation] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    pre_implementation_checklist: list[str] = Field(default_factory=list)
    execution_steps: list[str] = Field(default_factory=list)
    work_areas: list[WorkArea] = Field(default_factory=list)
    rollback: str | None = None
    tier_waiver: str | None = None
    handoff_policy: HandoffPolicy | None = None
    fallback_reevaluation: FallbackReevaluation | None = None
    implementation_declared: bool = False
    weakening_dispositions: list[WeakeningDisposition] = Field(default_factory=list)
    amends: list[str] = Field(default_factory=list)
    introduces: list[str] = Field(default_factory=list)
    principal: str | None = None
    gate_rounds: list[GateRound] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v not in _SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported schema_version {v}. Supported: {sorted(_SUPPORTED_VERSIONS)}")
        return v

    @field_validator("closes_criteria")
    @classmethod
    def _closes_criteria_tokens(cls, v: list[str]) -> list[str]:
        # Loose shape check only -- reject prose, accept every real <item-id>:<crit-id> token
        # (lettered criteria, hyphenated/triple-dotted/lettered-suffix item ids). Membership
        # (does the ref actually exist) stays owned by validate_platform_roadmap.
        for entry in v:
            if any(ch.isspace() for ch in entry):
                raise ValueError(
                    f"closes_criteria entry {entry!r} is not a valid '<item-id>:<crit-id>' token "
                    "(contains whitespace -- narrative/prose text belongs in context:, not closes_criteria)"
                )
            if entry.count(":") != 1:
                raise ValueError(
                    f"closes_criteria entry {entry!r} is not a valid '<item-id>:<crit-id>' token "
                    "(must contain exactly one ':' separating item-id and crit-id)"
                )
            item_id, crit_id = entry.split(":", 1)
            if not item_id or not crit_id:
                raise ValueError(
                    f"closes_criteria entry {entry!r} is not a valid '<item-id>:<crit-id>' token "
                    "(item-id and crit-id must both be non-empty)"
                )
        return v

    @field_validator("followon_recs")
    @classmethod
    def _followon_recs_tokens(cls, v: list[str]) -> list[str]:
        # Shape check only, mirroring _closes_criteria_tokens: a deferred half's intent belongs
        # on the rec itself (title, context, acceptance), never restated as prose here.
        for entry in v:
            if not _REC_ID_RE.fullmatch(entry):
                raise ValueError(
                    f"followon_recs entry {entry!r} is not a valid 'rec-NNNN' token -- the half's "
                    "intent belongs on the rec, not in this field"
                )
        return v

    @field_validator("amends")
    @classmethod
    def _amends_tokens(cls, v: list[str]) -> list[str]:
        for entry in v:
            if not _DECISION_TOKEN_RE.fullmatch(entry):
                raise ValueError(f"amends entry {entry!r} is not a valid 'dec:<number>[#<clause-id>]' token")
        return v

    @field_validator("introduces")
    @classmethod
    def _introduces_tokens(cls, v: list[str]) -> list[str]:
        for entry in v:
            if not entry or any(ch.isspace() for ch in entry):
                raise ValueError(f"introduces entry {entry!r} is not a valid name token (non-blank, no whitespace)")
        return v

    @field_validator("principal")
    @classmethod
    def _principal_token(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v or any(ch.isspace() for ch in v):
            raise ValueError(f"principal {v!r} is not a valid principal token (non-blank, no whitespace)")
        if "@" in v:
            raise ValueError(
                "principal must not contain '@': an email address is a private identifier (public-repository boundary)"
            )
        return v

    def _validate_edge_fields_version(self) -> None:
        present = sorted(_EDGE_FIELDS & self.model_fields_set)
        if present and self.schema_version < _EDGE_FIELDS_MIN_VERSION:
            raise ValueError(
                f"{', '.join(present)} require schema_version {_EDGE_FIELDS_MIN_VERSION} or above "
                f"(got schema_version {self.schema_version})"
            )

    def _validate_gate_rounds(self) -> None:
        if self.gate_rounds and self.schema_version < _EDGE_FIELDS_MIN_VERSION:
            raise ValueError(f"gate_rounds requires schema_version {_EDGE_FIELDS_MIN_VERSION} or above")
        for gate in _GATE_VERDICTS:
            rounds = [row.round for row in self.gate_rounds if row.gate == gate]
            if rounds != list(range(1, len(rounds) + 1)):
                raise ValueError(f"gate_rounds for {gate} must number 1..N in order with no gap, got {rounds}")

    def _validate_handoff_policy(self) -> None:
        if self.schema_version in {3, 4, 5}:
            if self.plan_type == "IMPLEMENTATION" and self.handoff_policy is None:
                raise ValueError(f"schema_version {self.schema_version} IMPLEMENTATION plans require handoff_policy")
            if self.plan_type != "IMPLEMENTATION" and self.handoff_policy is not None:
                raise ValueError(f"handoff_policy is only valid on schema_version {self.schema_version} IMPLEMENTATION plans")
        elif self.handoff_policy is not None:
            raise ValueError("handoff_policy is only valid with schema_version 3, 4 or 5")

    def _validate_expected_literals(self) -> None:
        """schema_version 5's assertion carrier (docs/contracts/vp-red-before.yaml#carrier_rule):
        a hermetic step may no longer smuggle an output assertion through a backtick sigil in
        free-prose `expected` -- it must declare `expected_literals` instead. Below v5, the new
        field is refused outright rather than silently accepted under extra=forbid's per-field
        blindness to cross-field version gating. The unsat guard is HARD-FAILING; the
        self-satisfying lint is ADVISORY and is never raised here -- see
        `self_satisfying_advisories` for its own (non-raising) call site."""
        for step in self.verification_plan:
            if self.schema_version < 5:
                if step.expected_literals is not None:
                    raise ValueError(
                        f"verification step {step.step}: expected_literals requires schema_version 5 or "
                        f"above (got schema_version {self.schema_version})"
                    )
                continue
            if not step.hermetic:
                if step.expected_literals:
                    raise ValueError(f"verification step {step.step}: expected_literals is only valid on a hermetic step")
                continue
            if _BACKTICK_LITERAL_RE.search(step.expected):
                raise ValueError(
                    f"verification step {step.step}: schema_version 5 hermetic steps must not carry a backtick "
                    "literal in expected -- declare verification_plan[].expected_literals instead"
                )
            if step.expected_literals:
                unsatisfiable = find_unsatisfiable_literals(step.command, step.expected_literals)
                if unsatisfiable:
                    raise ValueError(
                        f"verification step {step.step}: expected_literals {unsatisfiable} cannot be emitted by "
                        f"this command (output-suppressed chain) -- command={step.command!r}"
                    )

    def _validate_test_obligation_links(self) -> None:
        """Every obligation must name a verification_plan step that actually runs its evidence.

        A whole-selector substring test is not enough on its own: `pytest --ignore=tests/x.py`
        contains the selector while demonstrably skipping it, and `pytest tests/` runs a node id
        it never spells out. Exclusion is therefore a hard reject, and selector hosting is decided
        by pytest argument semantics rather than by text containment.
        """
        step_by_id = {step.step: step for step in self.verification_plan}
        for obligation in self.test_obligations:
            linked = step_by_id.get(obligation.verification_step)
            if linked is None:
                raise ValueError(
                    f"test obligation for {obligation.source!r} links missing verification_plan step "
                    f"{obligation.verification_step}"
                )
            evidence = obligation.evidence
            selectable, excluded = _partition_command(linked.command)
            if any(_argument_selects(value, evidence) for value in excluded):
                raise ValueError(
                    f"test obligation for {obligation.source!r} names evidence {evidence!r} that linked "
                    f"verification_plan step {obligation.verification_step} explicitly excludes"
                )
            hosted = (
                evidence in linked.command
                if obligation.command
                else any(_argument_selects(argument, evidence) for argument in selectable)
            )
            if not hosted:
                raise ValueError(
                    f"test obligation for {obligation.source!r} evidence is not executable by linked "
                    f"verification_plan step {obligation.verification_step}"
                )

    @model_validator(mode="after")
    def _validate_document(self) -> PlanDocument:
        expected_path = f"docs/plans/PLAN-{self.slug}.yaml"
        if self.plan_path != expected_path:
            raise ValueError(f"plan_path '{self.plan_path}' must equal '{expected_path}' (slug consistency)")

        step_ids = [vp.step for vp in self.verification_plan]
        dupes = sorted({s for s in step_ids if step_ids.count(s) > 1})
        if dupes:
            raise ValueError(f"verification_plan step ids must be unique; duplicates: {dupes}")

        if self.plan_type == "STRATEGIC" and not self.work_areas:
            raise ValueError("STRATEGIC plans require a non-empty work_areas list")
        if self.plan_type != "STRATEGIC" and self.work_areas:
            raise ValueError(f"work_areas are only valid on STRATEGIC plans (plan_type is {self.plan_type})")

        if self.plan_type == "IMPLEMENTATION" and not self.execution_steps:
            raise ValueError("IMPLEMENTATION plans require non-empty execution_steps")

        if self.schema_version >= 2:
            bad_phases = sorted({vp.phase for vp in self.verification_plan if vp.phase not in _V2_PHASE_ENUM})
            if bad_phases:
                raise ValueError(
                    f"schema_version 2 verification_plan[].phase must be one of {sorted(_V2_PHASE_ENUM)}, got: {bad_phases}"
                )
        self._validate_handoff_policy()
        self._validate_test_obligation_links()
        self._validate_expected_literals()
        self._validate_edge_fields_version()
        self._validate_gate_rounds()
        return self


CONTEXT_BLOCK_LINE_ADVISORY = 40
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")


def context_block_lines(path: str | Path) -> int:
    """Rendered line count of a plan's top-level `context:` block (0 when absent).

    Measures what a reader scrolls past, not len(doc.context): a 14-entry context of long folded
    scalars renders as 116 lines (PLAN-coverage-sidefile-gitignore.yaml). Counted from the
    `context:` line to the next top-level key, or to EOF when context is the final key.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        if not line.startswith("context:"):
            continue
        for offset in range(start + 1, len(lines)):
            if _TOP_LEVEL_KEY_RE.match(lines[offset]):
                return offset - start
        return len(lines) - start
    return 0


def self_satisfying_advisories(doc: PlanDocument) -> list[str]:
    """ADVISORY-only (docs/contracts/vp-red-before.yaml's self_satisfying_lint): schema_version 5
    hermetic steps whose expected_literals appear verbatim in their own command's text --
    redundant with that command's exit-0, not a defect. Never raises; the CLI in `main()` is this
    function's sole call site, printed as WARN, never appended to a failure list."""
    if doc.schema_version < 5:
        return []
    advisories: list[str] = []
    for step in doc.verification_plan:
        if not step.hermetic or not step.expected_literals:
            continue
        found = find_self_satisfying_literals(step.command, step.expected_literals)
        if found:
            advisories.append(
                f"verification step {step.step}: expected_literals {found} verdict={SELF_SATISFYING_VERDICT} "
                "(appear verbatim in this step's own command) -- redundant with exit-0, not a defect"
            )
    return advisories


def load(path: str | Path) -> PlanDocument:
    """Parse the YAML plan at path and return a validated PlanDocument.

    Also enforces the filename/slug dangling-reference guard: the file on disk
    must be named PLAN-{slug}.yaml.
    """
    path = Path(path)
    data = parse_yaml(path.read_text(encoding="utf-8"))
    doc = PlanDocument.model_validate(data)
    expected_name = f"PLAN-{doc.slug}.yaml"
    if path.name != expected_name:
        raise ValueError(f"Filename '{path.name}' does not match slug '{doc.slug}' (expected {expected_name})")
    return doc


def validate_paths(paths: list[Path]) -> list[tuple[Path, str]]:
    """Validate each path; return (path, error) tuples for failures."""
    failures: list[tuple[Path, str]] = []
    for path in paths:
        try:
            load(path)
        except Exception as exc:  # noqa: BLE001 -- any parse/validation error is a failure verdict
            failures.append((path, str(exc)))
    return failures


def main(argv: list[str] | None = None, plans_root: Path | None = None) -> int:
    root = plans_root if plans_root is not None else Path(__file__).resolve().parent.parent.parent / "docs" / "plans"
    parser = argparse.ArgumentParser(description="Plan document validator (PLAN-*.yaml)")
    parser.add_argument(
        "paths",
        nargs="*",
        help="PLAN-*.yaml paths to validate (default: all docs/plans/PLAN-*.yaml)",
    )
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] if args.paths else sorted(root.glob("PLAN-*.yaml"))
    if not paths:
        print("PASS: no PLAN-*.yaml files found.")
        return 0
    # One load() per path feeds the PASS/FAIL verdict, the context-span advisory, and the
    # self-satisfying advisory alike -- a second parse per passing plan would be pure waste
    # (the same principle validate_plan_documents.py states directly). validate_paths() stays
    # untouched below as its own public single-purpose helper (other callers use it directly).
    any_failed = False
    for path in paths:
        try:
            doc = load(path)
        except Exception as exc:  # noqa: BLE001 -- any parse/validation error is a failure verdict
            print(f"FAIL: {path}: {exc}")
            any_failed = True
            continue
        print(f"PASS: {path} validates against PlanDocument schema.")
        span = context_block_lines(path)
        if span > CONTEXT_BLOCK_LINE_ADVISORY:
            print(
                f"WARN: {path}: context block is {span} rendered lines "
                f"(advisory cap {CONTEXT_BLOCK_LINE_ADVISORY}) -- link evidence "
                f"(rec/PR/Decision ids, paths), do not restate it"
            )
        for advisory in self_satisfying_advisories(doc):
            print(f"WARN: {path}: {advisory}")
    return 1 if any_failed else 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
