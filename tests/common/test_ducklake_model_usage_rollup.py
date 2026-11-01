"""model_usage_rollup (rec-4719) over the golden chain: per-tree, per-model token totals against the independent oracle.

Module-level importorskip and full tier: every producer golden tree and slice 4 verification tree is run through the real
producer and append kernel into an in-memory telemetry catalog, then read back through the verb and compared with
scripts.telemetry_verify.derive_usage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from scripts.telemetry_verify import derive_usage  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, TENANT, case_names  # noqa: E402

VERB = "model_usage_rollup"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_SUBAGENT_TREE = "verification:subagent_nonfinal_midstream"
_MODEL_CALL = "observation_type = 'model_call'"


def _passes(tree: str) -> list[bool]:
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


def _verb(con: Any, **extra: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, VERB, **{**chain.DAY, **extra})


def _clone_call(con: Any, n: int, **replace: Any) -> None:
    ids = {"event_id": f"01ARZ3NDEKTSV4RRFFQ69G5F{n:02d}", "observation_id": f"01ARZ3NDEKTSV4RRFFQ69G6F{n:02d}"}
    chain.clone_row(con, "telemetry_observations", PROJECT, {**ids, **replace}, where=_MODEL_CALL)


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_verb_matches_oracle_on_every_golden_tree(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, _):
            oracle = derive_usage.model_usage_rollup(rows, {PROJECT: "real"}, **chain.DAY)
            assert chain.canonical(_verb(con)) == chain.canonical(oracle), (tree, final)


def test_sub_agent_model_rows_sum_into_the_root_tree() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, ids):
        (row,) = _verb(con)
        per_session = [
            chain.run_verb(con, "agent_run_token_rollup", session_id=ids[name])[0] for name in ("root", "agent:agentV1")
        ]
        assert row["root_session_id"] == ids["root"] and row["roots_in_tree"] == 1 and row["session_count"] == 2
        assert row["model_call_count"] == sum(r["model_call_count"] for r in per_session) > 0
        for column in ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation"):
            assert row[f"{column}_total"] == sum(r[f"{column}_total"] for r in per_session)
        _clone_call(con, 1, model="second-model")
        assert [(r["model"], r["model_call_count"]) for r in _verb(con)] == [
            ("claude-test-1", row["model_call_count"]),
            ("second-model", 1),
        ]


def test_totals_are_null_when_no_call_reports_them() -> None:
    blank = dict.fromkeys(("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation"))
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        _clone_call(con, 2, model="no-usage", **blank)
        by_model = {r["model"]: r for r in _verb(con)}
        row = by_model["no-usage"]
        assert (row["model_call_count"], row["usage_missing_count"]) == (1, 1)
        assert all(row[f"{c}_total"] is None for c in ("tokens_input", "tokens_output", "tokens_cache_read"))
        assert row["cache_creation_1h_total"] is None and by_model["claude-test-1"]["usage_missing_count"] == 0
        _clone_call(con, 3, model=None)
        assert [r["model"] for r in _verb(con)] == ["claude-test-1", "no-usage", None]


def test_conflicted_grain_key_marks_the_tree() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, rows, _):
        (clean,) = _verb(con)
        assert (clean["conflicted_grain_keys"], clean["derivation_conflicted"]) == ([], False)
        opener = next(r for r in rows["telemetry_sessions"] if r["event_kind"] == "open" and r["parent_session_id"] is None)
        chain.clone_row(
            con, "telemetry_sessions", PROJECT, {"branch": "conflicting-branch"}, where="parent_session_id IS NULL"
        )
        (conflicted,) = _verb(con)
        assert conflicted["derivation_conflicted"] is True
        assert [k["event_id"] for k in conflicted["conflicted_grain_keys"]] == [opener["event_id"]]
        assert conflicted["generations"] == clean["generations"]


def test_non_real_projects_and_out_of_window_trees_are_excluded() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        synthetic = "01ARZ3NDEKTSV4RRFFQ69G5FB1"
        db.insert_projects(
            con, [{"project_id": synthetic, "tenant_id": TENANT, "project_ref": "x/synthetic", "purpose": "synthetic"}]
        )
        chain.append_batches(con, chain.hold_back(chain.emit(_SUBAGENT_TREE, True)), project=(synthetic, "x/synthetic"))
        assert len(_verb(con)) == 1
        assert _verb(con, day_from="2026-01-02", day_to="2026-01-02") == []
        assert _verb(con, day_from="2025-12-26", day_to="2026-01-01") == _verb(con)
        assert datetime(2026, 1, 1, tzinfo=timezone.utc) <= _verb(con)[0]["session_started_at"]
