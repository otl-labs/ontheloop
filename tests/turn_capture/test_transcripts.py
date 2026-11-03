"""Transcript rows: purpose/origin per block, scrub before hash, empty and binary blocks, model-visible tool results,
tool_output rows, raw-identity omissions and MCP overflow."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter

import pytest

from src.turn_capture import transcripts
from src.turn_capture.cursor import CaptureCursor, SourceMutated
from src.turn_capture.render import render_rows_json
from src.turn_capture.streams import ROOT
from src.turn_capture.transcript import MemTree
from src.turn_capture.transcripts import Sidecars, build_transcripts, canonical_json, clean, text_of
from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    build_secret,
    make_ctx,
    make_files,
    parse_records,
    prompt,
    rows_of,
    text_block,
    thinking_block,
    tool_result,
    tool_use_block,
)

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}}


class GuardedTree(MemTree):
    """A MemTree that fails the test if a sidecar named in *forbidden* is ever read whole."""

    def __init__(self, files, forbidden=()):
        super().__init__(files)
        self.forbidden = set(forbidden)

    def sidecar(self, basename):
        assert basename not in self.forbidden, f"{basename} was materialised whole"
        return super().sidecar(basename)


def build(root, sidecar_files=None, cursor=None, diag=None, cap=None, forbidden=()):
    files = make_files(root, None, sidecar_files)
    tree = GuardedTree(files, forbidden)
    from src.turn_capture.streams import parse_tree

    side = Sidecars(tree, cursor)
    ctx = make_ctx() if cap is None else dataclasses.replace(make_ctx(), full_output_cap_bytes=cap)
    rows = build_transcripts(parse_tree(tree, None, True), ctx, side, diag if diag is not None else Counter())
    return rows_of(rows), side


def by(rows, **match):
    return [r for r in rows if all(r[k] == v for k, v in match.items())]


def test_scrubbed_before_hash_empty_and_binary_blocks() -> None:
    secret = build_secret("github")
    root = [
        prompt("u1", 1, "p1", [text_block(f"token {secret}"), text_block(""), IMG, 7]),
        assistant(
            "a1",
            2,
            "m1",
            [thinking_block(""), thinking_block("hmm"), text_block("hi"), tool_use_block("t1", "Bash", {"k": "v"})],
        ),
        tool_result("r1", 3, "t1", [IMG, text_block("caption")], pid="p1"),
    ]
    rows, _ = build(root)
    scrubbed = "token [REDACTED:GITHUB_TOKEN]"
    first = by(rows, external_ref="u1#0")[0]
    assert first["content"] == scrubbed and first["content_sha256"] == hashlib.sha256(scrubbed.encode()).hexdigest()
    assert first["content_bytes"] == len(scrubbed.encode()) and secret not in render_rows_json(rows)
    assert by(rows, external_ref="u1#1") == [] and by(rows, external_ref="u1#3") == []
    stub = json.loads(by(rows, external_ref="u1#2")[0]["content"])
    assert stub == {"bytes": 5, "media_type": "image/png", "sha256": hashlib.sha256(b"hello").hexdigest(), "type": "image"}
    assert by(rows, external_ref="a1#0") == []
    assert [by(rows, external_ref=f"a1#{i}")[0]["purpose"] for i in (1, 2, 3)] == ["thinking", "response", "tool_input"]
    result = by(rows, purpose="tool_result")[0]
    assert json.loads(result["content"])[0]["type"] == "image" and result["origin"] == "tool"


def test_purpose_origin_owner_and_columns() -> None:
    root = [
        prompt("u1", 1, "p1", "meta", isMeta=True),
        prompt("u2", 2, "p1", "human words"),
        prompt("u3", 3, "p1", "reminder", sourceToolUseID="x"),
        assistant("a1", 4, "m1", [tool_use_block("t1", "Bash", {"b": 2, "a": 1})], model="claude-x"),
        tool_result("r1", 5, "t1", "out", pid="p1"),
    ]
    rows, _ = build(root)
    meta, human, companion = (by(rows, external_ref=f"u{i}#0")[0] for i in (1, 2, 3))
    assert (meta["purpose"], meta["origin"]) == ("system", "harness") and (human["purpose"], human["origin"]) == (
        "prompt",
        "human",
    )
    assert (companion["purpose"], companion["origin"]) == ("system", "harness")
    assert all(r["observation_ref"] == f"{SID}/p1" for r in (meta, human, companion))
    tool_input = by(rows, purpose="tool_input")[0]
    assert (
        tool_input["content"] == '{"a":1,"b":2}'
        and tool_input["observation_ref"] == "m1"
        and tool_input["model"] == "claude-x"
    )
    result = by(rows, purpose="tool_result")[0]
    assert (result["observation_ref"], result["origin"], result["model"], result["event_kind"]) == (
        "t1",
        "tool",
        None,
        "point",
    )
    assert (
        result["entity_ref"] == result["external_ref"] == "r1#0"
        and result["content_uri"] is None
        and result["content_truncated"] is False
    )
    assert result["source_ordinal"] == 4 and result["session_ref"] == SID and result["producer"] == "claude_code"


def test_tool_result_stores_model_visible_text() -> None:
    info = {"persistedOutputPath": "/elsewhere/tool-results/big.txt", "persistedOutputSize": 4}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r1", 3, "t1", "preview of the output", pid="p1", tur=info),
    ]
    rows, _ = build(root, {"big.txt": "FULL OUTPUT"})
    result = by(rows, purpose="tool_result")[0]
    assert result["content"] == "preview of the output" and result["content_truncated"] is False
    assert result["external_ref"] == "r1#0" and result["origin"] == "tool"
    absent, _ = build(root, None)
    assert by(absent, purpose="tool_result")[0]["content"] == "preview of the output"
    assert by(absent, purpose="tool_output") == [] and all(r["content_truncated"] is False for r in absent)


def test_tool_output_row_or_omission_for_persisted_output() -> None:
    info = {"persistedOutputPath": "/elsewhere/tool-results/big.txt", "persistedOutputSize": 4}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1",
            2,
            "m1",
            [tool_use_block("t1", "Bash", {}), tool_use_block("t2", "Bash", {}), tool_use_block("t3", "Bash", {})],
        ),
        tool_result("r1", 3, "t1", "preview", pid="p1", tur=info),
        tool_result("r2", 3.1, "t2", "preview2", pid="p1", tur={**info, "persistedOutputPath": "dir/"}),
        tool_result("r3", 3.2, "t3", "", pid="p1", tur={"persistedOutputPath": "/x/tool-results/none.txt"}),
    ]
    diag: Counter[str] = Counter()
    rows, side = build(root, {"big.txt": "FULL OUTPUT"}, diag=diag)
    full = by(rows, external_ref="r1#0/full")[0]
    assert (full["purpose"], full["origin"], full["content"]) == ("tool_output", "tool", "FULL OUTPUT")
    assert full["entity_ref"] == "r1#0/full" and full["observation_ref"] == "t1" and full["content_truncated"] is False
    assert full["content_sha256"] == hashlib.sha256(b"FULL OUTPUT").hexdigest() and full["content_bytes"] == 11
    assert diag["sidecar_size_mismatch"] == 1
    assert by(rows, external_ref="r2#0")[0]["content"] == "preview2" and by(rows, external_ref="r2#0/full") == []
    assert by(rows, external_ref="r3#0") == [] and by(rows, external_ref="r3#0/full") == []
    assert side.used["big.txt"] == hashlib.sha256(b"FULL OUTPUT").hexdigest() and side.used["none.txt"] is None
    over, _ = build(root, {"big.txt": "x" * 40}, cap=30)
    omitted = by(over, external_ref="r1#0/full")[0]
    assert "content" not in omitted and omitted["content_omitted_reason"] == "oversize"
    assert omitted["content_sha256"] == hashlib.sha256(b"x" * 40).hexdigest() and omitted["content_bytes"] == 40
    assert omitted["content_truncated"] is False and omitted["purpose"] == "tool_output"


def test_over_cap_sidecar_is_never_materialised() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r1", 3, "t1", "preview", pid="p1", tur={"persistedOutputPath": "/x/tool-results/huge.txt"}),
    ]
    huge = "y" * 200_000
    rows, side = build(root, {"huge.txt": huge}, cap=100_000, forbidden=("huge.txt",))
    omitted = by(rows, external_ref="r1#0/full")[0]
    assert omitted["content_bytes"] == 200_000 and omitted["content_sha256"] == hashlib.sha256(huge.encode()).hexdigest()
    assert side.output("huge.txt", 100_000).digest.lines == 1 and side.used["huge.txt"] == omitted["content_sha256"]


def test_decode_growth_over_cap_is_omitted() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r1", 3, "t1", "preview", pid="p1", tur={"persistedOutputPath": "/x/tool-results/grow.txt"}),
    ]
    secret = build_secret("github")
    body = f"token {secret} " * 3
    rows, _ = build(root, {"grow.txt": body}, cap=len(body.encode()) - 30)
    row = by(rows, external_ref="r1#0/full")[0]
    assert row["content_omitted_reason"] == "oversize" and "content" not in row
    assert row["content_bytes"] == len(body.encode()) and row["content_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    fits, _ = build(root, {"grow.txt": body}, cap=len(body.encode()))
    assert "[REDACTED:GITHUB_TOKEN]" in by(fits, external_ref="r1#0/full")[0]["content"]
    assert secret not in render_rows_json(rows) + render_rows_json(fits)


MCP_TEXT = (
    "Error: result (9 characters) exceeds maximum allowed tokens. "
    "Output has been saved to /x/tool-results/mcp.txt\nFormat: json"
)


def test_mcp_overflow_output_is_captured() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "mcp__s__t", {})]),
        tool_result("r1", 3, "t1", MCP_TEXT, pid="p1", tur=MCP_TEXT, is_error=True),
    ]
    diag: Counter[str] = Counter()
    rows, _ = build(root, {"mcp.txt": '[{"big": true}]'}, diag=diag)
    visible = by(rows, external_ref="r1#0")[0]
    assert (visible["origin"], visible["content"], visible["content_truncated"]) == ("harness", MCP_TEXT, False)
    full = by(rows, external_ref="r1#0/full")[0]
    assert (full["purpose"], full["origin"], full["content"]) == ("tool_output", "tool", '[{"big": true}]')
    assert diag["unmatched_string_tool_use_result"] == 0
    missing, _ = build(root)
    assert by(missing, external_ref="r1#0/full") == [] and by(missing, external_ref="r1#0")[0]["origin"] == "harness"


def test_unmatched_string_tool_use_result_is_counted() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {}), tool_use_block("t2", "Bash", {})]),
        tool_result("r1", 3, "t1", "Error: no such file", pid="p1", tur="Error: no such file", is_error=True),
        tool_result("r2", 3.1, "t2", "x", pid="p1", tur="Output has been saved to dir/"),
    ]
    diag: Counter[str] = Counter()
    rows, _ = build(root, {}, diag=diag)
    assert diag["unmatched_string_tool_use_result"] == 2 and by(rows, purpose="tool_output") == []
    assert all(r["origin"] == "tool" for r in by(rows, purpose="tool_result"))


def _cursor(sidecars):
    return CaptureCursor("claude_code", 1, SID, "p", "fixed_non_rollover_allowance", 1, sidecars=sidecars)


def test_sidecar_pins_are_honoured_and_contradictions_raise() -> None:
    info = {"persistedOutputPath": "/x/tool-results/big.txt"}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r1", 3, "t1", "preview", pid="p1", tur=info),
    ]
    sha = hashlib.sha256(b"FULL").hexdigest()
    rows, _ = build(root, {"big.txt": "FULL"}, cursor=_cursor({"big.txt": sha}))
    assert by(rows, purpose="tool_output")[0]["content"] == "FULL"
    with pytest.raises(SourceMutated):
        build(root, {"big.txt": "CHANGED"}, cursor=_cursor({"big.txt": sha}))
    with pytest.raises(SourceMutated):
        build(root, None, cursor=_cursor({"big.txt": sha}))
    honoured, _ = build(root, {"big.txt": "FULL"}, cursor=_cursor({"big.txt": None}))
    assert by(honoured, purpose="tool_output") == [] and by(honoured, purpose="tool_result")[0]["content"] == "preview"


def test_unknown_blocks_and_helpers(monkeypatch) -> None:
    diag: Counter[str] = Counter()
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [{"type": "server_tool_use"}, {"type": "text", "text": None}, "junk"]),
    ]
    rows, _ = build(root, diag=diag)
    assert (
        diag["unknown_block_type:server_tool_use"] == 1
        and diag["unknown_block_type:None"] == 1
        and by(rows, purpose="response") == []
    )
    assert text_of(None) == "" and text_of("x") == "x" and text_of([text_block("a"), text_block("b")]) == "a\nb"
    assert text_of([]) == "" and json.loads(text_of([text_block("a"), {"type": "tool_reference"}]))[1] == {
        "type": "tool_reference"
    }
    assert text_of({"k": 1}) == '{"k":1}' and canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    assert clean("a\ud800b") == "a?b"
    assert transcripts._stub("x") == "x" and transcripts._stub({"type": "text"}) == {"type": "text"}
    bad = transcripts._stub({"type": "image", "source": {"data": "!!!not base64!!!"}})
    assert bad["bytes"] == len("!!!not base64!!!") and transcripts._stub({"type": "image"})["bytes"] == 0
    assert transcripts._stub({"type": "document", "source": {"data": "aGk=", "media_type": "x"}})["bytes"] == 2
    monkeypatch.setattr(transcripts, "HARNESS_INTERRUPT_SENTINELS", ("[Request interrupted]",))
    assert transcripts.is_sentinel("[Request interrupted]") and not transcripts.is_sentinel("other")
    monkeypatch.setattr(transcripts, "HARNESS_DENIAL_PREFIXES", ("Denied:",))
    assert transcripts.is_sentinel("Denied: tool")
    sentinel_root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r1", 3, "t1", "Denied: x", pid="p1"),
    ]
    assert by(build(sentinel_root)[0], purpose="tool_result")[0]["origin"] == "harness"


def test_open_turns_emit_no_transcript_rows() -> None:
    tree = parse_records(
        [prompt("u1", 1, "p1", "a"), assistant("a1", 2, "m1", [text_block("x")]), prompt("u2", 3, "p2", "b")], final=False
    )
    rows = build_transcripts(tree, make_ctx(), Sidecars(MemTree(make_files([prompt("u1", 1, "p1", "a")])), None), Counter())
    assert [r["external_ref"] for r in rows] == ["u1#0", "a1#0"] and ROOT in tree.streams


def test_orphan_and_late_tool_results() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r0", 3, "ghost", "orphan text", pid="p1"),
        prompt("u2", 4, "p2", "next"),
        tool_result("rl", 5, "t1", "late text", pid="p2"),
    ]
    rows, _ = build(root)
    orphan = by(rows, external_ref="r0#0")[0]
    assert (orphan["purpose"], orphan["observation_ref"], orphan["content"]) == ("tool_result", "ghost", "orphan text")
    assert by(rows, external_ref="rl#0") == []


def test_sidecar_vanishing_between_size_and_read_is_unavailable() -> None:
    class Vanishing(MemTree):
        def sidecar(self, basename):
            return None

    tree = Vanishing(make_files([prompt("u1", 1, "p1", "go")], None, {"gone.txt": "abc"}))
    side = Sidecars(tree, None)
    assert side.output("gone.txt", 100) is None and side.used["gone.txt"] is None


def test_harness_origins_and_api_error_system_row() -> None:
    interrupt = "[Request interrupted by user for tool use]"
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {}), tool_use_block("t2", "Bash", {})]),
        tool_result(
            "r1",
            3,
            "t1",
            "rejected text",
            pid="p1",
            is_error=True,
            tur="User rejected tool use",
            toolDenialKind="user-rejected",
        ),
        tool_result("r2", 3.1, "t2", interrupt, pid="p1", is_error=True),
        prompt("u2", 4, "p1", [text_block(interrupt)]),
        prompt("u3", 5, "p2", "hello again"),
        assistant(
            "e1",
            6,
            "00000000-0000-4000-8000-0000000000e1",
            [text_block("API Error: x")],
            model="<synthetic>",
            isApiErrorMessage=True,
        ),
        prompt("u4", 7, "p3", "last"),
    ]
    diag: Counter[str] = Counter()
    rows, _ = build(root, diag=diag)
    results = {r["external_ref"]: r for r in by(rows, purpose="tool_result")}
    assert results["r1#0"]["origin"] == "harness" and results["r2#0"]["origin"] == "harness"
    sentinel = by(rows, external_ref="u2#0")[0]
    assert (sentinel["purpose"], sentinel["origin"]) == ("system", "harness")
    assert by(rows, external_ref="u3#0")[0]["origin"] == "human"
    error = by(rows, external_ref="e1#0")[0]
    assert (error["purpose"], error["origin"], error["observation_ref"]) == ("system", "harness", f"{SID}/p2")
    assert "model" not in error and by(rows, purpose="response") == []
    assert diag["unmatched_string_tool_use_result"] == 0


def test_denial_kind_exempts_a_string_tool_use_result_from_the_unmatched_counter() -> None:
    diag: Counter[str] = Counter()
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {}), tool_use_block("t2", "Bash", {})]),
        tool_result("r1", 3, "t1", "x", pid="p1", is_error=True, tur="User rejected tool use", toolDenialKind="user-rejected"),
        tool_result("r2", 3.1, "t2", "y", pid="p1", is_error=True, tur="Error: other"),
        prompt("u2", 5, "p2", "next"),
    ]
    build(root, diag=diag)
    assert diag["unmatched_string_tool_use_result"] == 1
