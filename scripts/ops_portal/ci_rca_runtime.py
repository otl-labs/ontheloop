# complexity-waiver: decision-43
"""source=ci_rca WRITE-TIME + AUDIT RUNTIME.

Owner-concern: the cross-check spine that runs at file_rec() write time for source=ci_rca
recs (bundle load/verify, S3 existence, bundle-wins comparisons), the fingerprint dedup
read path, the back-validation batch report, and the occurrence-bump helper. Schema
definition/shape validation lives in ci_rca_schema.py; portal CRUD (file_rec/update_rec)
stays in the facade.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from scripts.ops_portal.ci_rca_schema import (
    _check_bundle_s3_existence,
    _load_and_verify_bundle,
    _stamp_warn_mode_reject,
    _validate_ci_rca_context_v2,
    preventive_deficiency,
)

logger = logging.getLogger(__name__)

# CIRCA-03(b)/(c): markers that classify a DuckLakeReader failure as a genuine connectivity
# outage (Neon scale-to-zero exhausting retries, or the Function URL itself unresolvable) --
# see src.common.ducklake_reader_client.DuckLakeReader._reader_url/_invoke, the only raise sites this
# helper's try/except can observe. Any OTHER exception (bad row_filter, unexpected shape, a
# non-connectivity RuntimeError) is NOT reader-unreachable and must propagate (Decision 55).
_READER_UNREACHABLE_MARKERS = (
    "cannot reach the DuckLake reader",
    "ducklake_reader",
)

_CROSS_CHECK_TAG_RE = re.compile(r"check-(\d+)")

# Phase-1 landing date for source=ci_rca context_v2_json (INTENT Section 6); the back-validation
# default floor so the report covers only the warn-mode window, not pre-schema history.
_CI_RCA_BACK_VALIDATE_DEFAULT_SINCE = "2026-06-15"
# INTENT known-gap (line 562): --refile-audit files at most this many audit recs per invocation.
_CI_RCA_BACK_VALIDATE_DAILY_CAP = 20


def _is_reader_unreachable_error(exc: Exception) -> bool:
    """Narrow fail-open classifier for the CI-RCA fingerprint dedup reader call (CIRCA-03)."""
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc)
    return any(marker in msg for marker in _READER_UNREACHABLE_MARKERS)


def find_open_ci_rca_rec_by_fingerprint(fingerprint: str, profile: Optional[str] = None) -> Optional[str]:
    """Return the id of an OPEN source=ci_rca rec whose context_v2_json.fingerprint matches, or None.

    Reads transit the closed DuckLake reader via a single-key STRUCTURAL row_filter
    (source = 'ci_rca') -- Decision 84 I-3, no caller SQL. The status=open filter and the
    fingerprint match are applied in-process on the returned rows (no json_extract named verb).

    Fails OPEN (returns None + a loud log) ONLY when the reader itself is unreachable
    (connectivity failure / Neon scale-to-zero exhausting retries, per
    _is_reader_unreachable_error). Any other exception raises (Decision 55) -- this is the
    SAME in-process reader path used by both the workflow's pre-agent skip (CIRCA-03(b)) and
    the write-time backstop (CIRCA-03(c)); a read cache is never a dedup source (CLAUDE.md).
    """
    from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

    try:
        rows = make_reader(profile=profile).current_state("ops_recommendations", row_filter="source = 'ci_rca'") or []
    except Exception as exc:  # noqa: BLE001
        if _is_reader_unreachable_error(exc):
            logger.warning(
                "[CI_RCA_DEDUP] DuckLake reader unreachable while searching for fingerprint=%s; "
                "failing open (dedup skipped, filing proceeds): %s",
                fingerprint,
                exc,
            )
            return None
        raise

    for row in rows:
        if row.get("status") != "open":
            continue
        ctx_raw = row.get("context_v2_json")
        if not ctx_raw:
            continue
        try:
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(ctx, dict) and ctx.get("fingerprint") == fingerprint:
            return row.get("id")
    return None


def _unobserved_pairs_from_scope(scope: list) -> list[dict[str, int]]:
    """Derive the authoritative {job_id, step_index} pairs the run's evidence did NOT retrieve
    a log for, from a verified bundle's retrieval_evidence.scope (AC7). Sorted for determinism."""
    pairs: list[dict[str, int]] = []
    for job_entry in scope:
        if not isinstance(job_entry, dict) or not isinstance(job_entry.get("job_id"), int):
            continue
        job_id = job_entry["job_id"]
        for step in job_entry.get("steps", []):
            if isinstance(step, dict) and step.get("log_retrieved") is False and isinstance(step.get("step_index"), int):
                pairs.append({"job_id": job_id, "step_index": step["step_index"]})
    pairs.sort(key=lambda p: (p["job_id"], p["step_index"]))
    return pairs


def _pair_set(value: Any) -> Optional[set[tuple[int, int]]]:
    """Parse a {job_id, step_index}-pair list into a comparable set. Returns None (never
    raises) for anything not shaped that way -- including a flat-index echo -- so a malformed
    agent echo compares as a mismatch rather than crashing the cross-check spine."""
    if not isinstance(value, list):
        return None
    pairs: set[tuple[int, int]] = set()
    for item in value:
        if not (isinstance(item, dict) and isinstance(item.get("job_id"), int) and isinstance(item.get("step_index"), int)):
            return None
        pairs.add((item["job_id"], item["step_index"]))
    return pairs


def _run_ci_rca_cross_check(
    context_v2_json: dict,
    s3_client_factory: Optional[Callable[[], Any]] = None,
) -> None:
    """Run the cross-check spine for source=ci_rca recs.

    Bundle-absent fail-loud (c12(ii)): when evidence_bundle_ref is absent/empty, the rec is
    forced onto the mandatory human-review route (rca_confidence='undetermined') instead of
    being accepted fully-unchecked. Strict mode rejects otherwise; warn mode logs and accepts.

    c5 (Section 4 check 7): when evidence_bundle_ref is present, verifies the S3 object exists
    via head_object (strict reject / warn on a missing object; degraded accept on
    upload_status='upload_failed'; fail-open accept on any S3 read error so a runner without
    S3 read access never has filing wedged).

    Then loads the local canonical evidence bundle (from evidence_bundle_ref.sha256), verifies
    its SHA-256, and compares bundle-wins fields.

    Raises ValueError in strict mode; logs warnings in warn mode.
    Never raises for SHA-256 mismatch (always loud-fail).
    """
    from scripts.ops_data_portal import get_ci_rca_strict_mode  # noqa: PLC0415

    mode = get_ci_rca_strict_mode()
    evidence_bundle_ref = context_v2_json.get("evidence_bundle_ref") or {}

    if not evidence_bundle_ref:
        rca_confidence = context_v2_json.get("rca_confidence")
        bundle_absent_msg = (
            "[CI_RCA_BUNDLE_ABSENT] rec filed with no evidence_bundle_ref (no evidence bundle was "
            "generated or referenced for this run). A rec with no evidence bundle MUST set "
            "rca_confidence='undetermined' so it routes to the mandatory human-review surface "
            "(preflight 'CI-RCA Mandatory Human Review'); otherwise it would be accepted fully-unchecked."
        )
        if rca_confidence == "undetermined":
            logger.warning("%s rca_confidence=undetermined -- routed to mandatory human review.", bundle_absent_msg)
            return
        if mode == "strict":
            raise ValueError(f"[CI_RCA_STRICT_MODE=strict] {bundle_absent_msg} rca_confidence={rca_confidence!r}.")
        logger.warning("[CI_RCA_STRICT_MODE=warn] %s rca_confidence=%r (rec filed anyway).", bundle_absent_msg, rca_confidence)
        _stamp_warn_mode_reject(context_v2_json, ["bundle_absent"])
        return

    s3_check = _check_bundle_s3_existence(evidence_bundle_ref, s3_client_factory=s3_client_factory)
    if s3_check == "missing":
        s3_msg = (
            f"[CI_RCA_EVIDENCE_S3_MISSING] evidence_bundle_ref.upload_status='ok' but no object exists at "
            f"{evidence_bundle_ref.get('s3_uri', '')!r}. The bundle may have been deleted, or the upload "
            "silently failed without updating upload_status."
        )
        if mode == "strict":
            raise ValueError(f"[CI_RCA_STRICT_MODE=strict] {s3_msg}")
        logger.warning("[CI_RCA_STRICT_MODE=warn] %s (rec filed anyway)", s3_msg)
        _stamp_warn_mode_reject(context_v2_json, ["s3_missing"])
    elif s3_check == "degraded":
        logger.warning(
            "[CI_RCA_EVIDENCE_S3_DEGRADED] evidence_bundle_ref.upload_status=%r (not 'ok') -- S3 existence "
            "not verified; bundle content is stale/local-only.",
            evidence_bundle_ref.get("upload_status"),
        )
    elif s3_check == "fail_open":
        logger.warning(
            "[CI_RCA_EVIDENCE_S3_FAIL_OPEN] S3 head_object check could not run (missing credentials, denied "
            "permission, or a malformed s3_uri) -- accepting without S3 verification so filing is never "
            "wedged by a runner lacking S3 read access."
        )

    try:
        bundle = _load_and_verify_bundle(evidence_bundle_ref)
    except ValueError as exc:
        # SHA-256 mismatch is always loud-fail
        raise ValueError(str(exc)) from exc

    if bundle is None:
        return  # bundle not found locally -- skip cross-check (Decision 88)

    # CIRCA-03: stamp portal-derived dedup metadata from the VERIFIED bundle (never
    # agent-authored) -- mirrors the warn_mode_reject Tier-B pattern. This is the sole place
    # fingerprint/failure_category enter context_v2_json; file_rec()'s write-time backstop
    # reads them from here, anchored to the same bundle whose SHA-256 was just verified above.
    if bundle.get("fingerprint"):
        context_v2_json["fingerprint"] = bundle["fingerprint"]
    if bundle.get("failure_category"):
        context_v2_json["failure_category"] = bundle["failure_category"]
    if bundle.get("workflow_to_tier_resolution"):
        context_v2_json["workflow_tier"] = bundle["workflow_to_tier_resolution"]
    # ci-rca-identity-lifecycle: affected_nodeids (v2 junit-parsed cause-group membership) and
    # escape_class (Decision 135 selection-manifest attribution, when the workflow step stamped
    # it onto the bundle) are likewise portal-derived from the verified bundle, never agent-authored.
    if bundle.get("affected_nodeids"):
        context_v2_json["affected_nodeids"] = bundle["affected_nodeids"]
    if bundle.get("escape_class"):
        context_v2_json["escape_class"] = bundle["escape_class"]
    # ci-rca-evidence-scope-declaration (AC7): stamp the authoritative unobserved-step set from
    # the verified bundle's retrieval_evidence.scope. A bundle with no scope block (pre-dating
    # this plan) degrades silently -- authoritative_pairs stays None and check-5 below is skipped.
    retrieval_evidence = bundle.get("retrieval_evidence")
    scope = retrieval_evidence.get("scope") if isinstance(retrieval_evidence, dict) else None
    authoritative_pairs: Optional[list[dict[str, int]]] = None
    if isinstance(scope, list):
        authoritative_pairs = _unobserved_pairs_from_scope(scope)
        context_v2_json["unobserved_steps_authoritative"] = authoritative_pairs

    detection_gap = context_v2_json.get("detection_gap") or {}
    agent_evg = detection_gap.get("earliest_viable_gate")
    bundle_evg = bundle.get("earliest_viable_gate")
    agent_escape = detection_gap.get("escape_mode")
    bundle_escape = bundle.get("escape_mode")
    bundle_vacuous = bundle.get("vacuous_pass")

    issues: list[str] = []

    # Check 1: "undetermined" mirror -- when bundle abstains, agent must mirror
    if bundle_evg == "undetermined" and agent_evg != "undetermined":
        issues.append(
            f"[CI_RCA_CROSS_CHECK check-1] bundle.earliest_viable_gate='undetermined' (probe abstained) "
            f"but agent set earliest_viable_gate={agent_evg!r}. Agent MUST mirror 'undetermined'. "
            "Set rca_confidence='undetermined' and mirror the bundle value."
        )

    # Check 2: earliest_viable_gate bundle-wins (when bundle has a concrete value)
    elif bundle_evg not in (None, "undetermined") and agent_evg not in (None, "undetermined"):
        if agent_evg != bundle_evg:
            issues.append(
                f"[CI_RCA_CROSS_CHECK check-2] detection_gap.earliest_viable_gate={agent_evg!r} "
                f"disagrees with evidence_bundle.earliest_viable_gate={bundle_evg!r}. "
                "The bundle is authoritative (bundle-wins). Accept the bundle value or file a "
                "source=ci_rca_evidence_dispute rec."
            )

    # Check 3: escape_mode -- abstention mirror (when the bundle abstains, the agent must not
    # contradict it with a concrete value), else bundle-wins (when both are concrete and disagree).
    # NOT a copy of check-1's shape: escape_mode is Optional[str]=None (ci_rca_schema.py:80-83)
    # while earliest_viable_gate is REQUIRED, so a bare `agent_escape != "undetermined"` predicate
    # would false-positive on every rec that legally OMITTED the optional field (agent_escape is
    # None) -- the `is not None` guard is what distinguishes "contradicted" from "omitted".
    if bundle_escape == "undetermined" and agent_escape is not None and agent_escape != "undetermined":
        issues.append(
            f"[CI_RCA_CROSS_CHECK check-3] bundle.escape_mode='undetermined' (probe abstained) but "
            f"agent set escape_mode={agent_escape!r}. Agent MUST mirror 'undetermined' when the probe "
            "abstains, or file a source=ci_rca_evidence_dispute rec."
        )
    elif bundle_escape not in (None, "undetermined") and agent_escape not in (None, "undetermined"):
        if agent_escape != bundle_escape:
            issues.append(
                f"[CI_RCA_CROSS_CHECK check-3] detection_gap.escape_mode={agent_escape!r} "
                f"disagrees with evidence_bundle.escape_mode={bundle_escape!r}. "
                "The bundle is authoritative (bundle-wins)."
            )

    # Check 4: vacuous_pass author-discipline rejection
    # Reject an author-discipline RCA when vacuous_pass=true unless:
    # (a) escape_mode=check_ran_vacuously (the vacuous pass IS the cause), or
    # (b) a typed failure_category dispute is filed (handled via dispute carve-out)
    if bundle_vacuous is True:
        author_discipline_keywords = ("author", "did not run", "skipped", "forgot", "missed", "ignored")
        detection_gap_text = detection_gap.get("gap_explanation", "").lower()
        why_chain_text = " ".join(context_v2_json.get("why_chain", [])).lower()
        full_text = f"{detection_gap_text} {why_chain_text}"
        is_author_discipline = any(kw in full_text for kw in author_discipline_keywords)
        escape_ok = agent_escape == "check_ran_vacuously" or bundle_escape == "check_ran_vacuously"
        if is_author_discipline and not escape_ok:
            issues.append(
                "[CI_RCA_CROSS_CHECK check-4] bundle.vacuous_pass=true but rec's gap_explanation / why_chain "
                "contains an author-discipline attribution (e.g. 'did not run', 'author skipped'). "
                "When vacuous_pass=true the gate failure was a TEST COLLECTION DEFECT, not author discipline. "
                "Set escape_mode='check_ran_vacuously' or file a source=ci_rca_evidence_dispute rec."
            )

    # Check 5: unobserved_steps echo vs. the code-computed unobserved_steps_authoritative
    # stamp. Compared as SETS of {job_id, step_index} pairs -- order-insensitive, and a
    # malformed (e.g. flat-index) echo compares as a mismatch rather than crashing. Only runs
    # when the bundle carried a scope block; a scope-absent bundle degrades silently (AC13(iii)).
    if authoritative_pairs is not None:
        authoritative_set = {(p["job_id"], p["step_index"]) for p in authoritative_pairs}
        echo_set = _pair_set(context_v2_json.get("unobserved_steps"))
        if echo_set != authoritative_set:
            issues.append(
                f"[CI_RCA_CROSS_CHECK check-5] unobserved_steps echo {context_v2_json.get('unobserved_steps')!r} "
                f"disagrees with the code-computed unobserved_steps_authoritative {authoritative_pairs!r}. "
                "Echo the bundle's retrieval_evidence.scope entries where log_retrieved is false as a list "
                "of {job_id, step_index} pairs."
            )

    if not issues:
        return

    combined = "; ".join(issues)
    if mode == "strict":
        raise ValueError(f"[CI_RCA_STRICT_MODE=strict] {combined}")
    logger.warning("[CI_RCA_STRICT_MODE=warn] cross-check issues (rec filed anyway): %s", combined)
    tags = [f"cross_check_check_{m.group(1)}" for m in (_CROSS_CHECK_TAG_RE.search(i) for i in issues) if m]
    _stamp_warn_mode_reject(context_v2_json, tags or ["cross_check_disagreement"])


def back_validate_ci_rca(
    cache_rows: Optional[list[dict]] = None,
    since: str = _CI_RCA_BACK_VALIDATE_DEFAULT_SINCE,
    refile_audit: bool = False,
    cap: int = _CI_RCA_BACK_VALIDATE_DAILY_CAP,
    profile: Optional[str] = None,
) -> dict:
    """Re-validate warn-period source=ci_rca recs against the current strict-mode schema (T1.13 c2 enabler).

    Reads the source-scoped current_state(row_filter "source = 'ci_rca'") through the closed
    DuckLake reader (no local recs cache, Decision 215; a reader failure raises). Every source=ci_rca
    rec with created_timestamp >= `since` is sorted into exactly one bucket, mirroring what
    strict-mode file_rec() actually does:

      legacy_no_schema -- context_v2_json is absent/empty. Historical rows filed before CIRCA-02
        (strict mode now raises ValueError for a NEW source=ci_rca write with no context_v2_json)
        are grandfathered: they were accepted when written and stay CONFORMANT here, excluded from
        the non-conformant set. This bucket only covers pre-CIRCA-02 rows -- a strict-mode file_rec
        call today cannot produce a new legacy_no_schema row. An empty dict is never validated for
        these recs (the load-bearing legacy rule).
      non_conformant -- context_v2_json present AND either (a) _validate_ci_rca_context_v2(),
        recomputed against the CURRENT schema (not the schema live when the rec was filed), returns
        deficiencies, or (b) the rec's stamped context_v2_json.warn_mode_reject marker carries a
        non-schema reason (bundle/S3/cross-check disagreement) -- read from the marker rather than
        recomputed, since those checks need network/bundle access this offline pass does not have.
      conformant -- context_v2_json present and passes both checks.

    With refile_audit=True, each non_conformant rec (capped at `cap` per invocation, INTENT's
    K=20/day known-gap) is filed via file_rec(source="ci_rca_warn_period_audit", priority="Low"),
    carrying the parent rec id and its failing-checks list. Default is report-only: zero writes.

    Args:
        cache_rows: Injected rows (test seam). Defaults to the source-scoped reader read when None.
        since: ISO date/timestamp floor on created_timestamp (inclusive).
        refile_audit: When True, files audit recs for non_conformant entries (capped at `cap`).
        cap: Max audit recs filed in this invocation.
        profile: Optional AWS profile override, passed through to file_rec.

    Returns:
        dict with keys: since, legacy_no_schema / non_conformant / conformant (lists of
        {id, has_context_v2[, failing_checks]}), aggregate (bucket counts + with_context_total +
        total + non_conformance_rate over the with-context subset), filed (list of newly-filed
        audit rec ids; empty unless refile_audit=True), audit_cap_reached (bool).
    """
    if cache_rows is None:
        from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

        cache_rows = make_reader(profile=profile).current_state("ops_recommendations", row_filter="source = 'ci_rca'") or []

    legacy_no_schema: list[dict] = []
    non_conformant: list[dict] = []
    conformant: list[dict] = []

    for row in cache_rows:
        if row.get("source") != "ci_rca":
            continue
        created = row.get("created_timestamp") or ""
        if created < since:
            continue
        rec_id = row.get("id", "")
        ctx_raw = row.get("context_v2_json") or ""
        if not ctx_raw:
            legacy_no_schema.append({"id": rec_id, "has_context_v2": False})
            continue
        if isinstance(ctx_raw, str):
            try:
                ctx = json.loads(ctx_raw)
            except json.JSONDecodeError:
                ctx = {}
        else:
            ctx = ctx_raw
        if not isinstance(ctx, dict):
            ctx = {}
        deficiencies = _validate_ci_rca_context_v2(ctx)
        warn_marker = ctx.get("warn_mode_reject") or {}
        # CIRCA-04: exclude ALL schema_-prefixed marker tags (not just the bare "schema_deficiency"),
        # since the per-rule stamp site now stamps stable schema_<rule> tags (e.g.
        # schema_why_chain_too_long) that must not double-count as a non_schema_reason.
        non_schema_reasons = [r for r in warn_marker.get("reasons", []) if not r.startswith("schema")]
        if "preventive_required" not in non_schema_reasons:
            deficiencies = deficiencies + preventive_deficiency(ctx)
        if deficiencies or non_schema_reasons:
            non_conformant.append({"id": rec_id, "has_context_v2": True, "failing_checks": deficiencies + non_schema_reasons})
        else:
            conformant.append({"id": rec_id, "has_context_v2": True})

    with_context_total = len(non_conformant) + len(conformant)
    non_conformance_rate = (len(non_conformant) / with_context_total) if with_context_total else 0.0

    result: dict = {
        "since": since,
        "legacy_no_schema": legacy_no_schema,
        "non_conformant": non_conformant,
        "conformant": conformant,
        "aggregate": {
            "legacy_no_schema_count": len(legacy_no_schema),
            "non_conformant_count": len(non_conformant),
            "conformant_count": len(conformant),
            "with_context_total": with_context_total,
            "total": len(legacy_no_schema) + with_context_total,
            "non_conformance_rate": non_conformance_rate,
        },
        "filed": [],
        "audit_cap_reached": False,
        "audit_cap": cap,
    }

    if refile_audit:
        from scripts.ops_data_portal import file_rec  # noqa: PLC0415

        to_file = non_conformant[:cap]
        result["audit_cap_reached"] = len(non_conformant) > cap
        for entry in to_file:
            failing = "; ".join(entry["failing_checks"]) or "unspecified"
            fields = {
                "title": f"CI-RCA warn-period audit: {entry['id']} non-conformant under strict-mode schema",
                "file": "scripts/ops_data_portal.py",
                "context": (
                    f"back_validate_ci_rca flagged parent {entry['id']} as non-conformant against the "
                    f"current CI_RCA_STRICT_MODE schema. Failing checks: {failing}."
                ),
                "acceptance": (
                    f"grep -q '{entry['id']}' logs/.recommendations-log.jsonl "
                    "&& grep -q '\"source\"' logs/.recommendations-log.jsonl"
                ),
                "effort": "XS",
                "priority": "Low",
                "source": "ci_rca_warn_period_audit",
                "risk": "low",
            }
            filed_id = file_rec(fields, profile=profile)
            result["filed"].append(filed_id)

    return result


def _print_ci_rca_back_validation_report(result: dict) -> None:
    """Print the back_validate_ci_rca() report in the default (non-JSON) text format."""
    print(f"CI-RCA back-validation report (since={result['since']})")
    print()
    for bucket, label in (
        ("legacy_no_schema", "LEGACY_NO_SCHEMA"),
        ("non_conformant", "NON_CONFORMANT"),
        ("conformant", "CONFORMANT"),
    ):
        for entry in result[bucket]:
            detail = f" failing_checks={entry['failing_checks']}" if "failing_checks" in entry else ""
            print(f"  [{label}] {entry['id']}{detail}")
    agg = result["aggregate"]
    print()
    print("--- Aggregate ---")
    print(f"  legacy_no_schema: {agg['legacy_no_schema_count']}")
    print(f"  non_conformant:   {agg['non_conformant_count']}")
    print(f"  conformant:       {agg['conformant_count']}")
    print(f"  with_context_total: {agg['with_context_total']}")
    print(f"  total: {agg['total']}")
    print(f"  non-conformance rate (over with-context subset): {agg['non_conformance_rate']:.1%}")
    if result["filed"]:
        print(f"  filed audit recs ({len(result['filed'])}): {', '.join(result['filed'])}")
    if result["audit_cap_reached"]:
        print(f"  audit cap reached: only the first {result['audit_cap']} non-conformant recs were filed")


def bump_ci_rca_occurrence(rec_id: str, profile: Optional[str] = None) -> int:
    """Increment occurrence_count / stamp last_seen on an existing source=ci_rca rec's
    context_v2_json (CIRCA-03(b)/(c)).

    Called by BOTH the workflow's fp_dedup step (scripts.ci_rca.dedup, per-bundle) after a live
    fingerprint match, AND the write-time backstop (file_rec in ops_data_portal.py) on a
    fingerprint hit -- both dedup paths now record recurrence through this same helper.

    Returns the new occurrence_count (starts at 2 -- the original filing was occurrence 1).
    """
    from scripts.ops_data_portal import _fetch_rec_from_reader, update_rec  # noqa: PLC0415

    existing = _fetch_rec_from_reader(rec_id, profile=profile)
    if existing is None:
        raise RuntimeError(f"bump_ci_rca_occurrence: {rec_id} does not exist in the current projection.")
    ctx_raw = existing.get("context_v2_json") or "{}"
    try:
        ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else dict(ctx_raw or {})
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    new_count = int(ctx.get("occurrence_count") or 1) + 1
    ctx["occurrence_count"] = new_count
    ctx["last_seen"] = datetime.now(timezone.utc).isoformat()
    update_rec(rec_id, {"context_v2_json": json.dumps(ctx)}, profile=profile)
    return new_count
