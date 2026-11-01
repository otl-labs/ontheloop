"""Engine-agnostic reader protocol and the DuckLake closed-boundary read client.

The Reader protocol defines the minimal verb surface so an alternative backing
implementation can satisfy it without changing call sites (CD.8
engine-interchangeability). DuckLakeReader is the sole implementation: every ops
read transits the SigV4-signed ducklake_reader Function URL, and the SCD2
latest-per-merge-key projection is materialised server-side in DuckLake.

Current state qualification (materialised by the reader, not the client):
- ops_recommendations, ops_decisions: latest row per id (Decision 56)
- ops_priority_queue: all entries from the latest curator run (Decision 70)

Credential resolution: resolve_aws_profile() returns the named agent_platform
profile when present (local / Claude-Code-on-the-web) and None when running
under CI OIDC (AWS_ACCESS_KEY_ID in environment), letting boto3 fall through
to ambient credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Protocol

from src.common.event_usage_fields import MODEL_USAGE_FIELDS, STARTUP_BASELINE_FIELDS, TOOL_USE_FIELDS

logger = logging.getLogger(__name__)

_DEFAULT_REGION = "eu-west-2"

# DuckLake is the SOLE ops-store backend (Decision 84 I-1).
_DUCKLAKE_READER_URL_ENV = "DUCKLAKE_READER_URL"
_DUCKLAKE_READER_FUNCTION_NAME = "agent-platform-ducklake-reader"

# SSM parameter paths declared in Lambda manifests' runtime_config[] (Decision 79 SSOT).
# Resolution order: env -> SSM -> terraform output -> GetFunctionUrlConfig.
_DUCKLAKE_READER_SSM_PATH = "/agent-platform/ducklake/reader_url"
_DUCKLAKE_WRITER_SSM_PATH = "/agent-platform/ducklake/writer_url"

# Transient reader-invoke resilience: reader ops are idempotent, so retry a transient 5xx with backoff before
# loud-failing. A 502 is a process exit or a deadline overrun, a 503 catalog_unavailable (a dead catalog connection);
# a typed 504 read_deadline_exceeded is the one answer never retried (the same read overruns again).
_READER_MAX_ATTEMPTS = 3
_READER_TRANSIENT_STATUS = frozenset({502, 503, 504})
_READER_RETRY_BACKOFF_S = (2.0, 4.0)
# Literal, not imported: the reader-only deadline module is not in this file's data-pipeline bundle.
_READ_DEADLINE_ERROR_TYPE = "read_deadline_exceeded"

_ORDER_BY_DEFAULT = "last_updated_timestamp"

# Single-key equality row_filter: `<col> = '<value>'`. Both sides are extracted and sent as a
# STRUCTURAL {column, value} filter -- never interpolated into SQL.
_SINGLE_KEY_FILTER_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'([^']*)'\s*$")


def _parse_single_key_filter(row_filter: str) -> tuple[str, str] | None:
    """Return the (column, value) pair of a `<col> = '<value>'` filter, or None if not that shape.

    rec-2170: the previous form returned only the value, discarding the column; the reader then
    bound it against the merge key (WHERE id = '<value>') -- a silent false zero for any
    non-merge-key filter. Keeping the pair makes the filter structural end to end.
    """
    m = _SINGLE_KEY_FILTER_RE.match(row_filter)
    return (m.group(1), m.group(2)) if m else None


def _resolve_function_url_via_ssm(ssm_path: str, *, profile: str | None, region: str) -> str | None:
    """Resolve a Function URL from an SSM parameter. None on any failure.

    Covers CC-web and CI environments where DUCKLAKE_*_URL is unset and there is no
    terraform binary: SSM is lighter than GetFunctionUrlConfig and requires no Lambda
    describe permission. The PlatformDev role carries ssm:GetParameter on the
    /agent-platform/ducklake/* path (Decision 81 endpoint-discovery grant).
    """
    try:
        import boto3  # noqa: PLC0415

        client = boto3.Session(profile_name=profile).client("ssm", region_name=region)
        resp = client.get_parameter(Name=ssm_path, WithDecryption=False)
        return resp["Parameter"]["Value"].rstrip("/") or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("ducklake_reader_client: SSM resolution failed for %s: %s", ssm_path, exc)
        return None


def _resolve_function_url_via_api(function_name: str, *, profile: str | None, region: str) -> str | None:
    """Resolve a Lambda Function URL via lambda:GetFunctionUrlConfig. None on any failure.

    Last-resort fallback for environments with neither the DUCKLAKE_*_URL env nor a terraform-init'd
    checkout -- principally the CI runner (T2.19 cutover), where the github_ci OIDC role carries the
    GetFunctionUrlConfig grant. Best-effort: any error (missing grant, throttle, boto3 absent) returns
    None so the caller can raise a single actionable error.
    """
    try:
        import boto3  # noqa: PLC0415

        client = boto3.Session(profile_name=profile).client("lambda", region_name=region)
        return client.get_function_url_config(FunctionName=function_name).get("FunctionUrl")
    except Exception as exc:  # noqa: BLE001 -- best-effort fallback; caller raises if this returns None
        logger.warning("ducklake_reader_client: GetFunctionUrlConfig fallback failed for %s: %s", function_name, exc)
        return None


class Reader(Protocol):
    """Minimal engine-agnostic read interface.

    Any implementation must satisfy this protocol without changing call sites (CD.8).

    named(), dq_check() and describe() are deliberately NOT part of this Protocol -- named() was
    already an undeclared DuckLakeReader-only extra before this file added describe(),
    so widening the Protocol to cover the new method would silently formalise that gap by omission
    rather than by decision. A caller that needs describe() holds a DuckLakeReader (or the
    narrower structural type it needs), not the Reader Protocol.
    """

    def current_state(
        self,
        table: str,
        *,
        partition_by: str = "id",
        order_by: str = _ORDER_BY_DEFAULT,
        row_filter: str | None = None,
        selected_fields: tuple[str, ...] | None = None,
        snapshot_id: int | None = None,
    ) -> list[dict]: ...

    def latest_snapshot(self, table: str) -> int | None: ...


# ---------------------------------------------------------------------------
# Declared verb projections (Decision 84 I-3) -- the true column set NAMED_READS' registered SQL
# actually returns for each verb. Mirrors src.common.ducklake_scd2_schema.NAMED_READS client-side,
# because that module is excluded from the data-pipeline Lambda manifest (unimportable at Lambda
# runtime) -- hence the literal version pin below, asserted (not commented) equal to the registry's
# own NAMED_READS_VERSION by tests/common/ducklake_reader_client/test_verb_fields_parity.py. Every
# fixed-column entry is proven against the verb's real SQL executed in DuckDB by that same test; the
# four SELECT * verbs carry the ALL_TABLE_COLUMNS sentinel instead of a hand-copied ~80-column list,
# so a routine field_semantics column add cannot silently drift them.
# ---------------------------------------------------------------------------

# Sentinel: this verb's column set equals its underlying table's full declared column set (a
# `SELECT *` verb), resolved at test/fixture time via ducklake_scd2_schema.resolve_table_spec().
ALL_TABLE_COLUMNS: object = object()

# Same-commit parity with src.common.ducklake_scd2_schema.NAMED_READS_VERSION, asserted by
# test_verb_fields_parity.py::test_version_pin_matches_registry.
_NAMED_READS_VERSION_PIN = 6

# Every event-journal verb's trailing response stamps (Decision 207: generations and conflicts are reported, never resolved).
_STAMP_FIELDS = ("generations", "conflicted_grain_keys", "derivation_conflicted")
_STATE_FIELDS = (
    "session_id",
    "parent_session_id",
    "project_id",
    "session_started_at",
    "state",
    "outcome",
    "duration_seconds",
    "last_event_timestamp",
    "as_of",
    "idle_threshold_seconds",
    *_STAMP_FIELDS,
)

VERB_FIELDS: dict[str, tuple[str, ...] | object] = {
    "open_recs": ("id", "title", "context", "created_timestamp", "automatable"),
    "rec_by_id": ALL_TABLE_COLUMNS,
    "recs_by_ids": ALL_TABLE_COLUMNS,
    "recs_for_files": ("id", "title", "status", "priority", "effort", "source", "file", "tags", "created_timestamp"),
    "recs_related": (
        "id",
        "title",
        "status",
        "priority",
        "effort",
        "source",
        "file",
        "created_timestamp",
        "direct",
        "score",
        "matched_terms",
        "snippet",
        "total",
        "direct_total",
    ),
    "open_recs_tally": ("open_count", "aging_count", "non_automatable_count"),
    "preflight_signal_rows": (
        "id",
        "title",
        "status",
        "source",
        "priority",
        "file",
        "automatable",
        "created_timestamp",
        "last_updated_timestamp",
        "context_v2_json",
        "context",
        "acceptance",
    ),
    "recs_by_title_prefix": ("id", "title", "status", "source"),
    "ci_rca_open": ("id", "title", "priority", "created_timestamp", "file"),
    "ci_rca_since": ("id",),
    "forward_fix_recursion": ("file", "cnt"),
    "budget_bypass_recent": ("id", "context", "created_timestamp"),
    "rec_history": ALL_TABLE_COLUMNS,
    "count_by_status": ("status", "n"),
    "decision_by_id": ALL_TABLE_COLUMNS,
    "decisions_max_updated": ("ts",),
    "priority_queue_current": ALL_TABLE_COLUMNS,
    # Event-journal verbs (the reader-only EVENT_READS registry, registry v6): derived columns only, plus the response stamps.
    "session_state_and_duration": _STATE_FIELDS,
    "sessions_window": _STATE_FIELDS,
    "sessions_window_all_purposes": _STATE_FIELDS,
    "session_process_event_rollup": ("session_id", "process_event_total", "steps_completed_total", *_STAMP_FIELDS),
    "session_friction_rollup": (
        "session_id",
        "rework_total",
        "exception_total",
        "label_counts",
        "unmapped_count",
        "classifier_version",
        *_STAMP_FIELDS,
    ),
    "agent_run_state_and_duration": (
        "agent_run_id",
        "session_id",
        "agent_type",
        "state",
        "outcome",
        "duration_seconds",
        "last_event_timestamp",
        "as_of",
        "idle_threshold_seconds",
        *_STAMP_FIELDS,
    ),
    "agent_run_token_rollup": (
        "session_id",
        "tokens_input_total",
        "tokens_output_total",
        "tokens_cache_read_total",
        "tokens_cache_creation_total",
        "model_call_count",
        "usage_missing_count",
        *_STAMP_FIELDS,
    ),
    "model_call_cost_estimate": (
        "session_id",
        "observation_id",
        "event_timestamp",
        "model",
        "provider",
        "billing_shape",
        "tokens_input",
        "tokens_output",
        "cost_usd",
        "cost_source",
        *_STAMP_FIELDS,
    ),
    "model_usage_rollup": (*MODEL_USAGE_FIELDS, *_STAMP_FIELDS),
    "tool_use_rollup": (*TOOL_USE_FIELDS, *_STAMP_FIELDS),
    "session_startup_baseline": (*STARTUP_BASELINE_FIELDS, *_STAMP_FIELDS),
    "telemetry_health_count": ("table_name", "record_count", "meta_schema"),
    "session_tree_rows": ("table_name", "row_json", "total_rows", "roots_in_tree"),
    "telemetry_orphan_counts": (
        "day",
        "join_id",
        "child_rows",
        "orphans_quiescent",
        "orphans_pending",
        "trees_quiescent",
        "trees_pending",
        "conflicted_keys",
    ),
    "telemetry_root_sessions": (
        "session_id",
        "entity_ref",
        "harness_session_key",
        "branch",
        "session_started_at",
        "last_event_timestamp",
        "generations",
        "conflicted_grain_keys",
        "derivation_conflicted",
    ),
    "transcript_content": (
        "storage",
        "encoding",
        "offset",
        "length",
        "content_bytes",
        "content_sha256",
        "data",
        "omitted_reason",
    ),
}


class ReaderInvokeError(RuntimeError):
    """A ducklake_reader invocation returned a non-200 response.

    Carries the HTTP status and the parsed JSON error body (when the body parses as JSON; None
    otherwise) alongside the flat message, so a caller building typed exceptions on top (e.g.
    scripts/agent_sdk/errors.py) can map the boundary's own {"ok": false, "error"/"error_type"}
    shapes instead of regexing this exception's message string. Defined HERE (not imported from
    scripts/agent_sdk/errors.py) because this module is bundled into the data-pipeline Lambda
    manifest, which enumerates scripts/ files explicitly and does not include
    scripts/agent_sdk/** -- a module-scope import from there would ModuleNotFoundError in the
    prod Lambda. errors.py imports FROM here, never the reverse.
    """

    def __init__(self, action: str | None, status: int | None, text: str, body: dict[str, Any] | None) -> None:
        self.action = action
        self.status = status
        self.text = text
        self.body = body
        super().__init__(f"ducklake_reader {action!r} failed (HTTP {status}): {text[:300]}")


class ResponseTooLargeError(ReaderInvokeError):
    """The reader refused a response above its size cap (HTTP 413 response_too_large, rec-4232).

    Never retried: the same request yields the same oversize answer. Narrow the read to a named verb.
    """

    def __init__(self, action: str | None, text: str, body: dict[str, Any] | None) -> None:
        super().__init__(action, 413, text, body)
        self.bytes = (body or {}).get("bytes")
        self.cap = (body or {}).get("cap")


class ReadDeadlineExceededError(ReaderInvokeError):
    """The reader interrupted an event-journal read at its deadline (HTTP 504 read_deadline_exceeded, rec-4474).

    Never retried: the same read overruns again. Narrow the window.
    """

    def __init__(self, action: str | None, text: str, body: dict[str, Any] | None) -> None:
        super().__init__(action, 504, text, body)


def _parse_error_body(text: str) -> dict[str, Any] | None:
    """Best-effort JSON-parse of a reader error response body; None if it isn't a JSON object."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class DuckLakeReader:
    """DuckLake closed-boundary read layer (T2.19 / Decision 81): reads transit ducklake_reader.

    Satisfies the Reader protocol over the AWS_IAM Function URL (SigV4-signed). `current_state`
    returns the `current` write-through projection (the SCD2 latest-per-merge-key is materialised in
    DuckLake itself, so there is no client-side dedup). There is no escape hatch: a reader failure
    raises (the portal's closed-boundary callers surface it; sync_ops/preflight catch to degrade
    gracefully).
    """

    def __init__(self, profile: str | None = None, region: str = _DEFAULT_REGION) -> None:
        self._profile = profile
        self._region = region

    def _reader_url(self) -> str:
        """Resolve the ducklake_reader Function URL.

        Resolution order (Decision 79 SSOT):
          1. env DUCKLAKE_READER_URL -- CI / explicit override
          2. SSM /agent-platform/ducklake/reader_url -- CC-web (no terraform binary)
          3. terraform output ducklake_reader_function_url -- local dev with initialized checkout
          4. lambda:GetFunctionUrlConfig -- last resort (CI runner, github_ci OIDC role)

        Loud-fail if all four are unavailable.
        """
        url = os.environ.get(_DUCKLAKE_READER_URL_ENV)
        if url:
            return url.rstrip("/")
        ssm_url = _resolve_function_url_via_ssm(_DUCKLAKE_READER_SSM_PATH, profile=self._profile, region=self._region)
        if ssm_url:
            return ssm_url
        import subprocess  # noqa: PLC0415

        try:
            proc = subprocess.run(
                ["terraform", "-chdir=terraform/personal", "output", "-raw", "ducklake_reader_function_url"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().rstrip("/")
        except FileNotFoundError:
            pass
        api_url = _resolve_function_url_via_api(_DUCKLAKE_READER_FUNCTION_NAME, profile=self._profile, region=self._region)
        if api_url:
            return api_url.rstrip("/")
        raise RuntimeError(
            f"{_DUCKLAKE_READER_URL_ENV} not set, SSM {_DUCKLAKE_READER_SSM_PATH!r} unavailable, "
            "terraform output 'ducklake_reader_function_url' unavailable, and "
            "lambda:GetFunctionUrlConfig fallback failed -- cannot reach the DuckLake reader "
            "(Decision 84: DuckLake is the sole ops backend)."
        )

    def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """SigV4-POST *payload* to the reader Function URL; return the parsed JSON body. Loud-fail on non-200."""
        import boto3  # noqa: PLC0415
        import requests  # noqa: PLC0415
        from botocore.auth import SigV4Auth  # noqa: PLC0415
        from botocore.awsrequest import AWSRequest  # noqa: PLC0415

        from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

        url = self._reader_url()
        body = json.dumps(payload)
        session = boto3.Session(profile_name=resolve_aws_profile(self._profile, default="agent_platform"))
        creds = session.get_credentials().get_frozen_credentials()

        last_status: int | None = None
        last_text = ""
        last_body: dict[str, Any] | None = None
        for attempt in range(_READER_MAX_ATTEMPTS):
            # Re-sign per attempt: SigV4 carries a timestamp, so a fresh request avoids skew on retry.
            aws_req = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
            SigV4Auth(creds, "lambda", self._region).add_auth(aws_req)
            resp = requests.post(url, data=body, headers=dict(aws_req.headers), timeout=180)
            if resp.status_code == 200:
                return resp.json()
            last_status, last_text = resp.status_code, resp.text
            last_body = _parse_error_body(resp.text)
            if resp.status_code == 413:
                raise ResponseTooLargeError(payload.get("action"), last_text, last_body)
            if resp.status_code == 504 and (last_body or {}).get("error_type") == _READ_DEADLINE_ERROR_TYPE:
                raise ReadDeadlineExceededError(payload.get("action"), last_text, last_body)
            if resp.status_code in _READER_TRANSIENT_STATUS and attempt < _READER_MAX_ATTEMPTS - 1:
                logger.warning(
                    "ducklake_reader %r HTTP %d (attempt %d/%d) -- retrying after backoff",
                    payload.get("action"),
                    resp.status_code,
                    attempt + 1,
                    _READER_MAX_ATTEMPTS,
                )
                time.sleep(_READER_RETRY_BACKOFF_S[attempt])
                continue
            break
        raise ReaderInvokeError(payload.get("action"), last_status, last_text, last_body)

    def current_state(
        self,
        table: str,
        *,
        partition_by: str = "id",
        order_by: str = _ORDER_BY_DEFAULT,
        row_filter: str | None = None,
        selected_fields: tuple[str, ...] | None = None,
        snapshot_id: int | None = None,
    ) -> list[dict]:
        """Return current-projection rows for *table*. `row_filter` pushes a WHERE down to the reader.

        partition_by/order_by/selected_fields/snapshot_id are part of the Reader protocol but are
        no-ops here: DuckLake materialises the current projection, so no client-side dedup or
        snapshot pinning is applied.
        """
        if row_filter is None:
            body = self._invoke({"action": "read_ops_current", "table": table})
            return list(body.get("rows", []))
        # Parameterize the single-key equality form (`<col> = '<value>'`) into the structural
        # {column, value} filter (rec-2170: the column travels with the value, and the reader
        # validates it against the table contract). Never interpolated into SQL.
        parsed = _parse_single_key_filter(row_filter)
        if parsed is None:
            raise ValueError(
                f"DuckLakeReader.current_state: row_filter must be a single-key equality "
                f"(\"<col> = '<value>'\"); got {row_filter!r}. Use named() for pre-established reads."
            )
        column, value = parsed
        body = self._invoke({"action": "read_ops_current", "table": table, "filter": {"column": column, "value": value}})
        return list(body.get("rows", []))

    def latest_snapshot(self, table: str) -> int | None:
        """DuckLake current is a live projection (no snapshot id). Returns None by contract."""
        return None

    def describe(self) -> dict[str, dict[str, Any]]:
        """Return the per-verb parameter schema for every NAMED_READS entry (CD.10 / CD.15).

        Mirrors named()'s loud-fail posture: an unreachable or erroring reader raises
        (ReaderInvokeError, or the _reader_url resolution RuntimeError) rather than degrading to
        a stale or hardcoded verb list -- a caller projecting a tool surface from this (e.g.
        scripts/agent_sdk/mcp_server.py) must never silently fall back to hand-registration.
        """
        body = self._invoke({"action": "describe"})
        return dict(body.get("verbs", {}))

    def named(self, verb: str, **params: Any) -> list[dict]:
        """Execute a pre-established read verb on the reader (Decision 84 I-3).

        The SQL lives server-side in the reader's registry; the caller names the verb and binds
        params. Loud-fail on an unknown verb, a param mismatch, or an unreachable reader -- a
        failure is never a silent empty result (Decision 55).
        """
        body = self._invoke({"action": "named_read", "verb": verb, "params": params})
        return list(body.get("rows", []))

    def dq_check(self, check: str, table: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one closed data-quality check on the reader; returns {check, table, violation_count, check_version, ...}.

        The reader owns the SQL (Decision 84 I-3): the caller names a kind from its fixed vocabulary and binds params.
        Raises on any non-200 answer (Decision 55); a typed 400 dq_check_input names the refused parameter.
        """
        return self._invoke({"action": "dq_check", "check": check, "table": table, "params": params or {}})


_MAX_RECS_BY_IDS = 50


def fetch_recs_by_ids(reader: Any, ids: Any) -> tuple[list[dict], list[str]]:
    """Fetch recommendations by id through the registered `named` verb; return (rows, missing ids).

    Strips and de-duplicates *ids* (order kept), refuses more than 50 with ValueError, and reports the
    requested ids that did not resolve so a caller never mistakes an absent rec for a present one.
    """
    wanted = list(dict.fromkeys(i.strip() for i in ids if isinstance(i, str) and i.strip()))
    if not wanted:
        return [], []
    if len(wanted) > _MAX_RECS_BY_IDS:
        raise ValueError(f"fetch_recs_by_ids: at most {_MAX_RECS_BY_IDS} ids per call; got {len(wanted)}")
    rows = list(reader.named("recs_by_ids", ids=",".join(wanted)))
    found = {r.get("id") for r in rows}
    return rows, [i for i in wanted if i not in found]


def make_reader(profile: str | None = None, table: str | None = None) -> Reader:
    """Return the operational Reader: DuckLakeReader for every ops table (Decision 84 I-1).

    The *table* parameter is retained for call-site compatibility; all ops_* tables transit the
    closed DuckLake boundary.
    """
    return DuckLakeReader(profile=profile)
