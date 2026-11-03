"""Mirror tests for scripts/checks/contracts/_glossary_stock.py (Decision 131), on tmp_path fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.contracts import _glossary_stock as gs

_NOTE = '> **Update (DATE):** Where "platform" or "product" here names a side, read it through the rows.'
_DATA: dict[str, Any] = {
    "read_as": {"note": _NOTE, "noted_decisions": [27], "records": ["docs/DECISIONS.md", "docs/plans/"]},
    "relative_words": {
        "identifiers": "agent-platform-*",
        "platform": {"say_instead": "On The Loop"},
        "product": {"say_instead": "adopter product"},
    },
}


def _entry(number: int, extra: str = "") -> str:
    return f"## Decision {number}: Older (Decided)\n\n**Decision:** older.\n\n{extra}---\n"


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def test_note_pattern_matches_any_iso_date_and_only_the_whole_line() -> None:
    pattern = gs.note_pattern(_NOTE)
    assert pattern.search(_NOTE.replace("DATE", "2026-11-02"))
    assert pattern.search("text\n" + _NOTE.replace("DATE", "2031-01-31") + "\nmore")
    assert not pattern.search(_NOTE)
    assert not pattern.search(_NOTE.replace("DATE", "soon"))
    assert not pattern.search(_NOTE.replace("DATE", "2026-11-02") + " extra")


def test_unnoted_passes_when_the_entry_carries_the_note(repo: Path) -> None:
    _write(repo, "docs/DECISIONS.md", "# D\n\n" + _entry(27, _NOTE.replace("DATE", "2026-11-02") + "\n\n"))
    assert gs.unnoted(repo, _DATA) == []


def test_unnoted_reports_a_missing_note(repo: Path) -> None:
    _write(repo, "docs/DECISIONS.md", "# D\n\n" + _entry(27))
    findings = gs.unnoted(repo, _DATA)
    assert len(findings) == 1 and "Decision 27 lacks" in findings[0] and "drop its number" in findings[0]


def test_unnoted_does_not_borrow_a_neighbours_note(repo: Path) -> None:
    text = "# D\n\n" + _entry(27) + "\n" + _entry(28, _NOTE.replace("DATE", "2026-11-02") + "\n\n")
    _write(repo, "docs/DECISIONS.md", text)
    assert len(gs.unnoted(repo, _DATA)) == 1


def test_unnoted_reports_a_decision_neither_file_holds(repo: Path) -> None:
    _write(repo, "docs/DECISIONS.md", "# D\n\n" + _entry(28))
    findings = gs.unnoted(repo, _DATA)
    assert len(findings) == 1 and "neither docs/DECISIONS.md nor docs/DECISIONS_ARCHIVE.md" in findings[0]


def test_unnoted_reports_when_no_decision_file_exists(repo: Path) -> None:
    assert len(gs.unnoted(repo, _DATA)) == 1


def test_unnoted_accepts_a_note_held_only_in_the_archive(repo: Path) -> None:
    _write(repo, "docs/DECISIONS.md", "# D\n")
    _write(repo, "docs/DECISIONS_ARCHIVE.md", "# A\n\n" + _entry(27, _NOTE.replace("DATE", "2026-11-02") + "\n\n"))
    assert gs.unnoted(repo, _DATA) == []


_HISTORY = (
    "key: the platform\n"  # 1
    "amendment_log:\n"  # 2
    "  - date: x\n"  # 3
    "    summary: >-\n"  # 4
    "      the platform\n"  # 5
    "      moved\n"  # 6
    "  - date: y\n"  # 7
    "    summary: a platform row\n"  # 8
    "after: the platform\n"  # 9
)


def test_history_lines_cover_the_block_value_from_first_item_to_last_scalar() -> None:
    assert gs.history_lines(_HISTORY) == frozenset(range(3, 9))


def test_history_lines_stop_before_a_key_following_a_block_scalar() -> None:
    text = "amendment_log:\n  - date: x\n    summary: >-\n      moved the platform\nnext: the platform\n"
    assert gs.history_lines(text) == frozenset({2, 3, 4})


def test_history_lines_leave_a_trailing_comment_and_following_key_scanned() -> None:
    text = "amendment_log:\n  - date: x\n    summary: a row\n# the platform\nnext: the platform\n"
    assert gs.history_lines(text) == frozenset({2, 3})


def test_history_lines_find_a_nested_field_level_log() -> None:
    text = "counts:\n  amendment_log:\n    - date: x\n      summary: s\n  other: v\n"
    assert gs.history_lines(text) == frozenset({3, 4})


def test_history_lines_cover_a_one_line_flow_sequence() -> None:
    text = "a: 1\namendment_log: [{date: x, summary: s}]\nb: 2\n"
    assert gs.history_lines(text) == frozenset({2})


def test_history_lines_handle_a_nested_sequence_item_and_a_block_scalar_at_end_of_file() -> None:
    text = "list:\n  - amendment_log:\n      - date: x\n        summary: >-\n          last\n"
    assert gs.history_lines(text) == frozenset({3, 4, 5})


def test_history_lines_handle_empty_values() -> None:
    assert gs.history_lines("amendment_log:\nb: 1\n") == frozenset()
    assert gs.history_lines("amendment_log: []\nb: 1\n") == frozenset({1})
    assert gs.history_lines("amendment_log:\n  - {}\nb: 1\n") == frozenset({2})


def test_history_lines_are_empty_for_a_file_that_does_not_compose_or_has_no_log() -> None:
    assert gs.history_lines("terms: [unclosed\n") == frozenset()
    assert gs.history_lines("") == frozenset()
    assert gs.history_lines("a: 1\n") == frozenset()


def _plan_fn(*lines: str) -> Any:
    return lambda text: text in lines


def _live(repo: Path, in_plan: Any = None, specs: list[str] | None = None) -> tuple[list[str], int]:
    return gs.live_hits(repo, _DATA, specs or [":(glob)docs/**/*.yaml", ":(glob)*.md"], in_plan or _plan_fn())


def test_live_hits_report_an_unqualified_word_with_path_and_line(repo: Path) -> None:
    _write(repo, "docs/c.yaml", "a: 1\nnote: the platform runs\n")
    hits, scanned = _live(repo)
    assert scanned == 1
    assert hits == [
        'docs/c.yaml:2: unqualified "platform" -- say instead: On The Loop, or quote it if it is not the relative word'
    ]


def test_live_hits_pass_clean_text_and_count_files(repo: Path) -> None:
    _write(repo, "docs/c.yaml", "a: 1\n")
    _write(repo, "README.md", "# clean\n")
    assert _live(repo) == ([], 2)


def test_live_hits_skip_records(repo: Path) -> None:
    _write(repo, "docs/DECISIONS.md", "the platform\n")
    _write(repo, "docs/plans/PLAN-x.yaml", "note: the platform\n")
    _write(repo, "docs/c.yaml", "a: 1\n")
    hits, scanned = _live(repo, specs=[":(glob)docs/**/*.md", ":(glob)docs/**/*.yaml"])
    assert hits == [] and scanned == 1


def test_live_hits_skip_amendment_log_rows_but_not_the_rest_of_the_file(repo: Path) -> None:
    _write(repo, "docs/c.yaml", _HISTORY)
    hits, _ = _live(repo)
    assert [h.split(": ")[0] for h in hits] == ["docs/c.yaml:1", "docs/c.yaml:9"]


def test_live_hits_do_not_apply_amendment_log_exemption_to_markdown(repo: Path) -> None:
    _write(repo, "README.md", "amendment_log:\n  - the platform\n")
    hits, _ = _live(repo)
    assert len(hits) == 1


def test_live_hits_skip_a_line_the_plan_carries(repo: Path) -> None:
    _write(repo, "docs/c.yaml", "note: the platform runs\nother: the product ships\n")
    hits, _ = _live(repo, in_plan=_plan_fn("note: the platform runs"))
    assert [h.split(": ")[0] for h in hits] == ["docs/c.yaml:2"]


def test_live_hits_scan_untracked_and_tracked_files(repo: Path) -> None:
    _write(repo, "docs/a.yaml", "a: the platform\n")
    _git(repo, "add", "-A")
    _write(repo, "docs/b.yaml", "b: the product\n")
    hits, scanned = _live(repo)
    assert scanned == 2 and len(hits) == 2


def test_live_hits_scan_a_file_that_does_not_compose_whole(repo: Path) -> None:
    _write(repo, "docs/c.yaml", "terms: [unclosed\namendment_log: the platform\n")
    hits, _ = _live(repo)
    assert len(hits) == 1


def test_live_hits_skip_an_unreadable_tracked_entry(repo: Path) -> None:
    (repo / "docs").mkdir()
    (repo / "docs/dangling.yaml").symlink_to(repo / "docs/missing.yaml")
    _git(repo, "add", "-A")
    assert _live(repo) == ([], 0)


def test_live_hits_are_empty_when_git_cannot_list(tmp_path: Path) -> None:
    assert _live(tmp_path) == ([], 0)
