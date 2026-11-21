"""tool_use_rollup (rec-4702) over the golden chain: tool-call counts by tree, agent, tool name and MCP server.

Module-level importorskip and full tier; the verb is compared with scripts.telemetry_verify.derive_usage on every golden
tree and on hand-built tool calls (an MCP tool, an errored close, an attribution key).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from scripts.telemetry_verify import derive_usage  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT, case_names  # noqa: E402

VERB = "tool_use_rollup"
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
_SUBAGENT_TREE = "verification:subagent_nonfinal_midstream"
_TOOL = "observation_type = 'tool_call' AND name = 'Bash'"
_MCP = "mcp__github__get_file_contents"


def _passes(tree: str) -> list[bool]:
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


def _verb(con: Any) -> list[dict[str, Any]]:
    return chain.run_verb(con, VERB, **chain.DAY)


def _call(
    con: Any, n: int, *, attribution: str | None, outcome: str | None, with_key: bool = True, project: str = PROJECT
) -> None:
    """One MCP tool_call observation: an open row (metadata as the producer writes it) and, with an outcome, its close."""
    obs = f"01ARZ3NDEKTSV4RRFFQ69G7F{n:02d}"
    meta = {"caller_type": "direct", **({"attribution_mcp_server": attribution} if with_key else {})}
    chain.clone_row(
        con,
        "telemetry_observations",
        project,
        {"event_id": f"01ARZ3NDEKTSV4RRFFQ69G8F{n:02d}", "observation_id": obs, "name": _MCP, "metadata": json.dumps(meta)},
        where=f"{_TOOL} AND event_kind = 'open'",
    )
    if outcome is not None:
        _close(con, n, outcome, project=project)


def _close(con: Any, n: int, outcome: str, *, project: str = PROJECT, at: datetime | None = None) -> None:
    replace: dict[str, Any] = {
        "event_id": f"01ARZ3NDEKTSV4RRFFQ69G9{outcome[0].upper()}{n:02d}",
        "observation_id": f"01ARZ3NDEKTSV4RRFFQ69G7F{n:02d}",
        "name": _MCP,
        "outcome": outcome,
    }
    if at is not None:
        replace["event_timestamp"] = at
    chain.clone_row(con, "telemetry_observations", project, replace, where=f"{_TOOL} AND event_kind = 'close'")


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_verb_matches_oracle_on_every_golden_tree(tree: str) -> None:
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, _):
            oracle = derive_usage.tool_use_rollup(rows, {PROJECT: "real"}, **chain.DAY)
            assert chain.canonical(_verb(con)) == chain.canonical(oracle), (tree, final)


def test_tool_use_counts_by_name_and_server_across_sessions() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        second = chain.register_project(con, 7)
        chain.append_batches(con, chain.hold_back(chain.emit(_SUBAGENT_TREE, True)), project=second)
        plain = {(r["is_root"], r["agent_name"], r["tool_name"]): r for r in _verb(con)}
        assert {r["call_count"] for r in plain.values() if r["tool_name"] == "Bash"} == {2}
        assert len(_verb(con)) == 4 and all(r["mcp_server"] is None for r in plain.values())
        _call(con, 1, attribution="github", outcome="error")
        _call(con, 2, attribution=None, outcome=None, with_key=False)
        _call(con, 3, attribution="github", outcome="blocked")
        _call(con, 4, attribution="github", outcome="success", project=second[0])
        mcp = [r for r in _verb(con) if r["tool_name"] == _MCP]
        assert sorted(((r["mcp_server"], r["attribution_mcp_server"], r["call_count"]) for r in mcp), key=str) == [
            ("github", "github", 1),
            ("github", "github", 2),
            ("github", None, 1),
        ]
        first_tree = [r for r in mcp if r["call_count"] != 1 or r["attribution_mcp_server"] is None]
        by_attribution = {r["attribution_mcp_server"]: r for r in first_tree}
        assert (by_attribution["github"]["error_count"], by_attribution["github"]["blocked_count"]) == (1, 1)
        assert by_attribution[None]["error_count"] == 0 and by_attribution["github"]["interrupted_count"] == 0
        assert {r["session_count"] for r in mcp} == {1}
        projects = {PROJECT: "real", second[0]: "real"}
        oracle = derive_usage.tool_use_rollup(chain.stored_rows(con), projects, **chain.DAY)
        assert chain.canonical(_verb(con)) == chain.canonical(oracle)


def test_counts_are_per_observation_and_split_by_the_current_close() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        _call(con, 5, attribution=None, outcome="error")
        _close(con, 5, "interrupted", at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc))
        (row,) = (r for r in _verb(con) if r["tool_name"] == _MCP)
        assert (row["call_count"], row["error_count"], row["interrupted_count"]) == (1, 0, 1)


_EMPTY = hashlib.sha256(b"").hexdigest()
_UNSET: Any = object()
_SRC = {
    "sub": ("ca1#0/tool_call_open", "ca1#0", "cr1#0/tool_call_close"),
    "root": ("a1#0/tool_call_open", "a1#0", "r1#0/tool_call_close"),
}


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _rid(kind: str, n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ{kind}{n:06d}"


def _repeat_call(
    con: Any,
    n: int,
    name: str,
    *,
    digest: str | None,
    out: str | None = None,
    out_bytes: int | None = 3,
    session: str = "sub",
    ordinal: Any = _UNSET,
    attribution: str | None = None,
) -> None:
    """One hand-built call (open, tool_input row, close) of tool *name*; a clone never links to the source call's input."""
    open_ref, input_ref, close_ref = _SRC[session]
    ref = f"rep{n}#0"
    obs = _rid("C", n)
    meta = json.dumps({"caller_type": "direct", "attribution_mcp_server": attribution})
    opened = {
        "event_id": _rid("A", n),
        "observation_id": obs,
        "external_ref": f"{ref}/tool_call_open",
        "name": name,
        "metadata": meta,
    }
    if ordinal is not _UNSET:
        opened["source_ordinal"] = ordinal
    chain.clone_row(con, "telemetry_observations", PROJECT, opened, where=f"external_ref = '{open_ref}'")
    if digest is not None:
        replace = {"event_id": _rid("D", n), "transcript_id": _rid("E", n), "external_ref": ref, "content_sha256": digest}
        chain.clone_row(con, "telemetry_transcripts", PROJECT, replace, where=f"external_ref = '{input_ref}'")
    closed = {
        "event_id": _rid("B", n),
        "observation_id": obs,
        "external_ref": f"{ref}/tool_call_close",
        "name": name,
        "output_sha256": out,
        "output_bytes": out_bytes,
    }
    chain.clone_row(con, "telemetry_observations", PROJECT, closed, where=f"external_ref = '{close_ref}'")


def _row(con: Any, name: str, **match: Any) -> dict[str, Any]:
    (row,) = (r for r in _verb(con) if r["tool_name"] == name and all(r[k] == v for k, v in match.items()))
    return row


def _counts(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (row["repeat_input_count"], row["repeat_output_count"], row["repeat_output_bytes"])


def _agrees(con: Any) -> None:
    oracle = derive_usage.tool_use_rollup(chain.stored_rows(con), {PROJECT: "real"}, **chain.DAY)
    assert chain.canonical(_verb(con)) == chain.canonical(oracle)


def test_repeat_input_count_same_tool_same_session() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        assert _counts(_row(con, "Bash")) == (1, 1, 3)
        for n, label in enumerate(("a", "a", "b"), start=1):
            _repeat_call(con, n, "RepIn", digest=_sha(label), out=None)
        row = _row(con, "RepIn")
        assert (row["call_count"], _counts(row)) == (3, (1, 0, 0))
        _repeat_call(con, 4, "RepIn", digest=_sha("a"), out=None)
        assert _counts(_row(con, "RepIn")) == (2, 0, 0)
        _agrees(con)


def test_repeat_output_count_and_bytes_on_latest_close() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        for n, (label, size) in enumerate((("x", 5), ("x", 5), ("z", 9)), start=10):
            _repeat_call(con, n, "RepOut", digest=_sha(f"in{n}"), out=_sha(label), out_bytes=size)
        assert _counts(_row(con, "RepOut")) == (0, 1, 5)
        later = {
            "event_id": _rid("F", 11),
            "event_timestamp": datetime(2026, 1, 1, 0, 9, tzinfo=timezone.utc),
            "output_sha256": _sha("z"),
            "output_bytes": 9,
        }
        chain.clone_row(
            con,
            "telemetry_observations",
            PROJECT,
            {**later, "observation_id": _rid("C", 11), "external_ref": "rep11#0/x"},
            where="external_ref = 'cr1#0/tool_call_close'",
        )
        assert _counts(_row(con, "RepOut")) == (0, 1, 9)
        _agrees(con)


def test_empty_withheld_and_cross_session_inputs_never_repeat() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        for n in (20, 21):
            _repeat_call(con, n, "RepWithheld", digest=_EMPTY, out=None)
        for n in (22, 23):
            _repeat_call(con, n, "RepUnlinked", digest=None, out=None)
        _repeat_call(con, 24, "RepShared", digest=_sha("shared"), out=_sha("shared-out"))
        _repeat_call(con, 25, "RepShared", digest=_sha("shared"), out=_sha("shared-out"), session="root")
        for n, size in ((26, 0), (27, 4), (28, None), (29, 4)):
            _repeat_call(con, n, "RepZero", digest=None, out=_sha("w"), out_bytes=size)
        rows = _verb(con)
        for name in ("RepWithheld", "RepUnlinked"):
            assert [_counts(r) for r in rows if r["tool_name"] == name] == [(0, 0, 0)]
        assert [_counts(r) for r in rows if r["tool_name"] == "RepShared"] == [(0, 0, 0), (0, 0, 0)]
        assert _counts(_row(con, "RepZero")) == (0, 1, 4)
        assert all(isinstance(v, int) for r in rows for v in _counts(r))
        _agrees(con)


def test_repeat_order_places_null_ordinal_first() -> None:
    with chain.loaded(_SUBAGENT_TREE, True) as (con, _, _):
        _repeat_call(con, 30, "RepOrd", digest=_sha("o"), out=None, attribution="late")
        _repeat_call(con, 31, "RepOrd", digest=_sha("o"), out=None, attribution="early", ordinal=None)
        assert _counts(_row(con, "RepOrd", attribution_mcp_server="early"))[0] == 0
        assert _counts(_row(con, "RepOrd", attribution_mcp_server="late"))[0] == 1
        _agrees(con)
