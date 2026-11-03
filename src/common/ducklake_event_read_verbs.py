"""Event-journal read registry: the verbs, their governed data and the data renderer (rec-4291, Decision 199 cl.1).

Pure data, no I/O, nothing that can raise at import. Registry data (the idle threshold, the friction rules block, the window
cap) lives here as versioned Python, hashed into each verb's read_version (event_read_version), so a rule edit re-versions
exactly the verbs that render it. Imported by the telemetry reader only; the ops registry (NAMED_READS) is untouched.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.common import ducklake_event_read_sql as sql
from src.common.ducklake_event_reads import EventRead

IDLE_THRESHOLD_SECONDS = 86400
MAX_WINDOW_DAYS = 7
MAX_TREE_ROWS = 2000

# REPORT-w1-friction-classifier.md section 2 (friction-k1 (a), friction-k2 (a)): rules are data, one row per rule.
FRICTION_RULES: Mapping[str, Any] = {
    "classifier_version": 1,
    "repeat_threshold": 3,
    "run_label": "repeated_tool_failure",
    "run_class": "rework",
    "rules": (
        ("r01", "process_event", "hook:never_on_main", "error", "branch_policy_block", "exception"),
        ("r02", "process_event", "hook:fresh_branch_base", "error", "stale_branch_base", "exception"),
        ("r03", "process_event", "hook:edit_scope_guard", "error", "scope_creep", "exception"),
        ("r04", "process_event", "gate:validate_scope_boundary", "error", "scope_creep", "exception"),
        ("r90", "process_event", None, "error", "unmapped_block", "exception"),
        ("r91", "process_event", None, "warning", "hook_fault", "exception"),
        ("r92", "tool_call", None, "error", "tool_error", "exception"),
        ("r93", "tool_call", None, "blocked", "tool_blocked", "exception"),
        ("r94", "process_event", None, "critical", "unmapped_block", "exception"),
    ),
}

_PRODUCTION = "Reads the production telemetry catalog through the telemetry reader."
_IDLE = {"idle_seconds": IDLE_THRESHOLD_SECONDS}
_ALL = (sql.SESSIONS, sql.OBSERVATIONS, sql.AGENTS, sql.TRANSCRIPTS)
_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _quote(value: str | None) -> str:
    return "CAST(NULL AS VARCHAR)" if value is None else "'" + value.replace("'", "''") + "'"


def render_data(data: Mapping[str, Any]) -> dict[str, str]:
    """Template placeholder -> SQL text for a verb's data block (integers, bare identifiers and the rules VALUES list)."""
    out: dict[str, str] = {}
    for key, value in data.items():
        if key == "rules":
            rows = ", ".join("(" + ", ".join(_quote(v) for v in rule) + ")" for rule in value)
            out[key] = f"(VALUES {rows}) AS v(rule_id, observation_type, signature, disposition, label, class)"
        elif isinstance(value, int):
            out[key] = str(value)
        elif isinstance(value, str) and _NAME.match(value):
            out[key] = value
        else:
            raise ValueError(f"event verb data {key!r} must be an int, a bare identifier or the rules list")
    return out


_TREE_NOTE = (
    f" (one tree is the rows sharing the root's tenant, project and session_started_at), at most {MAX_WINDOW_DAYS} UTC days."
)


def _derived_rows_note(rows: str) -> str:
    return f"{rows} Derived rows only, never a raw row or transcript content. {_PRODUCTION}"


EVENT_READS: dict[str, EventRead] = {
    e.verb: e
    for e in (
        EventRead(
            verb="session_state_and_duration",
            tables=_ALL,
            sql=sql.SESSION_STATE_SQL,
            params=("session_id", "as_of"),
            binding="session",
            data=_IDLE,
            description=_derived_rows_note(
                "One session's derived state (running, abandoned or a terminal outcome), outcome and wall-clock duration."
            ),
        ),
        EventRead(
            verb="session_process_event_rollup",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=sql.PROCESS_EVENT_ROLLUP_SQL,
            params=("session_id",),
            binding="session",
            description=_derived_rows_note("One session's process_event and completed-step totals."),
        ),
        EventRead(
            verb="session_friction_rollup",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=sql.FRICTION_ROLLUP_SQL,
            params=("session_id",),
            binding="session",
            data=FRICTION_RULES,
            description=_derived_rows_note(
                "One session's rework and exception totals, per-label counts and unmapped count (classifier_version stamped)."
            ),
        ),
        EventRead(
            verb="agent_run_state_and_duration",
            tables=(sql.AGENTS, sql.SESSIONS, sql.OBSERVATIONS, sql.TRANSCRIPTS),
            sql=sql.AGENT_RUN_STATE_SQL,
            params=("session_id", "as_of"),
            binding="session",
            data=_IDLE,
            description=_derived_rows_note("State, outcome and duration of each agent run of one child session."),
        ),
        EventRead(
            verb="agent_run_token_rollup",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=sql.AGENT_RUN_TOKEN_ROLLUP_SQL,
            params=("session_id",),
            binding="session",
            description=_derived_rows_note(
                "Uncached input, output, cache-read and cache-creation token totals over one child session's model calls, "
                "one count per call after the entity collapse."
            ),
        ),
        EventRead(
            verb="model_call_cost_estimate",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=sql.MODEL_CALL_COST_SQL,
            params=("session_id",),
            binding="session",
            paginable=True,
            description=_derived_rows_note(
                "Reported cost of each model call of one session with its cost_source "
                "(reported, allowance, local or unpriced); "
                "never a 0 standing in for an unknown."
            ),
        ),
        EventRead(
            verb="sessions_window",
            tables=_ALL,
            sql=sql.SESSIONS_WINDOW_SQL,
            params=("day_from", "day_to", "as_of"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            data=_IDLE,
            paginable=True,
            description=_derived_rows_note(
                f"One derived row per root session of a real-purpose project over at most {MAX_WINDOW_DAYS} UTC days."
            ),
        ),
        EventRead(
            verb="model_usage_rollup",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=sql.MODEL_USAGE_ROLLUP_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            description=_derived_rows_note(
                "Per-model token totals, one row per tree and model over the root and every sub-agent session's model calls"
                + _TREE_NOTE
            ),
        ),
        EventRead(
            verb="tool_use_rollup",
            tables=(sql.OBSERVATIONS, sql.SESSIONS, sql.AGENTS),
            sql=sql.TOOL_USE_ROLLUP_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            description=_derived_rows_note(
                "Tool-call counts, one row per tree, root or sub-agent, agent name, workflow, tool name and MCP server"
                + _TREE_NOTE
            ),
        ),
        EventRead(
            verb="session_startup_baseline",
            tables=(sql.OBSERVATIONS, sql.SESSIONS, sql.AGENTS),
            sql=sql.STARTUP_BASELINE_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            description=_derived_rows_note(
                "Each session's and sub-agent's first model call and its token classes, one row per session of each tree"
                + _TREE_NOTE
            ),
        ),
        EventRead(
            verb="sessions_window_all_purposes",
            tables=_ALL,
            sql=sql.SESSIONS_WINDOW_ALL_SQL,
            params=("day_from", "day_to", "as_of"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            data=_IDLE,
            paginable=True,
            agent_facing=False,
            description=_derived_rows_note(
                f"sessions_window over every project purpose (drills, harnesses, smoke), at most {MAX_WINDOW_DAYS} UTC days."
            ),
        ),
        EventRead(
            verb="transcript_content",
            tables=(sql.TRANSCRIPTS,),
            sql=sql.TRANSCRIPT_CONTENT_SQL,
            params=("session_id", "producer", "event_id", "parser_version", "offset", "max_bytes"),
            binding="session",
            data={"response": "content"},
            int_params=("parser_version", "offset", "max_bytes"),
            agent_facing=False,
            description=(
                "One stored transcript row's exact payload bytes by its full grain key (session_id, producer, event_id, "
                "parser_version), as standard base64 pages of at most 3 MiB: loop while offset + length < content_bytes, "
                "concatenate the decoded pages, then check the length and SHA-256 against the row. Storage omitted carries "
                "no data. Served by no deployed function: production serves no transcript content (Decision 236 cl.3)."
            ),
        ),
        EventRead(
            verb="session_tree_rows",
            tables=_ALL,
            sql=sql.TREE_ROWS_SQL,
            params=("session_id", "offset", "max_rows"),
            binding="tree",
            int_params=("offset", "max_rows"),
            agent_facing=False,
            description=(
                "Verification only. Every stored row of the one tree holding a root session (the rows sharing its tenant, "
                "project and session pin) in all four telemetry tables, any parser_version, no dedupe: table_name, the row "
                "as JSON (compared columns plus created_timestamp, never content or content_uri), total_rows and "
                f"roots_in_tree, paged by offset and max_rows (at most {MAX_TREE_ROWS}). " + _PRODUCTION
            ),
        ),
        EventRead(
            verb="telemetry_orphan_counts",
            tables=_ALL,
            sql=sql.ORPHAN_COUNTS_SQL,
            params=("day_from", "day_to", "as_of"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            data=_IDLE,
            agent_facing=False,
            description=(
                "Verification only. Per UTC day and telemetry join of real-purpose projects: child rows, orphaned child rows "
                "split by whether their tree is quiescent past the idle threshold at as_of, the quiescent and pending tree "
                f"counts and the conflicted grain-key count, over at most {MAX_WINDOW_DAYS} days. " + _PRODUCTION
            ),
        ),
        EventRead(
            verb="telemetry_root_sessions",
            tables=_ALL,
            sql=sql.ROOT_SESSIONS_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            agent_facing=False,
            description=(
                "Verification only. One row per root session of a real-purpose project over at most "
                f"{MAX_WINDOW_DAYS} UTC days: session_id, entity_ref, harness_session_key, branch, session_started_at, the "
                "tree's last event and the Decision 207 stamps. " + _PRODUCTION
            ),
        ),
        EventRead(
            verb="telemetry_health_count",
            tables=_ALL,
            sql=sql.TELEMETRY_HEALTH_SQL,
            agent_facing=False,
            description=(
                "Per-table committed record counts from catalog metadata only (no data file) and the pinned meta schema "
                "(preflight telemetry-store gauge). " + _PRODUCTION
            ),
        ),
    )
}
