# complexity-waiver: decision-43
"""Unified write gateway for recommendation and decision operations.

All writes to ops_recommendations and ops_decisions MUST go through this
module. Recs have no local read cache (Decision 215): writes never touch
logs/.recommendations-log.jsonl.

Failure mode (Decision 84 I-4): a write that cannot complete fails LOUDLY at
the call site -- there is no offline outbox. Entity ids (rec-NNN) are allocated
by the ducklake_writer atomically with the insert (I-2); decision numbering
authority is DECISIONS.md (the caller supplies decision_id).

Usage:
    from scripts.ops_data_portal import file_rec, update_rec
    rec_id = file_rec({"title": "...", "file": "...", "status": "open", ...})
    update_rec("rec-522", {"status": "closed", "execution_result": "success"})

CLI:
    python -m scripts.ops_data_portal --file-rec --title "..." --file "..." ...
    python -m scripts.ops_data_portal --update-rec rec-522 --status closed

Architecture (Decision 124): this module is a thin facade over the
scripts.ops_portal package. It KEEPS DEFINED the patch-epicentre
(file_rec/update_rec/propose_or_close_rec/_fetch_rec_from_reader/sync/
get_ci_rca_strict_mode) so the majority of existing test patch sites need zero
change; it imports every test-patched private dependency into this namespace so
patch("scripts.ops_data_portal.<sym>") keeps intercepting for facade-resident
callers; and it re-exports every public symbol plus the imported-name traps
(subprocess, ET, DECISIONS_JSONL, RECS_JSONL, Recommendation, validate_source)
a functions-only facade would otherwise miss.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess  # noqa: F401 -- imported-name trap; patch("scripts.ops_data_portal.subprocess.run") test target
import sys
import xml.etree.ElementTree as ET  # noqa: F401 -- imported-name trap; patch("...ET.parse") test target
from datetime import date
from pathlib import Path
from typing import Optional, cast

import yaml

from scripts.executor.jsonl_store import (
    DECISIONS_JSONL,  # noqa: F401 -- imported-name trap; read by decisions.py, re-exported here
    RECS_JSONL,  # noqa: F401 -- retained patch target (tests patch it); the recs cache is retired (Decision 215)
    Recommendation,
)
from scripts.executor.rec_write_guidance import validate_source
from scripts.ops_portal import decisions as _decisions
from scripts.ops_portal._common import _AWS_REGION, _REPO_ROOT, _SSO_PROFILE, ROOT  # noqa: F401
from scripts.ops_portal.cache import (  # noqa: F401
    _append_to_local_jsonl,
    _refresh_cache_after_write,
    _sanitize_record,
    _sync_table,
)
from scripts.ops_portal.ci_rca_lifecycle import (  # noqa: F401
    classify_closed_head,
    closed_head_of_chain,
    current_commit_sha,
    file_regression_record,
)
from scripts.ops_portal.ci_rca_runtime import (  # noqa: F401
    _CI_RCA_BACK_VALIDATE_DEFAULT_SINCE,
    _print_ci_rca_back_validation_report,
    _run_ci_rca_cross_check,
    back_validate_ci_rca,
    bump_ci_rca_occurrence,
    find_open_ci_rca_rec_by_fingerprint,
)
from scripts.ops_portal.ci_rca_schema import (  # noqa: F401
    _CI_RCA_VALID_MODES,
    _PORTAL_OWNED_KEYS,
    CiRcaContext,
    CiRcaEvidenceDispute,
    _classify_schema_deficiency,
    _DetectionGap,
    _EvidenceBundleRef,
    _stamp_warn_mode_reject,
    _validate_ci_rca_context_v2,
    _validate_ci_rca_dispute,
    apply_track_rules,
)
from scripts.ops_portal.cli import main
from scripts.ops_portal.closure_gate import (  # noqa: F401
    ARTIFACT_KINDS,
    KIND_STRENGTH,
    WAIVER_CATEGORIES,
    AcceptanceVerdictRefused,
    ClosureArtifactRequired,
    assert_acceptance_verdict,
    assert_closure_obligation,
    closure_stamps_applicable,
    confirmed_track_stamp,
    parse_context_json,
)
from scripts.ops_portal.maintenance_ops import (  # noqa: F401
    enqueue_findings,
    find_open_postmortem_for,
    purge_postmortems_for,
    repair_dependency_tokens,
    selftest_read,
    selftest_roundtrip,
)
from scripts.ops_portal.outcomes import FileRecOutcome
from scripts.ops_portal.risk_scoring import (  # noqa: F401
    _compute_risk_score,
    _derive_computed_fields,
    compute_automatable,
    compute_risk,
    load_capabilities,
)
from scripts.ops_portal.write_validators import gate_write  # noqa: F401
from scripts.ops_portal.writer_transport import (  # noqa: F401
    _ducklake_write,
    _project_ops_record,
    _resolve_writer_url,
)

logger = logging.getLogger(__name__)


class RecNotFound(RuntimeError):
    """Raised by update_rec when rec_id does not exist in the current DuckLake projection.

    Distinguishes an absent rec (a referential-existence miss) from every other RuntimeError
    update_rec can raise (reader-unreachable, a writer-transport failure) -- callers that need to
    treat "no such rec" as a benign skip, while any other RuntimeError must still surface loudly,
    catch this subclass specifically rather than discriminating on the exception's message text.
    """


_LAZY_DECISIONS_NAMES = frozenset(
    {
        "file_decision",
        "update_decision",
        "backfill_decisions_from_md",
        "_fetch_decision_from_reader",
    }
)


def __getattr__(name: str):
    # PEP 562 lazy re-export (rec-2729): an eager `from ... import name` binds a value once, at
    # this facade's first import -- under pytest-randomly, if that first import lands while a test
    # has mock.patch()-ed the submodule symbol, the facade permanently captures the Mock for the
    # rest of the session. Resolving via getattr() on _decisions at access time instead means every
    # access re-reads the submodule's current attribute, so patches are always visible and reverted.
    if name in _LAZY_DECISIONS_NAMES:
        return getattr(_decisions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_FEATURE_FLAGS_YAML = _REPO_ROOT / "config" / "feature_flags.yaml"

_ci_rca_strict_mode_cache: Optional[str] = None
_ci_rca_strict_mode_cache_path: Optional[Path] = None


def get_ci_rca_strict_mode() -> str:
    """Return the CI_RCA_STRICT_MODE flag value ('warn' or 'strict').

    Module-level cached read of config/feature_flags.yaml, re-read only when
    the bound _FEATURE_FLAGS_YAML path changes (no hot-reload for a constant
    path, Decision 88). Defaults to 'warn' when the key or file is absent.
    Raises ValueError for unrecognised values so misconfiguration is loud
    (Decision 55).
    """
    global _ci_rca_strict_mode_cache, _ci_rca_strict_mode_cache_path
    if _ci_rca_strict_mode_cache is not None and _ci_rca_strict_mode_cache_path == _FEATURE_FLAGS_YAML:
        return _ci_rca_strict_mode_cache
    try:
        data = yaml.safe_load(_FEATURE_FLAGS_YAML.read_text(encoding="utf-8")) or {}
        value = data.get("CI_RCA_STRICT_MODE", "warn")
    except (FileNotFoundError, OSError, yaml.YAMLError):
        value = "warn"
    if value not in _CI_RCA_VALID_MODES:
        raise ValueError(f"CI_RCA_STRICT_MODE={value!r} is not a valid mode; accepted: {sorted(_CI_RCA_VALID_MODES)}")
    _ci_rca_strict_mode_cache = value
    _ci_rca_strict_mode_cache_path = _FEATURE_FLAGS_YAML
    return value


def file_rec(
    fields: dict,
    profile: Optional[str] = None,
    _migration_int_id: Optional[int] = None,
    _skip_sync: bool = False,
    context_v2_json: Optional[dict] = None,
) -> str:
    """File a new recommendation and return its allocated ID (see file_rec_with_outcome)."""
    return file_rec_with_outcome(fields, profile, _migration_int_id, _skip_sync, context_v2_json).rec_id


def file_rec_with_outcome(
    fields: dict,
    profile: Optional[str] = None,
    _migration_int_id: Optional[int] = None,
    _skip_sync: bool = False,
    context_v2_json: Optional[dict] = None,
) -> FileRecOutcome:
    """File a new recommendation; the ducklake_writer allocates its ID atomically with the insert.

    On success returns a FileRecOutcome whose rec_id is the allocated ID (e.g. 'rec-2171'); inserted is False,
    with a reason, when a ci_rca dedup path returned an existing id instead of inserting. On any failure the
    call raises LOUDLY (Decision 84 I-4) -- there is no offline outbox; the
    transient-5xx retry inside _ducklake_write (idempotent via the per-call
    ULID) is the only retry.

    Args:
        fields: Rec fields (MUST include at minimum: title, file, status,
                source, effort, priority, context, acceptance, risk).
        profile: Optional AWS profile override (uses AWS_PROFILE env var by default).
        _migration_int_id: PRIVATE. Backfill-only: preserves a historical integer ID
            via a caller-keyed write_ops upsert instead of writer allocation. The id
            is formed as f"rec-{n:03d}" (zero-padded under 1000) so dependency /
            priority-queue FKs to padded ids still match. Must not be used elsewhere.
        _skip_sync: PRIVATE. Accepted for bulk-import callers; a no-op for recs (no local cache).
        context_v2_json: Optional structured CiRcaContext dict for source=ci_rca recs.
            When provided: validated against CiRcaContext in warn mode (deficiencies log
            a structured warning but do NOT raise); a >=80-char human summary is written
            into the legacy context column. When absent with source=ci_rca: a deprecation
            warning is logged and the rec is filed with legacy free-text context only.

    Returns:
        FileRecOutcome(rec_id, inserted, reason). Raises on failure (no offline mode).

    Raises:
        ValueError: If any required non-empty field is absent or blank.
        ValidationError: If fields fail Recommendation schema validation (online only).
    """
    fields = dict(fields)  # defensive copy -- do not mutate caller's dict

    if fields.get("source") == "ci_rca" and not (fields.get("file") or "").strip():
        raise ValueError(
            "source='ci_rca' requires non-empty source_file (the file implicated by the failure diagnosis); "
            "see .claude/agents/scheduled/ci-rca.md"
        )

    # Section-4 check-8 carve-out: dispute recs are validated ONLY against CiRcaEvidenceDispute
    # and NEVER subjected to the ci_rca checks 1-7 (no CiRcaContext, no source_file requirement).
    # This MUST run before (and short-circuit) the source=='ci_rca' block below.
    if fields.get("source") == "ci_rca_evidence_dispute":
        if context_v2_json is None:
            raise ValueError(
                "source='ci_rca_evidence_dispute' requires context_v2_json carrying a CiRcaEvidenceDispute payload"
            )
        errors = _validate_ci_rca_dispute(context_v2_json)
        if errors:
            raise ValueError(f"[ci_rca_evidence_dispute] context_v2_json failed validation: {'; '.join(errors)}")
        # Build >=80-char human summary for the legacy context column. CiRcaEvidenceDispute's
        # evidence_for_dispute carries min_length=120 (enforced above), so the composed summary
        # is always well over 80 chars -- no short-summary fallback is reachable here.
        disp = context_v2_json
        summary = (
            f"Dispute on {disp.get('disputed_field', '')}: "
            f"agent={disp.get('agent_value', '')!r} vs bundle={disp.get('bundle_value', '')!r}. "
            f"Evidence: {disp.get('evidence_for_dispute', '')[:200]}"
        )
        if not fields.get("context") or len((fields.get("context") or "").strip()) < 80:
            fields["context"] = summary

    # context_v2_json warn-mode validation for source=ci_rca (CI_RCA_STRICT_MODE; INTENT Section 1).
    # Must run before gate_write so the human summary can satisfy the context min_length floor.
    if fields.get("source") == "ci_rca":
        if context_v2_json is not None:
            for _owned in _PORTAL_OWNED_KEYS:
                context_v2_json.pop(_owned, None)
            deficiencies = _validate_ci_rca_context_v2(context_v2_json)
            if deficiencies:
                mode = get_ci_rca_strict_mode()
                if mode == "strict":
                    raise ValueError(
                        f"[CI_RCA_STRICT_MODE=strict] context_v2_json failed validation: {'; '.join(deficiencies)}"
                    )
                logger.warning(
                    "[CI_RCA_STRICT_MODE=warn] context_v2_json deficiencies (rec filed anyway): %s",
                    "; ".join(deficiencies),
                )
                _stamp_warn_mode_reject(context_v2_json, [_classify_schema_deficiency(d) for d in deficiencies])
            # Build a >=80-char human summary for the legacy context column from the structured schema.
            parts = []
            if context_v2_json.get("proximate_cause"):
                parts.append(f"Proximate cause: {context_v2_json['proximate_cause'][:400]}")
            if context_v2_json.get("corrective_action"):
                parts.append(f"Corrective: {context_v2_json['corrective_action'][:200]}")
            if context_v2_json.get("preventive_action"):
                parts.append(f"Preventive: {context_v2_json['preventive_action'][:200]}")
            summary = " | ".join(parts)
            if len(summary) < 80:
                summary = summary + " [ci_rca structured context -- see context_v2_json for full detail]"
            if not fields.get("context"):
                fields["context"] = summary
            elif len(fields["context"].strip()) < 80:
                fields["context"] = summary
        else:
            # CIRCA-02: strict mode rejects a legacy no-context_v2_json write for a NEW rec;
            # warn mode keeps accepting it (rollout window) with the deprecation log. Historical
            # rows filed before this schema landed are grandfathered by back_validate_ci_rca and
            # are never retro-rejected -- this check only fires for writes made through file_rec now.
            if get_ci_rca_strict_mode() == "strict":
                raise ValueError(
                    "[CI_RCA_STRICT_MODE=strict] source='ci_rca' requires context_v2_json; legacy "
                    "free-text-only ci_rca recs are no longer accepted in strict mode."
                )
            logger.warning(
                "[PORTAL] source=ci_rca rec filed with legacy free-text context (no context_v2_json). "
                "Migrate to context_v2_json per PLAN-ci-rca-schema-enforcement."
            )

    # Cross-check spine (c10): load local bundle, verify SHA-256, bundle-wins comparison
    if fields.get("source") == "ci_rca" and context_v2_json is not None:
        _run_ci_rca_cross_check(context_v2_json)

        # CIRCA-03(c): write-time dedup backstop -- the cross-run race guard. Runs AFTER the
        # cross-check spine so context_v2_json.fingerprint (stamped from the verified bundle
        # above) is available. A hit means this fingerprint's root cause already has an OPEN
        # rec, so this call bumps occurrence_count/last_seen on the existing rec (via
        # bump_ci_rca_occurrence) and returns its id instead of inserting a duplicate -- both
        # the workflow's fp_dedup path (scripts.ci_rca.dedup) and this backstop now record
        # recurrence through the same helper. force_rca (CI dedup-bypass env, Decision 74)
        # skips this guard entirely.
        if os.environ.get("CI_RCA_FORCE_RCA", "").strip().lower() not in ("1", "true"):
            fingerprint = context_v2_json.get("fingerprint")
            if fingerprint:
                existing_id = find_open_ci_rca_rec_by_fingerprint(fingerprint, profile=profile)
                if existing_id:
                    bump_ci_rca_occurrence(existing_id, profile=profile)
                    logger.info(
                        "[CI_RCA_DEDUP] fingerprint=%s matches open %s; skipping insert, bumped occurrence "
                        "(write-time backstop).",
                        fingerprint,
                        existing_id,
                    )
                    return FileRecOutcome(existing_id, False, "ci_rca_open_duplicate")

                # ci-rca-identity-lifecycle: no OPEN match. Status-aware chain: check the
                # fingerprint's CLOSED head (if any) and run the regression-vs-drop ancestry
                # classification (Decision 55 fail-closed to REGRESSION when the head has no
                # fixed_by_sha) -- NEVER a closed->open flip (the rec-2644 revive path, its
                # status-flip mutator and READ-ONLY recency finder, is fully retired; a closed
                # rec's fixed_by_sha is a closure PROOF, citing Decision 103's closure-proof
                # clause, that reopening would discard without contrary evidence).
                head = closed_head_of_chain(fingerprint, profile=profile)
                if head is not None:
                    failing_sha = current_commit_sha()
                    verdict = classify_closed_head(failing_sha, head)
                    if verdict == "drop":
                        logger.info(
                            "[CI_RCA_LIFECYCLE] fingerprint=%s failing_sha=%s is an ancestor of closed "
                            "%s's fixed_by_sha -- stale-code rerun, dropping (no insert, no bump, no reopen).",
                            fingerprint,
                            failing_sha,
                            head.rec_id,
                        )
                        return FileRecOutcome(head.rec_id, False, "ci_rca_stale_rerun")
                    fields, context_v2_json = file_regression_record(fields, context_v2_json, head.rec_id)
                    logger.info(
                        "[CI_RCA_LIFECYCLE] fingerprint=%s matches closed %s with no ancestry proof of "
                        "fix -- filing a NEW REGRESSION record (regression_of=%s); never reopening.",
                        fingerprint,
                        head.rec_id,
                        head.rec_id,
                    )

    if fields.get("source") == "ci_rca" and context_v2_json is not None:
        apply_track_rules(
            fields,
            context_v2_json,
            portal_regression=bool(context_v2_json.get("regression_of")),
            mode=get_ci_rca_strict_mode(),
        )

    _derive_computed_fields(fields)

    validate_source(fields.get("source", ""))
    if _migration_int_id is None:
        gate_write("ops_recommendations", fields, insert=True)
    else:
        gate_write("ops_recommendations", {**fields, "id": f"rec-{_migration_int_id:03d}"}, insert=False)

    merged = dict(fields)
    if context_v2_json is not None:
        merged["context_v2_json"] = json.dumps(context_v2_json)
    merged.pop("id", None)
    merged.setdefault("date", date.today().isoformat())

    response: dict = {}
    if _migration_int_id is not None:
        # Backfill path: the historical id is preserved via a caller-keyed write_ops upsert.
        rec_id = f"rec-{_migration_int_id:03d}"
        merged["id"] = rec_id
        Recommendation.model_validate(merged)
        response = _ducklake_write("ops_recommendations", merged, action="write_ops", profile=profile)
    else:
        # Fail fast client-side with a placeholder id; the writer's schema gate is authoritative.
        Recommendation.model_validate({**merged, "id": "rec-0"})
        # The writer allocates rec-NNN atomically with the insert (Decision 84 I-2). The
        # idempotency ULID makes a response-lost retry return the original allocation.
        from src.common.ducklake_runtime import mint_write_identity  # noqa: PLC0415

        response = _ducklake_write(
            "ops_recommendations",
            merged,
            action="file_ops",
            profile=profile,
            idempotency_ulid=mint_write_identity().ulid,
        )
        rec_id = response.get("key", "")
        if not rec_id:
            raise RuntimeError(f"ducklake_writer file_ops returned no allocated key: {response}")
        merged["id"] = str(rec_id)

    logger.info("[PORTAL] Filed %s: %s", rec_id, merged.get("title", ""))
    return FileRecOutcome(str(rec_id), True, None)


def _fetch_rec_from_reader(rec_id: str, profile: Optional[str] = None) -> Optional[dict]:
    """Fetch a single ops_recommendations record by id via the rec_by_id read verb.

    Closed boundary (Decision 81 cl.7 / Decision 84 I-3): the read transits the
    ducklake_reader named-verb surface; no SQL leaves the client. Decision 69:
    raises RuntimeError if the reader is unreachable. Never falls back to the
    local JSONL cache.

    Returns the record dict (coerced and sanitised) or None if not found.
    """
    if not re.fullmatch(r"rec-\d+", rec_id):
        raise ValueError(f"_fetch_rec_from_reader: invalid rec_id: {rec_id!r}")

    from scripts.sync.ops import _coerce_ops_rec_row  # noqa: PLC0415
    from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

    # make_reader() is annotated -> Reader (the Protocol, which deliberately does not declare
    # named()) but only ever constructs a DuckLakeReader; cast narrows the type at this one call
    # site rather than widening the Protocol by omission.
    reader = cast(DuckLakeReader, make_reader(profile=profile))
    rows = reader.named("rec_by_id", id=rec_id)
    if not rows:
        return None
    coerced = _coerce_ops_rec_row(dict(rows[0]))
    return _sanitize_record(coerced) if coerced is not None else None


def update_rec(
    rec_id: str,
    updates: dict,
    profile: Optional[str] = None,
    *,
    closure_artifact: Optional[str] = None,
    closure_waiver_category: Optional[str] = None,
    closure_waiver_reason: Optional[str] = None,
    closure_fix_sha: Optional[str] = None,
    acceptance_verdict: Optional[dict] = None,
    closing_sha: Optional[str] = None,
) -> bool:
    """Merge update fields into an existing recommendation and write via the DuckLake closed boundary.

    Reads the current record via the DuckLake reader, its sole backend. Raises RuntimeError if
    the warehouse is unreachable. Merges updates, validates the merged record (write-time content
    gate, Decision 66), asserts the Decision 186 closure obligation (see Raises), routes the
    write to _ducklake_write, writes through to local JSONL, then refreshes the read cache.

    The four closure_* kwargs (Decision 186) are keyword-only and shallow-merge into the written
    context_v2_json: closure_artifact/closure_waiver_category/closure_waiver_reason write their
    own keys; closure_fix_sha writes the EXISTING fixed_by_sha key (no new schema field --
    mirrors ci_rca_lifecycle.stamp_fixed_by_sha's target). Supplying closure_fix_sha in the SAME
    call as a closing write is what makes the artifact route satisfiable in one write.

    Returns:
        True on success.

    Raises:
        ValueError: The merged record failing the contract's row rules (gate_write; dated rules are the
            writer's alone and surface as its 422), or a
            closure_* kwarg supplied for a rec with no existing context_v2_json to stamp into.
        ClosureArtifactRequired: This write closes (bound set: closed/declined/superseded, from a
            not-already-bound status) an escape-classified rec with no resolvable
            closure_artifact and no well-formed waiver pair. Writes nothing on this path.
        AcceptanceVerdictRefused: acceptance_verdict (Decision 201) is supplied and does not
            resolve to a holds verdict keyed to this rec, its acceptance text and closing_sha.
            acceptance_verdict/closing_sha are gate INPUTS, never closure_stamps -- neither is
            stamped into context_v2_json, so closure_stamps_applicable never gates either.
        ValidationError: The merged record fails schema validation.
        RuntimeError: The warehouse is unreachable for the read step or the write fails.
    """
    # Referential existence (CD.33 cl.8 / D-5): an absent rec loud-fails. This replaces the prior
    # permissive `existing or {}` upsert-on-absent, which silently created a partial record.
    existing = _fetch_rec_from_reader(rec_id, profile=profile)
    if existing is None:
        raise RecNotFound(
            f"update_rec: {rec_id} does not exist in the current projection -- an absent rec cannot be "
            "updated (referential, CD.33 cl.8 / D-5). File it first via file_rec."
        )
    merged = {**existing, **updates}
    merged["id"] = rec_id  # always preserve the ID

    # Decision 186: stamp the closure_* kwargs into the MERGED context_v2_json blob before the
    # gate reads it -- closure_fix_sha writes the pre-existing fixed_by_sha key, never a new one.
    closure_stamps = {
        "closure_artifact": closure_artifact,
        "closure_waiver_category": closure_waiver_category,
        "closure_waiver_reason": closure_waiver_reason,
        "fixed_by_sha": closure_fix_sha,
    }
    closure_stamps = {k: v for k, v in closure_stamps.items() if v is not None}
    if closure_fix_sha:
        closure_stamps.update(confirmed_track_stamp(merged.get("source"), closure_fix_sha, ROOT))
    if closure_stamps:
        if not closure_stamps_applicable(merged.get("context_v2_json")):
            raise ValueError(
                f"update_rec: a closure_* kwarg was supplied for {rec_id}, which is not stampable per "
                "closure_stamps_applicable -- it carries no existing context_v2_json to stamp into."
            )
        ctx_for_stamp = parse_context_json(merged.get("context_v2_json"))
        ctx_for_stamp.update(closure_stamps)
        merged["context_v2_json"] = json.dumps(ctx_for_stamp)

    gate_write("ops_recommendations", merged, insert=False, existing=existing)

    Recommendation.model_validate(merged)  # raises on failure

    # Decision 186: the predicate reads existing UNION merged context -- context_v2_json carries no
    # contract rule and no monotonicity guard, so a closing write that
    # blanks it must not thereby erase its own obligation. Asserted immediately before the write.
    assert_closure_obligation(
        existing.get("status"),
        merged.get("status"),
        parse_context_json(existing.get("context_v2_json")),
        parse_context_json(merged.get("context_v2_json")),
    )
    assert_acceptance_verdict(
        existing.get("status"), merged.get("status"), rec_id, merged.get("acceptance"), closing_sha, acceptance_verdict
    )

    # ops_recommendations always routes to DuckLake (Decision 81 cl.7 / T2.19).
    _ducklake_write("ops_recommendations", merged, action="update_ops", profile=profile)
    logger.info("[PORTAL] Updated %s: %s", rec_id, list(updates.keys()))
    return True


def propose_or_close_rec(
    rec_id: str,
    verdict: str,
    evidence: str,
    *,
    deterministic: bool = False,
    profile: Optional[str] = None,
) -> Optional[str]:
    """Apply a relevance verdict to a rec (T3.8 / CD.36 close_proposed lifecycle support).

    Decision 70: verdict and lifecycle status are orthogonal; no new status enum value.
    CD.36 / Decision 55: only deterministic satisfied auto-closes; every semantically-judged
    verdict emits a close_proposed proposal string for a human to run.

    Args:
        rec_id:       The rec ID to act on (e.g. 'rec-042').
        verdict:      Relevance verdict from rec_relevance.evaluate_rec_relevance().
        evidence:     Evidence/proof string from the evaluator.
        deterministic: True when the verdict came from the acceptance probe (on-demand per-rec).
                       False for semantic signals (commit correlation, Jaccard, etc.).
        profile:      Optional AWS profile override for update_rec.

    Returns:
        None when the rec is auto-closed (deterministic satisfied) or no action is warranted
        (verdict is 'relevant' or 'unknown').
        A close_proposed command string for all other verdicts -- print this for the operator.

    Raises:
        ClosureArtifactRequired: Decision 186 point 2 narrows the deterministic-satisfied
            auto-close for an escape-classified rec: update_rec raises rather than closing
            silently, and this propagates unchanged to this function's only callers (agents).
    """
    if verdict in ("relevant", "unknown"):
        return None
    if deterministic and verdict == "satisfied":
        update_rec(rec_id, {"status": "closed", "resolution": evidence}, profile=profile)
        return None
    safe_evidence = evidence.replace('"', '\\"')
    return (
        f"bin/venv-python -m scripts.ops_data_portal --update-rec {rec_id}"
        f' --status closed --resolution "{safe_evidence}"'
        f"  # relevance={verdict}; if refused (escape-classified, Decision 186), add --closure-artifact "
        "<kind:ref> --closure-fix-sha <sha>, or --closure-waiver-category <cat> --closure-waiver-reason <text>"
    )


def sync(tables: Optional[list] = None) -> dict:
    """Pull the local read-cache fresh from the DuckLake reader (the single flush primitive).

    Args:
        tables: Ops table names to sync. Defaults to ops_decisions, ops_priority_queue.

    Returns:
        {"pulled": {table: rows}}

    Raises:
        ValueError: ops_recommendations requested (no recs cache, Decision 215).
        RuntimeError: If the reader boundary is unreachable.
    """
    from scripts.sync.ops import _pull_single_table  # noqa: PLC0415

    ops_tables = tables or ["ops_decisions", "ops_priority_queue"]
    if "ops_recommendations" in ops_tables:
        raise ValueError("ops_recommendations has no local cache (Decision 215): read recs through the named verbs")

    # Every migrated table is a live `current` projection behind the atomic catalog commit
    # (Decision 84 I-1): a cache pull per table is the whole job -- no drain/compact/view-refresh.
    pulled: dict[str, int] = {table: _pull_single_table(table) for table in ops_tables}
    return {"pulled": pulled}


if __name__ == "__main__":
    sys.exit(main())
