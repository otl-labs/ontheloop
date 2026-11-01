"""mcp__github__ payload normalization and pure oracles for the drain runbook (Decision 76).

A Python process cannot call MCP tools -- the agent calls mcp__github__actions_list /
actions_get / get_job_logs / actions_run_trigger between CLI invocations and hands this module
the raw JSON payload. Everything here is pure: normalize the payload's real shape (measured live,
not assumed from the gh CLI it replaces) to this module's internal shape, then decide.

Four measured shape hazards this module exists to close (rec-3381): the real payload uses `id`,
never `databaseId`; `created_at`, never `createdAt`; list_workflow_jobs double-nests as
{"jobs": {"jobs": [...]}}; and get_job_logs' real envelope is flat -- {job_id, logs_content,
message, original_length} -- with no logs[] array and no job_name key (the shape this module's own
planning context predicted before the payload was actually captured and measured).
"""

from __future__ import annotations

from typing import Any, Optional

from scripts import repo_identity
from scripts.ops.drain_glue_orphan._world import WorldMovedError

_REPO_OWNER, _REPO_NAME = repo_identity.owner_and_name()
_RECONCILE_DISPATCH_FILE = "reconcile.yml"
_APPLY_SANDBOX_DISPATCH_FILE = "terraform-apply-sandbox.yml"
_APPLY_SANDBOX_JOB_NAME = "apply-sandbox"
_REVIEW_STEP_NAME = "Subagent plan review (digest-fed, JSON-classified)"
_PLAN_STEP_NAME = "Terraform plan (workflow_dispatch -- fresh plan)"
_DESTRUCTION_LINE = "aws_glue_catalog_database.ops: Destruction complete"
_GLUE_ORPHAN_ADDRESS = "aws_glue_catalog_database.ops"
_PLAN_SUMMARY_MARKER = "Plan:"
_DESTROY_MARKER = " to destroy"
_NO_CHANGES_MARKER = "No changes."
_DESTROYED_LINE_MARKER = "will be destroyed"

_NON_TERMINAL_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})


def normalize_runs(payload: Any) -> list[dict[str, Any]]:
    """Accepts the raw actions_list(list_workflow_runs) envelope ({"workflow_runs": [...],
    "total_count": N}) or an already-unwrapped list. Reads `id` / `created_at` -- the real
    mcp__github__ field names -- never `databaseId` / `createdAt`, the gh-CLI-shaped keys the
    predecessor module read (rec-3381 hazards 1/2: `created_at` absence correlates ZERO candidates
    forever, the silent-failure hazard this normalizer exists to close)."""
    rows = payload.get("workflow_runs", []) if isinstance(payload, dict) else (payload or [])
    return [_normalize_run_row(row) for row in rows]


def normalize_run(payload: dict[str, Any]) -> dict[str, Any]:
    """actions_get(get_workflow_run) returns the same run-object shape as one element of
    list_workflow_runs -- one row normalizer, two callers."""
    return _normalize_run_row(payload)


def _normalize_run_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row.get("status"),
        "conclusion": row.get("conclusion"),
        "created_at": row.get("created_at", ""),
    }


def unwrap_jobs_payload(payload: Any) -> list[dict[str, Any]]:
    """actions_list(list_workflow_jobs) double-nests: {"jobs": {"jobs": [...], "total_count": N}}.
    The gh CLI's own `--json jobs` shape is single-nested ({"jobs": [...]}), so a normalizer
    written against that assumption resolves neither shape's job list correctly (rec-3381 hazard
    3, measured live against the reconcile.yml and terraform-apply-sandbox.yml runs this plan's
    context names). Handles the double- and single-nested dict shapes plus an already-unwrapped
    list."""
    if isinstance(payload, list):
        return payload
    inner = payload.get("jobs", [])
    if isinstance(inner, dict):
        return inner.get("jobs", [])
    return inner or []


def find_job(jobs: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    return next((j for j in jobs if j.get("name") == name), None)


def is_terminal(run: dict[str, Any]) -> bool:
    return run.get("status") not in _NON_TERMINAL_STATUSES


def find_in_flight_dispatch(runs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A dispatch-triggered run already NON-TERMINAL -- dispatching again would park a SECOND run
    on the one human approval this drain spends. `runs` is a bounded, already-filtered
    (workflow + event=workflow_dispatch) listing; this adds no filtering of its own beyond the
    terminal/non-terminal split."""
    for run in runs:
        if run.get("status") in _NON_TERMINAL_STATUSES:
            return run
    return None


def select_dispatch_candidate(runs: list[dict[str, Any]], dispatch_timestamp: str) -> Optional[dict[str, Any]]:
    """FAILS CLOSED (raises) on more than one candidate created_at >= dispatch_timestamp; never
    guesses, never picks the newest. An empty dispatch_timestamp must raise rather than silently
    matching the whole listing (`created_at >= ""` is true for every run) -- an unbound timestamp
    immediately after the irreversible dispatch would raise on multiple candidates anyway, but
    fail-closed here catches it one step earlier and names the real cause."""
    if not dispatch_timestamp:
        raise WorldMovedError("empty dispatch_timestamp -- refusing to correlate against an unbound window")
    candidates = [r for r in runs if r.get("created_at", "") >= dispatch_timestamp]
    if len(candidates) > 1:
        raise WorldMovedError(f"more than one dispatch candidate since {dispatch_timestamp} -- correlate by hand, never guess")
    return candidates[0] if candidates else None


def job_log_lines(envelope: dict[str, Any]) -> list[str]:
    """Reads the real get_job_logs envelope: {job_id, logs_content, message, original_length} --
    flat, no logs[] array, no job_name key. Raises when the returned content is shorter than
    original_length: a truncated log must never license a terminal_without_destruction verdict,
    nor a false "zero destroys" read on the converge four-fact oracle."""
    content = envelope.get("logs_content", "")
    lines = content.splitlines()
    original_length = envelope.get("original_length")
    if original_length is not None and len(lines) < original_length:
        raise WorldMovedError(
            f"job log truncated: {len(lines)} lines returned of {original_length} -- refetch with a larger "
            "tail_lines before reading a destruction verdict from this log"
        )
    return lines


def destruction_complete(envelope: dict[str, Any], marker: str = _DESTRUCTION_LINE) -> bool:
    return any(marker in line for line in job_log_lines(envelope))


def no_remaining_glue_delete(envelope: dict[str, Any], marker: str = _GLUE_ORPHAN_ADDRESS) -> bool:
    """Diagnostic only: is the ORPHAN address specifically absent from the plan log? Reported
    alongside fact 1 so a non-empty destroy set can be triaged per VP19's fix_if (the orphan
    itself vs some other retired address), and deliberately NOT the fact that gates the verdict --
    see plan_shows_zero_destroys."""
    return not any(marker in line for line in job_log_lines(envelope))


def _destroy_count(summary_line: str) -> int:
    """Reads N from a terraform 'Plan: A to add, B to change, N to destroy.' summary line."""
    prefix = summary_line[: summary_line.find(_DESTROY_MARKER)].split()
    token = prefix[-1] if prefix else ""
    if not token.isdigit():
        raise WorldMovedError(
            f"unparseable terraform plan summary {summary_line.strip()!r} -- refusing to read a destroy count "
            "from a line whose shape this oracle does not recognise"
        )
    return int(token)


def plan_shows_zero_destroys(envelope: dict[str, Any]) -> bool:
    """CONVERGE fact 1: the fresh main-tip plan destroys NOTHING.

    Widened from the orphan-address scan the pre-split module used, exactly as VP19 specifies: a
    destroy of ANY remaining retired address would route the guard and make this phase
    unreachable, so this oracle tests that premise rather than assuming it.

    PRESENCE-required, not absence-based. The predecessor read `orphan_address not in plan_log`
    with plan_log defaulting to "" whenever the plan step was missing, so an empty, truncated or
    wrong-job log scored as "no destroys" -- fail-open on the very fact that licenses converge. A
    log carrying no terraform plan verdict at all RAISES here instead of passing.
    """
    lines = job_log_lines(envelope)
    summaries = [line for line in lines if _PLAN_SUMMARY_MARKER in line and _DESTROY_MARKER in line]
    if not summaries and not any(_NO_CHANGES_MARKER in line for line in lines):
        raise WorldMovedError(
            "job log carries no terraform plan verdict (no 'Plan: ... to destroy' summary, no 'No changes.') -- "
            "refusing to read zero-destroys from a log that never planned; refetch the plan step's log"
        )
    if any(_DESTROYED_LINE_MARKER in line for line in lines):
        return False
    return all(_destroy_count(line) == 0 for line in summaries)


def resolve_apply_sandbox_job(jobs_payload: Any) -> dict[str, Any]:
    """apply-sandbox's own job, failing closed when the workflow topology has moved."""
    job = find_job(unwrap_jobs_payload(jobs_payload), _APPLY_SANDBOX_JOB_NAME)
    if job is None:
        raise WorldMovedError(
            f"world has moved -- re-assess: no job named {_APPLY_SANDBOX_JOB_NAME!r} in the supplied jobs payload"
        )
    return job


def assert_plan_step_ran(job: dict[str, Any]) -> None:
    """The plan log fact 1 reads comes from this step, so the step must have actually RUN.

    Presence alone is not enough: the step is gated `if: github.event_name == 'workflow_dispatch'`,
    so a push-triggered run carries it present-but-SKIPPED, producing no plan output at all. A
    name-only check would pass that job and hand fact 1 a log with nothing to find -- the
    absence-is-success shape this module refuses everywhere else, and the same reason the
    pre-split module's empty-plan_log default scored as "no destroys".
    """
    step = next((s for s in job.get("steps", []) if s.get("name") == _PLAN_STEP_NAME), None)
    if step is None:
        raise WorldMovedError(
            f"world has moved -- re-assess: job {_APPLY_SANDBOX_JOB_NAME!r} carries no step named "
            f"{_PLAN_STEP_NAME!r}; the plan log fact 1 reads does not exist, and treating its absence as "
            "'no destroys' would fail open"
        )
    if step.get("conclusion") != "success":
        raise WorldMovedError(
            f"step {_PLAN_STEP_NAME!r} concluded {step.get('conclusion')!r}, not 'success' -- it produced no "
            "plan output, so an absent destroy line means nothing; re-dispatch at ref=main via workflow_dispatch"
        )


def assert_log_matches_job(envelope: dict[str, Any], job: dict[str, Any]) -> Any:
    """The get_job_logs envelope carries the job_id it was fetched for. Nothing else in the chain
    ties the agent-supplied log to the correlated run, so without this a verdict about THIS job
    could be read out of some other job's log entirely."""
    envelope_job_id = envelope.get("job_id")
    job_id = job.get("id")
    if envelope_job_id is None or job_id is None or envelope_job_id != job_id:
        raise WorldMovedError(
            f"job-logs envelope reports job_id {envelope_job_id!r} but the correlated "
            f"{_APPLY_SANDBOX_JOB_NAME!r} job is {job_id!r} -- refusing to read a verdict from another job's log"
        )
    return job_id


def converge_guard_review_facts(jobs_payload: Any) -> dict[str, bool]:
    """guard_routed / review_approving, read from apply-sandbox's own step conclusions -- never
    inferred from log text. Fails closed (raises) when the job or its review step cannot be found:
    inferring guard_routed from either absence would fail OPEN on the authoritative safety oracle
    the whole converge phase exists to check."""
    job = resolve_apply_sandbox_job(jobs_payload)
    steps = {s.get("name"): s for s in job.get("steps", [])}
    review_step = steps.get(_REVIEW_STEP_NAME)
    if review_step is None:
        raise WorldMovedError(
            f"world has moved -- re-assess: job {_APPLY_SANDBOX_JOB_NAME!r} carries no step named "
            f"{_REVIEW_STEP_NAME!r}; the guard/review verdicts cannot be read, and inferring them from its "
            "absence would fail open"
        )
    return {
        "guard_routed": review_step.get("conclusion") == "skipped",
        "review_approving": review_step.get("conclusion") == "success",
    }
