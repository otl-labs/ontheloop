"""session_tree_rollup (rec-5257) over the golden chain: per-tree compaction counts, workflows and merge links.

Module-level importorskip and full tier: every producer golden tree and verification tree is run through the real producer
and append kernel into an in-memory telemetry catalog, then read back through the verb and compared with
scripts.telemetry_verify.derive_usage.
"""

from __future__ import annotations

from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from scripts.telemetry_verify import derive_usage  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, case_names  # noqa: E402

VERB = "session_tree_rollup"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_COMPACT_TREE = "corpus:hooks_resume_and_compact"
_SUBAGENT_TREE = "verification:subagent_nonfinal_midstream"
_SHA_A = "a" * 40
_SHA_B = "b" * 40


def _verb(con: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, VERB, **chain.DAY)


def _oracle(con: Any) -> list[dict[str, Any]]:
    return derive_usage.session_tree_rollup(chain.stored_rows(con), {PROJECT: "real"}, **chain.DAY)


def _link(con: Any, n: int, sha: str, at: str, **replace: Any) -> None:
    chain.clone_row(
        con,
        "telemetry_sessions",
        PROJECT,
        {
            "event_id": f"01ARZ3NDEKTSV4RRFFQ69H1F{n:02d}",
            "event_kind": "annotate",
            "producer": "ci_annotate",
            "external_ref": f"root-ref:merge:{sha}#0/annotate",
            "event_timestamp": at,
            "ci_outcome": "success",
            "pr_url": f"https://example.test/pr/{n}",
            "rec_ids": [f"rec-{n}"],
            **replace,
        },
        where="parent_session_id IS NULL AND event_kind = 'open'",
    )


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_verb_matches_oracle_on_every_golden_tree(tree: str) -> None:
    for final in [True, False]:
        with chain.loaded(tree, final) as (con, _, _):
            assert chain.canonical(_verb(con)) == chain.canonical(_oracle(con)), (tree, final)


def test_tree_rollup_counts_compact_events() -> None:
    with chain.loaded(_COMPACT_TREE, True) as (con, rows, _):
        produced = [r for r in rows["telemetry_sessions"] if r["event_kind"] == "compact"]
        (row,) = _verb(con)
        assert row["compact_count"] == len(produced) > 0 and row["compacted_session_count"] == 1
        assert row["merge_links"] == [] and "tokens_input_total" not in row
        chain.clone_row(con, "telemetry_sessions", PROJECT, {"parser_version": 99}, where="event_kind = 'compact'")
        assert _verb(con)[0]["compact_count"] == row["compact_count"]


def test_merge_links_carry_the_sha_and_link_columns() -> None:
    with chain.loaded(_COMPACT_TREE, True) as (con, _, _):
        _link(con, 1, _SHA_B, "2026-01-02T00:00:00Z")
        _link(con, 2, _SHA_A, "2026-01-01T12:00:00Z")
        (row,) = _verb(con)
        links = row["merge_links"]
        assert [link["merge_sha"] for link in links] == [_SHA_A, _SHA_B]
        assert links[0]["rec_ids"] == ["rec-2"] and links[0]["ci_outcome"] == "success"
        assert links[1]["pr_url"] == "https://example.test/pr/1"
        assert chain.canonical(_verb(con)) == chain.canonical(_oracle(con))


def test_workflows_list_every_tree_session() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, ids):
        chain.clone_row(
            con,
            "telemetry_sessions",
            PROJECT,
            {
                "session_id": "01ARZ3NDEKTSV4RRFFQ69GDF02",
                "event_id": "01ARZ3NDEKTSV4RRFFQ69GEF02",
                "workflow": "plan-critique",
            },
            where="parent_session_id IS NOT NULL",
        )
        (row,) = _verb(con)
        assert "plan-critique" in row["workflows"] and row["workflows"] == sorted(row["workflows"])
        assert row["session_count"] >= 3 and row["root_session_id"] == ids["root"]
        assert chain.canonical(_verb(con)) == chain.canonical(_oracle(con))
