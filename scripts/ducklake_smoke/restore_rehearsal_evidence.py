"""Evidence schema, exit-criteria checks, forbidden patterns and redaction for the restore rehearsal (rec-5023).

Contract: docs/contracts/restore-rehearsal.yaml. PUBLIC repository (Decision 101): evidence is counts only. Every print,
exception text and evidence write in the orchestrator goes through redact(); verify-evidence refuses a file holding any
forbidden pattern, so a leak fails loudly instead of being committed.
"""

from __future__ import annotations

import json
import re
from typing import Any

EVIDENCE_KEYS = (
    "run_id measured_at maintenance_zip_sha256 dump_bytes dump_ms copy_objects copy_bytes copy_ms restore_ms rewrite_ms "
    "restore_ok restore_exit_status rewritten attach_ok host_limit meta_schemas snapshot_ids absolute_paths foreign_owned "
    "foreign_acl foreign_acl_grantees missing_files ops_tables write_probe_key write_probe_in_scratch "
    "write_probe_in_production catalog_format_version prefix_sizes duckdb_copy_ms duckdb_catalog_rows duckdb_file_bytes "
    "duckdb_copy_tables torn_down"
).split()
VERSION_RECOVERY_KEYS = (
    "run_id measured_at object_under_versioning_prefix noncurrent_versions sha256_match deleted_after"
).split()

BUCKET_LITERALS = ["agent-platform-data-lake"]
_secrets: set[str] = set()

FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "dsn": re.compile(r"postgres(?:ql)?://\S+", re.I),
    "host": re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:neon\.tech|amazonaws\.com|rds\.amazonaws\.com)\b", re.I),
    "url": re.compile(r"https?://\S+", re.I),
    "presign_credential": re.compile(r"X-Amz-[A-Za-z-]+|\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b", re.I),
    "arn": re.compile(r"arn:aws[a-z-]*:\S+"),
    "account_id": re.compile(r"(?<!\d)\d{12}(?!\d)"),
    "s3_uri": re.compile(r"s3://\S+"),
}


def register_secret(value: str | None) -> None:
    """Remember a runtime secret (the target password, a presigned URL) so redact() and the scans know it."""
    if value:
        _secrets.add(value)


def register_bucket(name: str | None) -> None:
    if name and name not in BUCKET_LITERALS:
        BUCKET_LITERALS.append(name)


def redact(text: str) -> str:
    """Replace every forbidden pattern and every registered secret or bucket name with a fixed marker."""
    out = str(text)
    for value in sorted(_secrets | set(BUCKET_LITERALS), key=len, reverse=True):
        out = out.replace(value, "[redacted]")
    for pattern in FORBIDDEN_PATTERNS.values():
        out = pattern.sub("[redacted]", out)
    return out


def forbidden_hits(text: str) -> list[str]:
    hits = [name for name, pattern in FORBIDDEN_PATTERNS.items() if pattern.search(text)]
    hits += [f"bucket_name:{i}" for i, name in enumerate(BUCKET_LITERALS) if name in text]
    hits += ["secret" for value in _secrets if value in text]
    return hits


def redact_value(value: Any) -> Any:
    """Redact every string inside a JSON-shaped value."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    return value


def _present(evidence: dict[str, Any], *keys: str) -> bool:
    return all(evidence.get(k) is not None for k in keys)


def failed_criteria(evidence: dict[str, Any]) -> list[str]:
    """The contract's gating exit criteria that the evidence does not meet (file_catalog is reported, not gating)."""
    failed: list[str] = []
    tables = evidence.get("ops_tables") or []
    if not (evidence.get("restore_ok") is True and evidence.get("foreign_owned") == 0 and evidence.get("foreign_acl") == 0):
        failed.append("restored")
    if not (evidence.get("rewritten") is True and evidence.get("absolute_paths") == 0 and evidence.get("attach_ok") is True):
        failed.append("rewritten")
    if not (evidence.get("missing_files") == 0 and tables and all(t.get("read_ok") is True for t in tables)):
        failed.append("complete")
    faithful = bool(tables) and all(
        t.get("restored_count") is not None
        and t.get("restored_count") == t.get("prod_count")
        and t.get("restored_checksum") == t.get("prod_checksum")
        for t in tables
    )
    if not faithful:
        failed.append("faithful")
    if not (evidence.get("write_probe_in_scratch") is True and evidence.get("write_probe_in_production") is False):
        failed.append("one_way_writes")
    if not (
        _present(evidence, "dump_bytes", "dump_ms", "copy_ms", "restore_ms", "rewrite_ms") and evidence.get("prefix_sizes")
    ):
        failed.append("measured")
    if not evidence.get("catalog_format_version"):
        failed.append("format")
    torn = evidence.get("torn_down")
    if not (isinstance(torn, dict) and torn and all(v is True for v in torn.values())):
        failed.append("torn_down")
    return failed


def duckdb_copy_mismatches(evidence: dict[str, Any]) -> list[str]:
    rows = evidence.get("duckdb_copy_tables") or []
    return [str(r.get("table")) for r in rows if not (r.get("match_restored") is True and r.get("match_median") is True)] or (
        [] if rows else ["no duckdb_copy_tables rows"]
    )


def version_recovery_failures(evidence: dict[str, Any]) -> list[str]:
    checks = {
        "object_under_versioning_prefix": evidence.get("object_under_versioning_prefix") is True,
        "noncurrent_versions": isinstance(evidence.get("noncurrent_versions"), int) and evidence["noncurrent_versions"] >= 1,
        "sha256_match": evidence.get("sha256_match") is True,
        "deleted_after": evidence.get("deleted_after") is True,
    }
    return [name for name, ok in checks.items() if not ok]


def verify_text(text: str) -> tuple[int, list[str]]:
    """Return (exit code, output lines) for verify-evidence over a file's text."""
    hits = forbidden_hits(text)
    if hits:
        return 1, [f"FORBIDDEN_PATTERN {h}" for h in sorted(set(hits))]
    try:
        evidence = json.loads(text)
    except json.JSONDecodeError:
        return 1, ["NOT_JSON"]
    if not isinstance(evidence, dict):
        return 1, ["NOT_JSON"]
    if "sha256_match" in evidence or "noncurrent_versions" in evidence:
        failures = version_recovery_failures(evidence)
        missing = [k for k in VERSION_RECOVERY_KEYS if k not in evidence]
        failures += [f"missing:{k}" for k in missing]
        return (1, [f"FAILED {f}" for f in failures]) if failures else (0, ["VERSION_RECOVERY_OK"])
    missing = [k for k in EVIDENCE_KEYS if k not in evidence]
    failures = failed_criteria(evidence) + [f"missing:{k}" for k in missing]
    lines = [f"FAILED {f}" for f in failures]
    mismatches = duckdb_copy_mismatches(evidence)
    lines += ["DUCKDB_COPY_OK"] if not mismatches else [f"DUCKDB_COPY_MISMATCH {m}" for m in mismatches]
    if failures:
        return 1, lines
    return 0, ["RESTORE_REHEARSAL_OK", *lines]
