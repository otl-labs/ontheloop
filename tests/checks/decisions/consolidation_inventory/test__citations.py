"""Mirror of _citations.py: anchored and bare citations of a victim across the searched file kinds."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks.decisions.consolidation_inventory import _citations
from scripts.checks.decisions.consolidation_inventory._model import Gate

FORMS = [
    "Decision 9001 point 2",
    "Decision 9001 pt 3",
    "Decision 9001 pt3",
    "Decision 9001 cl.4",
    "Decision 9001 clause 5",
    "Decisions 9001 clauses 6",
    "per 9001 pt 7",
]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _hits(root: Path, include: bool = False):
    return _citations.scan(root, (9001,), Gate(root), include)


@pytest.mark.parametrize("form", FORMS)
def test_every_anchored_form_is_found(tmp_path: Path, form: str) -> None:
    _write(tmp_path, "AGENTS.md", f"See {form} for the rule.\n")
    anchored = [h for h in _hits(tmp_path) if h.kind == "anchored_citation"]
    assert [h.locator for h in anchored] == ["AGENTS.md:1"]
    assert not [h for h in _hits(tmp_path) if h.kind == "citation"]


def test_each_searched_file_kind_is_read(tmp_path: Path) -> None:
    text = "Decision 9001 pt 2\n"
    for rel in ("docs/contracts/c.yaml", "AGENTS.md", "docs/CLAUDE.md", ".claude/skills/s/SKILL.md", "docs/DECISIONS.md"):
        _write(tmp_path, rel, text)
    _write(tmp_path, "scripts/m.py", 'X = "Decision 9001 pt 2"\n')
    _write(tmp_path, "src/m.py", 'X = """\nDecision 9001 pt 2\n"""\n')
    _write(tmp_path, "notes/other.md", text)
    found = {h.locator.split(":")[0] for h in _hits(tmp_path) if h.kind == "anchored_citation"}
    assert found == {
        "docs/contracts/c.yaml", "AGENTS.md", "docs/CLAUDE.md", ".claude/skills/s/SKILL.md", "docs/DECISIONS.md",
        "scripts/m.py", "src/m.py",
    }  # fmt: skip


def test_comments_are_ignored_and_string_constants_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/m.py", '# Decision 9001 pt 2 in a comment\nNAME = "Decision 9001 cl.3"\n')
    anchored = [h for h in _hits(tmp_path) if h.kind == "anchored_citation"]
    assert [h.locator for h in anchored] == ["scripts/m.py:2"]


def test_bare_citations_count_per_file_unless_included(tmp_path: Path) -> None:
    _write(
        tmp_path, "AGENTS.md", "Decision 9001 here.\nDecision-9001 and dec-9001 and Decisions 5, 9001.\nDecision 90010 no.\n"
    )
    counted = [h for h in _hits(tmp_path) if h.kind == "citation"]
    assert len(counted) == 1 and counted[0].locator == "AGENTS.md" and counted[0].evidence[0].detail.startswith("4 citations")
    each = [h for h in _hits(tmp_path, True) if h.kind == "citation"]
    assert sorted(h.locator for h in each) == ["AGENTS.md:1", "AGENTS.md:2", "AGENTS.md:2", "AGENTS.md:2"]


def test_the_victims_own_log_sections_never_cite_themselves(tmp_path: Path) -> None:
    log = (
        "## Decision 9001: Victim (Decided)\n\nSelf-cite Decision 9001 pt 2.\n\n"
        "## Decision 9002: Other (Decided)\n\nSee Decision 9001 pt 4.\n"
    )
    _write(tmp_path, "docs/DECISIONS.md", log)
    anchored = [h for h in _hits(tmp_path) if h.kind == "anchored_citation"]
    assert [h.locator for h in anchored] == ["docs/DECISIONS.md:7"]
    assert all(h.gated is False for h in anchored)  # no approval-routing map in the fixture


def test_unparseable_and_skipped_code_files_never_raise(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/bad.py", 'X = "Decision 9001 pt 2"\ndef broken(:\n')
    _write(tmp_path, "scripts/__pycache__/cached.py", 'X = "Decision 9001 pt 2"\n')
    _write(tmp_path, "scripts/plain.py", "X = 1\n")
    assert _hits(tmp_path) == []
