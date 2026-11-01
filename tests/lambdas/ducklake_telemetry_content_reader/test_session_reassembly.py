"""session_transcript_masked over a real DuckDB catalog (full tier): every golden tree reassembles in the declared order."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

from scripts.telemetry_verify import decode_rows, read_tree  # noqa: E402
from scripts.telemetry_verify.dedupe import compute_generations, resolve_table  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_content_read_verbs import CONTENT_READS  # noqa: E402
from src.lambdas.ducklake_telemetry_content_reader import masked  # noqa: E402
from src.sensitive_values import mask  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import case_names  # noqa: E402

pytestmark = pytest.mark.unit

_ENTRY = CONTENT_READS["session_transcript_masked"]
_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [f"verification:{n}" for n in chain.verification.TREES]
_RANK = ("prompt", "system", "thinking", "response", "tool_input", "tool_result", "tool_output")


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["session_id"], row["producer"], row["event_id"], row["parser_version"])


def _pages(con: Any, session_id: str, size: int, include: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"session_id": session_id, "offset": offset, "max_rows": size}
        rows = ev.execute_entry(con, _ENTRY, params)
        (envelope,), _ = masked.serve_session(rows, ev.bind_values(_ENTRY, params), include, None)
        out.append(envelope)
        if envelope["next_offset"] is None:
            return out
        assert envelope["next_offset"] > offset
        offset = envelope["next_offset"]


def _block(ref: str | None) -> int:
    found = re.search(r"#([0-9]+)(/|$)", ref or "")
    return int(found.group(1)) if found else -1


def _expected_order(survivors: list[dict[str, Any]], root: str) -> list[tuple[Any, ...]]:
    first: dict[str, datetime] = {}
    for row in survivors:
        first[row["session_id"]] = min(first.get(row["session_id"], row["event_timestamp"]), row["event_timestamp"])

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        ref = row["external_ref"]
        purpose = _RANK.index(row["purpose"]) if row["purpose"] in _RANK else len(_RANK)
        ordinal = row["source_ordinal"] if row["source_ordinal"] is not None else -1
        return (
            row["session_id"] != root,
            first[row["session_id"]],
            row["session_id"],
            row["event_timestamp"],
            ordinal,
            _block(ref),
            (ref or "").endswith("/full"),
            purpose,
            row["producer"],
            row["event_id"],
            row["parser_version"],
        )

    return [_key(r) for r in sorted(survivors, key=sort_key)]


def _oracle(con: Any, session_id: str) -> tuple[list[dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]]]:
    tree = decode_rows(read_tree(lambda verb, **p: chain.run_verb(con, verb, **p), session_id))
    generations = compute_generations(tree.get("telemetry_sessions", []))
    survivors, _ = resolve_table("telemetry_transcripts", tree.get("telemetry_transcripts", []), generations)
    stored = {_key(r): r for r in chain.stored_rows(con)["telemetry_transcripts"]}
    return survivors, stored


@pytest.mark.parametrize("tree", _TREES)
def test_every_golden_tree_reassembles(tree: str) -> None:
    with chain.loaded(tree, True) as (con, _rows, ids):
        if not ids:
            return
        root = ids.get("root") or next(iter(ids.values()))
        survivors, stored = _oracle(con, root)
        want = _expected_order(survivors, root)
        whole = _pages(con, root, 2000)
        got = [(r["session_id"], r["producer"], r["event_id"], r["parser_version"]) for p in whole for r in p["rows"]]
        assert got == want
        assert want or tree != "corpus:close_cancelled_mid_stream"
        assert {p["total_rows"] for p in whole} == {len(want)} and all(not p["conflicts"] for p in whole)
        for page in whole:
            for r in page["rows"]:
                content = stored[(r["session_id"], r["producer"], r["event_id"], r["parser_version"])]["content"]
                assert r["text"] == (mask(content, "read_masked").text if content is not None else None)
        small = [
            (r["session_id"], r["producer"], r["event_id"], r["parser_version"])
            for p in _pages(con, root, 3)
            for r in p["rows"]
        ]
        assert small == want


def test_record_blocks_keep_source_order() -> None:
    with chain.loaded("corpus:close_cancelled_mid_stream", True) as (con, rows, ids):
        template = next(r for r in rows["telemetry_transcripts"] if r["session_id"] == ids["root"])
        at = template["event_timestamp"]
        base = "rec-order"
        planted = [
            ("01ARZ3NDEKTSV4RRFFQ69G5FA0", f"{base}#2", "response"),
            ("01ARZ3NDEKTSV4RRFFQ69G5FA1", f"{base}#1/full", "tool_output"),
            ("01ARZ3NDEKTSV4RRFFQ69G5FA2", f"{base}#1", "tool_result"),
            ("01ARZ3NDEKTSV4RRFFQ69G5FA3", f"{base}#0", "prompt"),
        ]
        new = [
            dict(
                template, event_id=eid, external_ref=ref, purpose=purpose, event_timestamp=at, source_ordinal=9000, content="x"
            )
            for eid, ref, purpose in planted
        ]
        db.insert_rows(con, "telemetry_transcripts", new)
        pages = _pages(con, ids["root"], 2)
        ours = [r["event_id"] for p in pages for r in p["rows"] if r["event_id"] in {e for e, _, _ in planted}]
        assert ours == [
            "01ARZ3NDEKTSV4RRFFQ69G5FA3",
            "01ARZ3NDEKTSV4RRFFQ69G5FA2",
            "01ARZ3NDEKTSV4RRFFQ69G5FA1",
            "01ARZ3NDEKTSV4RRFFQ69G5FA0",
        ]
        everything = [(r["session_id"], r["event_id"]) for p in pages for r in p["rows"]]
        for size in (1, 7):
            again = [(r["session_id"], r["event_id"]) for p in _pages(con, ids["root"], size) for r in p["rows"]]
            assert again == everything and len(set(again)) == len(again)


def test_tool_output_is_skipped_unless_included() -> None:
    with chain.loaded("corpus:close_cancelled_mid_stream", True) as (con, rows, ids):
        template = next(r for r in rows["telemetry_transcripts"] if r["session_id"] == ids["root"])
        db.insert_rows(
            con,
            "telemetry_transcripts",
            [
                dict(
                    template,
                    event_id="01ARZ3NDEKTSV4RRFFQ69G5FB0",
                    purpose="tool_output",
                    content="out",
                    external_ref="r#0/full",
                )
            ],
        )
        without = _pages(con, ids["root"], 2000, include=False)[0]
        with_all = _pages(con, ids["root"], 2000, include=True)[0]
        assert all(r["purpose"] != "tool_output" for r in without["rows"])
        assert len(with_all["rows"]) == len(without["rows"]) + 1
        assert without["total_rows"] == with_all["total_rows"] and without["next_offset"] is None
