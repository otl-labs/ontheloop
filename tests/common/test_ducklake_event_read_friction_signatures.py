"""friction_signature_counts (PLAN-friction-signature-coverage) over the golden chain: one row per tree, session workflow and
friction signature, compared with scripts.telemetry_verify.derive_usage on every golden tree and on hand-built facts.

Module-level importorskip and full tier.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from scripts.telemetry_verify import derive_usage  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_event_read_verbs import FRICTION_RULES  # noqa: E402
from src.telemetry.identity import derive_entity_key  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, case_names  # noqa: E402

VERB = "friction_signature_counts"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_VECTORS = yaml.safe_load(
    (Path(__file__).parents[1] / "telemetry" / "fixtures" / "friction_vectors.yaml").read_text(encoding="utf-8")
)["vectors"]
_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_REAL = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
_DRILL = "01BRZ3NDEKTSV4RRFFQ69G5FBX"
_START = datetime.fromisoformat("2026-09-25T10:00:00+00:00")
_WINDOW = {"day_from": "2026-09-25", "day_to": "2026-09-25"}
_HOOK_TREE = "corpus:hook_blocked_tool_call"
_SUBAGENT_TREE = "verification:subagent_nonfinal_midstream"


def _passes(tree: str) -> list[bool]:
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


def _verb(con: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, VERB, **chain.DAY)


def _session(name: str) -> str:
    return derive_entity_key("telemetry_sessions:session_id", _TENANT, _REAL, name, _START)


def _catalog() -> Any:
    con = db.open_memory_catalog()
    db.insert_projects(
        con,
        [
            {"project_id": _REAL, "tenant_id": _TENANT, "project_ref": "example/real", "purpose": "real"},
            {"project_id": _DRILL, "tenant_id": _TENANT, "project_ref": "example/drill", "purpose": "drill"},
        ],
    )
    return con


def _facts(session: str, rows: list[list[Any]], project: str = _REAL) -> list[dict[str, Any]]:
    out = []
    for obs_id, obs_type, kind, name, severity, outcome, parent, t in rows:
        out.append(
            db.base_row(
                project_id=project,
                session_id=session,
                session_started_at=_START,
                event_id=f"ev-{session[-6:]}-{obs_id}-{kind}",
                event_kind=kind,
                event_timestamp=_START.replace(second=t),
                source_ordinal=t,
                observation_id=f"{session[-6:]}-{obs_id}",
                observation_type=obs_type,
                name=name,
                severity=severity,
                outcome=outcome,
                parent_observation_id=None if parent is None else f"{session[-6:]}-{parent}",
            )
        )
    return out


def _bash_errors(n: int) -> list[list[Any]]:
    return [[f"t{i}", "tool_call", "close", "Bash", None, "error", None, i] for i in range(1, n + 1)]


def _synthetic(con: Any, *batches: tuple[str, list[list[Any]]], project: str = _REAL) -> None:
    db.insert_rows(con, "telemetry_observations", [r for s, rows in batches for r in _facts(_session(s), rows, project)])


def _oracle_equals_verb(con: Any, params: dict[str, str]) -> list[dict[str, Any]]:
    got = chain.run_verb(con, VERB, **params)
    oracle = derive_usage.friction_signature_counts(chain.stored_rows(con), {_REAL: "real"}, **params)
    assert chain.canonical(got) == chain.canonical(oracle)
    return got


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_verb_matches_oracle_on_every_golden_tree(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, _):
            oracle = derive_usage.friction_signature_counts(rows, {PROJECT: "real"}, **chain.DAY)
            assert chain.canonical(_verb(con)) == chain.canonical(oracle), (tree, final)


def test_generic_flag_marks_only_rules_without_signature_or_prefix() -> None:
    by_id = {r[0]: r for r in FRICTION_RULES["rules"]}
    seen = 0
    for tree in (_HOOK_TREE, "corpus:tool_result_error", _SUBAGENT_TREE):
        with chain.loaded(tree, True) as (con, _, _):
            for row in _verb(con):
                if row["rule_id"] == "run":
                    continue
                rule = by_id[row["rule_id"]]
                assert row["generic"] is (rule[2] is None and rule[3] is None), row
                assert (row["label"], row["class"]) == (rule[5], rule[6])
                seen += 1
    assert seen


def test_repeat_run_row_carries_rule_run() -> None:
    con = _catalog()
    _synthetic(con, ("a", _bash_errors(3)))
    rows = _oracle_equals_verb(con, _WINDOW)
    run = [r for r in rows if r["rule_id"] == "run"]
    assert [(r["label"], r["class"], r["generic"], r["signature"], r["disposition"], r["fact_count"]) for r in run] == [
        (FRICTION_RULES["run_label"], FRICTION_RULES["run_class"], False, "Bash", "error", 1)
    ]
    assert [r["fact_count"] for r in rows if r["rule_id"] == "r92"] == [3]
    assert {r["classifier_version"] for r in rows} == {FRICTION_RULES["classifier_version"]}


def test_unmapped_count_counts_generic_process_event_facts() -> None:
    checked = 0
    for vector in (v for v in _VECTORS if "unmapped" in v):
        session = derive_entity_key("telemetry_sessions:session_id", _TENANT, _REAL, vector["id"], _START)
        con = _catalog()
        rows = []
        for obs_id, obs_type, kind, name, severity, outcome, parent, t in vector["rows"]:
            rows.append(
                db.base_row(
                    project_id=_REAL,
                    session_id=session,
                    session_started_at=_START,
                    event_id=f"ev-{obs_id}-{kind}",
                    event_kind=kind,
                    event_timestamp=_START.replace(second=t),
                    source_ordinal=t,
                    observation_id=obs_id,
                    observation_type=obs_type,
                    name=name,
                    severity=severity,
                    outcome=outcome,
                    parent_observation_id=parent,
                )
            )
        db.insert_rows(con, "telemetry_observations", rows)
        (rollup,) = ev.execute(con, verb="session_friction_rollup", params={"session_id": session})
        assert rollup["unmapped_count"] == vector["unmapped"], vector["id"]
        assert rollup["classifier_version"] == FRICTION_RULES["classifier_version"]
        checked += 1
    assert checked >= 2


def test_absorbed_close_is_not_a_row() -> None:
    with chain.loaded(_HOOK_TREE, True) as (con, rows, _):
        rows_out = _verb(con)
        assert not [r for r in rows_out if r["label"] == "tool_blocked"]
        assert [r["fact_count"] for r in rows_out if r["signature"] == "hook:never_on_main"] == [1]
        assert chain.canonical(rows_out) == chain.canonical(
            derive_usage.friction_signature_counts(rows, {PROJECT: "real"}, **chain.DAY)
        )


def test_window_reads_real_purpose_projects_only() -> None:
    con = _catalog()
    gate = [["g1", "process_event", "point", "gate:validate_plan_documents", "error", None, None, 1]]
    _synthetic(con, ("a", gate))
    _synthetic(con, ("b", gate), project=_DRILL)
    rows = _oracle_equals_verb(con, _WINDOW)
    assert [(r["signature"], r["fact_count"], r["session_count"]) for r in rows] == [("gate:validate_plan_documents", 1, 1)]
    assert rows[0]["rule_id"] == "r20" and rows[0]["generic"] is False


def test_two_trees_in_one_window_label_independently() -> None:
    with chain.loaded(_HOOK_TREE, True) as (con, _, _):
        second = chain.register_project(con, 7)
        chain.append_batches(con, chain.hold_back(chain.emit(_SUBAGENT_TREE, True)), project=second)
        both = _verb(con)
        solo_rows = []
        for tree in (_HOOK_TREE, _SUBAGENT_TREE):
            with chain.loaded(tree, True) as (solo, _, _):
                solo_rows += _verb(solo)
        key = lambda r: (str(r["root_session_id"]), r["rule_id"], r["signature"], r["disposition"])  # noqa: E731
        assert sorted(map(key, both)) == sorted(map(key, solo_rows))
        projects = {PROJECT: "real", second[0]: "real"}
        assert chain.canonical(both) == chain.canonical(
            derive_usage.friction_signature_counts(chain.stored_rows(con), projects, **chain.DAY)
        )
    con = _catalog()
    _synthetic(con, ("a", _bash_errors(2)), ("b", _bash_errors(1)))
    rows = _oracle_equals_verb(con, _WINDOW)
    assert not [r for r in rows if r["rule_id"] == "run"]
    assert [r["fact_count"] for r in rows if r["rule_id"] == "r92"] == [3]
    con = _catalog()
    _synthetic(con, ("root", _bash_errors(2)), ("sub", _bash_errors(1)))
    other = _catalog()
    _synthetic(other, ("root", _bash_errors(2)))
    _synthetic(other, ("other", _bash_errors(1)))
    assert not [r for r in _oracle_equals_verb(con, _WINDOW) if r["rule_id"] == "run"]
    assert not [r for r in _oracle_equals_verb(other, _WINDOW) if r["rule_id"] == "run"]
    con = _catalog()
    hook = [["h1", "process_event", "point", "gate:validate_plan_documents", "error", None, None, 1]]
    root_close = [["c1", "tool_call", "close", "Bash", None, "error", None, 2]]
    root_rows = _facts(_session("root"), root_close)
    sub_rows = _facts(_session("sub"), hook)
    sub_rows[0]["parent_observation_id"] = root_rows[0]["observation_id"]
    db.insert_rows(con, "telemetry_observations", root_rows + sub_rows)
    rows = _oracle_equals_verb(con, _WINDOW)
    assert sorted((r["rule_id"], r["fact_count"]) for r in rows) == [("r20", 1), ("r92", 1)]
