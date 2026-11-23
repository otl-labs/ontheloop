"""CI-RCA evidence bundle generator and uploader.

CLI: python -m scripts.ci_rca.evidence \\
       --log-file LOG --workflow-name NAME --workflow-run-id ID [--jobs-file JOBS] \\
       [--junit-file JUNIT] [--print-bundle]

Reads pre-fetched CI run logs (NO gh dependency at runtime -- CC-web has no gh CLI).
Assembles one evidence_bundle.json per distinct CAUSE per docs/contracts/ci-rca-lifecycle.yaml,
uploads to the configured s3_agent_logs_bucket, falls back to logs/.ci-rca-evidence-pending/
on upload failure (loud signal, upload_status=upload_failed).

Fingerprint v2 (ci-rca-identity-lifecycle): the grouping key is anchored on error_signature (the
failure's deterministic in-code CAUSE signature -- scripts.ci_rca.fingerprint), parsed from the
run's junit report when available (--junit-file), else a normalized log-tail fallback. This
replaces the CIRCA-03(a) failed-check-name key, which collided any two pytest failures in the
same CI step (rec-2710 masking bug).
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.ci_rca.evidence._bundle_core import (
    _assemble_core,
    _resolve_current_pre_runtime,  # noqa: F401  (back-compat: from scripts.ci_rca.evidence import _resolve_current_pre_runtime)
    _slugify_workflow,
)
from scripts.ci_rca.evidence_input import load_retrieval_input
from scripts.ci_rca.fingerprint import (
    collapse_mass_failure,
    compute_fingerprint_v2,
    error_signature_from_junit,
    error_signature_from_log_tail,
    signature_for_collection_error,
    signature_for_declared_detail,
    signature_for_evidence_insufficient,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PENDING_DIR = ROOT / "logs" / ".ci-rca-evidence-pending"
_EVIDENCE_PREFIX = "ci-rca-evidence"


def _resolve_bucket() -> str:
    """Resolve agent-logs S3 bucket via config or S3_LOG_BUCKET env override."""
    env = os.environ.get("S3_LOG_BUCKET", "").strip()
    if env:
        return env
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from src.common.config import Config

        val = Config().get("aws.s3_agent_logs_bucket", "")
        if val:
            return val
    except Exception:
        pass
    try:
        personal_cfg = ROOT / "config" / "config.personal.yaml"
        if personal_cfg.exists():
            with personal_cfg.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            val = cfg.get("aws", {}).get("s3_agent_logs_bucket", "")
            if val:
                return val
    except Exception:
        pass
    return ""


def _canonical_json(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _compute_fingerprint(workflow_slug: str, failed_check: str, failure_category: str) -> str:
    """LEGACY v1 grouping key (CIRCA-03(a), superseded by fingerprint v2 -- ci-rca-identity-lifecycle).

    Retained verbatim for back-compat (other callers still recompute historical v1 hashes for
    comparison; no warehouse migration touches existing v1 fingerprint values) -- generate_bundles()
    no longer calls this. The live grouping key is scripts.ci_rca.fingerprint.compute_fingerprint_v2,
    anchored on error_signature (the failure's CAUSE) rather than failed_check (the CI STEP name,
    identical for any pytest failure in the same step -- the rec-2710 masking bug).
    """
    payload = "\0".join((workflow_slug, failed_check, failure_category))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_first_error_signature(log_text: str, failed_check: str) -> str:
    """Tie-breaker signature (CIRCA-03(a)) -- NOT the grouping key. Picks the first log line
    mentioning failed_check (else the first non-blank line) and normalizes volatile digit
    tokens (line numbers, durations, run ids) so it is stable across reruns of the same failure.
    """
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    if not lines:
        return ""
    match = next((ln for ln in lines if failed_check and failed_check in ln), None)
    line = match or lines[0]
    normalized = re.sub(r"\s+", " ", line)
    normalized = re.sub(r"\d+", "#", normalized)
    return normalized[:300]


def _sha256_of(obj: dict[str, Any]) -> str:
    payload = {k: v for k, v in obj.items() if k != "sha256"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _upload_to_s3(body: bytes, bucket: str, key: str) -> None:
    import boto3

    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    s3 = session.client("s3", region_name="eu-west-2")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def _write_pending(bundle: dict[str, Any], sha: str) -> Path:
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    dest = _PENDING_DIR / f"{sha}.json"
    dest.write_bytes(_canonical_json(bundle))
    logger.warning("EVIDENCE_BUNDLE_PENDING: upload_failed; bundle at %s", dest)
    return dest


def upload_and_persist(bundle: dict[str, Any], bucket: str) -> dict[str, str]:
    """Upload bundle to S3 or fall back to pending dir. Returns status dict."""
    sha = bundle["sha256"]
    key = f"{_EVIDENCE_PREFIX}/{sha}.json"
    s3_uri = f"s3://{bucket}/{key}"
    body = _canonical_json(bundle)

    if not bucket:
        logger.warning("EVIDENCE_BUCKET_UNRESOLVED: no bucket; writing to pending dir")
        pending = _write_pending(bundle, sha)
        return {"upload_status": "upload_failed", "s3_uri": "", "pending_path": str(pending)}

    try:
        _upload_to_s3(body, bucket, key)
        return {"upload_status": "ok", "s3_uri": s3_uri, "pending_path": ""}
    except Exception as exc:
        logger.error("S3 upload failed (%s/%s): %s", bucket, key, exc)
        pending = _write_pending(bundle, sha)
        return {"upload_status": "upload_failed", "s3_uri": "", "pending_path": str(pending)}


_ERROR_COLLECTING_RE = re.compile(r"ERROR collecting (\S+)")


def _collecting_module_paths(log_text: str) -> list[str]:
    """Every distinct module path named in a pytest `ERROR collecting <path>` header, in
    first-seen order -- the ACTUAL failing module (classify_failures' check_name for the
    collection_error taxonomy entry is the static pattern label "pytest_collection_error", not
    the path; this recovers the real path the collection-error signature must key on)."""
    seen: list[str] = []
    for m in _ERROR_COLLECTING_RE.finditer(log_text):
        path = m.group(1)
        if path not in seen:
            seen.append(path)
    return seen


def _resolve_error_signatures(
    log_text: str,
    failures: list[tuple[str, str, str]],
    junit_groups: list[tuple[str, list[str]]],
    truncation_reason: str | None = None,
    check_details: dict[str, list[str]] | None = None,
) -> list[tuple[str, str, str, list[str], str]]:
    """Resolve one (failure_category, failed_check, error_signature, affected_nodeids,
    classification_source) tuple per bundle, BEFORE mass-failure collapse. Collection errors key
    on the failing MODULE PATH; the evidence_insufficient refusal keys on the degenerate
    evidence_insufficient::<truncation_reason> signature (never log-tail derived, which would
    fabricate IDENTITY); everything else prefers a junit-parsed cause-group when available, else
    -- for a check that declared registry.failure_detail() (coverage-failure-attribution,
    check_details is scripts.ci_rca.taxonomy._load_check_details' {check: [detail, ...]} map,
    pre-loaded by the caller) -- the path-preserving signature_for_declared_detail derivation,
    else the pre-existing log-tail-derived signature per classify_failures() check. A check
    absent from check_details (or an empty/None map) falls back to log-tail unchanged, so a
    detail-free failure's signature is byte-identical to before this plan. junit_groups is
    pre-parsed by the caller (shared with has_junit_cause_group)."""
    check_details = check_details or {}
    collection_entries = [f for f in failures if f[0] == "collection_error"]
    refusal_entries = [f for f in failures if f[0] == "evidence_insufficient"]
    other_entries = [f for f in failures if f[0] not in ("collection_error", "evidence_insufficient")]

    resolved: list[tuple[str, str, str, list[str], str]] = []
    if collection_entries:
        module_paths = _collecting_module_paths(log_text) or [collection_entries[0][1]]
        cat = collection_entries[0][0]
        for path in module_paths:
            resolved.append((cat, path, signature_for_collection_error(path), [path], "collection_error_module_path"))

    if refusal_entries:
        cat, check, _src = refusal_entries[0]
        sig = signature_for_evidence_insufficient(truncation_reason or "unknown")
        resolved.append((cat, check, sig, [], "evidence_insufficient_refusal"))

    if junit_groups:
        default_cat, default_check = ("pytest_regression", "pytest")
        if other_entries:
            default_cat, default_check = other_entries[0][0], other_entries[0][1]
        for sig, nodeids in junit_groups:
            check_label = nodeids[0] if nodeids else default_check
            resolved.append((default_cat, check_label, sig, nodeids, "junit"))
    else:
        for cat, check, _src in other_entries:
            detail = check_details.get(check)
            if detail:
                sig = signature_for_declared_detail(check, detail)
                resolved.append((cat, check, sig, [], "declared_detail"))
            else:
                sig = error_signature_from_log_tail(log_text, tool=check)
                resolved.append((cat, check, sig, [], "log_tail"))

    return resolved


def _load_selection_manifest(selection_manifest_path: Path | None) -> dict[str, Any] | None:
    if selection_manifest_path is None or not selection_manifest_path.exists():
        return None
    try:
        data = json.loads(selection_manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not parse selection manifest %s: %s", selection_manifest_path, exc)
        return None


def _escape_class_for(nodeids: list[str], check: str, manifest: dict[str, Any] | None) -> str | None:
    """escape_class for a bundle (Decision 135 selection-manifest diff), or None when no
    manifest was supplied (a non-post-merge invocation, e.g. workflow_dispatch on a PR run)."""
    if manifest is None:
        return None
    from scripts.ops_portal.ci_rca_lifecycle import compute_escape_class  # noqa: PLC0415

    nodeid = nodeids[0] if nodeids else check
    return compute_escape_class(nodeid, manifest)


def generate_bundles(
    log_file: Path,
    workflow_name: str,
    workflow_run_id: int,
    jobs_file: Path | None = None,
    validate_path: Path | None = None,
    taxonomy_path: Path | None = None,
    repo: str | None = None,
    junit_path: Path | None = None,
    selection_manifest_path: Path | None = None,
    validation_result_path: Path | None = None,
    retrieval_limits: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse log (+ optional junit report), classify failure(s), assemble + hash bundles.

    Returns one bundle per distinct CAUSE (junit-parsed cause-group, or one per classify_failures
    check when no junit report is available) -- collapsed to a SINGLE run-level bundle when more
    than ~5 distinct new signatures are produced in one run (mass-failure collapse).

    selection_manifest_path (Decision 135, ci-rca-identity-lifecycle): when provided (a
    post-merge full-tier failure), each bundle's escape_class is computed by diffing its first
    affected nodeid against the merged PR's --pre affected-set selection manifest. Part of the
    hashed bundle payload (like fingerprint) so it is portal-derived, never agent-authored, when
    the write-time cross-check spine reads it back off the verified bundle.

    validation_result_path / retrieval_limits: validate.py's dispatch-attributed
    failed_check_attributions (Priority 0) and the retrieval envelope's `limits` dict, threaded
    into classify_failures alongside has_junit_cause_group (one shared junit parse).
    """
    from scripts.ci_rca.taxonomy import _load_check_details, classify_failures, load_taxonomy
    from scripts.ci_rca.vacuous_pass import (
        compute_coverage_regression,
        compute_merge_gate_test_coverage,
        deleted_test_files,
        merged_diff_files,
        parse_vacuous_pass,
    )

    log_text = log_file.read_text(encoding="utf-8", errors="replace")
    jobs: list[dict] | None = None
    if jobs_file and jobs_file.exists():
        try:
            raw = json.loads(jobs_file.read_text(encoding="utf-8", errors="replace"))
            jobs = raw.get("jobs") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
        except Exception:
            logger.warning("Could not parse jobs file %s", jobs_file)

    # Pre-compute shared evidence fields once per run
    vacuous_pass_val = parse_vacuous_pass(log_text)
    merged = merged_diff_files()
    deleted = deleted_test_files()
    coverage_reg = compute_coverage_regression(deleted)

    try:
        load_taxonomy(taxonomy_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("TAXONOMY_UNAVAILABLE: %s", exc)
        fallback_sig = error_signature_from_log_tail(log_text, tool="unknown")
        b: dict[str, Any] = {
            "schema_version": 3,
            "workflow_run_id": workflow_run_id,
            "workflow_name": workflow_name,
            "workflow_to_tier_resolution": "unknown",
            "failed_check": "unknown",
            "failure_category": "unknown",
            "fingerprint": compute_fingerprint_v2(_slugify_workflow(workflow_name), "unknown", fallback_sig),
            "fingerprint_version": 2,
            "error_signature": fallback_sig,
            "affected_nodeids": [],
            "first_error_signature": _normalize_first_error_signature(log_text, "unknown"),
            "classification_source": "taxonomy_fallback",
            "taxonomy_error": str(exc),
            "tier_membership": None,
            "earliest_viable_gate": "undetermined",
            "earliest_viable_gate_rationale": "Taxonomy unavailable",
            "pre_runtime_seconds": None,
            "runtime_confidence": None,
            "actual_gate_that_caught_it": None,
            "gate_is_postmerge_canary": False,
            "vacuous_pass": vacuous_pass_val,
            "merge_gate_test_coverage": "undetermined",
            "coverage_regression": coverage_reg,
            "escape_mode": "undetermined",
            "related_recs_by_category": [],
            "decision_records_cited": [],
            "ast_walker_version": 1,
            "taxonomy_version": 0,
        }
        b["sha256"] = _sha256_of(b)
        return [b]

    complete = True if retrieval_limits is None else bool(retrieval_limits.get("complete", True))
    truncation_reason = None if retrieval_limits is None else retrieval_limits.get("truncation_reason")
    junit_groups: list[tuple[str, list[str]]] = []
    if junit_path is not None and junit_path.exists():
        try:
            junit_groups = error_signature_from_junit(junit_path)
        except Exception as exc:  # noqa: BLE001 -- fall back to log-tail, never crash bundle generation
            logger.warning("junit parse failed at %s (%s); falling back to log-tail signatures", junit_path, exc)

    failures = classify_failures(
        log_text, jobs, taxonomy_path, validation_result_path, complete=complete, has_junit_cause_group=bool(junit_groups)
    )
    check_details = _load_check_details(validation_result_path)
    resolved = _resolve_error_signatures(log_text, failures, junit_groups, truncation_reason, check_details)
    manifest = _load_selection_manifest(selection_manifest_path)

    collapsed_sig = collapse_mass_failure([r[2] for r in resolved])
    if collapsed_sig is not None:
        cat = resolved[0][0] if resolved else "unknown"
        all_nodeids = sorted({n for r in resolved for n in r[3]})
        coverage = compute_merge_gate_test_coverage("mass_failure", merged)
        core = _assemble_core(
            workflow_run_id,
            workflow_name,
            "mass_failure",
            cat,
            "mass_failure_collapse",
            validate_path,
            taxonomy_path,
            vacuous_pass=vacuous_pass_val,
            merge_gate_test_coverage=coverage,
            coverage_regression=coverage_reg,
            first_error_signature=_normalize_first_error_signature(log_text, "mass_failure"),
            error_signature=collapsed_sig,
            affected_nodeids=all_nodeids,
        )
        escape_class = _escape_class_for(all_nodeids, "mass_failure", manifest)
        if escape_class is not None:
            core["escape_class"] = escape_class
        core["sha256"] = _sha256_of(core)
        return [core]

    bundles: list[dict[str, Any]] = []
    for cat, check, sig, nodeids, classification_source in resolved:
        coverage = compute_merge_gate_test_coverage(check, merged)
        core = _assemble_core(
            workflow_run_id,
            workflow_name,
            check,
            cat,
            classification_source,
            validate_path,
            taxonomy_path,
            vacuous_pass=vacuous_pass_val,
            merge_gate_test_coverage=coverage,
            coverage_regression=coverage_reg,
            first_error_signature=_normalize_first_error_signature(log_text, check),
            error_signature=sig,
            affected_nodeids=nodeids,
        )
        escape_class = _escape_class_for(nodeids, check, manifest)
        if escape_class is not None:
            core["escape_class"] = escape_class
        core["sha256"] = _sha256_of(core)
        bundles.append(core)
    return bundles


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate CI-RCA evidence bundles from pre-fetched logs.")
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--jobs-file", type=Path, default=None)
    parser.add_argument("--retrieval-envelope", type=Path, default=None)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--validate-path", type=Path, default=ROOT / "scripts" / "validate.py")
    parser.add_argument("--taxonomy-path", type=Path, default=None)
    parser.add_argument(
        "--junit-file",
        type=Path,
        default=None,
        help="pytest junit XML report for this run (v2 fingerprint cause-group parsing); "
        "falls back to a normalized log-tail signature when absent or unparseable.",
    )
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        default=None,
        help="the merged PR's --pre affected-set selection manifest (Decision 135) -- when "
        "given, each bundle's escape_class is computed by diffing against it.",
    )
    parser.add_argument(
        "--validation-result",
        dest="validation_result",
        type=Path,
        default=None,
        help="validate.py's logs/debug/validation-result.json (schema_version 2 attributions) -- Priority 0.",
    )
    parser.add_argument("--print-bundle", action="store_true")
    parser.add_argument(
        "--emit-dir",
        type=Path,
        default=None,
        help="Write each bundle to <dir>/<sha>.json regardless of S3 upload outcome; prints BUNDLE_LOCAL=<path>",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Never upload: take the unresolved-bucket path (pending file, upload_failed). Used by the CI RCA smoke.",
    )
    args = parser.parse_args(argv)

    if not args.log_file.exists():
        print(f"ERROR: log file not found: {args.log_file}", file=sys.stderr)
        sys.exit(1)

    retrieval = None
    if args.retrieval_envelope is not None:
        retrieval = load_retrieval_input(args.retrieval_envelope, args.log_file, args.workflow_run_id)

    bucket = "" if args.no_upload else _resolve_bucket()
    bundles = generate_bundles(
        log_file=args.log_file,
        workflow_name=args.workflow_name,
        workflow_run_id=args.workflow_run_id,
        jobs_file=args.jobs_file,
        validate_path=args.validate_path,
        taxonomy_path=args.taxonomy_path,
        repo=args.repo,
        junit_path=args.junit_file,
        selection_manifest_path=args.selection_manifest,
        validation_result_path=args.validation_result,
        retrieval_limits=retrieval["limits"] if retrieval is not None else None,
    )

    for bundle in bundles:
        if retrieval is not None:
            bundle["retrieval_evidence"] = retrieval
            bundle["sha256"] = _sha256_of({key: value for key, value in bundle.items() if key != "sha256"})
        if args.emit_dir is not None:
            args.emit_dir.mkdir(parents=True, exist_ok=True)
            local_path = args.emit_dir / f"{bundle['sha256']}.json"
            local_path.write_bytes(_canonical_json(bundle))
            print(f"BUNDLE_LOCAL={local_path}")
        result = upload_and_persist(bundle, bucket)
        if args.print_bundle:
            print(json.dumps({**bundle, **result}, indent=2, sort_keys=True))
        print(f"BUNDLE_SHA={bundle['sha256']}")
        print(f"FINGERPRINT={bundle.get('fingerprint', '')}")
        print(f"BUNDLE_S3_URI={result['s3_uri']}")
        print(f"BUNDLE_PATH={result['pending_path']}")
        if result["upload_status"] == "upload_failed":
            print("UPLOAD_STATUS=upload_failed", file=sys.stderr)


if __name__ == "__main__":
    main()
