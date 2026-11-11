#!/usr/bin/env python3
"""Loader for docs/contracts/actions-capacity.yaml (rec-5022): pydantic models, every key, extra=forbid.

No I/O at import. The literal 'actions-capacity.yaml' below is what the contract's evaluator resolution looks for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_BASENAME = "actions-capacity.yaml"
ROOT = Path(__file__).resolve().parents[2]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Evaluator(_Strict):
    check: str


class Header(_Strict):
    id: str
    contract_class: str = Field(alias="class")
    contract_version: int
    status: str
    description: str
    subject: str
    evaluator: Evaluator


class AmendmentEntry(_Strict):
    date: str
    semantic_break: bool
    change_class: str
    summary: str


class Cap(_Strict):
    concurrent_jobs: int
    basis: str
    scope: str


class JobRef(_Strict):
    workflow: str
    job: str
    event: Optional[str] = None


class SlotDemand(_Strict):
    threshold: float
    definition: str


class QueueP90(_Strict):
    threshold_seconds: float
    job: JobRef
    definition: str


class Trigger(_Strict):
    slot_demand: SlotDemand
    queue_p90: QueueP90
    peak_concurrency: str
    fires_when: str


class Validity(_Strict):
    evaluated_hour: str
    min_queue_samples_per_hour: int
    pooled_min_samples_24h: int
    pooled_min_samples_168h: int
    percentile_method: Literal["nearest_rank"]
    pooling: str
    min_evaluated_hours_24h: int


class Window(_Strict):
    table_hours: int
    bootstrap_hours: int
    overlap_hours: int
    request_budget_per_tick: int
    time_budget_seconds: int
    chaining: str


class Reading(_Strict):
    precedence: str
    verdicts: dict[str, str]
    green_predicate: list[str]
    projected_peak: str
    consumer_rule: str


class Alarm(_Strict):
    source: str
    classes: dict[str, str]
    episode: str
    step_outcome: str


class LedgerRow(_Strict):
    id: str
    items: list[str] = Field(default_factory=list)
    unit: str
    source: str
    job_minutes_per_unit: Optional[float] = None
    analog: Optional[list[JobRef]] = None
    multiplier: Optional[float] = None
    basis: str
    live_signal: Optional[JobRef] = None


class Ledger(_Strict):
    units: list[str]
    sources: dict[str, str]
    live_signal: str
    rows: list[LedgerRow]


class Contract(_Strict):
    contract: Header
    amendment_log: list[AmendmentEntry]
    cap: Cap
    trigger: Trigger
    validity: Validity
    window: Window
    reading: Reading
    alarm: Alarm
    ledger: Ledger
    trigger_response: str

    @property
    def slot_threshold(self) -> float:
        return self.trigger.slot_demand.threshold

    @property
    def queue_threshold_s(self) -> float:
        return self.trigger.queue_p90.threshold_seconds


def parse_contract(raw: Any) -> Contract:
    return Contract.model_validate(raw)


def load_contract(root: Optional[Path] = None) -> Contract:
    base = root if root is not None else ROOT
    path = base / "docs" / "contracts" / CONTRACT_BASENAME
    return parse_contract(yaml.safe_load(path.read_text(encoding="utf-8")))
