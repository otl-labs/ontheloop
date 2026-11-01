"""Mirror tests for src/common/ducklake_content_read_verbs.py: entries, binds, window, version."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.common import ducklake_content_read_verbs as cv
from src.common import ducklake_event_reads as ev
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.telemetry.identity import _assemble_ulid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
_SID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_EVENT = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def _ulid_at(when: datetime) -> str:
    return _assemble_ulid(int(when.timestamp() * 1000), bytes(10))


def test_registries_are_disjoint_and_hold_exactly_the_two_verbs() -> None:
    assert set(cv.CONTENT_READS) == {"transcript_row_masked", "session_transcript_masked"}
    assert set(cv.CONTENT_READS).isdisjoint(EVENT_READS)
    assert all("response" not in e.data for e in cv.CONTENT_READS.values())


def test_entry_params_bindings_and_ints() -> None:
    row, session = cv.CONTENT_READS["transcript_row_masked"], cv.CONTENT_READS["session_transcript_masked"]
    assert (row.binding, row.int_params) == ("session", ("parser_version", "offset", "max_bytes"))
    assert row.params == ("session_id", "producer", "event_id", "parser_version", "offset", "max_bytes")
    assert (session.binding, session.params, session.int_params) == (
        "tree",
        ("session_id", "offset", "max_rows"),
        ("offset", "max_rows"),
    )


def test_bind_values_accepts_exactly_the_params_and_binds_no_unknown_name() -> None:
    row, session = cv.CONTENT_READS["transcript_row_masked"], cv.CONTENT_READS["session_transcript_masked"]
    values = ev.bind_values(
        row, {"session_id": _SID, "producer": "p", "event_id": _EVENT, "parser_version": 3, "offset": 0, "max_bytes": 5}
    )
    assert set(values) == {
        "session_id",
        "part_from",
        "part_to",
        "producer",
        "event_id",
        "parser_version",
        "offset",
        "max_bytes",
    }
    assert ev.bind_values(session, {"session_id": _SID, "offset": 0, "max_rows": 10_000})["max_rows"] == 2000
    with pytest.raises(ev.EventReadInputError):
        ev.bind_values(session, {"session_id": _SID, "offset": 0, "max_rows": 1, "include_tool_output": True})


def test_rendered_sql_has_no_placeholder_and_binds_only_known_names() -> None:
    import re

    for entry in cv.CONTENT_READS.values():
        sql = ev.render_sql(entry)
        assert not re.findall(r"\{[a-z_]+\}", sql)
        assert set(re.findall(r"\$([a-z_]+)", sql)) <= {
            "session_id",
            "part_from",
            "part_to",
            "producer",
            "event_id",
            "parser_version",
            "offset",
            "max_bytes",
            "max_rows",
        }
    assert "external_ref" in ev.render_sql(cv.CONTENT_READS["session_transcript_masked"])


@pytest.mark.parametrize(("days", "inside"), [(89, True), (90, True), (91, False)])
def test_in_read_window_at_89_90_and_91_days(days: int, inside: bool) -> None:
    assert cv.in_read_window(_ulid_at(_NOW - timedelta(days=days)), _NOW) is inside


def test_content_read_version_is_stable_and_moves_with_the_sql() -> None:
    entry = cv.CONTENT_READS["transcript_row_masked"]
    version = cv.content_read_version(entry)
    assert version == cv.content_read_version(entry) and len(version) == 16
    assert cv.content_read_version(replace(entry, sql=entry.sql + " ")) == version
    assert cv.content_read_version(replace(entry, sql=entry.sql + " -- x")) != version
    assert cv.content_read_version(cv.CONTENT_READS["session_transcript_masked"]) != version


def test_tree_window_treats_naive_as_utc_and_utc_now_is_aware() -> None:
    assert cv.tree_in_read_window(datetime(2026, 10, 1), _NOW) is True
    assert cv.tree_in_read_window(_NOW - timedelta(days=cv.READ_WINDOW_DAYS + 1), _NOW) is False
    assert cv.tree_in_read_window(_NOW - timedelta(days=cv.READ_WINDOW_DAYS), _NOW) is True
    assert cv.utc_now().tzinfo is not None


def test_session_transcript_orders_by_the_declared_keys_in_order() -> None:
    sql = " ".join(cv.SESSION_TRANSCRIPT_SQL.split())
    order_by = sql.rsplit("ORDER BY ", 1)[1]
    keys = [
        "(tr.session_id = $session_id) DESC",
        "fe.first_ts",
        ", tr.session_id,",
        "tr.event_timestamp",
        "coalesce(tr.source_ordinal, -1)",
        "tr.external_ref LIKE '%/full'",
        "CASE tr.purpose WHEN 'prompt' THEN 0",
        "tr.producer",
        "tr.event_id",
        "tr.parser_version LIMIT $max_rows OFFSET $offset",
    ]
    cursor = 0
    for key in keys:
        found = order_by.find(key, cursor)
        assert found >= 0, key
        cursor = found + len(key)
