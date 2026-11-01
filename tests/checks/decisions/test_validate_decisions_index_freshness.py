"""Tests for validate_decisions_index_freshness() -- the DCG-08 drift/absence gate.

Mirrors tests/checks/deps/test_validate_dependency_graph_freshness.py's patch-seam pattern,
but this check's absence behavior is the OPPOSITE of validate_dependency_graph_freshness: the
decisions index is a REQUIRED committed artifact (project alignment fork 2), so a missing file FAILS
rather than no-ops.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.decisions.validate_decisions_index_freshness import (
    validate_decisions_index_freshness,
)
from scripts.decisions_index import build_index


class TestDecisionsIndexFreshness:
    def test_fails_when_export_is_absent(self, tmp_path: Path) -> None:
        """Absence is itself a failure -- the opposite of the dependency-graph precedent."""
        missing = tmp_path / "nonexistent.json"
        with patch("scripts.decisions_index._EXPORT_PATH", missing):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        assert len(failed) == 1
        msg = failed[0].lower()
        assert "missing" in msg or "regenerate" in msg

    def test_fails_when_export_is_a_hand_edit(self, tmp_path: Path) -> None:
        """A hand edit to the committed index (drift from the live corpus) fails."""
        export_path = tmp_path / "decisions-index.json"
        tampered_entry = {
            "number": 1,
            "title": "Tampered",
            "status": "Decided",
            "decided_date": "",
            "supersedes": [],
            "superseded_by": None,
            "amends": [],
        }
        tampered = {
            "decisions": [tampered_entry],
            "metadata": {"generated_by": "scripts.decisions_index"},
        }
        export_path.write_text(json.dumps(tampered), encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        assert len(failed) == 1
        msg = failed[0].lower()
        assert "stale" in msg or "drift" in msg or "decisions index" in msg

    def test_passes_when_export_matches_a_freshly_regenerated_tree(self, tmp_path: Path) -> None:
        """A freshly written, correctly-regenerated index passes cleanly."""
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(build_index(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        assert not failed

    def test_fails_when_export_is_unreadable_json(self, tmp_path: Path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text("not valid json {{{", encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        assert len(failed) == 1


class TestPassLineIsGatedOnTheFailureDelta:
    """The `if len(failed) == before:` guard's PASS line is asserted from both sides: it appears
    exactly when the delegate added nothing."""

    def test_pass_line_is_printed_when_the_index_is_current(self, tmp_path: Path, capsys) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(build_index(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        assert failed == []
        assert "PASS: decisions index is current." in capsys.readouterr().out

    def test_pass_line_is_withheld_when_the_index_is_stale(self, tmp_path: Path, capsys) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps({"decisions": [], "metadata": {}}), encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            validate_decisions_index_freshness(failed)
        out = capsys.readouterr().out
        assert failed != []
        assert "PASS: decisions index is current." not in out


_UNIT = "decisions"
_SYNTHETIC_INDEX = {
    "decisions": [
        {"number": n, "title": f"D{n}", "status": "Decided", "decided_date": "", "supersedes": [], "amends": []}
        for n in (1, 2, 3)
    ],
    "metadata": {"generated_by": "scripts.decisions_index"},
}


def _declared(export_path: Path, index: dict | None = None) -> tuple[list[str], registry._Declaration | None]:
    registry.pop_declaration()
    failed: list[str] = []
    with patch("scripts.decisions_index._EXPORT_PATH", export_path):
        if index is None:
            validate_decisions_index_freshness(failed)
        else:
            with patch("scripts.decisions_index.build_index", return_value=index):
                validate_decisions_index_freshness(failed)
    return failed, registry.pop_declaration()


class TestExaminedDeclaration:
    """Decision 170: the check declares examined over the regenerated decision entries compared."""

    def test_current_export_declares_examined_over_the_regenerated_entries(self, tmp_path: Path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(_SYNTHETIC_INDEX), encoding="utf-8")
        failed, decl = _declared(export_path, _SYNTHETIC_INDEX)
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count, decl.unit) == ("examined", 3, _UNIT)
        outcome = registry.build_outcome("validate_decisions_index_freshness", "check", decl, appended_to_failed=False)
        assert outcome.status == "enforced"

    def test_stale_export_counts_the_regenerated_side_not_the_committed_side(self, tmp_path: Path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps({"decisions": [], "metadata": {}}), encoding="utf-8")
        failed, decl = _declared(export_path, _SYNTHETIC_INDEX)
        assert len(failed) == 1
        assert decl is not None
        assert (decl.kind, decl.count, decl.unit) == ("examined", 3, _UNIT)
        outcome = registry.build_outcome("validate_decisions_index_freshness", "check", decl, appended_to_failed=True)
        assert outcome.status == "failed"

    def test_empty_corpus_declares_a_vacuous_domain(self, tmp_path: Path) -> None:
        empty = {"decisions": [], "metadata": {"generated_by": "scripts.decisions_index"}}
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(empty), encoding="utf-8")
        failed, decl = _declared(export_path, empty)
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 0)
        outcome = registry.build_outcome("validate_decisions_index_freshness", "check", decl, appended_to_failed=False)
        assert outcome.status == "vacuous"

    def test_live_tree_declares_the_full_regenerated_corpus(self, tmp_path: Path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(build_index(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        failed, decl = _declared(export_path)
        assert failed == []
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == len(build_index()["decisions"]) > 0

    def test_absent_export_fails_without_a_declaration(self, tmp_path: Path) -> None:
        failed, decl = _declared(tmp_path / "nonexistent.json", _SYNTHETIC_INDEX)
        assert len(failed) == 1
        assert decl is None

    def test_unreadable_export_fails_without_a_declaration(self, tmp_path: Path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text("not valid json {{{", encoding="utf-8")
        failed, decl = _declared(export_path, _SYNTHETIC_INDEX)
        assert len(failed) == 1
        assert decl is None
