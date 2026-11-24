"""Mirror of _pytest_scan.py: literal closure rules on synthetic test sources."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.checks.decisions.consolidation_inventory._model import Gate
from scripts.checks.decisions.consolidation_inventory._pytest_scan import literals, scan

HEADER = 'from pathlib import Path\nLOG = Path("docs") / "DECISIONS.md"\n'


def _scan(tmp_path: Path, source: str, victims=(9001,)):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text(source, encoding="utf-8")
    notes: list[dict[str, str]] = []
    hits = scan(tmp_path, tuple(victims), Gate(tmp_path), notes)
    return {h.locator.split("::", 1)[1]: h for h in hits}, notes


def test_literals_reads_ints_and_decision_strings() -> None:
    tree = ast.parse('X = (11, "Decisions 21, 22 and 23", "dec-031", "## Decision 41:", True, 1.5)')
    assert literals(tree) == {11, 21, 22, 23, 31, 41}


def test_module_constants_and_parametrize_lists_are_followed(tmp_path: Path) -> None:
    source = HEADER + (
        "import pytest\nPINNED = (9001, 5)\nOTHER = PINNED\n\n"
        "def test_via_constant():\n    assert OTHER and LOG\n\n"
        "@pytest.mark.parametrize('n', [9001, 2])\ndef test_param(n):\n    assert LOG and n\n"
    )
    found, _ = _scan(tmp_path, source)
    assert found["test_via_constant"].evidence[0].type == "dataflow_literal"
    assert found["test_param"].evidence[0].type == "dataflow_literal"


def test_helper_calls_and_class_attributes_are_followed(tmp_path: Path) -> None:
    source = HEADER + (
        "def _read():\n    return LOG.read_text()\n\ndef _num():\n    return 9001\n\n"
        "class TestK:\n    NUMBER = 9001\n\n    def test_attr(self):\n        assert self.NUMBER and _read()\n\n"
        "def test_helper():\n    assert _num() and _read()\n"
    )
    found, _ = _scan(tmp_path, source)
    assert found["TestK::test_attr"].tier == "probable"
    assert found["test_helper"].tier == "probable"


def test_cooccurrence_when_the_node_never_reaches_a_source(tmp_path: Path) -> None:
    source = HEADER + "def test_plain():\n    assert 9001\n\ndef test_other():\n    assert LOG and 5\n"
    found, _ = _scan(tmp_path, source)
    assert found["test_plain"].evidence[0].type == "cooccurrence"
    assert "test_other" not in found


def test_decision_strings_and_dec_ids_count(tmp_path: Path) -> None:
    source = HEADER + 'def test_text():\n    assert LOG and "Decisions 9000, 9001" and "dec-9002"\n'
    found, _ = _scan(tmp_path, source, victims=(9001, 9002))
    assert found["test_text"].victim in (9001, 9002)


def test_reader_imports_in_both_forms_count_as_sources(tmp_path: Path) -> None:
    source = (
        "from scripts import decisions_md\nfrom tests.fixtures import decision_entry\n\n"
        "def test_one():\n    assert decisions_md and 9001\n\ndef test_two():\n    assert decision_entry and 9001\n"
    )
    found, _ = _scan(tmp_path, source)
    assert found["test_one"].tier == "probable" and found["test_two"].tier == "probable"


def test_syntax_error_is_skipped_with_a_note(tmp_path: Path) -> None:
    found, notes = _scan(tmp_path, 'X = "DECISIONS.md"\ndef broken(:\n')
    assert found == {} and notes and "syntax error" in notes[0]["reason"]


def test_files_without_a_source_are_not_candidates(tmp_path: Path) -> None:
    found, _ = _scan(tmp_path, "def test_a():\n    assert 9001\n")
    assert found == {}


def test_import_forms_and_annotated_assignments(tmp_path: Path) -> None:
    source = (
        "from scripts.decisions_md import parse_decisions_md as parse\nimport scripts.checks._marker_guard as guard\n"
        "PIN: int = 9001\n\n"
        "def test_from_alias():\n    assert parse and PIN\n\ndef test_import_alias():\n    assert guard and PIN\n"
    )
    found, _ = _scan(tmp_path, source)
    assert found["test_from_alias"].tier == "probable" and found["test_import_alias"].tier == "probable"


def test_a_file_that_only_mentions_a_reader_in_a_comment_is_skipped(tmp_path: Path) -> None:
    found, _ = _scan(tmp_path, "# decision_entry fixtures are elsewhere\ndef test_a():\n    assert 9001\n")
    assert found == {}


def test_a_repository_without_tests_has_no_hits(tmp_path: Path) -> None:
    assert scan(tmp_path, (9001,), Gate(tmp_path), []) == []


def test_an_unreadable_test_file_is_a_note(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_dir.py").mkdir()
    notes: list[dict[str, str]] = []
    assert scan(tmp_path, (9001,), Gate(tmp_path), notes) == []
    assert notes and notes[0]["reason"].startswith("unreadable")
