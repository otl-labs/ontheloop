"""Deterministic, in-code v2 CI-RCA fingerprint (Decision 55: never LLM-authored).

Replaces the CIRCA-03(a) grouping key (workflow_slug/failed_check/failure_category -- the CI
STEP name, identical for any pytest failure in the same step) with a CAUSE-anchored key:
workflow_slug/failure_category/error_signature, where error_signature is derived from the
deepest in-app traceback frame + exception type + a normalized message head (junit-parsed) or
a normalized log-tail signature (non-pytest fallback). This is what makes two DISTINCT failing
tests get DIFFERENT fingerprints (anti-masking, rec-2710) while one infra error raised from a
shared src/ helper groups across the different tests that surface it (same cause).

A literal "v2" salt is folded into the hashed payload so a v2 fingerprint can never collide with
a historical v1 fingerprint (scripts/ci_rca/evidence's retained legacy _compute_fingerprint) --
no warehouse migration is required; the two keyspaces are disjoint by construction.

Public API: compute_fingerprint_v2, error_signature_from_junit, error_signature_from_log_tail,
signature_for_collection_error, deepest_in_app_frame, normalize_message_head,
collapse_mass_failure.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent

# --- v2 fingerprint -----------------------------------------------------------------------

_V2_SALT = "v2"

# Workflows that run the same full tier: a failure of one cause in any of them is one rec (rec-4900, Decision 142).
FULL_TIER_SIBLING_SLUGS = ("ci", "main_canary")


def compute_fingerprint_v2(workflow_slug: str, failure_category: str, error_signature: str) -> str:
    """Deterministic sha256 hex over (v2-salt, workflow_slug, failure_category, error_signature).

    Invariant to run_id/timestamp/head_sha (none of those feed the payload). Distinct across
    differing error_signature (the anti-masking property) or failure_category. The "v2" salt
    guarantees disjointness from any v1 (pre-fingerprint-v2) hash for the same logical failure.
    """
    payload = "\0".join((_V2_SALT, workflow_slug, failure_category, error_signature))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- message-head normalization ------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}(?:\.\w+)?")
_LARGE_INT_RE = re.compile(r"(?<![\w.])\d{4,}(?![\w.])")


def normalize_message_head(message: str) -> str:
    """First line of `message`, with volatile tokens scrubbed to stable placeholders.

    Scrubs (in order, so an earlier substitution never gets re-matched by a later, coarser
    pattern): ISO timestamps, UUIDs, long hex/run-id-shaped tokens, absolute/multi-segment
    paths, and large (>=4 digit) bare integers. Short quoted identifiers (e.g. 'foo' or a
    short numeric literal like "assert 1 == 2") are NOT targeted by any of these patterns, so
    they survive untouched -- they are usually the meaningful, stable part of the message.
    """
    stripped = message.strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0]
    s = _TIMESTAMP_RE.sub("<ts>", first_line)
    s = _UUID_RE.sub("<uuid>", s)
    s = _PATH_RE.sub("<path>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _LARGE_INT_RE.sub("<n>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:300]


# --- deepest in-app traceback frame -------------------------------------------------------

_FRAME_RE = re.compile(r'File "([^"]+)", line \d+, in (\S+)')
_NON_APP_MARKERS = (
    "site-packages",
    "dist-packages",
    "/_pytest/",
    "\\_pytest\\",
    "/pluggy/",
    "\\pluggy\\",
    "<frozen ",
    "conftest.py",
)


def _is_in_app_frame(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    return not any(marker.replace("\\", "/") in normalized for marker in _NON_APP_MARKERS)


_KNOWN_TOP_LEVEL_DIRS = ("src/", "scripts/", "tests/")


def _file_to_module(file_path: str, repo_root: Optional[Path] = None) -> str:
    """Best-effort repo-relative module dotted-path for a traceback frame's file path.

    Handles a real absolute path under repo_root, a CI-runner absolute path that does NOT
    match the local repo_root string (e.g. /home/runner/work/... vs a local checkout path,
    but still contains a recognizable src/scripts/tests top-level segment), and a
    synthetic/relative path (as used by unit-test fixtures that never touch the real
    filesystem) -- in all cases the result is a stable module::function identity string, never
    the raw absolute path.
    """
    root = repo_root if repo_root is not None else ROOT
    normalized = file_path.replace("\\", "/")
    root_str = str(root).replace("\\", "/").rstrip("/") + "/"
    if normalized.startswith(root_str):
        rel = normalized[len(root_str) :]
    else:
        rel = normalized.lstrip("/")
        for anchor in _KNOWN_TOP_LEVEL_DIRS:
            idx = rel.find(anchor)
            if idx != -1:
                rel = rel[idx:]
                break
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def deepest_in_app_frame(traceback_text: str, repo_root: Optional[Path] = None) -> Optional[str]:
    """Walk a traceback's `File "...", line N, in func` frames bottom-up (deepest first) and
    return the first one that is NOT in site-packages/pytest-internals/conftest, as
    "module::function" (no line number). Returns None if no frame is parseable or every frame
    is excluded.

    For a plain assertion failure inside a test function, the deepest frame IS the test
    function itself (in-app), so distinct tests never collide (anti-masking). For an error
    raised inside a shared src/ helper, the deepest frame is that helper -- so the SAME cause
    surfacing via different call paths (different tests) resolves to the SAME frame (cause
    grouping).
    """
    frames = _FRAME_RE.findall(traceback_text)
    for file_path, func_name in reversed(frames):
        if _is_in_app_frame(file_path):
            module = _file_to_module(file_path, repo_root)
            return f"{module}::{func_name}"
    return None


def _build_error_signature(exception_type: str, frame: str, message_head: str) -> str:
    return f"{exception_type}::{frame}::{message_head}"


# --- junit-parsed error signature (pytest path) -------------------------------------------


def _nodeid_from_testcase(testcase) -> str:
    file_attr = testcase.get("file")
    name = testcase.get("name", "")
    if file_attr:
        return f"{file_attr}::{name}"
    classname = testcase.get("classname", "")
    path = classname.replace(".", "/") + ".py"
    return f"{path}::{name}"


def _parse_failure_element(element) -> tuple[str, str]:
    """Return (exception_type, message) for a junit <failure>/<error> element."""
    type_attr = (element.get("type") or "").strip()
    message_attr = (element.get("message") or "").strip()
    text = (element.text or "").strip()

    exc_type = type_attr
    message = message_attr
    if not message and text:
        message = text.splitlines()[-1].strip()
    if not exc_type:
        if ":" in message:
            exc_type = message.split(":", 1)[0].strip() or "UnknownError"
        else:
            exc_type = "UnknownError"
    prefix = f"{exc_type}:"
    if message.startswith(prefix):
        message = message[len(prefix) :].strip()
    return exc_type, message


def error_signature_from_junit(junit_xml_path: Path, repo_root: Optional[Path] = None) -> list[tuple[str, list[str]]]:
    """Parse a pytest junit XML report; group failing/erroring testcases by identical
    (exception_type, deepest_in_app_frame, normalized_message_head) tuple.

    Returns one (error_signature, affected_nodeids) pair per distinct cause-group, in
    first-seen order. This is what makes an infra error raised from a shared src/ helper --
    hit by N distinct tests -- collapse into ONE group (affected_nodeids has N entries), while
    two genuinely different test failures (different deepest in-app frame or message) land in
    DIFFERENT groups. Raises on a missing/malformed file (Decision 55: fail loud); callers fall
    back to error_signature_from_log_tail on failure.
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(junit_xml_path)
    root_el = tree.getroot()

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for testcase in root_el.iter("testcase"):
        failure_el = testcase.find("failure")
        if failure_el is None:
            failure_el = testcase.find("error")
        if failure_el is None:
            continue
        nodeid = _nodeid_from_testcase(testcase)
        exc_type, message = _parse_failure_element(failure_el)
        traceback_text = failure_el.text or ""
        frame = deepest_in_app_frame(traceback_text, repo_root) or f"{nodeid.split('::', 1)[0]}::unknown"
        message_head = normalize_message_head(message)
        sig = _build_error_signature(exc_type, frame, message_head)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(nodeid)

    return [(sig, groups[sig]) for sig in order]


# --- non-pytest log-tail fallback (incl. terraform-apply-sandbox, Decision 92) -------------

_TERRAFORM_ERROR_LINE_RE = re.compile(r"^Error:\s*(.+)$")


def error_signature_from_log_tail(log_text: str, tool: str) -> str:
    """Non-pytest fallback error signature: `tool::normalized-representative-log-line`.

    Prefers a `terraform`-style `Error: ...` line when present (Decision 92: covers
    terraform-apply-sandbox apply failures), else falls back to the last non-blank log line.
    """
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    if not lines:
        return f"{tool}::<empty>"
    terraform_match = next((m.group(1) for ln in lines if (m := _TERRAFORM_ERROR_LINE_RE.match(ln))), None)
    representative = terraform_match or lines[-1]
    return f"{tool}::{normalize_message_head(representative)}"


# --- declared-detail keying (coverage-failure-attribution) --------------------------------


def signature_for_declared_detail(check: str, detail: list[str]) -> str:
    """Deterministic, PATH-PRESERVING signature derived from a check's own declared
    registry.failure_detail() (docs/contracts/check-accounting.yaml's failed_check_details,
    scripts.checks.registry / scripts.checks.validation_result) -- never routed through
    normalize_message_head, whose _PATH_RE would collapse any multi-segment path to the literal
    token "<path>" and re-introduce the exact same-fingerprint collision this channel exists to
    prevent (e.g. scripts/checks/hygiene/validate_vacuity_justified.py and
    scripts/ci_rca/taxonomy.py both normalize to "scripts<path>").

    `detail` is a check's own declared list of per-failure strings (e.g.
    "scripts/foo.py: 95.8% line coverage (expected >= 100%)" for validate_test_coverage). Each
    item's leading "<path>: " head is extracted verbatim (falling back to the item itself when no
    ": " separator is present, so an unrecognized shape still contributes something
    discriminating rather than being silently dropped); the extracted set is sorted and
    deduplicated so the signature is deterministic and order-independent, then joined with the
    check name so distinct checks declaring the same file never collide."""
    paths = sorted({(item.split(": ", 1)[0] if ": " in item else item) for item in detail})
    return f"{check}::{'::'.join(paths)}"


# --- collection-error keying ----------------------------------------------------------------


def signature_for_collection_error(module_path: str) -> str:
    """A pytest collection error keys on the failing MODULE PATH, not a traceback frame (there
    is no test body to attribute the failure to -- the module itself failed to import/collect)."""
    return f"collection_error::{module_path}"


# --- evidence-insufficient refusal keying (ci-rca-evidence-fidelity) -----------------------


def signature_for_evidence_insufficient(truncation_reason: str) -> str:
    """Degenerate deterministic signature for the evidence_insufficient refusal verdict, keyed
    on truncation_reason only (never on wherever the byte/line cap happened to land -- that
    would fabricate IDENTITY instead of CATEGORY). Two distinct truncated logs sharing a
    truncation_reason dedup onto the SAME chain; differing reasons key differently."""
    return f"evidence_insufficient::{truncation_reason}"


# --- mass-failure collapse -------------------------------------------------------------------


def collapse_mass_failure(signatures: list[str], threshold: int = 5) -> Optional[str]:
    """When a run produces more than ~`threshold` distinct NEW signatures, collapse them into
    ONE run-level signature (protects against a single mass-failure event fanning out into many
    unrelated-looking critical recs). Returns None when collapse does not apply (<= threshold
    distinct signatures) -- callers should use the individual signatures unchanged in that case.
    """
    distinct = sorted(set(signatures))
    if len(distinct) <= threshold:
        return None
    digest = hashlib.sha256("\0".join(distinct).encode("utf-8")).hexdigest()[:16]
    return f"mass_failure::{len(distinct)}_signatures::{digest}"
