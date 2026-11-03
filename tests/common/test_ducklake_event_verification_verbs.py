"""The verification verbs (session_tree_rows, telemetry_orphan_counts, telemetry_root_sessions) over the golden chain.

Module-level importorskip and full tier: every producer golden tree and slice 4 verification tree is run through the real
producer and append kernel into an in-memory telemetry catalog, then read back through the verbs and compared with the stored
rows and the independent oracle (scripts.telemetry_verify).
"""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.telemetry_verify import orphan_counts, read_tree  # noqa: E402
from scripts.telemetry_verify.joins import load_telemetry_joins  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_event_read_verbs import IDLE_THRESHOLD_SECONDS  # noqa: E402
from src.telemetry.append import append_events  # noqa: E402
from src.turn_capture.record_turn import PARSER_VERSION  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, TENANT, case_names  # noqa: E402

OTHER_PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRZ"
ABSENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_JOINS = load_telemetry_joins()
_REAL = {PROJECT: "real", OTHER_PROJECT: "real"}


def _named(con: Any):
    return lambda verb, **params: chain.run_verb(con, verb, **params)


def _expected(rows: dict[str, list[dict[str, Any]]], session_id: str) -> dict[str, list[dict[str, Any]]]:
    """The stored rows of the tree holding session_id, as the verb should return them (compared columns + created time)."""
    pin = next(r for t in chain.TABLES for r in rows[t] if r["session_id"] == session_id)
    tree = (pin["tenant_id"], pin["project_id"], pin["session_started_at"])

    def keep(row: dict[str, Any]) -> bool:
        return (row["tenant_id"], row["project_id"], row["session_started_at"]) == tree

    return {
        t: [{c: r[c] for c in (*ev.compared_columns(t), "created_timestamp")} for r in rows[t] if keep(r)]
        for t in chain.TABLES
    }


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["session_id"], row["producer"], row["event_id"], row["parser_version"], str(row["created_timestamp"]))


def _same_rows(got: dict[str, list[dict[str, Any]]], want: dict[str, list[dict[str, Any]]]) -> bool:
    return all(
        [chain.canonical(r) for r in sorted(got.get(t, []), key=_key)]
        == [chain.canonical(r) for r in sorted(want.get(t, []), key=_key)]
        for t in chain.TABLES
    )


def _tree_pages(con: Any, session_id: str, size: int = 2000) -> list[list[dict[str, Any]]]:
    return read_tree(_named(con), session_id, size)


def _decoded(pages: list[list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    from scripts.telemetry_verify import decode_rows  # noqa: PLC0415

    return decode_rows(pages)


def _append(con: Any, result: Any, project_id: str) -> None:
    for table, rows in chain.hold_back(result).batches:
        if rows:
            append_events(
                con,
                chain.specs()[table],
                copy.deepcopy(rows),
                tenant_id=TENANT,
                project_id=project_id,
                catalog=db.ALIAS,
                now=chain.latest_event(result) + timedelta(seconds=1),
            )


def _passes(tree: str) -> list[bool]:
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_tree_rows_return_every_stored_row_exactly_once(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, ids):
            if not ids:
                assert chain.run_verb(con, "session_tree_rows", session_id=ABSENT, offset=0, max_rows=10) == []
                continue
            for session_id in ids.values():
                pages = _tree_pages(con, session_id)
                got = _decoded(pages)
                assert _same_rows(got, _expected(rows, session_id)), (tree, final)
                assert {r["total_rows"] for page in pages for r in page} == {sum(len(v) for v in rows.values())}
                roots = {r["roots_in_tree"] for page in pages for r in page}
                assert roots == {1 if "root" in ids else 0}
                assert all("content" not in r and "content_uri" not in r for v in got.values() for r in v)


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_tree_rows_page_to_the_same_set_at_any_page_size(tree: str) -> None:
    with chain.loaded(tree, True) as (con, rows, ids):
        if not ids:
            return
        session_id = ids.get("root") or next(iter(ids.values()))
        whole = _decoded(_tree_pages(con, session_id))
        for size in (7, 2000):
            pages = _tree_pages(con, session_id, size)
            assert all(len(page) <= size for page in pages)
            assert _same_rows(_decoded(pages), whole)


def test_a_single_row_page_reassembles_the_tree() -> None:
    with chain.loaded("corpus:close_cancelled_mid_stream", True) as (con, rows, ids):
        pages = _tree_pages(con, ids["root"], 1)
        assert len(pages) == sum(len(v) for v in rows.values())
        assert _same_rows(_decoded(pages), _expected(rows, ids["root"]))


def _two_projects(con: Any, tree: str) -> dict[str, dict[str, str]]:
    db.insert_projects(
        con, [{"project_id": OTHER_PROJECT, "tenant_id": TENANT, "project_ref": "other/project", "purpose": "real"}]
    )
    _append(con, chain.emit(tree, True), OTHER_PROJECT)
    stored = chain.stored_rows(con)
    return {
        p: chain.session_ids({t: [r for r in v if r["project_id"] == p] for t, v in stored.items()})
        for p in (PROJECT, OTHER_PROJECT)
    }


def test_co_pinned_trees_stay_apart_and_collisions_are_counted() -> None:
    tree = "verification:subagent_nonfinal_midstream"
    with chain.loaded(tree, True) as (con, _, _):
        by_project = _two_projects(con, tree)
        rows = chain.stored_rows(con)
        assert by_project[PROJECT]["root"] != by_project[OTHER_PROJECT]["root"]
        for project, ids in by_project.items():
            mine = {t: [r for r in v if r["project_id"] == project] for t, v in rows.items()}
            got = _decoded(_tree_pages(con, ids["root"]))
            assert all(r["project_id"] == project for v in got.values() for r in v)
            assert _same_rows(got, _expected(mine, ids["root"]))
        root = next(r for r in rows["telemetry_sessions"] if r["session_id"] == by_project[PROJECT]["root"])
        twin_id = root["session_id"][:-1] + ("0" if root["session_id"][-1] != "0" else "1")
        twin = dict(root, session_id=twin_id, event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW", entity_ref="other-root-ref")
        db.insert_rows(con, "telemetry_sessions", [twin])
        for session_id in (by_project[PROJECT]["root"], twin_id):
            assert {
                r["roots_in_tree"]
                for r in chain.run_verb(con, "session_tree_rows", session_id=session_id, offset=0, max_rows=3)
            } == {2}
        other = _tree_pages(con, by_project[OTHER_PROJECT]["root"])
        assert {r["roots_in_tree"] for page in other for r in page} == {1}


def _window(con: Any, **extra: Any) -> dict[str, Any]:
    return {**chain.DAY, **extra}


def _verb_equals_oracle(con: Any, as_of: str) -> list[dict[str, Any]]:
    got = chain.run_verb(con, "telemetry_orphan_counts", **_window(con, as_of=as_of))
    oracle = orphan_counts(chain.stored_rows(con), _JOINS, as_of, IDLE_THRESHOLD_SECONDS, _REAL)
    assert got == oracle, as_of
    return got


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_orphan_counts_match_the_independent_count(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, ids):
            if not ids:
                assert chain.run_verb(con, "telemetry_orphan_counts", **_window(con, as_of="2026-06-01T00:00:00+00:00")) == []
                continue
            last = max(chain.last_event(rows, s) for s in ids.values())
            for as_of in chain.as_of_rule(last):
                got = _verb_equals_oracle(con, as_of)
                if final and any(r["event_kind"] == "close" for r in rows["telemetry_sessions"]):
                    assert all(r["orphans_quiescent"] == r["orphans_pending"] == 0 for r in got), (tree, as_of)


def test_orphans_split_by_the_exact_idle_threshold_and_co_pinned_trees_agree() -> None:
    tree = "corpus:close_cancelled_mid_stream"
    with chain.loaded(tree, True) as (con, rows, ids):
        by_project = _two_projects(con, tree)
        assert by_project
        rows = chain.stored_rows(con)
        planted = next(r for r in rows["telemetry_observations"] if r["project_id"] == PROJECT)
        db.insert_rows(
            con,
            "telemetry_observations",
            [
                dict(
                    planted,
                    event_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
                    observation_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
                    parent_observation_id="01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                )
            ],
        )
        rows = chain.stored_rows(con)
        last = max(r["event_timestamp"] for t in chain.TABLES for r in rows[t] if r["project_id"] == PROJECT)
        join = "parent_observation_id_join_observations_self_tree"
        for offset, state in ((0, "orphans_pending"), (1, "orphans_quiescent")):
            as_of = (last + timedelta(seconds=IDLE_THRESHOLD_SECONDS + offset)).isoformat()
            got = {r["join_id"]: r for r in _verb_equals_oracle(con, as_of)}
            other = "orphans_quiescent" if state == "orphans_pending" else "orphans_pending"
            assert (got[join][state], got[join][other]) == (1, 0), as_of


def _roots(con: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, "telemetry_root_sessions", **chain.DAY)


def test_root_sessions_carry_join_keys_and_stamps_for_real_projects_only() -> None:
    tree = "verification:subagent_nonfinal_midstream"
    with chain.loaded(tree, True) as (con, rows, ids):
        (root,) = _roots(con)
        assert root["session_id"] == ids["root"] and root["harness_session_key"] is None
        assert root["entity_ref"] == next(
            r["entity_ref"] for r in rows["telemetry_sessions"] if r["session_id"] == ids["root"]
        )
        tree_last = max(r["event_timestamp"] for t in chain.TABLES for r in rows[t])
        assert chain.canonical(root["last_event_timestamp"]) == chain.canonical(tree_last)
        assert (root["generations"], root["conflicted_grain_keys"], root["derivation_conflicted"]) == (
            [{"producer": "claude_code", "parser_version": PARSER_VERSION}],
            [],
            False,
        )
        opener = next(r for r in rows["telemetry_sessions"] if r["session_id"] == ids["root"] and r["event_kind"] == "open")
        db.insert_rows(
            con, "telemetry_sessions", [dict(opener, event_id="01ARZ3NDEKTSV4RRFFQ69G5FB0", harness_session_key="hk-1")]
        )
        assert _roots(con)[0]["harness_session_key"] == "hk-1"
        db.insert_rows(con, "telemetry_sessions", [dict(opener, branch="conflicting-branch")])
        (conflicted,) = _roots(con)
        assert conflicted["derivation_conflicted"] is True
        assert [k["event_id"] for k in conflicted["conflicted_grain_keys"]] == [opener["event_id"]]
        db.insert_projects(
            con,
            [
                {
                    "project_id": "01ARZ3NDEKTSV4RRFFQ69G5FB1",
                    "tenant_id": TENANT,
                    "project_ref": "x/synthetic",
                    "purpose": "synthetic",
                },
                {
                    "project_id": "01ARZ3NDEKTSV4RRFFQ69G5FB2",
                    "tenant_id": TENANT,
                    "project_ref": "x/drill",
                    "purpose": "drill",
                },
            ],
        )
        for project in ("01ARZ3NDEKTSV4RRFFQ69G5FB1", "01ARZ3NDEKTSV4RRFFQ69G5FB2"):
            _append(con, chain.emit(tree, True), project)
        assert [r["session_id"] for r in _roots(con)] == [ids["root"]]
