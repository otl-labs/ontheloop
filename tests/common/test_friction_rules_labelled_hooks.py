"""rec-4959's acceptance: a hook-denied call in the cloud shape reads its hook label through the reader's friction SQL.

The tree corpus:hook_denial_cloud_shape runs through the real producer (record_turn), the real append kernel on an in-memory
DuckDB telemetry catalog and the session_friction_rollup verb. Module-level importorskip in the assignment form
scripts/verification_graduation.py recognises.
"""

from __future__ import annotations

from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402

TREE = "corpus:hook_denial_cloud_shape"


def _labels() -> dict[str, int]:
    with chain.loaded(TREE, True, "memory", None) as (con, _rows, ids):
        (row,) = chain.run_verb(con, "session_friction_rollup", session_id=ids["root"])
    counts: Any = row["label_counts"]
    return {item["label"]: item["n"] for item in counts}


def test_labelled_branch_policy_block_classifies_as_branch_policy_block() -> None:
    labels = _labels()
    assert labels.get("branch_policy_block") == 1
    assert "tool_blocked" not in labels


def test_legacy_text_block_classifies_as_scope_creep() -> None:
    assert _labels().get("scope_creep") == 1


def test_unnamed_block_reads_unmapped_block() -> None:
    labels = _labels()
    assert labels.get("unmapped_block") == 1 and sum(labels.values()) == 3
