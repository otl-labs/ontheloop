"""LL-5: the checks written for Claude Code transcripts never judge a litellm tree (PLAN-litellm-claude-code-scoping).

The hand-written litellm tree of tests/fixtures/telemetry_litellm_tree.py counts toward none of the four stored-row silent
checks, and through the real writer and the reader verbs it reads running, then abandoned, or closed from its end record,
as the monitor's oracle re-derives it. The monitor case over the golden world lives in test_monitor.py.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.telemetry_verify import silent_checks as sc
from tests.fixtures.telemetry_litellm_tree import PARSER_VERSION, T0, litellm_batches, stored_rows

_STANDARD = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "data-modeling-standard.yaml"


def test_a_litellm_tree_counts_toward_no_silent_check() -> None:
    assert PARSER_VERSION >= sc.PARSER_FLOOR
    tree = stored_rows(litellm_batches(ended=False))
    tree["telemetry_sessions"].append(
        {
            **tree["telemetry_sessions"][0],
            "event_kind": "resume",
            "event_id": "resume",
            "event_timestamp": T0 + timedelta(seconds=9),
        }
    )
    tree["telemetry_agents"].append(
        {
            **tree["telemetry_agents"][0],
            "event_kind": "close",
            "event_id": "bg-close",
            "agent_run_id": "bg",
            "trigger": "subagent_background",
            "error": "notice_status:odd",
        }
    )
    assert sc.check_tree(tree) == dict.fromkeys(sc.CHECKS, (0, 0))


def test_claude_code_counts_are_unchanged_beside_a_litellm_tree() -> None:
    opened = stored_rows(litellm_batches(ended=False), "s-claude")["telemetry_sessions"][0]
    claude = {"telemetry_sessions": [{**opened, "producer": "claude_code", "harness_session_key": None, "checkout_sha": None}]}
    alone = sc.check_trees([claude])
    assert alone["harness_key_missing"] == (1, 1)
    assert sc.check_trees([claude, stored_rows(litellm_batches(ended=True))]) == alone


def _catalog(*, ended: bool) -> tuple[Any, Any, dict[str, list[dict[str, Any]]], str]:
    pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
    from tests.fixtures import telemetry_golden_chain as g

    con = g.open_catalog()
    g.append_batches(con, SimpleNamespace(batches=litellm_batches(ended=ended)), project=g.register_project(con, 1))
    rows = g.stored_rows(con)
    return g, con, rows, rows["telemetry_sessions"][0]["session_id"]


@pytest.mark.parametrize(
    ("ended", "after", "state"),
    [(False, timedelta(minutes=1), "running"), (False, timedelta(days=2), "abandoned"), (True, timedelta(days=2), "success")],
)
def test_a_litellm_root_reads_from_its_end_record_close(ended: bool, after: timedelta, state: str) -> None:
    g, con, rows, sid = _catalog(ended=ended)
    as_of = (T0 + after).isoformat()
    for verb in ("session_state_and_duration", "agent_run_state_and_duration"):
        deployed = g.run_verb(con, verb, session_id=sid, as_of=as_of)
        derived = g.run_oracle(rows, verb, session_id=sid, as_of=as_of)
        assert [r["state"] for r in deployed] == [r["state"] for r in derived] == [state], verb


def test_the_sessions_arrival_denominator_excludes_litellm_roots() -> None:
    tables = yaml.safe_load(_STANDARD.read_text(encoding="utf-8"))["arrival_expectations"]["tables"]
    denominator = tables["telemetry_sessions"]["denominator"]
    for token in ("A litellm root", "LL-5", "no harness_session_key", "telemetry_agents' residual expectation (rec-4951)"):
        assert token in denominator
    assert tables["telemetry_agents"]["owner"] == "rec-4951"
