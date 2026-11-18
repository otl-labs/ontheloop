"""MIRROR for a per_write column at the writer (rec-provenance-column-add): src/common/ducklake_writes.py stores what
it is sent for provenance_json (the portal, not the writer, withholds the carry-forward), and an omitted value binds NULL
in both MERGEs, the current one replacing the prior value."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

import src.common.ducklake_runtime as rt
from tests.fixtures.ducklake_fakes import FakeCon, ops_rec_record

pytestmark = pytest.mark.unit

COLUMN = "provenance_json"
VALUE = json.dumps(
    {
        "schema_version": 1,
        "client": "portal",
        "client_commit": "0123abc",
        "client_dirty": False,
        "ruleset_version": "r1",
        "checks": {"gate_write": "pass"},
    }
)
STORED_AT = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _merges(con: FakeCon) -> list[tuple[str, list]]:
    return [(s, p) for s, p in con.executed if s.startswith("MERGE INTO")]


def _bound(sql: str, params: list) -> object:
    select_list = re.search(r"USING \(SELECT (.*?)\) AS s ON", sql).group(1)
    names = re.findall(r"\? AS (\w+)", select_list)
    return params[names.index(COLUMN)]


def test_write_binds_provenance_json_as_sent() -> None:
    con = FakeCon(created_lookup=None)
    rt.write_scd2(con, ops_rec_record(provenance_json=VALUE), table="ops_recommendations")
    history, current = _merges(con)
    assert "ops_recommendations_history" in history[0] and "ops_recommendations_current" in current[0]
    assert _bound(*history) == VALUE and _bound(*current) == VALUE


def test_update_omitting_provenance_json_binds_null() -> None:
    prior = {"created_timestamp": STORED_AT, "status": "open"}
    con = FakeCon(created_lookup=[prior])
    rt.write_scd2(
        con,
        ops_rec_record(status="closed"),
        table="ops_recommendations",
        require_exists=True,
        sleep=lambda s: None,
    )
    history, current = _merges(con)
    assert _bound(*history) is None and _bound(*current) is None


def test_current_merge_updates_provenance_json() -> None:
    con = FakeCon(created_lookup=None)
    rt.write_scd2(con, ops_rec_record(), table="ops_recommendations")
    _, current = _merges(con)
    update_set = current[0].split("WHEN MATCHED THEN UPDATE SET ", 1)[1].split(" WHEN NOT MATCHED", 1)[0]
    assert f"{COLUMN} = s.{COLUMN}" in update_set
