"""Tests for the amendment_log compaction tool (rec-5452, Decision 275 cl.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.contracts import amendment_compact as ac

CFG: dict[str, Any] = {
    "keep_full": 2,
    "terse_row": {"keys": ["date", "semantic_break", "change_class", "summary"], "summary_max_chars": 160},
    "exempt_files": ["docs/contracts/north-star.yaml"],
}
LONG = "wording that runs on for a good while so the row is longer than the terse cap " * 3


def block(date: str, slug: str, rec: int, extra: str = "", sb: str = "false") -> str:
    return (
        f'  - date: "{date}"\n    semantic_break: {sb}\n    change_class: field_add\n    summary: >-\n'
        f"      PLAN-{slug} (rec-{rec}): {LONG}\n      Decision {rec} lands.\n{extra}"
    )


def flow(date: str, slug: str, rec: int, extra: str = "") -> str:
    return (
        f'  - {{date: "{date}", semantic_break: false, change_class: field_add, summary: "PLAN-{slug} (rec-{rec}): {LONG}"'
        f"{extra}}}\n"
    )


def make(tmp_path: Path, rows: list[str], name: str = "c.yaml", tail: str = "") -> Path:
    path = tmp_path / name
    head = "# header comment\ncontract:\n  id: c   # keep\n\n# about the log\namendment_log:\n"
    path.write_text(head + "".join(rows) + tail, encoding="utf-8")
    return path


TAIL = "\n# next key comment\nother:\n  k: 1\n"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "load_retention", lambda root=None: CFG)


def run(*args: str) -> int:
    return ac.main([*args])


def test_block_and_flow_rows_compact_to_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        block("2026-11-09", "n1", 11),
        block("2026-11-08", "n2", 12),
        block("2026-11-02", "old-b", 13),
        flow("2026-11-01", "old-f", 14),
    ]
    path = make(tmp_path, rows)
    assert run("--write", str(path)) == 0
    out = path.read_text(encoding="utf-8").splitlines()
    assert (
        '  - {date: "2026-11-02", semantic_break: false, change_class: field_add, summary: "PLAN-old-b (rec-13); Decision 13"}'
        in out
    )
    assert '  - {date: "2026-11-01", semantic_break: false, change_class: field_add, summary: "PLAN-old-f (rec-14)"}' in out
    log = yaml.safe_load(path.read_text(encoding="utf-8"))["amendment_log"]
    assert [r["date"] for r in log] == ["2026-11-09", "2026-11-08", "2026-11-02", "2026-11-01"]
    assert all(set(r) == set(CFG["terse_row"]["keys"]) for r in log[2:])
    assert "compactable=2" in capsys.readouterr().out


def test_non_log_bytes_and_row_count_unchanged(tmp_path: Path) -> None:
    rows = [block("2026-11-09", "n1", 11), block("2026-11-08", "n2", 12), block("2026-11-02", "old", 13)]
    path = make(tmp_path, rows, tail=TAIL)
    before = path.read_text(encoding="utf-8")
    assert run("--write", str(path)) == 0
    after = path.read_text(encoding="utf-8")
    assert after.endswith("\n\n# next key comment\nother:\n  k: 1\n")
    assert after.startswith("# header comment\ncontract:\n  id: c   # keep\n\n# about the log\namendment_log:\n")
    assert after.splitlines()[: -0 or None].count("") == before.splitlines().count("")
    assert len(yaml.safe_load(after)["amendment_log"]) == len(yaml.safe_load(before)["amendment_log"]) == 3
    assert yaml.safe_load(after)["other"] == {"k": 1}
    assert len(after.splitlines()) == len(before.splitlines()) - 5


def test_newest_keep_full_kept_by_date_not_position(tmp_path: Path) -> None:
    dates = ["2026-11-03", "2026-11-09", "2026-11-01", "2026-11-07", "2026-11-05", "2026-11-02"]
    path = make(tmp_path, [block(d, f"r{i}", 20 + i) for i, d in enumerate(dates)])
    assert run("--write", str(path)) == 0
    log = yaml.safe_load(path.read_text(encoding="utf-8"))["amendment_log"]
    full = {r["date"] for r in log if len(r) == 4 and len(r["summary"]) > 160}
    assert full == {"2026-11-09", "2026-11-07"}
    assert {r["date"] for r in log if len(r["summary"]) <= 160} == set(dates) - full


def test_refusals_are_listed_not_applied(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        block("2026-11-20", "k1", 1),
        block("2026-11-19", "k2", 2),
        block("2026-11-10", "extra", 3, extra='    migration_story: "m"\n'),
        f'  - date: "2026-11-09"\n    semantic_break: false\n    change_class: x\n    summary: "{LONG}"\n',
        flow("2026-11-08", "over", 4).replace(f"(rec-4): {LONG}", "(" + ", ".join(f"rec-{n}" for n in range(100, 140)) + ")"),
        block("2026-11-07", "cmt", 5).replace("    summary: >-", "    # inline note\n    summary: >-"),
        block("2026-11-06", "brk", 6, sb="true"),
        block("2026-11-05", "ok", 7),
    ]
    path = make(tmp_path, rows)
    exempt = make(
        tmp_path,
        [block("2026-11-01", "e1", 8)] * 0 + [block(f"2026-10-0{n}", f"e{n}", 30 + n) for n in range(1, 5)],
        "north-star.yaml",
    )
    exempt_before = exempt.read_bytes()
    assert run(str(path), str(exempt)) == 0
    out = capsys.readouterr().out
    assert "compactable=1 refused=4[extra_keys=1,no_pointer=1,overflow=1,span_comment=1]" in out
    assert "north-star.yaml: skipped (exempt)" in out
    assert run("--write", str(path), str(exempt)) == 0
    text = path.read_text(encoding="utf-8")
    assert "migration_story" in text and "# inline note" in text and "semantic_break: true" in text
    assert "PLAN-ok (rec-7); Decision 7" in text
    assert exempt.read_bytes() == exempt_before


def test_second_run_is_a_no_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 40 + n) for n in range(1, 9)], tail=TAIL)
    assert run("--write", str(path)) == 0
    once = path.read_bytes()
    capsys.readouterr()
    assert run("--write", str(path)) == 0
    assert path.read_bytes() == once
    assert "TOTAL files=1 compactable=0" in capsys.readouterr().out


def test_rows_caps_compactions(tmp_path: Path) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 50 + n) for n in range(1, 8)])
    assert run("--write", "--rows", "2", str(path)) == 0
    log = yaml.safe_load(path.read_text(encoding="utf-8"))["amendment_log"]
    assert [len(r["summary"]) <= 160 for r in log] == [True, True, False, False, False, False, False]
    assert run("--check", str(path)) == 1


def test_check_exits_1_while_compactable_rows_remain(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 60 + n) for n in range(1, 5)])
    assert run("--check", str(path)) == 1
    assert run("--write", str(path)) == 0
    assert run("--check", str(path)) == 0
    only_refused = make(
        tmp_path,
        [
            block("2026-11-09", "a", 1),
            block("2026-11-08", "b", 2),
            block("2026-11-01", "x", 3, extra='    migration_story: "m"\n'),
        ],
        "r.yaml",
    )
    capsys.readouterr()
    assert run("--check", str(only_refused)) == 0
    out = capsys.readouterr().out
    assert "extra_keys=1" in out and "  refused row 2 date=2026-11-01: extra_keys" in out
    assert not list(tmp_path.glob("*.tmp"))


def test_unreadable_and_malformed_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    assert run(str(bad)) == 1
    assert "unreadable" in capsys.readouterr().out
    assert run(str(tmp_path / "missing.yaml")) == 1
    nonlist = tmp_path / "nl.yaml"
    nonlist.write_text("amendment_log: nope\n", encoding="utf-8")
    assert run(str(nonlist)) == 0
    assert "skipped=1" in capsys.readouterr().out
    odd = make(
        tmp_path,
        [
            block("2026-11-09", "a", 1),
            block("2026-11-08", "b", 2),
            "  - just text\n",
            '  - {date: "2026-11-01", summary: "PLAN-x"}\n',
            '  - {date: "2026-11-02", semantic_break: false, change_class: x, summary: 7}\n',
        ],
        "odd.yaml",
    )
    assert run(str(odd)) == 0
    assert "malformed=3" in capsys.readouterr().out
    before = odd.read_bytes()
    monkeypatch.setattr(ac, "load_retention", lambda root=None: None)
    assert run("--write", str(odd)) == 1
    assert "config_unreadable" in capsys.readouterr().out
    assert odd.read_bytes() == before
    monkeypatch.setattr(ac, "load_retention", lambda root=None: CFG)
    scratch = make(tmp_path / "..", [block(f"2026-11-0{n}", f"p{n}", n) for n in range(1, 6)], "north-star.yaml")
    assert run(str(scratch.resolve())) == 0
    assert "skipped (exempt)" in capsys.readouterr().out


def test_usage_errors_exit_2() -> None:
    for args in (["--write", "--check"], ["--rows", "0"]):
        with pytest.raises(SystemExit) as exc:
            run(*args)
        assert exc.value.code == 2


def test_proof_failure_refuses_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 70 + n) for n in range(1, 6)], tail=TAIL)
    before = path.read_bytes()
    real = ac.splice_rows

    def evil(text: str, spans: Any, summaries: Any) -> str:
        return real(text, spans, summaries).replace("other:", "othEr:")

    monkeypatch.setattr(ac, "splice_rows", evil)
    assert run("--write", str(path)) == 1
    assert "proof_failed" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_dry_run_reports_lines_and_bytes_freed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 80 + n) for n in range(1, 5)])
    before = path.read_bytes()
    assert run(str(path)) == 0
    out = capsys.readouterr().out
    assert path.read_bytes() == before
    assert "compactable=2" in out and "lines_freed=10 " in out
    assert out.strip().splitlines()[-1].startswith("TOTAL files=1 compactable=2 refused=0 lines_freed=10 bytes_freed=")


def test_crlf_and_selected_rows_helper(tmp_path: Path) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 90 + n) for n in range(1, 5)])
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    text = path.read_bytes().decode("utf-8")
    assert ac.selected_rows(text, CFG) == [0, 1]
    assert run("--write", str(path)) == 0
    assert b"\r\n" in path.read_bytes() and b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    assert ac.selected_rows("a: [oops", CFG) == []


def test_inline_comment_and_anchor_rows_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    anchored = block("2026-11-06", "anc", 6).replace("    change_class: field_add", "    change_class: &cc field_add", 1)
    inline = block("2026-11-05", "inl", 7).replace("change_class: field_add", "change_class: field_add  # why", 1)
    path = make(
        tmp_path, [block("2026-11-09", "a", 1), block("2026-11-08", "b", 2), anchored, inline, block("2026-11-04", "fine", 8)]
    )
    assert run("--write", str(path)) == 0
    out = capsys.readouterr().out
    assert "compactable=1 refused=2[malformed=1,span_comment=1]" in out
    text = path.read_text(encoding="utf-8")
    assert "&cc" in text and "# why" in text and "PLAN-fine (rec-8); Decision 8" in text


@pytest.mark.parametrize(
    ("mutation", "expect"),
    [
        (lambda t: t.replace("PLAN-p1 (rec-71); Decision 71", "PLAN-p1 (rec-71)"), "proof (b): row 0: pointer tokens dropped"),
        (
            lambda t: t.replace('summary: "PLAN-p1 (rec-71); Decision 71"}', "summary: ["),
            "proof (b): the output does not parse",
        ),
    ],
)
def test_proof_b_failures_refuse_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mutation: Any, expect: str
) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 70 + n) for n in range(1, 8)], tail=TAIL)
    before = path.read_bytes()
    real = ac.splice_rows
    monkeypatch.setattr(ac, "splice_rows", lambda text, spans, summaries: mutation(real(text, spans, summaries)))
    assert run("--write", str(path)) == 1
    assert expect in capsys.readouterr().out
    assert path.read_bytes() == before


def test_span_failure_marks_every_row_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", n) for n in range(1, 6)])
    monkeypatch.setattr(ac, "locate_log", lambda text: None)
    assert run(str(path)) == 0
    assert "compactable=0 refused=3[malformed=3]" in capsys.readouterr().out


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
    spans = ac.locate_log(DOC)
    assert spans is not None
    assert [(s.first, s.last, s.refusal) for s in spans] == [(2, 7, None), (9, 11, None)]
    assert spans[0].texts == ("x", "false", "c") and spans[1].indent == "  "


def test_splice_replaces_only_the_chosen_range() -> None:
    spans = ac.locate_log(DOC)
    assert spans is not None
    out = ac.splice_rows(DOC, {1: spans[1]}, {1: 'say "hi" \\ ok'})
    assert out.splitlines()[9] == '  - {date: y, semantic_break: false, change_class: c, summary: "say \\"hi\\" \\\\ ok"}'
    assert out.endswith("# trailing\nb: 2\n")
    assert ac.lines_match(DOC, out, [spans[1]])
    assert not ac.lines_match(DOC, out, [spans[0]])
    assert not ac.lines_match(DOC, out.replace("b: 2", "b: 3"), [spans[1]])
    assert not ac.lines_match(DOC, out + "x\n", [spans[1]])
    assert not ac.lines_match(DOC, "", [spans[1]])


def test_locate_log_none_and_refusals() -> None:
    assert ac.locate_log("a: [oops") is None
    assert ac.locate_log("- a\n- b\n") is None
    assert ac.locate_log("a: 1\n") is None
    assert ac.locate_log("amendment_log: nope\n") is None
    flow_seq = ac.locate_log("amendment_log: [{date: a}, {date: b}]\n")
    assert flow_seq is not None and {s.refusal for s in flow_seq} == {"span_comment"}
    scalar_row = ac.locate_log("amendment_log:\n  - text\n")
    assert scalar_row is not None and scalar_row[0].refusal == "malformed"
    comment = ac.locate_log("amendment_log:\n  - date: a\n    # c\n    summary: s\n")
    assert comment is not None and comment[0].refusal == "span_comment"
    shared = ac.locate_log(
        "amendment_log:\n  - {date: a, semantic_break: false, change_class: c, summary: s}\n  - {date: b}\n"
    )
    assert shared is not None and shared[0].refusal is None and shared[1].refusal == "malformed"


def test_split_lines_matches_yaml_line_breaks() -> None:
    assert ac.split_lines("a\r\nb\rc\nd") == ["a\r\n", "b\r", "c\n", "d"]
    assert ac.split_lines("") == []


def test_prove_catches_content_and_untouched_row_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = make(tmp_path, [block(f"2026-11-0{n}", f"p{n}", 30 + n) for n in range(1, 5)], tail=TAIL).read_text(
        encoding="utf-8"
    )
    plan = ac._plan(text, CFG)
    assert plan is not None
    monkeypatch.setattr(ac, "lines_match", lambda old, new, spans: True)
    assert "outside the log" in str(ac._prove(text, text.replace("k: 1", "k: 2"), plan, [0], CFG, False))
    assert "untouched row 3" in str(ac._prove(text, text.replace("Decision 34", "Decision 35"), plan, [], CFG, False))
