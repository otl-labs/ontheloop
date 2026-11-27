"""Named-read registry -- the pre-established read verbs the reader Lambda serves (extracted from ducklake_scd2_schema.py).

Moved by the telemetry table-registration plan (Decision 124 facade-plus-siblings, Decision 128
decompose-by-default); ducklake_scd2_schema re-exports every name here. Pure and I/O-free. This module has no
module-scope import of the facade: the one facade use (read_version's column projection for SELECT * verbs)
is a function-local deferred import, and read_version is computed at call time, never at import.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Named-read registry -- the pre-established read verbs the reader Lambda serves (Decision 84 I-3).
# Verb SQL is server-side trusted content: callers name a verb and bind params; no caller SQL
# crosses the boundary on this path. `{tbl}` = current projection, `{hist}` = history table.
# ---------------------------------------------------------------------------

# 6: the registry format spans two verb classes (these ops verbs and the reader-only EVENT_READS); no verb here changed.
NAMED_READS_VERSION = 6

# Sources whose rows preflight's bounded read model carries (single source for the derivations and the SQL).
PREFLIGHT_SIGNAL_SOURCES: tuple[str, ...] = (
    "ci_rca",
    "ci_rca_evidence_dispute",
    "ci_rca_probe_health",
    "tf_drift",
    "budget_bypass",
    "budget_breach",
)
_SIGNAL_SOURCES_SQL = ", ".join(f"'{src}'" for src in PREFLIGHT_SIGNAL_SOURCES)
_SIGNAL = f"source IN ({_SIGNAL_SOURCES_SQL})"
_OPEN_CRITICAL = "(status = 'open' AND lower(priority) = 'critical')"
_HEAVY_ROW = (
    f"({_OPEN_CRITICAL} OR ({_SIGNAL} AND (status IN ('open', 'in_progress') "
    "OR created_timestamp > (current_timestamp - INTERVAL 30 DAY))))"
)

# recs_related (Decision 215 cl.3): the five binds are unnested exactly once each, in params order (files, near, terms,
# exclude_ids, max_rows). `cand` is MATERIALIZED so the per-row regex runs only on rows that passed the contains()
# prefilter; the exclude guard is referenced in WHERE so DuckDB cannot optimise error() away.
_RECS_RELATED_SQL = (
    "WITH q AS (SELECT p AS path FROM (SELECT unnest(string_split(?, ',')) AS p) WHERE p <> ''), "
    "near AS (SELECT split_part(x, '|', 1) AS path, CAST(split_part(x, '|', 2) AS DOUBLE) AS w "
    "FROM (SELECT unnest(string_split(?, ',')) AS x) WHERE x <> ''), "
    "terms_in AS (SELECT split_part(x, '|', 1) AS term, CAST(split_part(x, '|', 2) AS DOUBLE) AS w "
    "FROM (SELECT unnest(string_split(?, ',')) AS x) WHERE x <> ''), "
    "ex AS (SELECT string_split(?, ',') AS ids), "
    "guard AS (SELECT CASE WHEN len(ids) > 200 "
    "THEN error('recs_related: exclude_ids over 200 ids -- narrow the query') END AS g FROM ex), "
    "openr AS (SELECT id, title, status, priority, effort, source, file, created_timestamp, context, "
    "lower(title || ' ' || coalesce(context, '')) AS doc FROM {tbl} WHERE status = 'open'), "
    "n AS (SELECT count(*) AS n_open FROM openr), "
    "cand AS MATERIALIZED (SELECT o.id, o.doc, t.term, t.w FROM openr o JOIN terms_in t ON contains(o.doc, t.term)), "
    "hits AS (SELECT id, term, w FROM cand "
    "WHERE regexp_matches(doc, '(^|[^a-z0-9_])' || regexp_escape(term) || '($|[^a-z0-9_])')), "
    "df AS (SELECT term, count(*) AS df FROM hits GROUP BY term), "
    "term_scores AS (SELECT h.id, sum(h.w * ln(1 + n.n_open / (1 + df.df))) AS term_w, "
    "count(*) FILTER (WHERE df.df <= 0.05 * n.n_open) AS admitting_hits, "
    "string_agg(h.term, ' ' ORDER BY h.term) AS matched_terms "
    "FROM hits h JOIN df USING (term) CROSS JOIN n GROUP BY h.id), "
    "nearw AS (SELECT path, max(w) AS w FROM near WHERE path <> '' GROUP BY path), "
    "scored AS (SELECT o.id, o.title, o.status, o.priority, o.effort, o.source, o.file, o.created_timestamp, "
    "coalesce(o.file IN (SELECT path FROM q), false) AS direct, "
    "coalesce(nw.w, 0) AS near_w, coalesce(ts.term_w, 0) AS term_w, coalesce(ts.admitting_hits, 0) AS admitting_hits, "
    "ts.matched_terms, left(o.context, 200) AS snippet "
    "FROM openr o CROSS JOIN guard CROSS JOIN ex "
    "LEFT JOIN nearw nw ON nw.path = o.file "
    "LEFT JOIN term_scores ts ON ts.id = o.id "
    "WHERE guard.g IS NULL AND NOT list_contains(ex.ids, o.id)) "
    "SELECT id, title, status, priority, effort, source, file, created_timestamp, direct, near_w + term_w AS score, "
    "matched_terms, snippet, count(*) OVER () AS total, count(*) FILTER (WHERE direct) OVER () AS direct_total "
    "FROM scored WHERE direct OR near_w > 0 OR admitting_hits > 0 "
    "ORDER BY direct DESC, CASE lower(priority) WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 3 ELSE 4 END, score DESC, created_timestamp DESC, id "
    "LIMIT least(CAST(? AS INTEGER), 100)"
)

# recs_by_tag (rec-5700): the seven binds are read once each, in params order, in the leading `args` CTE; every param is
# required and empty string is the default for the optional filters. `p` holds the parsed values and the guard (error()
# is referenced in WHERE so DuckDB cannot optimise it away); column names are explicit everywhere (no SELECT *).
_RECS_BY_TAG_ZONE = "'[T ][0-9]{2}:[0-9]{2}.*(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'"
_RECS_BY_TAG_STATUSES = "['open', 'in_progress', 'closed', 'failed', 'declined', 'superseded']"
_RECS_BY_TAG_STS = (
    f"CASE status_v WHEN '' THEN ['open'] WHEN 'all' THEN {_RECS_BY_TAG_STATUSES} ELSE string_split(status_v, ',') END"
)
_RECS_BY_TAG_SQL = (
    "WITH args AS (SELECT CAST(? AS VARCHAR) AS tag_v, CAST(? AS VARCHAR) AS status_v, CAST(? AS VARCHAR) AS since_v, "
    "CAST(? AS VARCHAR) AS priority_v, CAST(? AS VARCHAR) AS source_v, CAST(? AS VARCHAR) AS before_v, "
    "CAST(? AS VARCHAR) AS max_v), "
    "p AS (SELECT tag_v, priority_v, source_v, "
    f"{_RECS_BY_TAG_STS} AS sts, "
    f"CASE WHEN since_v = '' THEN NULL ELSE CAST(since_v AS TIMESTAMPTZ) END AS since_ts, "
    f"CASE WHEN before_v = '' THEN NULL ELSE CAST(split_part(before_v, '|', 1) AS TIMESTAMPTZ) END AS before_ts, "
    "split_part(before_v, '|', 2) AS before_id, "
    "CAST(max_v AS INTEGER) AS n, "
    "CASE WHEN tag_v = '' THEN error('recs_by_tag: tag must not be empty') "
    f"WHEN NOT list_has_all({_RECS_BY_TAG_STATUSES}, {_RECS_BY_TAG_STS}) "
    "THEN error('recs_by_tag: status must be open, in_progress, closed, failed, declined, superseded, a comma list "
    "of them, or all') "
    f"WHEN since_v <> '' AND NOT regexp_matches(since_v, {_RECS_BY_TAG_ZONE}) "
    "THEN error('recs_by_tag: since_ts needs an explicit Z or numeric offset') "
    "WHEN before_v <> '' AND len(string_split(before_v, '|')) <> 2 "
    "THEN error('recs_by_tag: before must be ts|id with exactly one |') "
    f"WHEN before_v <> '' AND NOT regexp_matches(split_part(before_v, '|', 1), {_RECS_BY_TAG_ZONE}) "
    "THEN error('recs_by_tag: before timestamp needs an explicit Z or numeric offset') "
    "WHEN NOT regexp_matches(max_v, '^[1-9][0-9]{0,8}$') "
    "THEN error('recs_by_tag: max_rows must be an integer of at least 1') END AS g FROM args) "
    "SELECT r.id, r.title, r.status, r.priority, r.effort, r.source, r.file, r.tags, r.created_timestamp, "
    "r.last_updated_timestamp, count(*) OVER () AS total "
    "FROM p CROSS JOIN {tbl} r "
    "WHERE p.g IS NULL AND list_contains(r.tags, p.tag_v) AND list_contains(p.sts, r.status) "
    "AND (p.since_ts IS NULL OR r.last_updated_timestamp >= p.since_ts) "
    "AND (p.priority_v = '' OR lower(r.priority) = lower(p.priority_v)) "
    "AND (p.source_v = '' OR r.source = p.source_v) "
    "AND (p.before_ts IS NULL OR r.last_updated_timestamp < p.before_ts "
    "OR (r.last_updated_timestamp = p.before_ts AND r.id < p.before_id)) "
    "ORDER BY r.last_updated_timestamp DESC, r.id DESC "
    "LIMIT (SELECT least(n, 100) FROM p)"
)


@dataclass(frozen=True)
class NamedRead:
    """One pre-established read verb: fixed SQL over a fixed table with named bind params."""

    verb: str
    table: str
    sql: str
    params: tuple[str, ...] = ()
    description: str = ""
    paginable: bool = False  # True => the caller-supplied `limit` (named_read) may bound this verb's rows
    agent_facing: bool = True  # False => not projected as an MCP agent tool; excluded from read_version


NAMED_READS: dict[str, NamedRead] = {
    nr.verb: nr
    for nr in (
        NamedRead(
            verb="open_recs",
            table="ops_recommendations",
            sql=("SELECT id, title, context, created_timestamp, automatable FROM {tbl} WHERE status = 'open' ORDER BY id"),
            description=(
                "deprecated: use open_recs_tally or preflight_signal_rows (removal rec-4240). "
                "Open recommendations with the fields the preflight tally consumes."
            ),
            paginable=True,
            agent_facing=False,
        ),
        NamedRead(
            verb="rec_by_id",
            table="ops_recommendations",
            sql="SELECT * FROM {tbl} WHERE id = ?",
            params=("id",),
            description="deprecated: use recs_by_ids (removal rec-4240). Single rec by id (portal fetch-before-update).",
        ),
        NamedRead(
            verb="recs_by_ids",
            table="ops_recommendations",
            sql="SELECT * FROM {tbl} WHERE list_contains(string_split(?, ','), id) ORDER BY id",
            params=("ids",),
            description="Recommendations by id: one id or a comma-joined list (batch lookup; replaces rec_by_id).",
        ),
        NamedRead(
            verb="recs_for_files",
            table="ops_recommendations",
            sql=(
                "SELECT id, title, status, priority, effort, source, file, tags, created_timestamp FROM {tbl} "
                "WHERE status = 'open' AND list_contains(string_split(?, ','), file) ORDER BY id"
            ),
            params=("files",),
            description=(
                "deprecated: use recs_related via scripts.recs_related (removal rec-4240). "
                "INTERIM direct-hit relevance read: open recs targeting any file in the comma-joined list."
            ),
        ),
        NamedRead(
            verb="recs_related",
            table="ops_recommendations",
            sql=_RECS_RELATED_SQL,
            params=("files", "near", "terms", "exclude_ids", "max_rows"),
            description=(
                "Ranked, bounded relevance lookup of open recs behind scripts.recs_related (direct anchor hits, weighted "
                "near paths, whole-token term hits); a relevance lookup, not a bulk path."
            ),
            agent_facing=False,
        ),
        NamedRead(
            verb="recs_by_tag",
            table="ops_recommendations",
            sql=_RECS_BY_TAG_SQL,
            params=("tag", "status", "since_ts", "priority", "source", "before", "max_rows"),
            description=(
                "Recommendations whose tags contain one tag, newest last_updated_timestamp first. Every param is required; "
                "empty string means the default. status: empty means open, one value, a comma list, or all (every status). "
                "since_ts: empty means no bound, else an ISO timestamp with explicit Z or offset, rows updated at or "
                "after it. "
                "priority (case-insensitive) and source: empty means any. before: empty means the first page, else the "
                "ts|id of the last row of the previous page (last_updated_timestamp|id), rows strictly older. max_rows has "
                "no default: an integer of at least 1, capped at 100. total counts every matching row past the cursor, "
                "before the cap, so a caller sees truncation."
            ),
        ),
        NamedRead(
            verb="open_recs_tally",
            table="ops_recommendations",
            sql=(
                "SELECT COUNT(*) AS open_count, "
                "COUNT(*) FILTER (WHERE created_timestamp < (current_timestamp - INTERVAL 31 DAY)) AS aging_count, "
                "COUNT(*) FILTER (WHERE lower(CAST(automatable AS VARCHAR)) = 'false') AS non_automatable_count "
                "FROM {tbl} WHERE status = 'open'"
            ),
            description="One row: open, aging (>30 days) and non-automatable counts over open recommendations.",
        ),
        NamedRead(
            verb="preflight_signal_rows",
            table="ops_recommendations",
            sql=(
                "SELECT id, title, status, source, priority, file, automatable, created_timestamp, "
                "last_updated_timestamp, "
                f"CASE WHEN {_SIGNAL} OR {_OPEN_CRITICAL} THEN context_v2_json END AS context_v2_json, "
                f"CASE WHEN {_HEAVY_ROW} THEN context END AS context, "
                f"CASE WHEN {_HEAVY_ROW} THEN acceptance END AS acceptance "
                f"FROM {{tbl}} WHERE {_SIGNAL} OR {_OPEN_CRITICAL} "
                "OR (status = 'open' AND list_contains(string_split(?, ','), id)) ORDER BY id"
            ),
            params=("followon_ids",),
            description="Bounded preflight read model: signal-source, open-critical and named follow-on rows.",
            agent_facing=False,
        ),
        NamedRead(
            verb="recs_by_title_prefix",
            table="ops_recommendations",
            sql="SELECT id, title, status, source FROM {tbl} WHERE title LIKE ? ORDER BY id",
            params=("title_prefix",),
            description="Recommendations whose title starts with the bound prefix (postmortem supersede sweep).",
            paginable=True,
        ),
        NamedRead(
            verb="ci_rca_open",
            table="ops_recommendations",
            sql=(
                "SELECT id, title, priority, created_timestamp, file FROM {tbl} "
                "WHERE source = 'ci_rca' AND status IN ('open', 'in_progress') "
                "ORDER BY created_timestamp DESC, id LIMIT 5"
            ),
            description="Most recent open/in-progress CI-RCA recommendations (preflight hard-block surface).",
        ),
        NamedRead(
            verb="ci_rca_since",
            table="ops_recommendations",
            sql=("SELECT id FROM {tbl} WHERE source = 'ci_rca' AND created_timestamp > CAST(? AS TIMESTAMPTZ)"),
            params=("since_ts",),
            description="CI-RCA recommendations created after the bound timestamp (liveness alert).",
        ),
        NamedRead(
            verb="forward_fix_recursion",
            table="ops_recommendations",
            sql=(
                "SELECT file, COUNT(*) AS cnt FROM {tbl} "
                "WHERE source = 'ci_rca' AND created_timestamp > CAST(? AS TIMESTAMPTZ) "
                "GROUP BY file HAVING COUNT(*) >= 3"
            ),
            params=("since_ts",),
            description="Files targeted by >=3 CI-RCA recommendations since the bound timestamp.",
        ),
        NamedRead(
            verb="budget_bypass_recent",
            table="ops_recommendations",
            sql=(
                "SELECT id, context, created_timestamp FROM {tbl} "
                "WHERE source = 'budget_bypass' "
                "AND created_timestamp > (current_timestamp - INTERVAL 7 DAY) "
                "ORDER BY created_timestamp DESC, id LIMIT 10"
            ),
            description="budget_bypass recommendations filed in the last 7 days (fast-tier drift alert).",
        ),
        NamedRead(
            verb="rec_history",
            table="ops_recommendations",
            sql="SELECT * FROM {hist} WHERE id = ? ORDER BY last_updated_timestamp DESC, ulid DESC",
            params=("id",),
            description="Prior SCD2 history versions of a rec, newest-first (agent-facing history read).",
        ),
        NamedRead(
            verb="count_by_status",
            table="ops_recommendations",
            sql="SELECT status, COUNT(*) AS n FROM {tbl} GROUP BY status ORDER BY status",
            description="Recommendation count per lifecycle status.",
        ),
        NamedRead(
            verb="decision_by_id",
            table="ops_decisions",
            sql="SELECT * FROM {tbl} WHERE id = ?",
            params=("id",),
            description="Single decision by dec-NNN id (portal fetch-before-update).",
        ),
        NamedRead(
            verb="decisions_max_updated",
            table="ops_decisions",
            sql="SELECT max(last_updated_timestamp) AS ts FROM {tbl}",
            description="Latest decision update timestamp (roadmap freshness input).",
        ),
        NamedRead(
            verb="priority_queue_current",
            table="ops_priority_queue",
            sql=(
                "SELECT * FROM {tbl} WHERE queue_run_id = ("
                "SELECT queue_run_id FROM {tbl} ORDER BY last_updated_timestamp DESC LIMIT 1) "
                "ORDER BY rank"
            ),
            description="All entries of the latest curator run (Decision 70 correlated-subquery pattern).",
        ),
    )
}


def _params_schema(params: tuple[str, ...]) -> dict[str, Any]:
    """A minimal JSON-schema-shaped object over *params* (all bind values are strings at this boundary)."""
    return {
        "type": "object",
        "properties": {p: {"type": "string"} for p in params},
        "required": list(params),
    }


_SELECT_STAR = re.compile(r"\bSELECT\s+\*", re.IGNORECASE)


def read_version(verb: str, semantics: dict[str, Any] | None = None) -> str:
    """Opaque per-verb compatibility stamp: first 16 hex of sha256 over the verb's canonical definition.

    Covers the verb name, bound table, params, paginable flag, whitespace-normalised SQL and, for SELECT *
    verbs, the bound table's resolved column projection (so a column change re-versions them although their
    SQL text is unchanged). Computed at call time, compared by equality only (no ordering is implied); one
    verb's change never re-versions another.
    """
    nr = NAMED_READS[verb]
    sql = " ".join(nr.sql.split())
    definition: dict[str, Any] = {
        "verb": nr.verb,
        "table": nr.table,
        "params": list(nr.params),
        "paginable": nr.paginable,
        "sql": sql,
    }
    if _SELECT_STAR.search(sql):
        from src.common.ducklake_scd2_schema import resolve_table_spec  # noqa: PLC0415 -- deferred; facade imports this module

        definition["columns"] = [list(c) for c in resolve_table_spec(nr.table, semantics).ordered_columns]
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def describe_named_reads(semantics: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Per-verb parameter schema for every NAMED_READS entry (agent-facing `describe`, CD.10 / CD.15)."""
    return {
        verb: {
            "table": nr.table,
            "description": nr.description,
            "params": list(nr.params),
            "paginable": nr.paginable,
            "agent_facing": nr.agent_facing,
            "params_schema": _params_schema(nr.params),
            "read_version": read_version(verb, semantics),
        }
        for verb, nr in NAMED_READS.items()
    }
