"""Mirror tests for scripts/telemetry_verify/joins.py."""

from __future__ import annotations

from pathlib import Path

from scripts.telemetry_verify.joins import load_telemetry_joins

_FLOOR = {
    ("telemetry_observations", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_transcripts", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_agents", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_sessions", "parent_session_id", "telemetry_sessions", "session_id"),
    ("telemetry_transcripts", "observation_id", "telemetry_observations", "observation_id"),
    ("telemetry_agents", "observation_id", "telemetry_observations", "observation_id"),
    ("telemetry_observations", "parent_observation_id", "telemetry_observations", "observation_id"),
}


def test_telemetry_joins_load_from_the_contract() -> None:
    pairs = {join[1:] for join in load_telemetry_joins()}
    assert _FLOOR <= pairs
    assert all(child.startswith("telemetry_") and parent.startswith("telemetry_") for child, _, parent, _ in pairs)


def test_non_telemetry_joins_are_skipped_and_direction_follows_cardinality(tmp_path: Path) -> None:
    path = tmp_path / "joins.yaml"
    path.write_text(
        "joins:\n"
        "  a: {from: telemetry_sessions.session_id, to: telemetry_agents.session_id, cardinality: one_to_many}\n"
        "  b: {from: telemetry_agents.observation_id, to: telemetry_observations.observation_id, cardinality: many_to_one}\n"
        "  c: {from: ops_execution_plans.planning_session_id, to: telemetry_sessions.session_id, cardinality: many_to_one}\n",
        encoding="utf-8",
    )
    assert load_telemetry_joins(path) == [
        ("a", "telemetry_agents", "session_id", "telemetry_sessions", "session_id"),
        ("b", "telemetry_agents", "observation_id", "telemetry_observations", "observation_id"),
    ]
