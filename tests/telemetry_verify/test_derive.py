"""Mirror tests for scripts/telemetry_verify/derive.py (stdlib and yaml only, fast tier).

The committed derivation cases and friction vectors are rebuilt as stored rows here with this module's own identity (a
case:name string per session; the oracle treats ids as opaque), restating the approach of
tests/common/test_ducklake_event_read_verbs.py rather than importing it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.telemetry_verify import derive
from src.turn_capture.record_turn import PARSER_VERSION

_TELEMETRY = Path(__file__).resolve().parents[1] / "telemetry"
_CASES = yaml.safe_load((_TELEMETRY / "fixtures" / "derivation_cases.yaml").read_text(encoding="utf-8"))
_FRICTION = yaml.safe_load((_TELEMETRY / "fixtures" / "friction_vectors.yaml").read_text(encoding="utf-8"))["vectors"]
_ROW_KEYS = {"session", "at", "so", "created"}
_TIME_KEYS = {"as_of", "last_event_timestamp", "session_started_at"}
_STAMP = "2026-09-25T10:00:00+00:00"


def _ids(case: dict[str, Any]) -> dict[str, str]:
    return {name: f"{case['id']}:{name}" for name in case["sessions"]}


def _tables(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ids = _ids(case)
    out: dict[str, list[dict[str, Any]]] = {}
    for table, rows in case["rows"].items():
        for row in rows:
            session = case["sessions"][row["session"]]
            stored = {
                "producer": "claude_code",
                "parser_version": 1,
                "created_timestamp": row.get("created", _STAMP),
                **{k: v for k, v in row.items() if k not in _ROW_KEYS},
                "session_id": ids[row["session"]],
                "session_started_at": session["started_at"],
                "event_timestamp": row["at"],
                "source_ordinal": row.get("so"),
                "project_id": f"proj-{session.get('project', 'real')}",
            }
            if table == "telemetry_sessions" and session.get("parent"):
                stored["parent_session_id"] = ids[session["parent"]]
            out.setdefault(table, []).append(stored)
    return out


def _resolve(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict) and set(value) == {"session"}:
        return ids[value["session"]]
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


def _same(actual: Any, expected: Any, key: str) -> bool:
    if key in _TIME_KEYS and isinstance(expected, str):
        return actual == datetime.fromisoformat(expected)
    return actual == expected


def _oracle(case: dict[str, Any], expect: dict[str, Any]) -> list[dict[str, Any]]:
    tables, ids = _tables(case), _ids(case)
    verb, params = expect["verb"], expect["params"]
    projects = {f"proj-{name}": spec["purpose"] for name, spec in _CASES["projects"].items()}
    if verb in ("sessions_window", "sessions_window_all_purposes"):
        return derive.sessions_window(
            tables, projects, params["day_from"], params["day_to"], params["as_of"], all_purposes=verb.endswith("purposes")
        )
    session = ids[expect["session"]]
    if "as_of" in params:
        return getattr(derive, verb)(tables, session, params["as_of"])
    return getattr(derive, verb)(tables, session)


_EXPECTS = [
    pytest.param(case, expect, id=f"{case['id']}-{expect['verb']}-{i}")
    for case in _CASES["cases"]
    for i, expect in enumerate(case["expect"])
]


@pytest.mark.parametrize(("case", "expect"), _EXPECTS)
def test_oracle_matches_every_committed_derivation_case(case: dict[str, Any], expect: dict[str, Any]) -> None:
    actual = _oracle(case, expect)
    wanted = _resolve(expect["rows"], _ids(case))
    assert len(actual) == len(wanted), f"{case['id']}/{expect['verb']}: {actual}"
    for got, want in zip(actual, wanted):
        for key, value in want.items():
            assert _same(got[key], value, key), f"{case['id']}/{expect['verb']}/{key}: {got[key]!r} != {value!r}"


_SESSION_END_CASES = [
    c
    for c in _CASES["cases"]
    if c["id"].startswith(("c_at_rest", "c_mid_turn", "c_producer_close", "c_turn_", "c_blocked_turn", "c_duration_is"))
]


def test_oracle_session_end_follows_the_contract_branches() -> None:
    assert len(_SESSION_END_CASES) >= 10
    for case in _SESSION_END_CASES:
        for expect in case["expect"]:
            actual = _oracle(case, expect)
            wanted = _resolve(expect["rows"], _ids(case))
            assert len(actual) == len(wanted), f"{case['id']}: {actual}"
            for got, want in zip(actual, wanted):
                for key, value in want.items():
                    assert _same(got[key], value, key), f"{case['id']}/{key}: {got[key]!r} != {value!r}"


def _friction_rows(vector: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for obs_id, obs_type, kind, name, severity, outcome, parent, t in vector["rows"]:
        rows.append(
            {
                "producer": "claude_code",
                "parser_version": 1,
                "created_timestamp": _STAMP,
                "session_id": vector["id"],
                "session_started_at": _STAMP,
                "event_id": f"ev-{obs_id}-{kind}",
                "event_kind": kind,
                "event_timestamp": datetime(2026, 9, 25, 10, 0, t, tzinfo=timezone.utc),
                "source_ordinal": t,
                "observation_id": obs_id,
                "observation_type": obs_type,
                "name": name,
                "severity": severity,
                "outcome": outcome,
                "parent_observation_id": parent,
            }
        )
    return rows


@pytest.mark.parametrize("vector", _FRICTION, ids=[v["id"] for v in _FRICTION])
def test_oracle_matches_every_friction_vector(vector: dict[str, Any]) -> None:
    rows = _friction_rows(vector)
    facts = derive.friction_facts(rows)
    assert sorted((ref, label) for ref, label, _ in facts) == sorted(tuple(e) for e in vector["expected"]), vector["id"]
    rollup = derive.session_friction_rollup({"telemetry_observations": rows}, vector["id"])
    (row,) = rollup
    assert row["rework_total"] == sum(1 for f in facts if f[2] == "rework")
    assert row["exception_total"] == sum(1 for f in facts if f[2] == "exception")
    assert sum(item["n"] for item in row["label_counts"]) == len(facts)
    assert row["classifier_version"] == 1
    assert (row["rework_total"], row["exception_total"], row["label_counts"]) != (0, 0, []) or not facts


def test_derived_golden_headers_match_parser_version() -> None:
    for name in ("derived_golden_state.yaml", "derived_golden_rollups.yaml"):
        header = yaml.safe_load((_TELEMETRY / "fixtures" / name).read_text(encoding="utf-8"))["parser_version"]
        assert header == PARSER_VERSION, f"{name}: re-author the expectations by hand before bumping PARSER_VERSION"


def test_an_unknown_session_answers_no_rows_from_every_verb() -> None:
    none = {"telemetry_sessions": [], "telemetry_observations": []}
    when = "2026-09-25T12:00:00+00:00"
    assert derive.session_state_and_duration(none, "ghost", when) == []
    assert derive.agent_run_state_and_duration(none, "ghost", when) == []
    assert derive.agent_run_token_rollup(none, "ghost") == []
    assert derive.session_process_event_rollup(none, "ghost") == []
    assert derive.session_friction_rollup(none, "ghost") == []
    assert derive.model_call_cost_estimate(none, "ghost") == []
