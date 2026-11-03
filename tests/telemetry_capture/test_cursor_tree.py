"""Mirror tests for scripts/telemetry_capture/cursor_tree.py, duckdb-free."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from scripts.telemetry_capture.cursor_tree import CursorTree, SidecarMutated, call_refs, expected_rows
from scripts.telemetry_verify import ledger
from src.turn_capture.cursor import CaptureCursor
from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import MemTree
from tests.fixtures import telemetry_verification_trees as trees
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    case_cap,
    case_names,
    fork_boundary,
    load_case,
    mem_tree,
    root_lines,
    with_root,
)


def _cursor(lines: dict[str, int], sidecars: dict[str, str | None] | None = None) -> CaptureCursor:
    return CaptureCursor(
        "claude_code", 3, SID, "a/b", "fixed_non_rollover_allowance", 1, lines_consumed=lines, sidecars=sidecars or {}
    )


def test_cursor_tree_cuts_each_stream_and_honours_sidecar_pins() -> None:
    sha = hashlib.sha256(b"present body").hexdigest()
    files = {
        f"{SID}.jsonl": "r0\nr1\nr2\nr3\npartial",
        f"{SID}/subagents/agent-a1.jsonl": "a0\na1\n",
        f"{SID}/subagents/agent-a2.jsonl": "b0\n",
        f"{SID}/tool-results/absent.txt": "appeared later",
        f"{SID}/tool-results/present.txt": "present body",
        f"{SID}/tool-results/changed.txt": "edited body",
        f"{SID}/tool-results/free.txt": "unpinned",
    }
    tree = MemTree(files)
    pins = {"absent.txt": None, "present.txt": sha, "changed.txt": sha, "gone.txt": sha}
    view = CursorTree(tree, _cursor({"root": 2, "a1": 1}, pins))
    assert view.session_id == SID and view.agent_ids() == ["a1"], "only the streams the cursor holds"
    assert view.read("root") == b"r0\nr1\n" and view.read("a1") == b"a0\n"
    assert view.read("a2") is None, "a stream the cursor does not hold is no stream"
    assert CursorTree(tree, _cursor({"root": 9})).read("root") == b"r0\nr1\nr2\nr3\n", "a partial last line is never consumed"
    assert CursorTree(tree, _cursor({"root": 0})).read("root") == b""
    assert view.sidecar("absent.txt") is None and view.sidecar_size("absent.txt") is None
    assert list(view.sidecar_chunks("absent.txt")) == [], "pinned absent reads absent though it exists now"
    assert view.sidecar("present.txt") == b"present body" and view.sidecar_size("present.txt") == len(b"present body")
    assert b"".join(view.sidecar_chunks("present.txt")) == b"present body"
    assert view.sidecar("free.txt") == b"unpinned" and view.sidecar("nope.txt") is None
    with pytest.raises(SidecarMutated):
        view.sidecar("changed.txt")
    with pytest.raises(SidecarMutated):
        list(view.sidecar_chunks("changed.txt"))
    with pytest.raises(SidecarMutated):
        view.sidecar("gone.txt")
    with pytest.raises(SidecarMutated):
        view.sidecar_size("gone.txt")


def _emission(result: Any) -> dict[tuple[str, str], dict[str, Any]]:
    return {(table, row["external_ref"]): row for table, rows in result.batches for row in rows}


def test_expected_rows_are_the_full_emission_set_after_finalization() -> None:
    checked = 0
    for name in case_names():
        files, _ = load_case(name)
        lines = root_lines(files)
        cut = max(len(lines) // 2, fork_boundary(files))
        if cut >= len(lines):
            continue
        with case_cap(name):
            first = record_turn(
                mem_tree(with_root(files, "".join(lines[:cut]))), None, project_ref=PROJECT_REF, session_final=True
            )
            if first.next_cursor is None:
                continue
            second = record_turn(mem_tree(files), first.next_cursor, project_ref=PROJECT_REF, session_final=False)
            cursor = second.next_cursor
            assert cursor is not None and cursor.finalize_cuts, name
            union = {**_emission(first), **_emission(second)}
            assert not (set(_emission(first)) & set(_emission(second))), f"{name}: a resumed pass re-emits nothing"
            expected = _emission(expected_rows(CursorTree(mem_tree(files), cursor), cursor))
        assert expected == union, name
        checked += 1
    assert checked >= 10, "the finalize-then-resume equality was exercised over the corpus"


def test_call_refs_match_the_producer_model_call_refs() -> None:
    files = trees.resume_with_cost_state()
    tree = mem_tree(files)
    finding = ledger.reconcile_ledger(tree)[0]
    calls = ledger.prefix_calls(tree, finding.cut_line)
    message_ids = sorted(message for per_model in calls.values() for _, _, message in per_model)
    final = record_turn(tree, None, project_ref=PROJECT_REF, session_final=True)
    emitted = {row["external_ref"] for row in final.observations if row["observation_type"] == "model_call"}
    refs = call_refs(message_ids)
    assert refs and set(refs) <= emitted, "every counted call has a model_call row in the session_final emission"
    assert call_refs([]) == [] and call_refs(["m1"]) == ["m1/model_call"]
