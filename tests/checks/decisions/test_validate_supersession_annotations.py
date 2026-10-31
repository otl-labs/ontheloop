"""Tests for validate_supersession_annotations(). Mirror of
scripts/checks/decisions/validate_supersession_annotations.py (DPI-01/DPI-06/DPI-03-ext,
PLAN-decisions-supersession-guard). Synthetic tmp_path fixtures exercise every branch; a
non-vacuity class at the bottom runs the pure extractor and the full entrypoint against the REAL
repo files, asserting > 0 edges without asserting an absolute count (test-count-coupling
anti-pattern, validate_test_count_coupling)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.decisions.validate_supersession_annotations import (
    extract_supersession_edges,
    validate_supersession_annotations,
)
from scripts.decisions_md import parse_decisions_md

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_decisions(root: Path, live: str = "", archive: str | None = "") -> None:
    """Write docs/DECISIONS.md (always) and docs/DECISIONS_ARCHIVE.md (unless archive is None,
    which omits the file entirely -- the missing-file coverage case)."""
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "DECISIONS.md").write_text(live, encoding="utf-8")
    if archive is not None:
        (docs_dir / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")


def _write_waivers(
    root: Path,
    waivers: list[dict] | None = None,
    raw_text: str | None = None,
) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "decision_supersession_waivers.yaml"
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
        return
    data = {"waivers": waivers or []}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


class TestExtractSupersessionEdges:
    def test_extracts_superseder_victim_file_triple(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges == [(2, 1, "docs/DECISIONS.md")]

    def test_self_reference_is_skipped(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 9: Self-citing (Decided)\n\n**Decision:** amends Decision 9 point 1.\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges == []

    def test_repeated_mention_within_one_block_is_deduped(self, tmp_path: Path) -> None:
        """A header parenthetical AND body prose both saying 'amends Decision N' for the SAME
        edge must count once, not twice (avoids double-counting/duplicate FAIL lines)."""
        _write_decisions(
            tmp_path,
            live="## Decision 5: Something (amends Decision 3 clause 2) (Decided)\n\n"
            "**Decision:** This amends Decision 3 clause 2 as described above.\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges == [(5, 3, "docs/DECISIONS.md")]

    def test_missing_archive_file_is_tolerated(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n",
            archive=None,
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges == [(2, 1, "docs/DECISIONS.md")]

    def test_missing_archive_file_is_tolerated_end_to_end(self, tmp_path: Path) -> None:
        """Exercises the same missing-archive tolerance through the full entrypoint, so every
        helper that independently loops over (LIVE, ARCHIVE) -- not just the pure extractor --
        takes its missing-file branch."""
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded by Decision 2.\n\n"
            "**Warehouse ID:** dec-001\n",
            archive=None,
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []


class TestEnvelopeBorneEdges:
    """PLAN-decision-entry-flow-governance / Decision 167: envelope-declared amends/supersedes
    edges are unioned into extract_supersession_edges so an envelope-bearing entry cannot
    silently escape this guard just because it carries no textual "amends Decision N" prose."""

    def test_envelope_amends_edge_is_extracted(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 5: Envelope-only amends (Decided)\n\n"
            "```yaml\nnumber: 5\namends: [1]\n```\n\n**Decision:** No textual amends prose here.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert (5, 1, "docs/DECISIONS.md") in edges

    def test_envelope_supersedes_edge_is_extracted(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 5: Envelope-only supersedes (Decided)\n\n"
            "```yaml\nnumber: 5\nsupersedes: [1]\n```\n\n**Decision:** No textual prose here.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert (5, 1, "docs/DECISIONS.md") in edges

    def test_envelope_only_entry_without_annotation_fails_the_guard(self, tmp_path: Path) -> None:
        """The whole point: an envelope-declared edge with no forward pointer on the victim
        must not silently escape -- it fails exactly like the textual-prose form does."""
        _write_decisions(
            tmp_path,
            live="## Decision 5: Envelope amends, no annotation (Decided)\n\n"
            "```yaml\nnumber: 5\namends: [1]\n```\n\n**Decision:** No back-pointer named here at all.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n\n**Decision:** No back-pointer here.\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]

    def test_envelope_edge_with_annotation_passes(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 5: Envelope amends, annotated (Decided)\n\n"
            "```yaml\nnumber: 5\namends: [1]\n```\n\n**Decision:** No textual prose here.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded by Decision 5.\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []

    def test_envelope_self_reference_is_skipped(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 5: Self-citing envelope (Decided)\n\n```yaml\nnumber: 5\namends: [5]\n```\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges == []

    def test_envelope_and_textual_edge_for_the_same_pair_is_not_duplicated(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 5: Both forms (Decided)\n\n"
            "```yaml\nnumber: 5\namends: [1]\n```\n\n**Decision:** This also says amends Decision 1 in prose.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n",
        )
        edges = extract_supersession_edges(tmp_path)
        assert edges.count((5, 1, "docs/DECISIONS.md")) == 1

    def test_no_envelope_entry_is_unaffected(self, tmp_path: Path) -> None:
        """An entry with no envelope at all must not spuriously contribute edges."""
        _write_decisions(tmp_path, live="## Decision 5: No envelope (Decided)\n\n**Status:** Decided\n")
        assert extract_supersession_edges(tmp_path) == []

    def test_scalar_amends_does_not_crash_and_contributes_no_edge(self, tmp_path: Path) -> None:
        """Code-review follow-up: a malformed 'amends: 150' (scalar, not a list) is rejected as
        a malformed envelope at the shared extract_entry_envelope layer -- this extractor must
        not crash on list(envelope.get("amends") or []), and must contribute zero edges for it."""
        _write_decisions(
            tmp_path,
            live="## Decision 5: Malformed amends (Decided)\n\n```yaml\nnumber: 5\namends: 150\n```\n",
        )
        assert extract_supersession_edges(tmp_path) == []


class TestSupersessionAnnotationSubCheck:
    def test_unannotated_full_supersession_fails(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n\n**Decision:** No back-pointer here.\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]

    def test_plain_decision_mention_annotates(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded by Decision 2, see its rationale.\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []

    def test_superseded_by_header_annotates(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Superseded by Decision 2) (Decided)\n\n**Status:** Superseded\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []

    def test_mismatched_superseded_by_header_does_not_annotate(self, tmp_path: Path) -> None:
        """A '(Superseded by Decision N)' header pointing at a DIFFERENT decision than the real
        superseder must not satisfy the annotation test (proves the number comparison, not just
        pattern presence, is load-bearing)."""
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Superseded by Decision 99) (Decided)\n\n**Status:** Superseded\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]

    def test_waived_edge_passes(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded\n\n**Decision:** No back-pointer here.\n",
        )
        _write_waivers(tmp_path, waivers=[{"superseder": 2, "victim": 1, "reason": "test waiver"}])
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []

    def test_stale_waiver_warns_but_does_not_fail(self, tmp_path: Path, capsys) -> None:
        """A waived edge whose victim IS now annotated is a WARN (stale waiver), never a FAIL."""
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded by Decision 2.\n",
        )
        _write_waivers(tmp_path, waivers=[{"superseder": 2, "victim": 1, "reason": "now-stale waiver"}])
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []
        assert "stale" in capsys.readouterr().out.lower()


class TestDuplicateNumberSubCheck:
    def test_live_file_duplicate_number_fails(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 1: First (Decided)\n\n**Status:** Decided\n\n---\n\n"
            "## Decision 1: Duplicate (Decided)\n\n**Status:** Decided\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]

    def test_live_archive_pair_fails(self, tmp_path: Path, capsys) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 7: Live copy (Decided)\n\n**Status:** Decided\n",
            archive="## Decision 7: Archive copy (Decided)\n\n**Status:** Decided\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]
        out = capsys.readouterr().out
        assert "FAIL" in out and "Decision 7 appears in both" in out

    def test_retired_allowlist_key_fails(self, tmp_path: Path, capsys) -> None:
        _write_decisions(tmp_path, live="## Decision 7: Live copy (Decided)\n\n**Status:** Decided\n")
        _write_waivers(tmp_path, raw_text="waivers: []\nlive_archive_pair_allowlist: []\n")
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert len(failed) == 1 and "live_archive_pair_allowlist" in failed[0] and "retired" in failed[0]


class TestAccounting:
    def test_run_declares_the_heading_count(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live=(
                "## Decision 1: One (Decided)\n\n**Status:** Decided\n\n## Decision 2: Two (Decided)\n\n**Status:** Decided\n"
            ),
            archive="## Decision 3: Three (Decided)\n\n**Status:** Decided\n",
        )
        _write_waivers(tmp_path)
        registry.pop_declaration()
        validate_supersession_annotations([], root=tmp_path)
        declaration = registry.pop_declaration()
        assert declaration is not None and declaration.kind == "examined"
        assert declaration.count == 3 and declaration.unit == "decisions"

    def test_missing_waiver_file_declares_skipped(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 1: One (Decided)\n\n**Status:** Decided\n")
        registry.pop_declaration()
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        declaration = registry.pop_declaration()
        assert failed and declaration is not None and declaration.kind == "skipped"


class TestWarehouseIdSubCheck:
    def test_conforming_warehouse_id_passes(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 12: Something (Decided)\n\n**Status:** Decided\n\n**Warehouse ID:** dec-012\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []

    def test_nonconforming_warehouse_id_fails(self, tmp_path: Path) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 12: Something (Decided)\n\n**Status:** Decided\n\n**Warehouse ID:** dec-099\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == ["Decision supersession-annotation guard"]

    def test_block_without_warehouse_id_is_skipped(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 12: Something (Decided)\n\n**Status:** Decided\n")
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []


class TestWaiverFileLoading:
    def test_missing_waivers_file_fails(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 1: Something (Decided)\n\n**Status:** Decided\n")
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert len(failed) == 1
        assert "decision_supersession_waivers.yaml" in failed[0]

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 1: Something (Decided)\n\n**Status:** Decided\n")
        _write_waivers(tmp_path, raw_text="waivers: [unterminated\n")
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert len(failed) == 1
        assert "decision_supersession_waivers.yaml" in failed[0]

    def test_non_dict_top_level_fails(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 1: Something (Decided)\n\n**Status:** Decided\n")
        _write_waivers(tmp_path, raw_text="- just\n- a\n- list\n")
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert len(failed) == 1
        assert "decision_supersession_waivers.yaml" in failed[0]

    def test_empty_waivers_file_treated_as_no_waivers(self, tmp_path: Path) -> None:
        _write_decisions(tmp_path, live="## Decision 1: Something (Decided)\n\n**Status:** Decided\n")
        _write_waivers(tmp_path, raw_text="")
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []


class TestFullEntrypointIntegration:
    def test_fully_green_fixture_prints_pass(self, tmp_path: Path, capsys) -> None:
        _write_decisions(
            tmp_path,
            live="## Decision 2: New (Decided)\n\n**Decision:** Supersedes Decision 1.\n\n---\n\n"
            "## Decision 1: Old (Decided)\n\n**Status:** Superseded by Decision 2.\n",
        )
        _write_waivers(tmp_path)
        failed: list[str] = []
        validate_supersession_annotations(failed, root=tmp_path)
        assert failed == []
        assert "PASS:" in capsys.readouterr().out


class TestNonVacuityRealRepo:
    """Runs the pure extractor and the full entrypoint against the REAL repo files. Asserts
    non-vacuity (> 0) only -- never an absolute count (test-count-coupling anti-pattern); the
    corpus grows over time as new Decisions are authored."""

    def test_extractor_enumerates_a_positive_edge_count(self) -> None:
        edges = extract_supersession_edges(_REPO_ROOT)
        assert len(edges) > 0

    def test_real_repo_guard_is_green(self) -> None:
        failed: list[str] = []
        validate_supersession_annotations(failed, root=_REPO_ROOT)
        assert failed == []

    def test_default_root_resolves_to_common_root(self) -> None:
        """Calling with no root= argument at all exercises the _common.ROOT default (the real
        entrypoint signature used by scripts/validate.py's dispatch)."""
        failed: list[str] = []
        validate_supersession_annotations(failed)
        assert failed == []


class TestDecisionsMdDedupWarning:
    """parse_decisions_md's first-wins dedup site (scripts/decisions_md.py) emits a stderr
    WARNING naming the dropped decision number whenever it discards a duplicate -- the returned
    parse result is unchanged (behavior-preserving edit)."""

    def test_duplicate_decision_number_emits_stderr_warning(self, tmp_path: Path, capsys) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)
        decisions_path = docs_dir / "DECISIONS.md"
        decisions_path.write_text(
            "## Decision 1: First (Decided)\n\n**Status:** Decided\n\n---\n\n"
            "## Decision 1: Duplicate (Decided)\n\n**Status:** Decided\n",
            encoding="utf-8",
        )
        result = parse_decisions_md(paths=[decisions_path])
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "duplicate decision number 1" in err
        assert "DECISIONS.md" in err
        # Behavior-preserving: still exactly one (first-parsed) entry for decision 1.
        assert len(result) == 1
        assert result[0]["title"] == "First"

    def test_no_duplicate_emits_no_warning(self, tmp_path: Path, capsys) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)
        decisions_path = docs_dir / "DECISIONS.md"
        decisions_path.write_text("## Decision 1: Only one (Decided)\n\n**Status:** Decided\n", encoding="utf-8")
        parse_decisions_md(paths=[decisions_path])
        assert capsys.readouterr().err == ""
