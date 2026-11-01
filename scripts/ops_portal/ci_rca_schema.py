"""source=ci_rca structured-context SCHEMA (CIRCA / INTENT Section 1 + Section 4 check 8).

CiRcaContext + CiRcaEvidenceDispute pydantic models and their shape validators, the
evidence-bundle SHA-256 verifier, the schema-deficiency classifier, and the warn-mode
reject stamp. Owner-concern: schema definition and verification, not write-time
dispatch (that lives in ci_rca_runtime.py) or portal CRUD (file_rec, kept in the
facade).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from scripts.ops_portal._common import _AWS_REGION, ROOT

_CI_RCA_VALID_MODES = frozenset({"warn", "strict"})
_WHY_CHAIN_SYSTEMIC_KEYWORDS = frozenset(
    {
        "gate",
        "tier",
        "policy",
        "contract",
        "gap",
        "missing",
        "absent",
        "placement",
        "scope",
        "invariant",
        "enforcement",
    }
)
_WHY_CHAIN_CITATION_RE = re.compile(r"[\w./-]+\.(py|yaml|yml|tf|md|sh):\d+")


class _EvidenceBundleRef(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    s3_uri: str = ""
    upload_status: str = Field(pattern=r"^(ok|upload_failed)$")

    @model_validator(mode="after")
    def _validate_s3_uri_conditional(self) -> "_EvidenceBundleRef":
        # CIRCA-06: an S3 outage (upload_status='upload_failed') must not make evidence-carrying
        # recs unfilable in strict mode -- s3_uri is permitted empty on the degraded path. When
        # upload_status='ok' the object was actually uploaded, so s3_uri must look like a real URI.
        if self.upload_status == "ok" and not re.match(r"^s3://", self.s3_uri):
            raise ValueError("s3_uri must match ^s3:// when upload_status='ok'")
        return self


class _UnobservedStep(BaseModel):
    """A {job_id, step_index} pair identifying one scope entry the run's evidence did not
    retrieve a log for. RUN-scoped, never bundle-scoped (evidence.py assigns the same
    retrieval_evidence object to every bundle of a multi-failure run) -- see
    docs/contracts/ci-rca-lifecycle.yaml for the stamp-versus-echo split."""

    job_id: int = Field(ge=1)
    step_index: int = Field(ge=1)


class _WhyChainTerminusOverride(BaseModel):
    """CIRCA-08: typed why_chain_terminus_override -- a bare truthy dict must not bypass the
    terminus systemic-keyword/citation floor for free; the override itself must justify why."""

    reason: str = Field(min_length=80, max_length=400)


class _DetectionGap(BaseModel):
    earliest_viable_gate: str = Field(pattern=r"^(pre|presubmit|CI|undetermined)$")
    # CIRCA-09: 'unknown' covers not_a_gate workflows (e.g. terraform-apply-sandbox) where the
    # evidence bundle emits a null actual-gate; the agent mirrors that as 'unknown' rather than
    # fabricating pre/presubmit/CI.
    actual_gate_that_caught_it: str = Field(pattern=r"^(pre|presubmit|CI|unknown)$")
    gap_explanation: str = Field(min_length=120, max_length=600)
    escape_mode: Optional[str] = Field(
        default=None,
        pattern=r"^(check_ran_vacuously|tier_misplaced|no_premerge_gate_by_design|undetermined)$",
    )

    @field_validator("gap_explanation")
    @classmethod
    def _gap_has_file_citation(cls, v: str) -> str:
        if not _WHY_CHAIN_CITATION_RE.search(v):
            raise ValueError("gap_explanation must contain a file:line citation (e.g. scripts/validate.py:284)")
        return v


class CiRcaContext(BaseModel):
    """Structured context schema for source=ci_rca recommendations (INTENT Section 1).

    Enforced in warn mode by file_rec() when CI_RCA_STRICT_MODE=warn; raises in strict mode.
    Shape-only validation for prior_art_citation and evidence_bundle_ref (existence checks deferred
    to PLAN-ci-rca-evidence-script Phase 2).
    """

    schema_version: int = Field(default=1, ge=1, le=2)
    proximate_cause: str = Field(min_length=100, max_length=600)
    why_chain: list[str] = Field(min_length=3, max_length=7)
    why_chain_terminus_override: Optional[_WhyChainTerminusOverride] = None
    detection_gap: _DetectionGap
    recurrence_class: str = Field(pattern=r"^(novel|instance_of_known_pattern|regression)$")
    prior_art_citation: Optional[str] = None  # shape-only; existence check deferred (Phase 2)
    corrective_action: str = Field(min_length=100, max_length=600)
    preventive_action: Optional[str] = Field(default=None, min_length=100, max_length=800)
    evidence_bundle_ref: Optional[_EvidenceBundleRef] = None  # shape-only; S3 check deferred (Phase 2)
    rca_confidence: Optional[str] = Field(
        default=None,
        pattern=r"^(high|medium|low|undetermined)$",
    )
    # Portal-derived (Decision 66 Tier B), never agent-authored -- stamped by
    # _stamp_warn_mode_reject() when a warn-mode write would have been rejected in strict
    # mode. Declared explicitly (not left to silent extra) so it survives a round-trip;
    # CiRcaContext has no extra="forbid" but undeclared fields are dropped by the
    # pydantic default (extra='ignore').
    warn_mode_reject: Optional[dict] = None
    # CIRCA-03: dedup metadata, portal-derived from the verified evidence bundle (never
    # agent-authored) by _run_ci_rca_cross_check() -- mirrors the warn_mode_reject Tier-B
    # pattern. fingerprint is the classifier-anchored grouping key; occurrence_count/last_seen
    # track recurrences of the SAME open rec (bumped by the workflow's fp_dedup update_rec call,
    # never by the write-time backstop in file_rec()).
    fingerprint: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_category: Optional[str] = None
    occurrence_count: Optional[int] = Field(default=None, ge=1)
    last_seen: Optional[str] = None
    # ci-rca-identity-lifecycle: status-aware chain + regression + escape-attribution fields.
    # All Optional[...]=None -- backward-compatible additions, NO schema_version ceiling raise
    # (Decision 84/103/63: projection fields, never new ops_recommendations columns).
    regression_of: Optional[str] = Field(default=None, pattern=r"^rec-\d+$")
    fixed_by_sha: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{7,40}$")
    affected_nodeids: Optional[list[str]] = None
    flaky: Optional[bool] = None
    escape_class: Optional[str] = Field(default=None, pattern=r"^(no-edge|capped|unknown-data-edge)$")
    # ci-rca-evidence-scope-declaration: distinct keys are what make check-5 safe -- a shared
    # key would let the code-computed stamp overwrite the agent's echo before the cross-check
    # compares them. unobserved_steps is agent-authored (a comprehension probe: "what the
    # agent believed it did not observe"); unobserved_steps_authoritative is portal-derived,
    # stamped in code from the verified evidence bundle (never agent-authored), mirroring the
    # fingerprint/failure_category/affected_nodeids/escape_class pattern above. Both Optional
    # so a bundle-absent rec still validates and back_validate_ci_rca does not reclassify every
    # historical rec.
    unobserved_steps: Optional[list[_UnobservedStep]] = None
    unobserved_steps_authoritative: Optional[list[_UnobservedStep]] = None
    # Decision 186 (closure-time artifact obligation): THREE more Optional[...]=None additions,
    # same backward-compatible pattern as the ci-rca-identity-lifecycle block above -- NO
    # schema_version ceiling raise. closure_waiver_category is deliberately a BARE ALTERNATION
    # pattern (not a single "^(<cat>):\s*\S.*$" shape) so validate_ci_rca_lifecycle_projection's
    # live-model-derived enum obligation forces docs/contracts/ci-rca-lifecycle.yaml to declare a
    # matching enum:, making WAIVER_CATEGORIES schema-enforced in both --pre and full tiers.
    closure_artifact: Optional[str] = Field(default=None, pattern=r"^(shard|pytest|check|fixture):.+$")
    closure_waiver_category: Optional[str] = Field(
        default=None,
        pattern=r"^(stale_no_recurrence|environment_only|no_premerge_gate_by_design|risk_accepted)$",
    )
    closure_waiver_reason: Optional[str] = Field(default=None, min_length=1)
    # Decision 226: repair_track is the agent's diagnosis; repair_track_confirmed is stamped at fix time from the
    # fix commit's diff and workflow_tier from the verified bundle -- both portal-derived, never agent-authored.
    repair_track: Optional[str] = Field(default=None, pattern=r"^(test_defect|product_regression)$")
    repair_track_confirmed: Optional[str] = Field(default=None, pattern=r"^(test_defect|product_regression)$")
    workflow_tier: Optional[str] = None

    @field_validator("why_chain")
    @classmethod
    def _validate_why_chain_entries(cls, v: list[str], info: Any) -> list[str]:
        # CIRCA-04 ceiling: loosening-only and version-gated -- schema_version < 2 keeps the
        # original 250-char ceiling (no historical row is newly rejected); schema_version 2
        # raises the ceiling to 400. schema_version validates before why_chain (declaration
        # order), so info.data already carries it; default to 1 if somehow absent.
        schema_version = info.data.get("schema_version", 1)
        ceiling = 400 if schema_version == 2 else 250
        for i, entry in enumerate(v):
            if len(entry) < 40:
                raise ValueError(f"why_chain[{i}] is too short ({len(entry)} chars; min 40)")
            if len(entry) > ceiling:
                raise ValueError(f"why_chain[{i}] is too long ({len(entry)} chars; max {ceiling})")
        return v

    @model_validator(mode="after")
    def _validate_terminus(self) -> "CiRcaContext":
        if self.why_chain_terminus_override:
            return self
        final = self.why_chain[-1] if self.why_chain else ""
        lower = final.lower()
        has_systemic = any(kw in lower for kw in _WHY_CHAIN_SYSTEMIC_KEYWORDS)
        has_citation = bool(_WHY_CHAIN_CITATION_RE.search(final))
        errors: list[str] = []
        if not has_systemic:
            errors.append(f"why_chain final entry lacks a systemic keyword from {sorted(_WHY_CHAIN_SYSTEMIC_KEYWORDS)!r}")
        if not has_citation:
            errors.append("why_chain final entry lacks a file:line citation (e.g. scripts/validate.py:284)")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def is_track_a(context: dict) -> bool:
    """Decision 226 cl.1: a tests-only diagnosis counts only on the CI tier with no regression."""
    return (
        context.get("repair_track") == "test_defect"
        and not context.get("regression_of")
        and context.get("workflow_tier") == "CI"
    )


def preventive_deficiency(context: dict) -> list[str]:
    if context.get("preventive_action") or is_track_a(context):
        return []
    return ["preventive_action is required unless the red is Track A (is_track_a)"]


def derive_ci_rca_priority(context: dict) -> str:
    return "High" if is_track_a(context) else "Critical"


_PORTAL_OWNED_KEYS = ("workflow_tier", "repair_track_confirmed", "regression_of")


def apply_track_rules(fields: dict, context_v2_json: dict, *, portal_regression: bool, mode: str) -> None:
    """Derive priority and enforce the conditional preventive_action (Decision 226).

    When the portal itself made the red Track B (it set regression_of, or no verified bundle stamped
    workflow_tier) the missing preventive is stamped, never raised, so a red is never left unfiled.
    """
    fields["priority"] = derive_ci_rca_priority(context_v2_json)
    deficiency = preventive_deficiency(context_v2_json)
    if not deficiency:
        return
    portal_made_b = context_v2_json.get("repair_track") == "test_defect" and (
        portal_regression or not context_v2_json.get("workflow_tier")
    )
    if portal_made_b:
        _stamp_warn_mode_reject(context_v2_json, ["preventive_required"])
        context_v2_json["warn_mode_reject"]["mode_at_write"] = mode
        return
    if mode == "strict":
        raise ValueError("[CI_RCA_SCHEMA] " + "; ".join(deficiency))
    _stamp_warn_mode_reject(context_v2_json, ["preventive_required"])


def _validate_ci_rca_context_v2(context_v2_json: dict) -> list[str]:
    """Validate a context_v2_json dict against CiRcaContext. Returns a list of deficiency strings (empty = valid)."""
    from pydantic import ValidationError as PydanticError  # noqa: PLC0415

    try:
        CiRcaContext.model_validate(context_v2_json)
        return []
    except PydanticError as exc:
        return [str(e["msg"]) for e in exc.errors()]


class CiRcaEvidenceDispute(BaseModel):
    """Structured context schema for source=ci_rca_evidence_dispute recs (INTENT Section 4 check 8).

    Hard-enforced regardless of CI_RCA_STRICT_MODE: there is no legacy free-text variant for the
    dispute path, so no warn->strict migration window is needed. A malformed dispute payload always raises.
    """

    parent_rec_id: str = Field(pattern=r"^rec-\d+$")
    disputed_field: str = Field(pattern=r"^(earliest_viable_gate|actual_gate_that_caught_it|failure_category)$")
    agent_value: str = Field(min_length=1)
    bundle_value: str = Field(min_length=1)
    evidence_for_dispute: str = Field(min_length=120)


def _validate_ci_rca_dispute(context_v2_json: dict) -> list[str]:
    """Validate a context_v2_json dict against CiRcaEvidenceDispute. Returns a list of error strings (empty = valid)."""
    from pydantic import ValidationError as PydanticError  # noqa: PLC0415

    try:
        CiRcaEvidenceDispute.model_validate(context_v2_json)
        return []
    except PydanticError as exc:
        return [str(e["msg"]) for e in exc.errors()]


def _load_and_verify_bundle(evidence_bundle_ref: dict) -> dict | None:
    """Load the local canonical bundle and verify its SHA-256.

    Returns the bundle dict on success, None if bundle is absent (non-strict skip).
    Raises ValueError if SHA-256 mismatches (loud-fail regardless of mode).
    """
    import hashlib  # noqa: PLC0415

    sha256 = evidence_bundle_ref.get("sha256", "")
    if not sha256:
        return None

    # Search standard emit dirs for the bundle file
    candidates = [
        ROOT / "logs" / ".ci-rca-evidence-pending" / f"{sha256}.json",
    ]
    # CIRCA-01: env-parameterised, sha-keyed emit-dir candidate. A single BUNDLE_LOCAL_PATH
    # cannot cover multi-failure CI runs where the evidence step emits N bundles (N shas) to
    # CI_RCA_BUNDLE_EMIT_DIR/<sha>.json -- this candidate resolves each rec's own bundle by sha.
    emit_dir = os.environ.get("CI_RCA_BUNDLE_EMIT_DIR", "").strip()
    if emit_dir:
        candidates.insert(0, Path(emit_dir) / f"{sha256}.json")
    # Also try any BUNDLE_LOCAL path if set in env
    env_path = os.environ.get("CI_RCA_BUNDLE_LOCAL_PATH", "").strip()
    if env_path:
        candidates.insert(0, Path(env_path))

    bundle_path = next((p for p in candidates if p.exists()), None)
    if bundle_path is None:
        return None

    raw = bundle_path.read_bytes()
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bundle at {bundle_path} is not valid JSON: {exc}") from exc

    # SHA-256 verify: compute over all fields except sha256 itself
    payload = {k: v for k, v in bundle.items() if k != "sha256"}
    computed = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    if computed != sha256:
        raise ValueError(
            f"[CI_RCA_CROSS_CHECK] SHA-256 mismatch for bundle {sha256}: "
            f"computed={computed!r} -- bundle file may be corrupt or tampered. "
            "This is a loud-fail regardless of CI_RCA_STRICT_MODE."
        )
    return bundle


def _check_bundle_s3_existence(
    evidence_bundle_ref: dict,
    s3_client_factory: Optional[Callable[[], Any]] = None,
) -> str:
    """Verify the evidence bundle S3 object exists (c5 / INTENT Section 4 check 7).

    Returns one of:
      "ok"       -- upload_status='ok' and head_object confirms the object exists.
      "missing"  -- upload_status='ok' but head_object reports the object is absent.
      "degraded" -- upload_status != 'ok' (e.g. 'upload_failed'): no S3 call is made,
                    the bundle is a known-local-only/stale artefact.
      "fail_open" -- the S3 read itself could not be evaluated (bad s3_uri shape, missing
                     credentials, permission denied, or any other client error). Never
                     blocks filing -- the IAM grant for CI read access rides with c2.
    """
    upload_status = evidence_bundle_ref.get("upload_status")
    if upload_status != "ok":
        return "degraded"

    s3_uri = evidence_bundle_ref.get("s3_uri", "")
    match = re.match(r"^s3://([^/]+)/(.+)$", s3_uri)
    if not match:
        return "fail_open"
    bucket, key = match.group(1), match.group(2)

    try:
        if s3_client_factory is not None:
            client = s3_client_factory()
        else:
            import boto3  # noqa: PLC0415

            profile = os.environ.get("AWS_PROFILE")
            session = boto3.Session(profile_name=profile) if profile else boto3.Session()
            client = session.client("s3", region_name=_AWS_REGION)
        client.head_object(Bucket=bucket, Key=key)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        exc_str = str(exc) + type(exc).__name__
        if any(marker in exc_str for marker in ("404", "NoSuchKey", "NotFound")):
            return "missing"
        return "fail_open"


# CIRCA-04: substring -> stable schema_<rule> tag, checked in order (first match wins) against
# each pydantic deficiency message so a warn-mode schema reject is decomposable at read time
# instead of collapsing into the bare "schema_deficiency" bucket.
_SCHEMA_DEFICIENCY_RULES: tuple[tuple[str, str], ...] = (
    ("is too long", "schema_why_chain_too_long"),
    ("is too short", "schema_why_chain_too_short"),
    ("lacks a systemic keyword", "schema_terminus_missing_keyword"),
    ("lacks a file:line citation", "schema_missing_citation"),
)


def _classify_schema_deficiency(msg: str) -> str:
    """Map one pydantic deficiency message to a stable schema_<rule> tag (fallback: schema_deficiency)."""
    for substring, tag in _SCHEMA_DEFICIENCY_RULES:
        if substring in msg:
            return tag
    return "schema_deficiency"


def _stamp_warn_mode_reject(context_v2_json: dict, reasons: list[str]) -> None:
    """Stamp a warn-mode would-reject marker onto context_v2_json IN PLACE (c3 enabler).

    context_v2_json is a shared mutable dict passed by reference through file_rec() and
    _run_ci_rca_cross_check() -- multiple warn-mode branches (schema-deficiency, bundle-
    absent, S3-missing, cross-check checks 1-4) may each call this before the final
    json.dumps, so reasons accumulate rather than overwrite. Callers gate this so it is
    reached ONLY on the warn-mode branch (strict mode raises before any stamp).
    """
    marker = context_v2_json.setdefault("warn_mode_reject", {"reasons": [], "mode_at_write": "warn"})
    for reason in reasons:
        if reason not in marker["reasons"]:
            marker["reasons"].append(reason)
