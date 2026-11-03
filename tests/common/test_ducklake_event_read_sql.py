"""The event-read SQL templates render completely and every scoped verb binds its partition predicate (no duckdb needed)."""

from __future__ import annotations

import re

from src.common import ducklake_event_read_sql as sql
from src.common import ducklake_event_reads as ev
from src.common.ducklake_event_read_verbs import EVENT_READS


def test_every_template_renders_with_no_unbound_placeholder() -> None:
    for verb, entry in EVENT_READS.items():
        rendered = ev.render_sql(entry)
        assert re.findall(r"\{[a-z_]+\}", rendered) == [], verb
        if entry.binding != "none":
            assert "session_started_at >= $part_from AND session_started_at < $part_to" in rendered, verb
        assert (entry.binding == "session") == ("session_id = $session_id" in rendered), verb
        assert ";" not in rendered, f"{verb}: one SQL statement per call"
    assert re.findall(r"\{[a-z_]+\}", ev.render_sql(EVENT_READS["telemetry_health_count"])) == []


def test_dedupe_block_is_written_once_and_shared() -> None:
    state = sql.SESSION_STATE_SQL
    assert state.count("gen AS (") == 1
    assert sql.SESSIONS_WINDOW_SQL.count(sql.STATE_CTES) == sql.SESSIONS_WINDOW_ALL_SQL.count(sql.STATE_CTES) == 1
    assert sql.STATE_CTES in state and sql.STATE_CTES in sql.AGENT_RUN_STATE_SQL
    assert "row(" in sql.conflict_select(sql.SESSIONS) and "count(DISTINCT row(" in sql.conflict_select(sql.OBSERVATIONS)
