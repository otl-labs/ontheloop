"""DuckLake + prod-class code-drift alarms (T2.38/T2.43, Decision 125/126).

Reads each governed function's deploy record (scripts.build_lambda_deploy.
read_deploy_record) and applies a REACHABILITY oracle (Decision 103): fresh
iff the latest main commit touching source paths reachable FROM the recorded
source_git_sha equals the latest such commit on HEAD -- not bare equality,
so a deploy from a later, non-source-touching commit (e.g. a workflow_dispatch
recovery run) is correctly reported fresh. A null/empty/unresolvable recorded
sha is always stale (fail closed). Mirrors escalate()'s idempotent
file/update/close pattern via scripts.rec_episode.run_episode, but ANY stale
function triggers exactly ONE deduped rec (never one per function). Never
writes a deploy record; never redeploys; never runs build_lambda. Alarm-not-gate (priority High,
never source=ci_rca). Part of the scripts.convergence_health package -- see
scripts/convergence_health/__init__.py for the full public surface.
"""

from __future__ import annotations

import subprocess
from typing import Any, Callable, Optional

from scripts.rec_episode import run_episode

DUCKLAKE_SOURCE_PATHSPECS: tuple[str, ...] = (
    "src/lambdas/ducklake_writer",
    "src/lambdas/ducklake_reader",
    "src/lambdas/ducklake_maintenance",
    "src/lambdas/ducklake_catalog_dr",
    "src/lambdas/ducklake_telemetry_writer",
    "src/lambdas/ducklake_telemetry_reader",
    "src/common/ducklake_*.py",
    "src/row_rules",
    "src/telemetry",
    "config/lambda/ducklake",
)


def _assert_full_history(runner: Callable[[list[str]], str]) -> None:
    """Fail loud when the working clone is shallow (Decision 55 anti-masking).

    The reachability oracle below resolves "the latest watched-source commit reachable from
    <recorded sha>" via ``git log <sha> -- <pathspecs>``; on a shallow clone that lookup can
    silently truncate at the fetch boundary and misreport drift with no error at all.
    convergence-health.yml checks out with fetch-depth 0 specifically so this holds -- this guard
    exists so a future regression of that setting fails loud instead of quietly reporting a false
    drift result.
    """
    is_shallow = runner(["git", "rev-parse", "--is-shallow-repository"]).strip()
    if is_shallow == "true":
        raise RuntimeError(
            "scripts.convergence_health.code_drift: refusing to run the reachability oracle "
            "against a shallow git clone (git rev-parse --is-shallow-repository == true). "
            "convergence-health.yml must check out with fetch-depth 0; unshallow the clone "
            "(git fetch --unshallow) and retry."
        )


def _is_fresh(
    record: Optional[dict[str, Any]],
    latest_sha: str,
    runner: Callable[[list[str]], str],
    pathspecs: tuple[str, ...],
) -> bool:
    """Reachability oracle (Decision 103): a deploy record is fresh iff the latest watched-source
    commit reachable FROM its recorded source_git_sha equals the latest watched-source commit on
    HEAD (`latest_sha`). A push-triggered deploy sets source_git_sha to the exact commit that
    touched source; a workflow_dispatch recovery deploy can set it to a LATER commit that never
    itself touches source -- both are correctly FRESH here, unlike a bare equality comparison
    (the defect that let a dead sensor report false drift after a workflow_dispatch recovery).

    Fails closed (returns False) when: there is no record at all, `latest_sha` itself could not
    be resolved (empty -- never treat "nothing to compare against" as fresh), the recorded
    source_git_sha is null or empty (e.g. a break-glass local deploy -- see
    build_lambda_deploy.write_deploy_record, which deliberately never fabricates a sha), or the
    recorded sha is unresolvable in history (the git lookup then yields "" and can never match a
    non-empty latest_sha).
    """
    if record is None or not latest_sha:
        return False
    recorded_sha = record.get("source_git_sha")
    if not recorded_sha:
        return False
    reachable = runner(["git", "log", "-1", "--format=%H", recorded_sha, "--", *pathspecs]).strip()
    return bool(reachable) and reachable == latest_sha


def _build_ducklake_drift_context(stale_functions: list[str], latest_sha: str) -> str:
    fn_list = ", ".join(sorted(stale_functions))
    return (
        f"The following DuckLake Lambda function(s) have a deployed CodeSha256 whose recorded "
        f"source_git_sha does not reach (or has no deploy record for) the latest main commit "
        f"touching ducklake source ({latest_sha[:12] if latest_sha else 'unknown'}): {fn_list}. The "
        "latest ducklake-source commit reachable from the recorded deploy sha is stale, absent, or "
        "unresolvable (Decision 103 reachability oracle). Redeploy via the governed channel "
        "(.github/workflows/deploy-ducklake-lambdas.yml -- push to main touching ducklake source, "
        "or workflow_dispatch) to bring the deployed code current. This rec closes automatically on "
        "the next sensor tick once every function's deploy record reaches the latest ducklake-source "
        "commit."
    )


def _build_drift_acceptance(stale_functions: list[str], latest_sha: str, channel: str) -> str:
    """Chain a reachability probe per stale function (Decision 103): the deterministic
    satisfaction oracle (scripts.rec_relevance) runs this same acceptance command, so it must
    encode the same reachability check the sensor itself now uses -- not a bare equality grep
    against `latest_sha`, which a later correct redeploy (e.g. a subsequent workflow_dispatch run
    off a newer commit) would fail even though the drift is genuinely resolved. Extracts the
    current deploy record's source_git_sha and asserts it is a git-ancestor of (or equal to)
    `latest_sha` via the same `git merge-base --is-ancestor` idiom used elsewhere in this repo
    for reachability checks (e.g. scripts/executor/branch_lifecycle.py,
    scripts/ops_portal/ci_rca_lifecycle.py). `test -n "$sha"` guards against an empty extraction
    (missing/malformed record) silently short-circuiting to a false pass.
    """
    return " && ".join(
        f"sha=$(aws s3 cp s3://agent-platform-data-lake/deploy-records/{channel}/{fn}.json - "
        f'--profile agent_platform | grep -oE \'"source_git_sha":[[:space:]]*"[0-9a-f]{{40}}"\' '
        f'| grep -oE \'[0-9a-f]{{40}}\') && test -n "$sha" && git merge-base --is-ancestor {latest_sha} "$sha"'
        for fn in sorted(stale_functions)
    )


def _build_ducklake_drift_rec_fields(stale_functions: list[str], latest_sha: str) -> dict[str, Any]:
    return {
        "title": "DuckLake Lambda code drift -- deployed code stale vs latest main",
        "file": ".github/workflows/deploy-ducklake-lambdas.yml",
        "status": "open",
        "source": "ducklake_code_drift",
        "priority": "High",
        "effort": "S",
        "risk": "medium",
        "verification_tier": "V2",
        "context": _build_ducklake_drift_context(stale_functions, latest_sha),
        "acceptance": _build_drift_acceptance(stale_functions, latest_sha, "ducklake"),
    }


def detect_ducklake_code_drift(
    git_runner: Optional[Callable[[list[str]], str]] = None,
    s3_client: Any = None,
    portal_caller: Optional[Any] = None,
    open_recs: Optional[list[dict[str, Any]]] = None,
    profile: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotent ducklake code-drift alarm: file/update/close exactly one rec per episode.

    Args:
        git_runner:    Injected callable(argv) -> stdout, mirroring count_unapplied_tf_commits.
                       When None, shells out to the real git binary.
        s3_client:     Injected boto3-like S3 client passed through to read_deploy_record for
                       each of the four ducklake functions. When None, a real client is created
                       (never at import time).
        portal_caller: Injected callable(action, fields) for testability, mirroring escalate().
                       When None, uses scripts.ops_data_portal.file_rec / update_rec directly.
        open_recs:     Pre-fetched rows (for testing) -- treated as if they were already the
                       result of the scoped ducklake_code_drift read. When None, fetches live via
                       current_state("ops_recommendations", row_filter="source = "
                       "'ducklake_code_drift'") (never a bulk fetch of every open rec).
        profile:       AWS profile for the reader / portal / S3 client.

    Returns:
        {"action": "file"|"update"|"unchanged"|"close"|"none"|"skipped", "rec_id": str|None}
    """
    from scripts.build_lambda_config import _build_ducklake_function_zip_keys  # noqa: PLC0415
    from scripts.build_lambda_deploy import read_deploy_record  # noqa: PLC0415

    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.Session(profile_name=profile).client("s3")

    def _default_runner(cmd: list[str]) -> str:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip()

    runner = git_runner or _default_runner
    _assert_full_history(runner)
    latest_sha = runner(["git", "log", "-1", "--format=%H", "--", *DUCKLAKE_SOURCE_PATHSPECS]).strip()

    stale_functions: list[str] = []
    for function in _build_ducklake_function_zip_keys():
        record = read_deploy_record(function, s3_client=s3_client)
        if not _is_fresh(record, latest_sha, runner, DUCKLAKE_SOURCE_PATHSPECS):
            stale_functions.append(function)

    def _build_fields() -> dict[str, Any]:
        return _build_ducklake_drift_rec_fields(stale_functions, latest_sha)

    def _build_update(existing: dict[str, Any]) -> dict[str, Any]:
        return {"context": _build_ducklake_drift_context(stale_functions, latest_sha)}

    def _build_close(existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "closed",
            "resolution": (
                "All DuckLake deploy records match the latest main commit touching ducklake source; drift resolved."
            ),
        }

    return run_episode(
        source="ducklake_code_drift",
        over_threshold=bool(stale_functions),
        build_fields=_build_fields,
        build_update=_build_update,
        build_close=_build_close,
        portal_caller=portal_caller,
        rows=open_recs,
        profile=profile,
    )


# ---------------------------------------------------------------------------
# Prod-class code-drift alarm (T2.43 / Decision 125/126)
#
# Mirrors detect_ducklake_code_drift exactly, scoped to the prod-class functions
# named scheduled-agent-dispatcher and findings-processor, and their
# deploy-records/prod/<function>.json records (read_deploy_record channel="prod"). ANY stale
# function triggers exactly ONE deduped prod_code_drift rec (never one per function). Never
# writes a deploy record; never redeploys; never runs build_lambda. Alarm-not-gate (priority
# High, never source=ci_rca).
# ---------------------------------------------------------------------------

# Kept in sync with .github/workflows/deploy-prod-lambdas.yml's `on.push.paths` filter (single
# conceptual source per rec-2686 -- a path added to one must be added to the other) so the drift
# sensor's notion of "source changed" never diverges from what actually triggers a deploy.
PROD_SOURCE_PATHSPECS: tuple[str, ...] = (
    "src/data/handlers",
    "scripts/aws_profile.py",
    "scripts/llm/github_models_client.py",
    "scripts/llm/client.py",
    "scripts/llm/utils.py",
    "scripts/run_scheduled_agent.py",
    "scripts/s3_log_store.py",
    "scripts/tool_runtime.py",
    "config/lambda/data-pipeline",
)

_PROD_FUNCTION_NAMES: tuple[str, ...] = (
    "agent-platform-scheduled-agent-dispatcher",
    "agent-platform-findings-processor",
)


def _build_prod_drift_context(stale_functions: list[str], latest_sha: str) -> str:
    fn_list = ", ".join(sorted(stale_functions))
    return (
        f"The following prod-class Lambda function(s) have a deployed CodeSha256 whose recorded "
        f"source_git_sha does not reach (or has no deploy record for) the latest main commit "
        f"touching prod source ({latest_sha[:12] if latest_sha else 'unknown'}): {fn_list}. The "
        "latest prod-source commit reachable from the recorded deploy sha is stale, absent, or "
        "unresolvable (Decision 103 reachability oracle). Redeploy via the governed channel "
        "(.github/workflows/deploy-prod-lambdas.yml -- push to main touching prod source, or "
        "workflow_dispatch) to bring the deployed code current. This rec closes automatically on "
        "the next sensor tick once every function's deploy record reaches the latest prod-source "
        "commit."
    )


def _build_prod_drift_rec_fields(stale_functions: list[str], latest_sha: str) -> dict[str, Any]:
    return {
        "title": "Prod-class Lambda code drift -- deployed code stale vs latest main",
        "file": ".github/workflows/deploy-prod-lambdas.yml",
        "status": "open",
        "source": "prod_code_drift",
        "priority": "High",
        "effort": "S",
        "risk": "medium",
        "verification_tier": "V2",
        "context": _build_prod_drift_context(stale_functions, latest_sha),
        "acceptance": _build_drift_acceptance(stale_functions, latest_sha, "prod"),
    }


def detect_prod_code_drift(
    git_runner: Optional[Callable[[list[str]], str]] = None,
    s3_client: Any = None,
    portal_caller: Optional[Any] = None,
    open_recs: Optional[list[dict[str, Any]]] = None,
    profile: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotent prod-class code-drift alarm: file/update/close exactly one rec per episode.

    Mirrors detect_ducklake_code_drift; see that function's docstring for the argument contract.
    The only differences: the three prod-class function names (vs the four ducklake functions),
    PROD_SOURCE_PATHSPECS (vs DUCKLAKE_SOURCE_PATHSPECS), source="prod_code_drift" (vs
    "ducklake_code_drift"), and read_deploy_record(..., channel="prod") (vs the ducklake default).

    Returns:
        {"action": "file"|"update"|"unchanged"|"close"|"none"|"skipped", "rec_id": str|None}
    """
    from scripts.build_lambda_deploy import read_deploy_record  # noqa: PLC0415

    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.Session(profile_name=profile).client("s3")

    def _default_runner(cmd: list[str]) -> str:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip()

    runner = git_runner or _default_runner
    _assert_full_history(runner)
    latest_sha = runner(["git", "log", "-1", "--format=%H", "--", *PROD_SOURCE_PATHSPECS]).strip()

    stale_functions: list[str] = []
    for function in _PROD_FUNCTION_NAMES:
        record = read_deploy_record(function, s3_client=s3_client, channel="prod")
        if not _is_fresh(record, latest_sha, runner, PROD_SOURCE_PATHSPECS):
            stale_functions.append(function)

    def _build_fields() -> dict[str, Any]:
        return _build_prod_drift_rec_fields(stale_functions, latest_sha)

    def _build_update(existing: dict[str, Any]) -> dict[str, Any]:
        return {"context": _build_prod_drift_context(stale_functions, latest_sha)}

    def _build_close(existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "closed",
            "resolution": ("All prod-class deploy records match the latest main commit touching prod source; drift resolved."),
        }

    return run_episode(
        source="prod_code_drift",
        over_threshold=bool(stale_functions),
        build_fields=_build_fields,
        build_update=_build_update,
        build_close=_build_close,
        portal_caller=portal_caller,
        rows=open_recs,
        profile=profile,
    )
