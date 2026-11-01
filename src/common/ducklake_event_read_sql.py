"""SQL templates for the telemetry event-journal reads (Decision 199, 207).

Strings only: nothing here runs SQL, reads a file or can raise at import. Placeholders ({scope}, {sessions}, {cmp_sessions},
{idle_seconds}, {rules}, ...) are SQL text rendered server-side by src.common.ducklake_event_reads; every caller value reaches
the statement as a bound named parameter ($session_id, $as_of, $part_from, $part_to, $meta_schema), never as text.

One rule, one copy: the R4/R5 dedupe, the conflict report, the session/agent-run state rule and the root turn rule (SE-1..SE-3)
are each written once here and shared by every verb that needs them (a named verb never calls another verb).
"""

from __future__ import annotations

SESSIONS = "telemetry_sessions"
OBSERVATIONS = "telemetry_observations"
TRANSCRIPTS = "telemetry_transcripts"
AGENTS = "telemetry_agents"

_KEY = {SESSIONS: "s", OBSERVATIONS: "o", AGENTS: "a", TRANSCRIPTS: "t"}
_PLACEHOLDER = {SESSIONS: "sessions", OBSERVATIONS: "observations", AGENTS: "agents", TRANSCRIPTS: "transcripts"}

# R4: V*(producer, session) is the highest parser_version of that producer's open rows for the session, inside the scope.
GENERATIONS_CTE = (
    "gen AS (SELECT producer, session_id, max(parser_version) AS v_star FROM {sessions} "
    "WHERE {scope} AND event_kind = 'open' GROUP BY producer, session_id)"
)

# R5c: one model_call per observation_id -- producer precedence, then the latest event_timestamp (final usage wins), then a
# total order. Only model_call rows collapse; a tool_call open and close legitimately share an observation_id.
_MODEL_CALL_COLLAPSE = (
    "o AS (SELECT * FROM o_ev QUALIFY observation_type <> 'model_call' OR observation_id IS NULL OR row_number() OVER ("
    "PARTITION BY observation_id ORDER BY CASE producer WHEN 'claude_code' THEN 0 WHEN 'litellm' THEN 1 ELSE 2 END, "
    "event_timestamp DESC, created_timestamp, producer, event_id) = 1)"
)


def dedupe_ctes(table: str) -> str:
    """R5a (retire rows below or above V*) then R5b (event collapse per producer and event_id) for one telemetry table."""
    k = _KEY[table]
    live = (
        f"{k}_live AS (SELECT x.* FROM {{{_PLACEHOLDER[table]}}} x LEFT JOIN gen USING (producer, session_id) "
        "WHERE {scope} AND (gen.v_star IS NULL OR x.parser_version = gen.v_star))"
    )
    collapse = (
        f"SELECT * FROM {k}_live QUALIFY row_number() OVER (PARTITION BY producer, event_id "
        "ORDER BY parser_version DESC, created_timestamp) = 1"
    )
    if table == OBSERVATIONS:
        return f"{live}, o_ev AS ({collapse}), {_MODEL_CALL_COLLAPSE}"
    return f"{live}, {k} AS ({collapse})"


def conflict_select(table: str) -> str:
    """Grain keys holding more than one distinct row, over the scope's rows BEFORE retirement (Decision 207 R5b).

    The compare is one row value over every compared column, never a per-column DISTINCT (which would ignore NULLs and hide a
    NULL-versus-value conflict).
    """
    return (
        f"SELECT '{table}' AS table_name, producer, event_id, parser_version, min(session_id) AS session_id "
        f"FROM {{{_PLACEHOLDER[table]}}} WHERE {{scope}} GROUP BY producer, event_id, parser_version "
        f"HAVING count(DISTINCT row({{cmp_{_PLACEHOLDER[table]}}})) > 1"
    )


def compose(tables: tuple[str, ...], body: str) -> str:
    """WITH gen, each table's deduped CTEs, the conflict report and the per-session stamps, then *body*."""
    parts = [GENERATIONS_CTE, *(dedupe_ctes(t) for t in tables)]
    parts.append("conflicts AS (" + " UNION ALL ".join(conflict_select(t) for t in tables) + ")")
    parts.append(
        "gens AS (SELECT session_id, list(struct_pack(producer := producer, parser_version := v_star) ORDER BY producer) "
        "AS generations FROM gen GROUP BY session_id)"
    )
    parts.append(
        "conf AS (SELECT session_id, list(struct_pack(table_name := table_name, producer := producer, event_id := event_id, "
        "parser_version := parser_version) ORDER BY table_name, producer, event_id, parser_version) "
        "AS conflicted_grain_keys FROM conflicts GROUP BY session_id)"
    )
    return "WITH " + ", ".join(parts) + (" " if body.startswith("SELECT") else ", ") + body


STAMPS_SELECT = (
    "coalesce(gens.generations, []) AS generations, coalesce(conf.conflicted_grain_keys, []) AS conflicted_grain_keys, "
    "conf.session_id IS NOT NULL AS derivation_conflicted"
)
STAMPS_JOIN = "LEFT JOIN gens ON gens.session_id = {sid} LEFT JOIN conf ON conf.session_id = {sid}"

_IDLE = "epoch(CAST($as_of AS TIMESTAMPTZ)) - epoch(l.last_event_timestamp) > {idle_seconds}"

# Session and agent-run state, written once (telemetry_sessions.yaml state / duration_seconds; telemetry_agents.yaml duration).
# ROOT: the latest lifecycle row (annotate excluded) by event_timestamp, source_ordinal (NULL first), close last on a tie, then
# event_id; the root state, outcome and duration are read through ROOT_TURN_CTES (SE-1..SE-3, Decision 224): a producer close
# stands only while no turn opened after it, else the latest turn's close outcome, else running / abandoned by idleness.
# SUB-AGENT: its agent-run pair.
STATE_CTES = (
    "sess AS (SELECT session_id, max(parent_session_id) AS parent_session_id, max(project_id) AS project_id, "
    "min(session_started_at) AS session_started_at FROM s GROUP BY session_id), "
    "lc AS (SELECT session_id, event_kind, outcome FROM s WHERE event_kind <> 'annotate' "
    "QUALIFY row_number() OVER (PARTITION BY session_id ORDER BY event_timestamp DESC, coalesce(source_ordinal, -1) DESC, "
    "(event_kind = 'close') DESC, event_id DESC) = 1), "
    "cl AS (SELECT session_id, event_timestamp FROM s WHERE event_kind = 'close' "
    "QUALIFY row_number() OVER (PARTITION BY session_id ORDER BY event_timestamp DESC, coalesce(source_ordinal, -1) DESC, "
    "event_id DESC) = 1), "
    "last_ev AS (SELECT session_id, max(event_timestamp) AS last_event_timestamp FROM ("
    "SELECT session_id, event_timestamp FROM s UNION ALL SELECT session_id, event_timestamp FROM a "
    "UNION ALL SELECT session_id, event_timestamp FROM o UNION ALL SELECT session_id, event_timestamp FROM t) "
    "GROUP BY session_id), "
    "runs AS (SELECT session_id, agent_run_id, any_value(agent_type) AS agent_type, "
    "min(event_timestamp) FILTER (WHERE event_kind = 'open') AS opened_at, "
    "max(event_timestamp) FILTER (WHERE event_kind = 'close') AS closed_at, "
    "arg_max(outcome, event_timestamp) FILTER (WHERE event_kind = 'close') AS run_outcome FROM a "
    "GROUP BY session_id, agent_run_id), "
    "run_state AS (SELECT r.session_id, r.agent_run_id, r.agent_type, r.opened_at, r.closed_at, r.run_outcome, "
    "CASE WHEN r.closed_at IS NOT NULL THEN CASE r.run_outcome WHEN 'success' THEN 'success' WHEN 'failed' THEN 'failed' "
    "WHEN 'timeout' THEN 'failed' WHEN 'throttled' THEN 'failed' END "
    f"WHEN {_IDLE} THEN 'abandoned' ELSE 'running' END AS state, "
    "CASE WHEN r.closed_at IS NOT NULL AND r.opened_at IS NOT NULL "
    "THEN CAST(trunc(epoch(r.closed_at) - epoch(r.opened_at)) AS BIGINT) END AS duration_seconds, "
    "l.last_event_timestamp AS last_event_timestamp "
    "FROM runs r LEFT JOIN last_ev l ON l.session_id = r.session_id), "
    "child AS (SELECT * FROM run_state QUALIFY row_number() OVER (PARTITION BY session_id "
    "ORDER BY opened_at DESC NULLS LAST, agent_run_id DESC) = 1)"
)

# SE-1..SE-3 (telemetry_sessions.yaml state / duration_seconds): the root turn evidence, composed only into the three
# session-state verbs. Every ordering is event_timestamp, coalesce(source_ordinal, -1), event_id, latest last.
_TURN_LATEST = (
    "QUALIFY row_number() OVER (PARTITION BY {k}session_id ORDER BY {k}event_timestamp DESC, "
    "coalesce({k}source_ordinal, -1) DESC, {k}event_id DESC) = 1"
)
ROOT_TURN_CTES = (
    "t_open AS (SELECT session_id, observation_id, event_timestamp, coalesce(source_ordinal, -1) AS ord FROM o "
    "WHERE observation_type = 'turn' AND event_kind = 'open' " + _TURN_LATEST.format(k="") + "), "
    "t_close AS (SELECT c.session_id AS session_id, c.outcome AS outcome FROM o c JOIN t_open ON "
    "t_open.session_id = c.session_id AND t_open.observation_id = c.observation_id "
    "WHERE c.observation_type = 'turn' AND c.event_kind = 'close' " + _TURN_LATEST.format(k="c.") + "), "
    "t_any AS (SELECT session_id, event_timestamp FROM o WHERE observation_type = 'turn' AND event_kind = 'close' "
    + _TURN_LATEST.format(k="")
    + "), "
    "pc AS (SELECT session_id, event_timestamp, coalesce(source_ordinal, -1) AS ord FROM s WHERE event_kind = 'close' "
    + _TURN_LATEST.format(k="")
    + ")"
)

# The producer close stands only while no turn opened after it (not after: a tie keeps the close).
_PRODUCER_CLOSE_STANDS = (
    "(lc.event_kind = 'close' AND (t_open.session_id IS NULL OR t_open.event_timestamp < pc.event_timestamp "
    "OR (t_open.event_timestamp = pc.event_timestamp AND t_open.ord <= pc.ord)))"
)
_TURN_AT_REST = "(t_close.session_id IS NOT NULL AND t_close.outcome IS NOT NULL)"
_TURN_OUTCOME = (
    "CASE t_close.outcome WHEN 'success' THEN 'success' WHEN 'error' THEN 'failed' WHEN 'interrupted' THEN 'cancelled' "
    "WHEN 'blocked' THEN 'failed' END"
)

_STATE_SELECT = (
    "SELECT sess.session_id AS session_id, sess.parent_session_id AS parent_session_id, sess.project_id AS project_id, "
    "sess.session_started_at AS session_started_at, "
    f"CASE WHEN sess.parent_session_id IS NULL THEN CASE WHEN {_PRODUCER_CLOSE_STANDS} THEN lc.outcome "
    f"WHEN {_TURN_AT_REST} AND {_IDLE} THEN {_TURN_OUTCOME} WHEN {_TURN_AT_REST} THEN 'running' "
    f"WHEN {_IDLE} THEN 'abandoned' ELSE 'running' END "
    f"WHEN child.session_id IS NULL THEN CASE WHEN {_IDLE} THEN 'abandoned' ELSE 'running' END "
    "ELSE child.state END AS state, "
    f"CASE WHEN sess.parent_session_id IS NULL THEN CASE WHEN {_PRODUCER_CLOSE_STANDS} THEN lc.outcome "
    f"WHEN {_TURN_AT_REST} AND {_IDLE} THEN {_TURN_OUTCOME} END "
    "ELSE child.run_outcome END AS outcome, "
    "CASE WHEN sess.parent_session_id IS NULL THEN CASE WHEN greatest(pc.event_timestamp, t_any.event_timestamp) IS NOT NULL "
    "THEN CAST(trunc(epoch(greatest(pc.event_timestamp, t_any.event_timestamp)) - epoch(sess.session_started_at)) "
    "AS BIGINT) END "
    "ELSE child.duration_seconds END AS duration_seconds, "
    "l.last_event_timestamp AS last_event_timestamp, CAST($as_of AS TIMESTAMPTZ) AS as_of, "
    "CAST({idle_seconds} AS BIGINT) AS idle_threshold_seconds, "
    f"{STAMPS_SELECT} "
    "FROM sess LEFT JOIN lc ON lc.session_id = sess.session_id LEFT JOIN pc ON pc.session_id = sess.session_id "
    "LEFT JOIN t_open ON t_open.session_id = sess.session_id LEFT JOIN t_close ON t_close.session_id = sess.session_id "
    "LEFT JOIN t_any ON t_any.session_id = sess.session_id "
    "LEFT JOIN last_ev l ON l.session_id = sess.session_id LEFT JOIN child ON child.session_id = sess.session_id "
    + STAMPS_JOIN.format(sid="sess.session_id")
)

_ALL_TABLES = (SESSIONS, OBSERVATIONS, AGENTS, TRANSCRIPTS)
_ORDER_WINDOW = " ORDER BY sess.session_started_at, sess.session_id"

_ROOT_STATE_CTES = STATE_CTES + ", " + ROOT_TURN_CTES

SESSION_STATE_SQL = compose(_ALL_TABLES, _ROOT_STATE_CTES + " " + _STATE_SELECT + _ORDER_WINDOW)

# A window answers root sessions only; the real-purpose variant excludes drill and synthetic projects structurally (a join on
# the shared project purpose marker), not through an opt-in parameter.
SESSIONS_WINDOW_ALL_SQL = compose(
    _ALL_TABLES, _ROOT_STATE_CTES + " " + _STATE_SELECT + " WHERE sess.parent_session_id IS NULL" + _ORDER_WINDOW
)
SESSIONS_WINDOW_SQL = compose(
    _ALL_TABLES,
    _ROOT_STATE_CTES
    + " "
    + _STATE_SELECT
    + " WHERE sess.parent_session_id IS NULL AND sess.project_id IN (SELECT project_id FROM {projects} WHERE purpose = 'real')"
    + _ORDER_WINDOW,
)

AGENT_RUN_STATE_SQL = compose(
    _ALL_TABLES,
    STATE_CTES + " SELECT rs.agent_run_id AS agent_run_id, rs.session_id AS session_id, rs.agent_type AS agent_type, "
    "rs.state AS state, rs.run_outcome AS outcome, rs.duration_seconds AS duration_seconds, "
    "rs.last_event_timestamp AS last_event_timestamp, CAST($as_of AS TIMESTAMPTZ) AS as_of, "
    "CAST({idle_seconds} AS BIGINT) AS idle_threshold_seconds, "
    + STAMPS_SELECT
    + " FROM run_state rs "
    + STAMPS_JOIN.format(sid="rs.session_id")
    + " ORDER BY rs.opened_at, rs.agent_run_id",
)

_IDS = "ids AS (SELECT session_id FROM s UNION SELECT session_id FROM o)"
_ROLLUP_TAIL = " FROM ids LEFT JOIN cnt ON cnt.session_id = ids.session_id " + STAMPS_JOIN.format(sid="ids.session_id")

PROCESS_EVENT_ROLLUP_SQL = compose(
    (SESSIONS, OBSERVATIONS),
    _IDS + ", cnt AS (SELECT session_id, count(*) FILTER (WHERE observation_type = 'process_event') AS process_event_total, "
    "count(*) FILTER (WHERE observation_type = 'step' AND event_kind = 'close') AS steps_completed_total "
    "FROM o GROUP BY session_id) SELECT ids.session_id AS session_id, "
    "coalesce(cnt.process_event_total, 0) AS process_event_total, "
    "coalesce(cnt.steps_completed_total, 0) AS steps_completed_total, "
    + STAMPS_SELECT
    + _ROLLUP_TAIL
    + " ORDER BY ids.session_id",
)

# Token totals are NULL (not 0) when no model_call carries usage; usage_missing_count says how many calls had none.
AGENT_RUN_TOKEN_ROLLUP_SQL = compose(
    (SESSIONS, OBSERVATIONS),
    _IDS + ", cnt AS (SELECT session_id, sum(tokens_input) AS tokens_input_total, sum(tokens_output) AS tokens_output_total, "
    "sum(tokens_cache_read) AS tokens_cache_read_total, sum(tokens_cache_creation) AS tokens_cache_creation_total, "
    "count(*) AS model_call_count, "
    "count(*) FILTER (WHERE tokens_input IS NULL OR tokens_output IS NULL) AS usage_missing_count "
    "FROM o WHERE observation_type = 'model_call' GROUP BY session_id) SELECT ids.session_id AS session_id, "
    "cnt.tokens_input_total AS tokens_input_total, cnt.tokens_output_total AS tokens_output_total, "
    "cnt.tokens_cache_read_total AS tokens_cache_read_total, "
    "cnt.tokens_cache_creation_total AS tokens_cache_creation_total, "
    "coalesce(cnt.model_call_count, 0) AS model_call_count, coalesce(cnt.usage_missing_count, 0) AS usage_missing_count, "
    + STAMPS_SELECT
    + _ROLLUP_TAIL
    + " ORDER BY ids.session_id",
)

# Reported cost only (rec-4291 / rec-4031): an allowance-billed or local call (Decision 206, Decision 234 clause 4) is NULL
# by contract whatever cost it stores, a call with no reported cost is unpriced, never 0.
# The price table is rec-4031's and lands behind this same verb signature.
MODEL_CALL_COST_SQL = compose(
    (SESSIONS, OBSERVATIONS),
    "SELECT o.session_id AS session_id, o.observation_id AS observation_id, o.event_timestamp AS event_timestamp, "
    "o.model AS model, o.provider AS provider, o.billing_shape AS billing_shape, o.tokens_input AS tokens_input, "
    "o.tokens_output AS tokens_output, "
    "CASE WHEN o.billing_shape IN ('fixed_non_rollover_allowance', 'local') THEN NULL "
    "ELSE o.cost_usd_reported END AS cost_usd, "
    "CASE WHEN o.billing_shape = 'fixed_non_rollover_allowance' THEN 'allowance' "
    "WHEN o.billing_shape = 'local' THEN 'local' "
    "WHEN o.cost_usd_reported IS NOT NULL THEN 'reported' ELSE 'unpriced' END AS cost_source, "
    + STAMPS_SELECT
    + " FROM o "
    + STAMPS_JOIN.format(sid="o.session_id")
    + " WHERE o.observation_type = 'model_call' ORDER BY o.event_timestamp, o.observation_id",
)

# Friction (REPORT-w1-friction-classifier.md section 2): facts are process_events at severity error/warning/critical and
# tool_call closes with outcome error/blocked; one label per fact (an exact signature beats the generic rule); a blocked
# tool_call whose own hook block is already a fact is not counted twice; rework is the repeat-run rule.
FRICTION_CTES = (
    "f AS (SELECT * FROM o WHERE (observation_type = 'process_event' AND severity IN ('error', 'warning', 'critical')) "
    "OR (observation_type = 'tool_call' AND event_kind = 'close' AND outcome IN ('error', 'blocked'))), "
    "rules AS (SELECT * FROM {rules}), "
    "labelled AS (SELECT f.session_id AS session_id, f.observation_id AS ref, r.label AS label, r.class AS class, "
    "row_number() OVER (PARTITION BY f.observation_id ORDER BY r.signature IS NULL, r.rule_id) AS rk "
    "FROM f JOIN rules r ON r.observation_type = f.observation_type AND r.disposition = coalesce(f.severity, f.outcome) "
    "AND (r.signature IS NULL OR r.signature = f.name) "
    "WHERE NOT (f.observation_type = 'tool_call' AND f.outcome = 'blocked' AND EXISTS ("
    "SELECT 1 FROM f h WHERE h.observation_type = 'process_event' AND h.severity = 'error' "
    "AND h.parent_observation_id = f.observation_id))), "
    "seq0 AS (SELECT session_id, observation_id, parent_observation_id, name, outcome, "
    "row_number() OVER (PARTITION BY parent_observation_id, name ORDER BY event_timestamp, source_ordinal) AS pos, "
    "row_number() OVER (PARTITION BY parent_observation_id, name, outcome ORDER BY event_timestamp, source_ordinal) AS pos_o "
    "FROM o WHERE observation_type = 'tool_call' AND event_kind = 'close'), "
    "seq AS (SELECT *, pos - pos_o AS run FROM seq0), "
    "friction AS (SELECT session_id, ref, label, class FROM labelled WHERE rk = 1 UNION ALL "
    "SELECT min(session_id), min_by(observation_id, pos), '{run_label}', '{run_class}' FROM seq WHERE outcome = 'error' "
    "GROUP BY parent_observation_id, name, run HAVING count(*) >= {repeat_threshold})"
)

# Facts only (ref, label, class): the test seam for the 17 committed friction vectors, sharing FRICTION_CTES with the verb.
FRICTION_FACTS_SQL = compose((SESSIONS, OBSERVATIONS), FRICTION_CTES + " SELECT ref, label, class FROM friction")

FRICTION_ROLLUP_SQL = compose(
    (SESSIONS, OBSERVATIONS),
    FRICTION_CTES + ", ids AS (SELECT session_id FROM s UNION SELECT session_id FROM o), "
    "by_label AS (SELECT session_id, label, count(*) AS n FROM friction GROUP BY session_id, label), "
    "cnt AS (SELECT session_id, count(*) FILTER (WHERE class = 'rework') AS rework_total, "
    "count(*) FILTER (WHERE class = 'exception') AS exception_total, "
    "count(*) FILTER (WHERE label = 'unmapped_block') AS unmapped_count FROM friction GROUP BY session_id), "
    "labels AS (SELECT session_id, list(struct_pack(label := label, n := n) ORDER BY label) AS label_counts "
    "FROM by_label GROUP BY session_id) "
    "SELECT ids.session_id AS session_id, coalesce(cnt.rework_total, 0) AS rework_total, "
    "coalesce(cnt.exception_total, 0) AS exception_total, coalesce(labels.label_counts, []) AS label_counts, "
    "coalesce(cnt.unmapped_count, 0) AS unmapped_count, CAST({classifier_version} AS BIGINT) AS classifier_version, "
    + STAMPS_SELECT
    + _ROLLUP_TAIL.replace("LEFT JOIN gens", "LEFT JOIN labels ON labels.session_id = ids.session_id LEFT JOIN gens", 1)
    + " ORDER BY ids.session_id",
)

# Catalog metadata only (no data file): per-table committed record counts and the pinned meta schema as a bound value.
TELEMETRY_HEALTH_SQL = (
    "SELECT t.table_name AS table_name, coalesce(st.record_count, 0) AS record_count, "
    "CAST($meta_schema AS VARCHAR) AS meta_schema FROM {metadata}.ducklake_table t "
    "LEFT JOIN {metadata}.ducklake_table_stats st ON st.table_id = t.table_id "
    "WHERE t.end_snapshot IS NULL AND t.table_name IN ('telemetry_sessions', 'telemetry_observations', "
    "'telemetry_transcripts', 'telemetry_agents') ORDER BY t.table_name"
)

# transcript_content's resolve step: every stored row of the exact grain key (any parser_version, no generation retirement or
# dedupe), the payload columns the content module serves from, and compared_versions -- the count of distinct row values over
# the compared columns (one row value, so a NULL-versus-value difference counts, as in conflict_select).
TRANSCRIPT_CONTENT_SQL = (
    "WITH k AS (SELECT * FROM {transcripts} WHERE {scope} AND producer = $producer AND event_id = $event_id "
    "AND parser_version = $parser_version), "
    "c AS (SELECT count(DISTINCT row({cmp_transcripts})) AS compared_versions FROM k) "
    "SELECT k.content, k.content_uri, k.content_sha256, k.content_bytes, k.content_omitted_reason, c.compared_versions "
    "FROM k CROSS JOIN c ORDER BY k.created_timestamp, k.content_uri"
)


# Verification verbs (telemetry slice 4, rec-4025). A tree is the rows sharing one (tenant_id, project_id, session_started_at)
# key: every row of a tree carries the root's pin (Decision 199 cl.2), its tenant and its project.
_TREE_KEY = "tenant_id, project_id, session_started_at"

# session_tree_rows: the exact stored rows (raw: no retirement, no dedupe, every parser_version) of the tree holding
# $session_id, the compared columns plus created_timestamp, never content or content_uri (representation-only columns). The
# key resolves server-side inside the root's calendar-day partition; total_rows is the tree's row count on every page.
_TREE_BRANCH = (
    "SELECT '{table}' AS table_name, session_id, producer, event_id, parser_version, created_timestamp, "
    "to_json(r) AS row_json "
    "FROM (SELECT {{cmp_{name}}}, created_timestamp FROM {{{name}}} x JOIN tk USING (" + _TREE_KEY + ") WHERE {{scope}}) r"
)
TREE_ROWS_SQL = (
    "WITH tk AS (SELECT DISTINCT "
    + _TREE_KEY
    + " FROM ("
    + " UNION ALL ".join(
        f"SELECT {_TREE_KEY} FROM {{{name}}} WHERE {{scope}} AND $session_id = session_id" for name in _PLACEHOLDER.values()
    )
    + ")), all_rows AS ("
    + " UNION ALL ".join(_TREE_BRANCH.format(table=table, name=_PLACEHOLDER[table]) for table in _ALL_TABLES)
    + "), roots AS (SELECT count(DISTINCT s.session_id) AS roots_in_tree FROM {sessions} s JOIN tk USING ("
    + _TREE_KEY
    + ") "
    "WHERE {scope} AND s.parent_session_id IS NULL) "
    "SELECT table_name, CAST(row_json AS VARCHAR) AS row_json, count(*) OVER () AS total_rows, "
    "(SELECT roots_in_tree FROM roots) AS roots_in_tree FROM all_rows "
    "ORDER BY table_name, session_id, producer, event_id, parser_version, created_timestamp, CAST(row_json AS VARCHAR) "
    "LIMIT $max_rows OFFSET $offset"
)

# telemetry_orphan_counts: per (day, join) over R4/R5-deduped rows of real-purpose projects. {telemetry_joins} renders at
# render time (never import) into two CTEs: jn (the join ids) and edges (one row per non-null child FK value with whether a
# parent value exists among the live rows of the same tree key). A tree is quiescent when as_of exceeds its last event by more
# than the idle threshold; an orphan in a pending tree may still be explained by a later flush.
_LIVE = {SESSIONS: "s", OBSERVATIONS: "o", AGENTS: "a", TRANSCRIPTS: "t"}
_REAL = "project_id IN (SELECT project_id FROM {projects} WHERE purpose = 'real')"
_DAY = "strftime(timezone('UTC', tq.session_started_at), '%Y-%m-%d')"

ORPHAN_COUNTS_SQL = compose(
    _ALL_TABLES,
    "{telemetry_joins}, tl AS (SELECT "
    + _TREE_KEY
    + ", max(event_timestamp) AS last_event_timestamp FROM ("
    + " UNION ALL ".join(f"SELECT {_TREE_KEY}, event_timestamp FROM {k}" for k in _LIVE.values())
    + ") GROUP BY "
    + _TREE_KEY
    + "), tq AS (SELECT tl.*, epoch(CAST($as_of AS TIMESTAMPTZ)) - epoch(tl.last_event_timestamp) "
    "> {idle_seconds} AS quiescent FROM tl WHERE " + _REAL + "), "
    "td AS (SELECT " + _DAY + " AS day, count(*) FILTER (WHERE quiescent) AS trees_quiescent, "
    "count(*) FILTER (WHERE NOT quiescent) AS trees_pending FROM tq GROUP BY 1), "
    "ca AS (SELECT " + _DAY + " AS day, e.join_id AS join_id, count(*) AS child_rows, "
    "count(*) FILTER (WHERE NOT e.has_parent AND tq.quiescent) AS orphans_quiescent, "
    "count(*) FILTER (WHERE NOT e.has_parent AND NOT tq.quiescent) AS orphans_pending "
    "FROM edges e JOIN tq USING (" + _TREE_KEY + ") GROUP BY 1, 2), "
    "sd AS (SELECT DISTINCT session_id, project_id, session_started_at FROM ("
    + " UNION ALL ".join(
        f"SELECT session_id, project_id, session_started_at FROM {{{_PLACEHOLDER[t]}}} WHERE {{scope}}" for t in _ALL_TABLES
    )
    + ")), cf AS (SELECT strftime(timezone('UTC', sd.session_started_at), '%Y-%m-%d') AS day, count(*) AS conflicted_keys "
    "FROM conflicts JOIN sd USING (session_id) WHERE sd." + _REAL + " GROUP BY 1) "
    "SELECT td.day AS day, jn.join_id AS join_id, coalesce(ca.child_rows, 0) AS child_rows, "
    "coalesce(ca.orphans_quiescent, 0) AS orphans_quiescent, coalesce(ca.orphans_pending, 0) AS orphans_pending, "
    "td.trees_quiescent AS trees_quiescent, td.trees_pending AS trees_pending, "
    "coalesce(cf.conflicted_keys, 0) AS conflicted_keys FROM td CROSS JOIN jn "
    "LEFT JOIN ca ON ca.day = td.day AND ca.join_id = jn.join_id LEFT JOIN cf ON cf.day = td.day ORDER BY td.day, jn.join_id",
)

# telemetry_root_sessions: one row per root session of a real-purpose project in the window, with the harness's own session key
# and branch (each the first non-null value by event_timestamp, source_ordinal, event_id), the tree-wide last event and the
# Decision 207 stamps. The verification monitor selects trees by last_event_timestamp, so it is the TREE's, not the root's.
_FIRST = (
    "min_by({c}, struct_pack(t := event_timestamp, o := coalesce(source_ordinal, -1), e := event_id)) "
    "FILTER (WHERE {c} IS NOT NULL)"
)
ROOT_SESSIONS_SQL = compose(
    _ALL_TABLES,
    "rt AS (SELECT session_id, max(tenant_id) AS tenant_id, max(project_id) AS project_id, "
    "min(session_started_at) AS session_started_at, max(parent_session_id) AS parent_session_id, "
    + ", ".join(f"{_FIRST.format(c=c)} AS {c}" for c in ("entity_ref", "harness_session_key", "branch"))
    + " FROM s GROUP BY session_id), "
    "tl AS (SELECT "
    + _TREE_KEY
    + ", max(event_timestamp) AS last_event_timestamp FROM ("
    + " UNION ALL ".join(f"SELECT {_TREE_KEY}, event_timestamp FROM {k}" for k in _LIVE.values())
    + ") GROUP BY "
    + _TREE_KEY
    + ") "
    "SELECT rt.session_id AS session_id, rt.entity_ref AS entity_ref, rt.harness_session_key AS harness_session_key, "
    "rt.branch AS branch, rt.session_started_at AS session_started_at, tl.last_event_timestamp AS last_event_timestamp, "
    + STAMPS_SELECT
    + " FROM rt LEFT JOIN tl USING ("
    + _TREE_KEY
    + ") "
    + STAMPS_JOIN.format(sid="rt.session_id")
    + " WHERE rt.parent_session_id IS NULL AND rt."
    + _REAL
    + " ORDER BY rt.session_started_at, rt.session_id",
)


# Usage verbs (rec-4719, rec-4702, rec-4703): per-tree and per-session derived rows over the deduped scope of one real-purpose
# window, never a cross-tree sum (Decision 199 cl.1). A tree is the rows sharing (tenant_id, project_id, session_started_at).
_REAL_PROJECT = "project_id IN (SELECT project_id FROM {projects} WHERE purpose = 'real')"
_FIRST_OF = ", ".join(f"{_FIRST.format(c=c)} AS {c}" for c in ("workflow", "harness_session_key", "branch"))
_TREE_USING = " USING (" + _TREE_KEY + ")"
_USAGE_TREE = (
    "st AS (SELECT " + _TREE_KEY + ", session_id FROM s UNION SELECT " + _TREE_KEY + ", session_id FROM o), "
    "ses AS (SELECT " + _TREE_KEY + ", session_id, max(parent_session_id) AS parent_session_id, "
    "max(parent_session_id) IS NULL AS is_root, " + _FIRST_OF + " FROM s "
    "WHERE " + _REAL_PROJECT + " GROUP BY " + _TREE_KEY + ", session_id), "
    "tr AS (SELECT " + _TREE_KEY + ", min(session_id) FILTER (WHERE is_root) AS root_session_id, "
    "count(*) FILTER (WHERE is_root) AS roots_in_tree FROM st LEFT JOIN ses USING (" + _TREE_KEY + ", session_id) "
    "WHERE " + _REAL_PROJECT + " GROUP BY " + _TREE_KEY + "), "
    "tl AS (SELECT " + _TREE_KEY + ", max(event_timestamp) AS last_event_timestamp FROM ("
    "SELECT " + _TREE_KEY + ", event_timestamp FROM s UNION ALL SELECT " + _TREE_KEY + ", event_timestamp FROM o) "
    "GROUP BY " + _TREE_KEY + "), "
    "tg AS (SELECT " + _TREE_KEY + ", list(struct_pack(producer := producer, parser_version := v_star) "
    "ORDER BY producer, v_star) AS generations FROM (SELECT DISTINCT st.tenant_id, st.project_id, st.session_started_at, "
    "gen.producer, gen.v_star FROM st JOIN gen USING (session_id)) GROUP BY " + _TREE_KEY + "), "
    "tc AS (SELECT " + _TREE_KEY + ", list(struct_pack(table_name := table_name, producer := producer, event_id := event_id, "
    "parser_version := parser_version) ORDER BY table_name, producer, event_id, parser_version) AS conflicted_grain_keys "
    "FROM conflicts JOIN (SELECT DISTINCT "
    + _TREE_KEY
    + ", session_id FROM st) USING (session_id) GROUP BY "
    + _TREE_KEY
    + "), "
    "tx AS (SELECT tr.*, r.harness_session_key AS harness_session_key, r.branch AS branch, r.workflow AS workflow, "
    "tl.last_event_timestamp AS last_event_timestamp, coalesce(tg.generations, []) AS generations, "
    "coalesce(tc.conflicted_grain_keys, []) AS conflicted_grain_keys, len(coalesce(tc.conflicted_grain_keys, [])) > 0 "
    "AS derivation_conflicted FROM tr LEFT JOIN tl"
    + _TREE_USING
    + " LEFT JOIN tg"
    + _TREE_USING
    + " LEFT JOIN tc"
    + _TREE_USING
    + " LEFT JOIN ses r ON (tr.tenant_id, tr.project_id, tr.session_started_at, tr.root_session_id) = "
    "(r.tenant_id, r.project_id, r.session_started_at, r.session_id))"
)
_TREE_COLS = "tx.root_session_id, tx.harness_session_key, tx.branch, tx.session_started_at, tx.roots_in_tree"
_TREE_STAMPS = "tx.generations, tx.conflicted_grain_keys, tx.derivation_conflicted"
_AGENT_NAME = (
    "ag AS (SELECT session_id, arg_max(agent_name, struct_pack(t := event_timestamp, o := coalesce(source_ordinal, -1), "
    "e := event_id)) FILTER (WHERE agent_name IS NOT NULL) AS agent_name FROM a GROUP BY session_id)"
)
_LATEST = "ORDER BY event_timestamp DESC, coalesce(source_ordinal, -1) DESC, event_id DESC"
_TOKENS = ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")

MODEL_USAGE_ROLLUP_SQL = compose(
    (SESSIONS, OBSERVATIONS),
    _USAGE_TREE + ", mu AS (SELECT " + _TREE_KEY + ", model, count(DISTINCT session_id) AS session_count, "
    "count(*) AS model_call_count, "
    + ", ".join(f"sum({c}) AS {c}_total" for c in _TOKENS)
    + ", sum(cache_creation_1h_tokens) AS cache_creation_1h_total, sum(cache_creation_5m_tokens) AS cache_creation_5m_total, "
    "count(*) FILTER (WHERE tokens_input IS NULL OR tokens_output IS NULL) AS usage_missing_count FROM o "
    "WHERE observation_type = 'model_call' GROUP BY " + _TREE_KEY + ", model) "
    "SELECT tx.root_session_id, tx.harness_session_key, tx.branch, tx.workflow, tx.session_started_at, "
    "tx.last_event_timestamp, tx.roots_in_tree, mu.model, mu.session_count, mu.model_call_count, mu.tokens_input_total, "
    "mu.tokens_output_total, mu.tokens_cache_read_total, mu.tokens_cache_creation_total, mu.cache_creation_1h_total, "
    "mu.cache_creation_5m_total, mu.usage_missing_count, "
    + _TREE_STAMPS
    + " FROM mu JOIN tx"
    + _TREE_USING
    + " ORDER BY tx.session_started_at, tx.root_session_id, tx.project_id, mu.model NULLS LAST",
)

TOOL_USE_ROLLUP_SQL = compose(
    (SESSIONS, OBSERVATIONS, AGENTS),
    _USAGE_TREE + ", " + _AGENT_NAME + ", "
    "op AS (SELECT " + _TREE_KEY + ", session_id, observation_id, name, "
    "json_extract_string(metadata, '$.attribution_mcp_server') AS attribution_mcp_server FROM o "
    "WHERE observation_type = 'tool_call' AND event_kind = 'open' QUALIFY row_number() OVER (PARTITION BY session_id, "
    "observation_id ORDER BY event_timestamp, coalesce(source_ordinal, -1), event_id) = 1), "
    "oc AS (SELECT session_id, observation_id, outcome FROM o WHERE observation_type = 'tool_call' "
    "AND event_kind = 'close' QUALIFY row_number() OVER (PARTITION BY session_id, observation_id " + _LATEST + ") = 1) "
    "SELECT " + _TREE_COLS + ", coalesce(ses.is_root, FALSE) AS is_root, "
    "CASE WHEN coalesce(ses.is_root, FALSE) THEN NULL ELSE ag.agent_name END AS agent_name, ses.workflow AS workflow, "
    "op.name AS tool_name, nullif(regexp_extract(op.name, '^mcp__(.+?)__', 1), '') AS mcp_server, "
    "op.attribution_mcp_server AS attribution_mcp_server, count(*) AS call_count, "
    "count(*) FILTER (WHERE oc.outcome = 'error') AS error_count, count(*) FILTER (WHERE oc.outcome = 'blocked') "
    "AS blocked_count, count(*) FILTER (WHERE oc.outcome = 'interrupted') AS interrupted_count, "
    "count(DISTINCT op.session_id) AS session_count, "
    + _TREE_STAMPS
    + " FROM op JOIN tx"
    + _TREE_USING
    + " LEFT JOIN ses ON (ses.tenant_id, ses.project_id, ses.session_started_at, "
    "ses.session_id) = (op.tenant_id, op.project_id, op.session_started_at, op.session_id) "
    "LEFT JOIN ag ON ag.session_id = op.session_id LEFT JOIN oc ON (oc.session_id, oc.observation_id) = "
    "(op.session_id, op.observation_id) GROUP BY ALL ORDER BY tx.session_started_at, tx.root_session_id, "
    "is_root DESC, agent_name NULLS FIRST, workflow, tool_name, mcp_server, attribution_mcp_server",
)

STARTUP_BASELINE_SQL = compose(
    (SESSIONS, OBSERVATIONS, AGENTS),
    _USAGE_TREE + ", " + _AGENT_NAME + ", "
    "fc AS (SELECT session_id, count(*) OVER (PARTITION BY session_id) AS n, event_timestamp, model, "
    + ", ".join(_TOKENS[:1] + _TOKENS[2:])
    + ", cache_creation_1h_tokens, cache_creation_5m_tokens, tokens_output, tokens_input IS NULL OR tokens_output IS NULL "
    "AS usage_missing FROM o WHERE observation_type = 'model_call' QUALIFY row_number() OVER (PARTITION BY session_id "
    "ORDER BY event_timestamp, coalesce(source_ordinal, -1), event_id) = 1) "
    "SELECT ses.session_id, tx.root_session_id, ses.parent_session_id, ses.is_root, tx.harness_session_key, tx.branch, "
    "ses.workflow, CASE WHEN ses.is_root THEN NULL ELSE ag.agent_name END AS agent_name, tx.session_started_at, "
    "tx.roots_in_tree, "
    "coalesce(fc.n, 0) AS model_call_count, fc.event_timestamp AS first_call_at, fc.model, fc.tokens_input, "
    "fc.tokens_cache_read, fc.tokens_cache_creation, fc.cache_creation_1h_tokens, fc.cache_creation_5m_tokens, "
    "fc.tokens_output, fc.usage_missing, "
    + STAMPS_SELECT
    + " FROM ses JOIN tx"
    + _TREE_USING
    + " LEFT JOIN ag ON ag.session_id = ses.session_id LEFT JOIN fc ON fc.session_id = ses.session_id "
    + STAMPS_JOIN.format(sid="ses.session_id")
    + " ORDER BY tx.session_started_at, tx.root_session_id, tx.project_id, ses.is_root DESC, ses.session_id",
)
