"""Tests for the amendment_log span locator and splice (rec-5452)."""

from __future__ import annotations

from scripts.checks.contracts import _amendment_splice as sp

DOC = (
    "a: 1\n"
    "amendment_log:\n"
    "  - date: x\n"
    "    semantic_break: false\n"
    "    change_class: c\n"
    "    items:\n"
    "      - one\n"
    "    summary: s\n"
    "\n"
    "  - {date: y, semantic_break: false,\n"
    "     change_class: c, summary: t\n"
    "  }\n"
    "# trailing\n"
    "b: 2\n"
)


def test_locate_log_spans_cover_content_only() -> None:
    spans = sp.locate_log(DOC)
    assert spans is not None
    assert [(s.first, s.last, s.refusal) for s in spans] == [(2, 7, None), (9, 11, None)]
    assert spans[0].texts == ("x", "false", "c") and spans[1].indent == "  "


def test_splice_replaces_only_the_chosen_range() -> None:
    spans = sp.locate_log(DOC)
    assert spans is not None
    out = sp.splice(DOC, {1: spans[1]}, {1: 'say "hi" \\ ok'})
    assert out.splitlines()[9] == '  - {date: y, semantic_break: false, change_class: c, summary: "say \\"hi\\" \\\\ ok"}'
    assert out.endswith("# trailing\nb: 2\n")
    assert sp.lines_match(DOC, out, [spans[1]])
    assert not sp.lines_match(DOC, out, [spans[0]])
    assert not sp.lines_match(DOC, out.replace("b: 2", "b: 3"), [spans[1]])
    assert not sp.lines_match(DOC, out + "x\n", [spans[1]])
    assert not sp.lines_match(DOC, "", [spans[1]])


def test_locate_log_none_and_refusals() -> None:
    assert sp.locate_log("a: [oops") is None
    assert sp.locate_log("- a\n- b\n") is None
    assert sp.locate_log("a: 1\n") is None
    assert sp.locate_log("amendment_log: nope\n") is None
    flow_seq = sp.locate_log("amendment_log: [{date: a}, {date: b}]\n")
    assert flow_seq is not None and {s.refusal for s in flow_seq} == {"span_comment"}
    scalar_row = sp.locate_log("amendment_log:\n  - text\n")
    assert scalar_row is not None and scalar_row[0].refusal == "malformed"
    comment = sp.locate_log("amendment_log:\n  - date: a\n    # c\n    summary: s\n")
    assert comment is not None and comment[0].refusal == "span_comment"
    shared = sp.locate_log(
        "amendment_log:\n  - {date: a, semantic_break: false, change_class: c, summary: s}\n  - {date: b}\n"
    )
    assert shared is not None and shared[0].refusal is None and shared[1].refusal == "malformed"


def test_split_lines_matches_yaml_line_breaks() -> None:
    assert sp.split_lines("a\r\nb\rc\nd") == ["a\r\n", "b\r", "c\n", "d"]
    assert sp.split_lines("") == []
