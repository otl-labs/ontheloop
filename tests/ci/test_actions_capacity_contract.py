"""The Actions capacity contract loader (rec-5022)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts.ci import actions_capacity_contract as acc

ROOT = Path(__file__).resolve().parents[2]


def test_real_contract_loads() -> None:
    c = acc.load_contract(ROOT)
    assert c.cap.concurrent_jobs == 20
    assert c.slot_threshold == 16
    assert c.queue_threshold_s == 300
    assert c.trigger.queue_p90.job.job == "main-validate"
    assert c.validity.min_queue_samples_per_hour == 10
    assert (c.validity.pooled_min_samples_24h, c.validity.pooled_min_samples_168h) == (10, 20)
    assert c.validity.min_evaluated_hours_24h == 12
    assert (c.window.table_hours, c.window.bootstrap_hours, c.window.overlap_hours) == (168, 24, 2)
    assert (c.window.request_budget_per_tick, c.window.time_budget_seconds) == (400, 180)
    assert c.alarm.source == "actions_capacity"
    assert set(c.alarm.classes) == {"capacity_breach", "gauge_blind"}
    assert len(c.ledger.rows) == 4
    assert c.contract.contract_class == "D"


def test_unknown_key_is_refused() -> None:
    raw = yaml.safe_load((ROOT / "docs/contracts/actions-capacity.yaml").read_text(encoding="utf-8"))
    raw["cap"]["surprise"] = 1
    with pytest.raises(ValidationError):
        acc.parse_contract(raw)
