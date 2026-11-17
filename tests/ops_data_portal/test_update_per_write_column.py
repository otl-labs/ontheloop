"""Plan C1 (rec-provenance-shape-check): a per_write column is never carried forward from the prior row by the portal's two
update paths (update_rec, update_decision); an update that names it sends the named value; the gate and the closure
checks still see the whole prior row."""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import patch

from scripts.ops_portal import write_validators as wv
from scripts.ops_portal.write_validators import carry_forward
from src.common import ducklake_runtime as rt
from tests.fixtures.ops_rec_rows import reader_rec_row, writer_stand_in

_STORED = '{"schema_version": 1}'


def _per_write(table: str, column: str):
    real = rt.resolve_table_spec

    def fake(name: str, *args: Any, **kwargs: Any):
        spec = real(name, *args, **kwargs)
        if name != table:
            return spec
        return dataclasses.replace(spec, fields={**spec.fields, column: {**spec.fields[column], "per_write": True}})

    return patch.object(wv, "resolve_table_spec", side_effect=fake)


def _update_rec(updates: dict[str, Any], **patches: Any) -> list[dict[str, Any]]:
    from scripts.ops_data_portal import update_rec

    existing = reader_rec_row(context_v2_json=_STORED)
    write = writer_stand_in([existing])
    with (
        _per_write("ops_recommendations", "context_v2_json"),
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
        patch("scripts.ops_data_portal._ducklake_write", side_effect=write),
        patch("scripts.ops_data_portal._sync_table"),
    ):
        update_rec(existing["id"], updates)
    return [rec for _, _, rec in write.writes]


def test_update_drops_per_write_column_from_prior_row() -> None:
    (sent,) = _update_rec({"priority": "High"})
    assert "context_v2_json" not in sent and sent["priority"] == "High"


def test_update_naming_a_per_write_column_sends_it() -> None:
    (sent,) = _update_rec({"context_v2_json": '{"schema_version": 2}'})
    assert sent["context_v2_json"] == '{"schema_version": 2}'


def test_gate_and_closure_checks_still_see_the_whole_prior_row() -> None:
    from scripts import ops_data_portal as portal

    existing = reader_rec_row(context_v2_json=_STORED)
    with (
        _per_write("ops_recommendations", "context_v2_json"),
        patch.object(portal, "_fetch_rec_from_reader", return_value=dict(existing)),
        patch.object(portal, "_ducklake_write", side_effect=writer_stand_in([existing])),
        patch.object(portal, "_sync_table"),
        patch.object(portal, "gate_write", wraps=portal.gate_write) as gate,
    ):
        portal.update_rec(existing["id"], {"priority": "High"})
    assert gate.call_args.kwargs["existing"]["context_v2_json"] == _STORED


def test_update_decision_drops_per_write_column_from_prior_row(tmp_path) -> None:
    existing = {
        "id": "dec-042",
        "title": "D",
        "status": "open",
        "reversal_conditions": "stored",
        "created_timestamp": "2026-05-01T00:00:00+00:00",
        "last_updated_timestamp": "2026-05-01T00:00:00+00:00",
    }
    with (
        _per_write("ops_decisions", "reversal_conditions"),
        patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=dict(existing)),
        patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as write,
        patch("scripts.ops_portal.decisions.DECISIONS_JSONL", tmp_path / "d.jsonl"),
        patch("scripts.ops_portal.decisions._sync_table"),
    ):
        from scripts.ops_data_portal import update_decision

        update_decision("dec-042", {"status": "closed"})
    sent = write.call_args[0][1]
    assert "reversal_conditions" not in sent and sent["status"] == "closed"


def test_carry_forward_without_a_per_write_column_returns_the_row_unchanged() -> None:
    row = {"id": "rec-1", "title": "t"}
    assert carry_forward("ops_recommendations", row) == row
