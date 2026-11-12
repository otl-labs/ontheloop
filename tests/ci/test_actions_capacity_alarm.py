"""The Actions capacity alarm episodes (rec-5022): injected portal caller and rows, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import actions_capacity_alarm as alarm
from scripts.ci import actions_capacity_gauge as g
from scripts.ci import actions_capacity_github as gh
from scripts.ci.actions_capacity_contract import load_contract
from scripts.executor.acceptance_lint import lint_acceptance_command

CONTRACT = load_contract()
NOW = datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc)
URL = "https://github.com/o/r/actions/runs/9"


def full_rows(count: int, slot: float = 1.0, merges: int = 0, waits: list[int] | None = None) -> list[g.HourRow]:
    last = g._floor(NOW)
    rows = [
        g.HourRow(
            gh.iso(last - timedelta(hours=i)),
            True,
            slot_demand=slot,
            merges=merges,
            queue_waits_s=list(waits or []),
            queue_samples=len(waits or []),
            job_minutes={".github/workflows/x.yml|work|push": slot * 60},
        )
        for i in range(count, 0, -1)
    ]
    return g.pool_table(rows, CONTRACT)


def read(table: list[g.HourRow]) -> g.Reading:
    return g.verdict(table, g.projection(table, CONTRACT), CONTRACT, NOW, no_open_rec=True)


class Portal:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __call__(self, verb: str, payload: dict[str, Any]) -> Any:
        if self.fail:
            raise RuntimeError("portal down")
        self.calls.append((verb, payload))
        return "rec-9001"


def breach_table(slot: float = 17.0, count: int = 30) -> list[g.HourRow]:
    table = full_rows(count, waits=[9])
    table[-1].slot_demand = slot
    return table


def run_act(table: list[g.HourRow], portal: Portal, rows: list[dict[str, Any]], diagnose: bool = False) -> str:
    return alarm.act(read(table), table, CONTRACT, NOW, URL, diagnose=diagnose, portal=portal, rows=rows)


def open_rec(cls: str, context: str) -> dict[str, Any]:
    return {"id": "rec-9001", "source": "actions_capacity", "status": "open", "context": context}


def test_breach_files_one_rec_then_updates_only_on_change() -> None:
    portal = Portal()
    table = breach_table()
    run_act(table, portal, [])
    assert [v for v, _ in portal.calls] == ["file"] and portal.calls[0][1]["source"] == "actions_capacity"
    ctx = portal.calls[0][1]["context"]
    again = Portal()
    run_act(table, again, [open_rec("capacity_breach", ctx)])
    assert again.calls == []
    table2 = breach_table()
    table2[-2].slot_demand = 18.0
    changed = Portal()
    run_act(table2, changed, [open_rec("capacity_breach", ctx)])
    assert [v for v, _ in changed.calls] == ["update"]


def test_breach_rec_closes_when_enough_hours_and_no_breach() -> None:
    portal = Portal()
    ctx = "class=capacity_breach breaching_hours=2026-11-11T11:00:00Z values=[] run=u"
    run_act(full_rows(30, waits=[9]), portal, [open_rec("capacity_breach", ctx)])
    assert (
        [v for v, _ in portal.calls] == ["close"]
        and portal.calls[0][1]["status"] == "closed"
        and URL in portal.calls[0][1]["resolution"]
    )
    projected = full_rows(30, slot=12.0, merges=10, waits=[9])
    assert read(projected).verdict == "red_projected"
    portal2 = Portal()
    run_act(projected, portal2, [open_rec("capacity_breach", ctx)])
    assert [v for v, _ in portal2.calls] == ["close"]


def test_insufficient_data_keeps_open_breach_rec() -> None:
    portal = Portal()
    ctx = "class=capacity_breach breaching_hours=x values=[] run=u"
    table = full_rows(3, waits=[9])
    assert read(table).verdict == "insufficient_data"
    out = run_act(table, portal, [open_rec("capacity_breach", ctx)])
    assert out
    assert not any(v == "close" for v, _ in portal.calls)


def test_red_projected_files_nothing() -> None:
    portal = Portal()
    table = full_rows(30, slot=12.0, merges=10, waits=[9])
    run_act(table, portal, [])
    assert portal.calls == []


def test_blind_class_files_when_evaluated_hours_low() -> None:
    portal = Portal()
    run_act(full_rows(4, waits=[9]), portal, [])
    assert len(portal.calls) == 1 and portal.calls[0][0] == "file" and "class=gauge_blind" in portal.calls[0][1]["context"]
    ctx = portal.calls[0][1]["context"]
    healthy = Portal()
    run_act(full_rows(30, waits=[9]), healthy, [open_rec("gauge_blind", ctx)])
    assert [v for v, _ in healthy.calls] == ["close"]


def test_two_classes_are_separate_episodes() -> None:
    portal = Portal()
    table = full_rows(4, waits=[9])
    table[-1].slot_demand = 17.0
    run_act(table, portal, [])
    classes = sorted(p["context"].split()[0] for _, p in portal.calls)
    assert classes == ["class=capacity_breach", "class=gauge_blind"]
    only_blind = Portal()
    blind_ctx = next(p["context"] for _, p in portal.calls if "gauge_blind" in p["context"])
    run_act(table, only_blind, [open_rec("gauge_blind", blind_ctx)])
    assert [p["context"].split()[0] for _, p in only_blind.calls] == ["class=capacity_breach"]


def test_diagnose_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    portal = Portal()
    assert run_act(breach_table(), portal, [], diagnose=True) == "diagnose"
    assert portal.calls == [] and "diagnose" in capsys.readouterr().out


def test_rec_context_carries_trigger_response_verbatim() -> None:
    portal = Portal()
    run_act(breach_table(), portal, [])
    ctx = portal.calls[0][1]["context"]
    assert " ".join(CONTRACT.trigger_response.split()) in ctx and URL in ctx and len(ctx) >= 80 and "slot_demand=17.0" in ctx


def test_alarm_source_literal_matches_contract() -> None:
    text = Path(alarm.__file__).read_text(encoding="utf-8")
    assert '"source": "actions_capacity"' in text and CONTRACT.alarm.source == alarm.ALARM_SOURCE == "actions_capacity"


def test_write_failure_propagates() -> None:
    with pytest.raises(RuntimeError, match="portal down"):
        run_act(breach_table(), Portal(fail=True), [])


def test_reader_failure_is_read_failed_and_writes_nothing() -> None:
    class Boom:
        def current_state(self, *a: Any, **k: Any) -> Any:
            raise OSError("unreachable")

    assert alarm.open_rec_exists(reader=Boom()) is None
    assert alarm.read_open_recs(reader=Boom()) is None
    assert alarm.open_rec_exists(rows=[]) is False and alarm.open_rec_exists(rows=[open_rec("x", "c")]) is True


def test_build_fields_pass_the_portal_write_gate() -> None:
    from scripts.ops_portal.write_validators import gate_write

    for cls in ("capacity_breach", "gauge_blind"):
        ok, why = lint_acceptance_command(alarm.acceptance_for(cls), require_discrimination=True)
        assert ok, why
        ctx = alarm._context(cls, ["2026-11-11T11:00:00Z"], breach_table(), CONTRACT, URL)
        fields = alarm._fields(cls, ctx)
        assert fields["automatable"] is False and len(fields["title"]) >= 10
        gate_write("ops_recommendations", fields, insert=True)
