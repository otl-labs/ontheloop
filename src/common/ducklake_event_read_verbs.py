"""Event-journal read registry: the verbs, their governed data and the data renderer (rec-4291, Decision 199 cl.1).

Pure data, no I/O, nothing that can raise at import. Registry data (the idle threshold, the friction rules block, the window
cap) lives here as versioned Python, hashed into each verb's read_version (event_read_version), so a rule edit re-versions
exactly the verbs that render it. The friction SQL text lives here, composed from the sql module's pieces (still data, no
I/O). Imported by the telemetry reader only; the ops registry (NAMED_READS) is untouched.
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
    "classifier_version": 3,
    "repeat_threshold": 3,
    "run_label": "repeated_tool_failure",
    "run_class": "rework",
    "rules": (
        ("r01", "process_event", "hook:never_on_main", None, "error", "branch_policy_block", "exception"),
        ("r02", "process_event", "hook:fresh_branch_base", None, "error", "stale_branch_base", "exception"),
        ("r03", "process_event", "hook:edit_scope_guard", None, "error", "scope_creep", "exception"),
        ("r04", "process_event", "gate:validate_scope_boundary", None, "error", "scope_creep", "exception"),
        ("r05", "process_event", "hook:scheduled_agent_log_only", None, "error", "scheduled_agent_write_block", "exception"),
        ("r06", "process_event", "hook:handoff_evidence_gate", None, "error", "handoff_evidence_block", "exception"),
        ("r07", "process_event", "hook:merge_gate", None, "error", "merge_gate_block", "exception"),
        ("r08", "process_event", "hook:rec_writer_tripwire", None, "error", "rec_write_block", "exception"),
        ("r09", "process_event", "hook:protected_action_gate", None, "error", "protected_action_block", "exception"),
        ("r10", "process_event", None, "status:", "warning", "reported_skip", "notice"),
        ("r11", "process_event", None, "status:", "error", "reported_failure", "exception"),
        ("r20", "process_event", "gate:validate_plan_documents", None, "error", "check_failure", "exception"),
        ("r21", "process_event", "gate:precommit_changed", None, "error", "check_failure", "exception"),
        ("r22", "process_event", "gate:pytest_diff", None, "error", "check_failure", "exception"),
        ("r23", "process_event", "gate:validate_plan_scope_closure", None, "error", "check_failure", "exception"),
        ("r24", "process_event", "gate:validate_weakening_dispositions", None, "error", "check_failure", "exception"),
        ("r25", "process_event", "gate:validate_merge_step", None, "error", "check_failure", "exception"),
        ("r26", "process_event", "gate:lint", None, "error", "check_failure", "exception"),
        ("r27", "process_event", "gate:validate_contract_drift", None, "error", "check_failure", "exception"),
        ("r28", "process_event", "gate:validate_sloc_limits", None, "error", "check_failure", "exception"),
        ("r29", "process_event", "gate:validate_decisions_index_freshness", None, "error", "check_failure", "exception"),
        ("r30", "process_event", "gate:validate_glossary", None, "error", "check_failure", "exception"),
        ("r31", "process_event", "gate:validate_tier_demotion_markers", None, "error", "check_failure", "exception"),
        ("r32", "process_event", "gate:validate_graduation_completeness", None, "error", "check_failure", "exception"),
        ("r33", "process_event", "gate:validate_fallback_reevaluation", None, "error", "check_failure", "exception"),
        ("r34", "process_event", "gate:validate_project_region", None, "error", "check_failure", "exception"),
        ("r35", "process_event", "gate:validate_pre_glob_closure", None, "error", "check_failure", "exception"),
        ("r36", "process_event", "gate:validate_cc_limits", None, "error", "check_failure", "exception"),
        ("r37", "process_event", "gate:validate_prose_budget_raises", None, "error", "check_failure", "exception"),
        ("r38", "process_event", "gate:validate_handoff_full_tier", None, "error", "check_failure", "exception"),
        ("r39", "process_event", "gate:validate_graduated_rerun_plan_readers", None, "error", "check_failure", "exception"),
        ("r50", "process_event", "precommit:detect-secrets", None, "error", "precommit_failure", "exception"),
        ("r90", "process_event", None, None, "error", "unmapped_block", "exception"),
        ("r91", "process_event", None, None, "warning", "hook_fault", "exception"),
        ("r92", "tool_call", None, None, "error", "tool_error", "exception"),
        ("r93", "tool_call", None, None, "blocked", "tool_blocked", "exception"),
        ("r94", "process_event", None, None, "critical", "unmapped_block", "exception"),
    ),
}

# Friction (REPORT-w1-friction-classifier.md section 2; classifier_version 3): facts are process_events at severity
# error/warning/critical and tool_call closes with outcome error/blocked; one label per fact (an exact signature beats a
# literal signature prefix, which beats the generic rule); a tool_call close at error or blocked whose own child
# process_event at error is already a fact is absorbed, not counted twice; rework is the repeat-run rule over named closes.
# The text lives here, composed from the sql module's pieces (still data, no I/O).
FRICTION_CTES = (
    "f AS (SELECT * FROM o WHERE (observation_type = 'process_event' AND severity IN ('error', 'warning', 'critical')) "
    "OR (observation_type = 'tool_call' AND event_kind = 'close' AND outcome IN ('error', 'blocked'))), "
    "rules AS (SELECT * FROM {rules}), "
    "absorbed AS (SELECT c.session_id AS session_id, c.observation_id AS observation_id FROM f c "
    "WHERE c.observation_type = 'tool_call' AND c.outcome IN ('error', 'blocked') AND EXISTS ("
    "SELECT 1 FROM f h WHERE h.observation_type = 'process_event' AND h.severity = 'error' "
    "AND h.session_id = c.session_id AND h.parent_observation_id = c.observation_id)), "
    "labelled AS (SELECT f.tenant_id AS tenant_id, f.project_id AS project_id, f.session_started_at AS session_started_at, "
    "f.session_id AS session_id, f.observation_id AS ref, f.observation_type AS observation_type, f.name AS name, "
    "coalesce(f.severity, f.outcome) AS disposition, r.rule_id AS rule_id, r.label AS label, r.class AS class, "
    "(r.signature IS NULL AND r.signature_prefix IS NULL) AS generic, "
    "row_number() OVER (PARTITION BY f.observation_id "
    "ORDER BY r.signature IS NULL, r.signature_prefix IS NULL, r.rule_id) AS rk "
    "FROM f JOIN rules r ON r.observation_type = f.observation_type AND r.disposition = coalesce(f.severity, f.outcome) "
    "AND (r.signature IS NULL OR r.signature = f.name) "
    "AND (r.signature_prefix IS NULL OR starts_with(f.name, r.signature_prefix)) "
    "WHERE f.observation_id NOT IN (SELECT observation_id FROM absorbed)), "
    "seq0 AS (SELECT tenant_id, project_id, session_started_at, session_id, observation_id, parent_observation_id, name, "
    "outcome, "
    "row_number() OVER (PARTITION BY session_id, parent_observation_id, name "
    "ORDER BY event_timestamp, source_ordinal) AS pos, "
    "row_number() OVER (PARTITION BY session_id, parent_observation_id, name, outcome "
    "ORDER BY event_timestamp, source_ordinal) AS pos_o "
    "FROM o WHERE observation_type = 'tool_call' AND event_kind = 'close' AND name IS NOT NULL), "
    "seq AS (SELECT *, pos - pos_o AS run FROM seq0), "
    "friction AS (SELECT tenant_id, project_id, session_started_at, session_id, ref, observation_type, name, disposition, "
    "rule_id, label, class, generic FROM labelled WHERE rk = 1 UNION ALL "
    "SELECT any_value(tenant_id), any_value(project_id), any_value(session_started_at), session_id, "
    "min_by(observation_id, pos), 'tool_call', name, 'error', 'run', '{run_label}', '{run_class}', FALSE FROM seq "
    "WHERE outcome = 'error' GROUP BY session_id, parent_observation_id, name, run HAVING count(*) >= {repeat_threshold})"
)

# Facts only (ref, label, class): the test seam for the committed friction vectors, sharing FRICTION_CTES with the verb.
FRICTION_FACTS_SQL = sql.compose((sql.SESSIONS, sql.OBSERVATIONS), FRICTION_CTES + " SELECT ref, label, class FROM friction")

FRICTION_ROLLUP_SQL = sql.compose(
    (sql.SESSIONS, sql.OBSERVATIONS),
    FRICTION_CTES + ", ids AS (SELECT session_id FROM s UNION SELECT session_id FROM o), "
    "by_label AS (SELECT session_id, label, count(*) AS n FROM friction GROUP BY session_id, label), "
    "cnt AS (SELECT session_id, count(*) FILTER (WHERE class = 'rework') AS rework_total, "
    "count(*) FILTER (WHERE class = 'exception') AS exception_total, "
    "count(*) FILTER (WHERE observation_type = 'process_event' AND generic) AS unmapped_count FROM friction "
    "GROUP BY session_id), "
    "abs_cnt AS (SELECT session_id, count(*) AS absorbed_count FROM absorbed GROUP BY session_id), "
    "labels AS (SELECT session_id, list(struct_pack(label := label, n := n) ORDER BY label) AS label_counts "
    "FROM by_label GROUP BY session_id) "
    "SELECT ids.session_id AS session_id, coalesce(cnt.rework_total, 0) AS rework_total, "
    "coalesce(cnt.exception_total, 0) AS exception_total, coalesce(labels.label_counts, []) AS label_counts, "
    "coalesce(cnt.unmapped_count, 0) AS unmapped_count, coalesce(abs_cnt.absorbed_count, 0) AS absorbed_count, "
    "CAST({classifier_version} AS BIGINT) AS classifier_version, "
    + sql.STAMPS_SELECT
    + sql._ROLLUP_TAIL.replace(
        "LEFT JOIN gens",
        "LEFT JOIN labels ON labels.session_id = ids.session_id LEFT JOIN abs_cnt ON abs_cnt.session_id = ids.session_id "
        "LEFT JOIN gens",
        1,
    )
    + " ORDER BY ids.session_id",
)

# friction_signature_counts: one row per tree, session workflow and friction signature over the usage trees; the order is
# the oracle's (NULLS LAST on every key; project_id breaks a tie between rootless trees).
FRICTION_SIGNATURE_SQL = sql.compose(
    (sql.SESSIONS, sql.OBSERVATIONS),
    sql._USAGE_TREE + ", " + FRICTION_CTES + ", fs AS (SELECT fr.tenant_id AS tenant_id, fr.project_id AS project_id, "
    "fr.session_started_at AS session_started_at, ses.workflow AS workflow, fr.observation_type AS observation_type, "
    "fr.name AS signature, fr.disposition AS disposition, fr.rule_id AS rule_id, fr.label AS label, fr.class AS class, "
    "fr.generic AS generic, count(*) AS fact_count, count(DISTINCT fr.session_id) AS session_count FROM friction fr "
    "LEFT JOIN ses ON (ses.tenant_id, ses.project_id, ses.session_started_at, ses.session_id) = "
    "(fr.tenant_id, fr.project_id, fr.session_started_at, fr.session_id) GROUP BY ALL) "
    "SELECT tx.root_session_id, tx.harness_session_key, tx.branch, tx.session_started_at, tx.roots_in_tree, fs.workflow, "
    "fs.observation_type, fs.signature, fs.disposition, fs.rule_id, fs.label, fs.class, fs.generic, fs.fact_count, "
    "fs.session_count, CAST({classifier_version} AS BIGINT) AS classifier_version, "
    + sql._TREE_STAMPS
    + " FROM fs JOIN tx"
    + sql._TREE_USING
    + " ORDER BY tx.session_started_at, tx.root_session_id NULLS LAST, tx.project_id, fs.workflow NULLS LAST, "
    "fs.observation_type NULLS LAST, fs.signature NULLS LAST, fs.disposition NULLS LAST, fs.rule_id NULLS LAST",
)

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
            out[key] = (
                f"(VALUES {rows}) AS v(rule_id, observation_type, signature, signature_prefix, disposition, label, class)"
            )
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
            sql=FRICTION_ROLLUP_SQL,
            params=("session_id",),
            binding="session",
            data=FRICTION_RULES,
            description=_derived_rows_note(
                "One session's rework and exception totals, per-label counts, unmapped count and absorbed_count "
                "(error or blocked closes absorbed into their own error event; classifier_version stamped). "
                "unmapped_count counts the process-event facts a generic rule labelled."
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
            verb="session_tree_rollup",
            tables=(sql.SESSIONS, sql.OBSERVATIONS),
            sql=sql.TREE_ROLLUP_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            description=_derived_rows_note(
                "One row per tree with its compact_count and compacted_session_count, the distinct workflows of its sessions "
                "and its merge_links (merge_sha, merged_at, pr_url, rec_ids, ci_outcome); no token column" + _TREE_NOTE
            ),
        ),
        EventRead(
            verb="friction_signature_counts",
            tables=(sql.OBSERVATIONS, sql.SESSIONS),
            sql=FRICTION_SIGNATURE_SQL,
            params=("day_from", "day_to"),
            binding="window",
            max_window_days=MAX_WINDOW_DAYS,
            data=FRICTION_RULES,
            description=_derived_rows_note(
                "Friction facts, one row per tree, session workflow and signature (observation type, signature, disposition, "
                "the labelling rule, label, class and whether that rule is generic) with fact and session counts" + _TREE_NOTE
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
                "tree's last event, host_run_ref (the root's top-level run identity, NULL for an interactive root) and the "
                "Decision 207 stamps. " + _PRODUCTION
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
