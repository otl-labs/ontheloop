"""A merge link row (telemetry_sessions annotate, producer ci_annotate) passes the write boundary without coverage_after."""

from __future__ import annotations

import copy
import functools
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.contracts._rule_grammar import grammar_errors
from src.telemetry.append import EventTableSpec, prepare_batch
from src.telemetry.gate import GateError
from tests.fixtures import telemetry_golden_chain as chain
from tests.fixtures.turn_capture_ducklake import load_specs

TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"


@functools.lru_cache(maxsize=1)
def _spec() -> EventTableSpec:
    return load_specs()["telemetry_sessions"]


def _link(**over: Any) -> dict[str, Any]:
    root = next(r for r in chain.emit("verification:subagent_nonfinal_midstream", True).sessions if r["event_kind"] == "open")
    row = copy.deepcopy(root)
    started = row["session_started_at"]
    row.update(
        event_kind="annotate",
        producer="ci_annotate",
        external_ref=f"{row['entity_ref']}:merge:0123abc#0/annotate",
        event_timestamp=started + timedelta(hours=2),
        pr_url="https://example.test/pr/1",
        ci_outcome="success",
        coverage_after=None,
    )
    row.update(over)
    return {k: v for k, v in row.items() if v is not None}


def _prepare(row: dict[str, Any]) -> Any:
    now = row["event_timestamp"] + timedelta(seconds=1)
    return prepare_batch(_spec(), [row], tenant_id=TENANT, project_id=PROJECT, now=now)


def test_a_link_row_without_coverage_after_passes_the_write_boundary() -> None:
    assert len(_prepare(_link()).rows) == 1


def test_a_link_row_still_requires_ci_outcome_and_pr_url() -> None:
    for column in ("ci_outcome", "pr_url"):
        row = _link()
        row.pop(column)
        with pytest.raises(GateError, match=column):
            _prepare(row)


def test_a_temporary_exemption_without_review_by_fails_the_grammar() -> None:
    intent = {"required_when": {"event_kind": ["annotate"]}}
    bad = {"required_when": {"class": "temporary", "reason": "r", "owner": "rec-4599"}}
    errors = grammar_errors(
        "telemetry_sessions",
        "event",
        {"coverage_after": {"nullable": True}},
        {"coverage_after": {**intent, "write_time_exemptions": bad}},
        Path("."),
    )
    assert any("review_by" in e for e in errors), errors
