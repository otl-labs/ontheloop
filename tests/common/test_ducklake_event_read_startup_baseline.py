"""session_startup_baseline (rec-4703) over the golden chain: each session's and sub-agent's first model call.

Module-level importorskip and full tier; the verb is compared with scripts.telemetry_verify.derive_usage on every golden tree
and on hand-built calls (an earlier call with the 1h/5m split, a later larger one, a session with no call).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from scripts.telemetry_verify import derive_usage  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, case_names  # noqa: E402

VERB = "session_startup_baseline"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_SUBAGENT_TREE = "verification:subagent_nonfinal_midstream"
_EARLY = datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=timezone.utc)
_LATE = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)


def _passes(tree: str) -> list[bool]:
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


def _verb(con: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, VERB, **chain.DAY)


def _add_call(con: Any, n: int, where: str, **replace: Any) -> None:
    ids = {"event_id": f"01ARZ3NDEKTSV4RRFFQ69GBF{n:02d}", "observation_id": f"01ARZ3NDEKTSV4RRFFQ69GCF{n:02d}"}
    chain.clone_row(
        con, "telemetry_observations", PROJECT, {**ids, **replace}, where=f"observation_type = 'model_call' AND {where}"
    )


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_verb_matches_oracle_on_every_golden_tree(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, _):
            oracle = derive_usage.session_startup_baseline(rows, {PROJECT: "real"}, **chain.DAY)
            assert chain.canonical(_verb(con)) == chain.canonical(oracle), (tree, final)


def test_first_model_call_baseline_per_session_and_agent_run() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, rows, ids):
        before = {r["session_id"]: r for r in _verb(con)}
        root, agent = before[ids["root"]], before[ids["agent:agentV1"]]
        assert (root["is_root"], root["agent_name"], root["root_session_id"]) == (True, None, ids["root"])
        assert (agent["is_root"], agent["agent_name"], agent["parent_session_id"]) == (False, "Explore", ids["root"])
        assert root["model_call_count"] > 0 and agent["model_call_count"] > 0
        in_root = f"session_id = '{ids['root']}'"
        _add_call(
            con,
            1,
            in_root,
            event_timestamp=_EARLY,
            tokens_input=7,
            tokens_cache_read=11,
            tokens_cache_creation=13,
            cache_creation_1h_tokens=3,
            cache_creation_5m_tokens=10,
            tokens_output=5,
            model="early-model",
        )
        _add_call(con, 2, in_root, event_timestamp=_LATE, tokens_input=9999, tokens_cache_read=9999, model="late-model")
        after = {r["session_id"]: r for r in _verb(con)}
        first = after[ids["root"]]
        assert first["model_call_count"] == root["model_call_count"] + 2
        assert (first["first_call_at"], first["model"]) == (_EARLY, "early-model")
        assert (first["tokens_input"], first["tokens_cache_read"], first["tokens_cache_creation"], first["tokens_output"]) == (
            7,
            11,
            13,
            5,
        )
        assert (first["cache_creation_1h_tokens"], first["cache_creation_5m_tokens"], first["usage_missing"]) == (3, 10, False)
        assert after[ids["agent:agentV1"]] == agent
        no_usage = {"tokens_input": None, "tokens_output": None}
        _add_call(con, 3, f"session_id = '{ids['agent:agentV1']}'", event_timestamp=_EARLY, **no_usage)
        assert {r["session_id"]: r for r in _verb(con)}[ids["agent:agentV1"]]["usage_missing"] is True
        chain.clone_row(
            con,
            "telemetry_sessions",
            PROJECT,
            {"session_id": "01ARZ3NDEKTSV4RRFFQ69GDF01", "event_id": "01ARZ3NDEKTSV4RRFFQ69GEF01"},
            where="parent_session_id IS NOT NULL",
        )
        empty = next(r for r in _verb(con) if r["session_id"] == "01ARZ3NDEKTSV4RRFFQ69GDF01")
        assert empty["model_call_count"] == 0 and empty["is_root"] is False
        called = ("first_call_at", "model", "tokens_input", "tokens_output", "cache_creation_1h_tokens", "usage_missing")
        assert all(empty[c] is None for c in called)
        oracle = derive_usage.session_startup_baseline(chain.stored_rows(con), {PROJECT: "real"}, **chain.DAY)
        assert chain.canonical(_verb(con)) == chain.canonical(oracle)
