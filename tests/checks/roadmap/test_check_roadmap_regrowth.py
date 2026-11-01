"""Tests for check_roadmap_regrowth(). Mirror of
scripts/checks/roadmap/check_roadmap_regrowth.py, and the behavioural home for its two companion
registration surfaces -- the scripts/checks/roadmap/_manifest.py Entry and the
config/ci_rca_taxonomy.yaml row.

Every case drives synthetic in-memory or tmp_path roadmaps; the one live-document case asserts
the LINE GRAMMAR, never today's integers. TestCompletionCompactionContract is the exception --
it reads the two live contract documents (tier-item-lifecycle.yaml, exit-criteria-ledger.yaml)
as a coupling test, not a synthetic-roadmap case.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks._marker_guard import load_decision_bodies
from scripts.checks.hygiene._declaring_coverage import is_fully_declared, measure_check
from scripts.checks.roadmap import check_roadmap_regrowth as module
from scripts.checks.roadmap._manifest import ENTRIES
from scripts.checks.roadmap.check_roadmap_regrowth import (
    _COMPACTION_BASELINE_LINES,
    _ESCALATE_HEADROOM_LINES,
    check_roadmap_regrowth,
    growth_line,
    malformed_state_ids,
    terminal_cd_ids,
    terminal_item_ids,
    unusable_reason,
)

_GROWTH_GRAMMAR = re.compile(
    r"^\s*GROWTH lines=\d+ baseline=\d+ growth=-?\d+ headroom=-?\d+ escalate=(?:true|false) threshold=\d+$"
)

_COMPACT_ROADMAP = {
    "document": {"id": "synthetic"},
    "candidate_decisions": [
        {"id": "CD.1", "state": "ratified", "detail": "one compact line retained by Decision 147 point 1"},
        {"id": "CD.2", "state": "pending", "detail": "line one\nline two\nline three"},
    ],
    "tier_items": [
        {"id": "T0.1", "status": "complete", "intent": "one line", "files_in_scope": []},
        {"id": "T0.2", "status": "in_progress", "progress_note": "narrative that is fine on a live item"},
    ],
}


def _write(tmp_path: Path, document: dict, pad_lines: int = 0) -> Path:
    """Write a synthetic roadmap under tmp_path and return its path."""
    path = tmp_path / "ROADMAP-PLATFORM.yaml"
    path.write_text(yaml.safe_dump(document) + ("# pad\n" * pad_lines), encoding="utf-8")
    return path


def _run(path: Path) -> tuple[list[str], object]:
    """Drive the guard against one roadmap path and return (failed, declaration)."""
    failed: list[str] = []
    check_roadmap_regrowth(failed, roadmap_path=path)
    return failed, registry.pop_declaration()


class TestReportOnly:
    """`failed` is never appended to -- on the live document, on a compact synthetic, and on an
    over-ceiling synthetic carrying re-accumulated terminal prose."""

    def test_the_live_roadmap_fails_nothing(self) -> None:
        failed, declaration = _run(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")

        assert failed == []
        assert declaration is not None and declaration.kind == "examined" and declaration.unit == "terminal_records"

    def test_a_compact_synthetic_fails_nothing(self, tmp_path: Path) -> None:
        failed, declaration = _run(_write(tmp_path, _COMPACT_ROADMAP))

        assert failed == []
        assert declaration is not None and declaration.kind == "examined"

    def test_an_over_ceiling_roadmap_carrying_terminal_prose_still_fails_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        document = {
            "candidate_decisions": [{"id": "CD.9", "state": "superseded", "detail": "a\nb\nc"}],
            "tier_items": [{"id": "T9.9", "status": "complete", "progress_note": "narrative\nprose"}],
        }

        failed, declaration = _run(_write(tmp_path, document, pad_lines=11_000))

        out = capsys.readouterr().out
        assert failed == []
        assert "escalate=true" in out
        assert "CD.9" in out and "T9.9" in out
        assert declaration is not None and declaration.kind == "examined"

    def test_the_guard_never_writes_the_roadmap(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _COMPACT_ROADMAP)
        before = path.read_bytes()

        _run(path)

        assert path.read_bytes() == before


class TestGrowthLine:
    """The growth observable's declared grammar."""

    def test_the_line_matches_the_declared_grammar(self) -> None:
        assert _GROWTH_GRAMMAR.match(growth_line(9368))

    def test_growth_is_measured_against_the_compaction_baseline(self) -> None:
        assert f"growth={9000 - _COMPACTION_BASELINE_LINES}" in growth_line(9000)

    def test_headroom_is_measured_against_the_decision_114_ceiling(self) -> None:
        assert "lines=9000 baseline=7329 growth=1671 headroom=1000" in growth_line(9000)

    def test_the_live_document_emits_the_grammar(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")

        growth = [line for line in capsys.readouterr().out.splitlines() if "GROWTH" in line]
        assert len(growth) == 1
        assert _GROWTH_GRAMMAR.match(growth[0])


class TestEscalateThreshold:
    """The named trip threshold Decision 147's reversal condition is stated against."""

    def test_escalate_reads_false_above_the_threshold(self) -> None:
        assert "escalate=false" in growth_line(10_000 - _ESCALATE_HEADROOM_LINES)

    def test_escalate_reads_true_below_the_threshold(self) -> None:
        assert "escalate=true" in growth_line(10_000 - _ESCALATE_HEADROOM_LINES + 1)

    def test_escalate_reads_true_over_the_ceiling_without_failing_anything(self, tmp_path: Path) -> None:
        failed, _declaration = _run(_write(tmp_path, _COMPACT_ROADMAP, pad_lines=11_000))

        assert failed == []

    def test_the_threshold_is_five_hundred_lines(self) -> None:
        assert _ESCALATE_HEADROOM_LINES == 500
        assert "threshold=500" in growth_line(9000)


class TestTerminalSelectors:
    """Only terminal records are selected, and only for the content Decision 147 removed."""

    def test_only_ratified_or_superseded_candidate_decisions_are_selected(self) -> None:
        cds = [
            {"id": "CD.A", "state": "ratified", "detail": "a\nb"},
            {"id": "CD.B", "state": "superseded", "realization_evidence": "a\nb"},
            {"id": "CD.C", "state": "pending", "detail": "a\nb\nc"},
        ]

        assert terminal_cd_ids(cds) == ["CD.A", "CD.B"]

    def test_only_complete_or_reserved_tier_items_are_selected(self) -> None:
        items = [
            {"id": "T.A", "status": "complete", "progress_note": "x"},
            {"id": "T.B", "status": "reserved", "note": "x"},
            {"id": "T.C", "status": "in_progress", "progress_note": "x"},
        ]

        assert terminal_item_ids(items) == ["T.A", "T.B"]

    def test_multi_line_intent_on_a_terminal_item_is_regrowth(self) -> None:
        assert terminal_item_ids([{"id": "T.D", "status": "complete", "intent": "one\ntwo"}]) == ["T.D"]

    def test_a_non_empty_files_in_scope_on_a_terminal_item_is_regrowth(self) -> None:
        assert terminal_item_ids([{"id": "T.E", "status": "complete", "files_in_scope": ["a.py"]}]) == ["T.E"]

    def test_decomposition_hints_as_a_list_are_counted(self) -> None:
        assert terminal_item_ids([{"id": "T.F", "status": "complete", "decomposition_hints": ["a", "b"]}]) == ["T.F"]

    def test_a_non_string_prose_value_is_measured_rather_than_ignored(self) -> None:
        assert terminal_item_ids([{"id": "T.G", "status": "complete", "note": 7}]) == ["T.G"]

    def test_a_fully_compact_terminal_item_is_not_reported(self) -> None:
        item = {"id": "T.H", "status": "complete", "intent": "one line", "files_in_scope": [], "note": None}

        assert terminal_item_ids([item]) == []


class TestCompactAllowance:
    """Decision 147 point 1 retains ONE compact line on a terminal CD (CD.7's supersession
    marker, which validate_candidate_decision_supersession reads as a literal phrase)."""

    def test_one_compact_detail_line_is_allowed(self) -> None:
        cd = {"id": "CD.7", "state": "superseded", "detail": "CD.7 fully superseded by CD.28. Original in DECISIONS.md."}

        assert terminal_cd_ids([cd]) == []

    def test_two_lines_of_detail_are_reported(self) -> None:
        assert terminal_cd_ids([{"id": "CD.7", "state": "superseded", "detail": "one\ntwo"}]) == ["CD.7"]

    def test_blank_lines_do_not_count_toward_the_allowance(self) -> None:
        assert terminal_cd_ids([{"id": "CD.7", "state": "ratified", "detail": "one\n\n   \n"}]) == []

    def test_detail_and_realization_evidence_are_summed(self) -> None:
        cd = {"id": "CD.8", "state": "ratified", "detail": "one", "realization_evidence": "two"}

        assert terminal_cd_ids([cd]) == ["CD.8"]

    def test_the_live_cd_7_marker_is_not_reported(self) -> None:
        document = yaml.safe_load((_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8"))

        assert "CD.7" not in terminal_cd_ids(document["candidate_decisions"])


class TestUnavailableRoadmapSkips:
    """EVERY unusable-input shape routes to the ONE declared skipped(reason) exit with `failed`
    untouched -- never an escaping exception, which dispatch_recording (no try/except) would let
    abort the whole tier."""

    @staticmethod
    def _case(tmp_path: Path, name: str, body: str | bytes) -> Path:
        path = tmp_path / name
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
        return path

    def test_an_absent_roadmap_skips(self, tmp_path: Path) -> None:
        failed, declaration = _run(tmp_path / "nope.yaml")

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"

    def test_undecodable_bytes_skip(self, tmp_path: Path) -> None:
        failed, declaration = _run(self._case(tmp_path, "c2.yaml", bytes([255, 254, 253])))

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"

    def test_malformed_yaml_skips(self, tmp_path: Path) -> None:
        failed, declaration = _run(self._case(tmp_path, "c1.yaml", "tier_items: [unclosed\n"))

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"

    @pytest.mark.parametrize(
        ("name", "body", "fragment"),
        [
            ("c3.yaml", "- a\n", "not a mapping"),
            ("c4.yaml", "tier_items: 3\n", "tier_items is not a list"),
            ("c5.yaml", "tier_items: [null]\n", "tier_items holds a non-mapping entry"),
            ("c6.yaml", "tier_items: [3]\n", "tier_items holds a non-mapping entry"),
            ("c7.yaml", "tier_items: []\ncandidate_decisions: [x]\n", "candidate_decisions holds a non-mapping entry"),
        ],
    )
    def test_a_structurally_unusable_document_skips(self, tmp_path: Path, name: str, body: str, fragment: str) -> None:
        failed, declaration = _run(self._case(tmp_path, name, body))

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"
        assert fragment in declaration.reason

    def test_a_usable_document_has_no_unusable_reason(self) -> None:
        assert unusable_reason(_COMPACT_ROADMAP) is None


class TestArchivedRecords:
    """Decision 227: records moved to the archive sibling are still observed. Their regrowth joins the
    TERMINAL-ITEM ids, one ARCHIVE line counts them, and every unusable archive shape takes the single
    skip -- naming the archive -- without raising."""

    @staticmethod
    def _archive(tmp_path: Path, body: str | bytes) -> None:
        path = tmp_path / "ROADMAP-PLATFORM-ARCHIVE.yaml"
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")

    def test_regrowth_in_an_archived_record_is_reported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        records = [
            {"id": "T0.9", "status": "complete", "progress_note": "narrative re-grown in the archive"},
            {"id": "T0.0", "status": "complete", "intent": "one line"},
        ]
        roadmap = _write(tmp_path, _COMPACT_ROADMAP)
        self._archive(tmp_path, yaml.safe_dump({"archive_of": "docs/ROADMAP-PLATFORM.yaml", "tier_items": records}))

        failed, declaration = _run(roadmap)
        out = capsys.readouterr().out

        assert failed == []
        assert declaration is not None and declaration.kind == "examined"
        assert "  TERMINAL-ITEM terminal=1 regrown=1 ids=['T0.9']" in out
        assert "  ARCHIVE records=2" in out

    def test_an_absent_archive_is_no_records(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        failed, declaration = _run(_write(tmp_path, _COMPACT_ROADMAP))

        assert failed == []
        assert declaration is not None and declaration.kind == "examined"
        assert "  ARCHIVE records=0" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            (b"\xff\xfe\xfd", "UnicodeDecodeError"),
            ("tier_items: [unclosed\n", "ParserError"),
            ("- a\n", "document is not a mapping"),
            ("tier_items: 3\n", "tier_items is not a list"),
            ("tier_items: [3]\n", "tier_items holds a non-mapping record"),
        ],
    )
    def test_an_unusable_archive_takes_the_single_skip(self, tmp_path: Path, body: str | bytes, fragment: str) -> None:
        roadmap = _write(tmp_path, _COMPACT_ROADMAP)
        self._archive(tmp_path, body)

        failed, declaration = _run(roadmap)

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"
        assert "docs/ROADMAP-PLATFORM-ARCHIVE.yaml unusable" in declaration.reason and fragment in declaration.reason

    def test_an_unreadable_archive_takes_the_single_skip(self, tmp_path: Path) -> None:
        roadmap = _write(tmp_path, _COMPACT_ROADMAP)
        (tmp_path / "ROADMAP-PLATFORM-ARCHIVE.yaml").mkdir()

        failed, declaration = _run(roadmap)

        assert failed == []
        assert declaration is not None and declaration.kind == "skipped"
        assert "docs/ROADMAP-PLATFORM-ARCHIVE.yaml unusable (IsADirectoryError)" in declaration.reason

    def test_the_live_tree_reports_its_archive_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        failed, _ = _run(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")

        assert failed == []
        assert re.search(r"^  ARCHIVE records=\d+$", capsys.readouterr().out, re.M)


class TestNonStringStateIsCountedNotRaised:
    """A mapping entry whose `status`/`state` value is itself a YAML list or mapping passes every
    shape gate and then makes a bare `entry.get(...) in <frozenset>` raise TypeError on the
    unhashable value -- a tier-aborting raise, since dispatch_recording wraps a check body with no
    try/except. Every read goes through the by-name string guard instead: such a record is neither
    terminal nor non-terminal, is counted on the MALFORMED-STATE line, and reaches no membership
    test at all.
    """

    _SHAPES = (("list", ["complete"]), ("mapping", {"of": "complete"}), ("null", None), ("number", 7))

    @pytest.mark.parametrize(("label", "value"), _SHAPES)
    def test_a_non_string_status_selects_nothing_and_raises_nothing(self, label: str, value: object) -> None:
        items = [{"id": f"T.{label}", "status": value, "progress_note": "prose"}]

        assert terminal_item_ids(items) == []
        assert malformed_state_ids(items, "status") == [f"T.{label}"]

    @pytest.mark.parametrize(("label", "value"), _SHAPES)
    def test_a_non_string_state_selects_nothing_and_raises_nothing(self, label: str, value: object) -> None:
        cds = [{"id": f"CD.{label}", "state": value, "detail": "one\ntwo\nthree"}]

        assert terminal_cd_ids(cds) == []
        assert malformed_state_ids(cds, "state") == [f"CD.{label}"]

    @pytest.mark.parametrize(("label", "value"), _SHAPES)
    def test_the_whole_guard_reports_the_shape_and_still_declares_examined(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str, value: object
    ) -> None:
        document = {
            "tier_items": [{"id": "T9.9", "status": value}],
            "candidate_decisions": [{"id": "CD.9", "state": value}],
        }

        failed, declaration = _run(_write(tmp_path, document))

        out = capsys.readouterr().out
        assert failed == []
        assert declaration is not None and declaration.kind == "examined"
        assert "MALFORMED-STATE cds=1 items=1 ids=['CD.9', 'T9.9']" in out

    def test_a_string_state_is_never_counted_as_malformed(self) -> None:
        """Discrimination: the malformed count is a shape verdict, not a catch-all."""
        assert malformed_state_ids([{"id": "T.A", "status": "complete"}], "status") == []

    def test_the_live_document_reports_no_malformed_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")

        assert "MALFORMED-STATE cds=0 items=0 ids=[]" in capsys.readouterr().out


class TestBaselineMatchesDecision147:
    """The compaction baseline constant is DERIVED from Decision 147's own entry, not asserted
    from the plan text."""

    def test_the_constant_equals_the_figure_decision_147_states(self) -> None:
        body = load_decision_bodies()[147]

        stated = {int(figure.replace(",", "")) for figure in re.findall(r"9,996 -> ([\d,]+) lines", body)}

        assert stated == {_COMPACTION_BASELINE_LINES}

    def test_decision_147_names_the_anti_regrowth_follow_on(self) -> None:
        assert "rec-2781" in load_decision_bodies()[147]


class TestCompletionCompactionContract:
    """docs/contracts/tier-item-lifecycle.yaml#completion_compaction is the single home of the
    Decision 147 compact form (PLAN-roadmap-terminal-compaction); this class pins the contract
    coupling so a future edit to either contract or to _ITEM_PROSE_FIELDS cannot silently drift
    out of sync with the mechanical observable above.
    """

    @staticmethod
    def _tier_item_lifecycle() -> dict:
        return yaml.safe_load((_common.ROOT / "docs" / "contracts" / "tier-item-lifecycle.yaml").read_text(encoding="utf-8"))

    @staticmethod
    def _exit_criteria_ledger() -> dict:
        return yaml.safe_load((_common.ROOT / "docs" / "contracts" / "exit-criteria-ledger.yaml").read_text(encoding="utf-8"))

    def test_completion_compaction_names_every_compactable_field_and_cites_decision_147(self) -> None:
        text = self._tier_item_lifecycle()["completion_compaction"]

        for field in (*module._ITEM_PROSE_FIELDS, "intent", "files_in_scope", "open_questions", "known_gaps"):
            assert field in text, field
        assert "Decision 147" in text

    def test_completion_compaction_exceptions_is_well_formed(self) -> None:
        contract = self._tier_item_lifecycle()
        exceptions = contract["completion_compaction_exceptions"]
        compactable = {*module._ITEM_PROSE_FIELDS, "intent", "files_in_scope"}

        assert exceptions
        for item_id, entry in exceptions.items():
            assert set(entry["fields"]) <= compactable, (item_id, entry["fields"])
            pinned_by = entry["pinned_by"]
            file_part = pinned_by.split("::")[0]
            assert (_common.ROOT / file_part).exists(), pinned_by

    def test_the_three_walk_clauses_reference_completion_compaction_and_drop_the_old_note_wording(self) -> None:
        contract = self._tier_item_lifecycle()

        for key in ("bookkeeping_walk", "tier_item_freshness_gate", "replacement_closure_check"):
            clause = contract[key]
            assert "completion_compaction" in clause, key
            assert "a note citing the evidence" not in clause, key
            assert "supersession note" not in clause, key

    def test_completion_compaction_carries_the_reserved_item_and_bootstrap_clauses(self) -> None:
        text = self._tier_item_lifecycle()["completion_compaction"]

        assert "successor pointer" in text
        assert "one-line intent" in text
        assert "bootstrap_completion_exempt" in text

    def test_the_raw_contract_text_declares_the_residual_on_one_line(self) -> None:
        raw = (_common.ROOT / "docs" / "contracts" / "tier-item-lifecycle.yaml").read_text(encoding="utf-8")

        matches = [line for line in raw.splitlines() if "completion_compaction has no enforcing check" in line]
        assert len(matches) == 1, matches

    def test_exit_criteria_ledger_status_semantics_points_at_completion_compaction(self) -> None:
        semantics = self._exit_criteria_ledger()["fields"]["status"]["semantics"]

        assert "completion_compaction" in semantics
        assert "item's own note" not in semantics

    def test_exit_criteria_ledger_known_limitation_no_longer_claims_at_least_two(self) -> None:
        invariants = "\n".join(self._exit_criteria_ledger()["audit_invariants"])

        assert "at least two .claude/skills/ entries" not in invariants


class TestGuardAccountingDeclaration:
    """Decision 170 arm (a): a NEW registered check declares on every reachable exit and has no
    grandfather path, plus the registration surfaces that make it dispatchable."""

    def test_every_reachable_exit_declares(self) -> None:
        row = measure_check("check_roadmap_regrowth", registry.resolve("check_roadmap_regrowth"))

        assert row.undeclared == 0
        assert is_fully_declared(row)

    def test_the_check_is_on_neither_the_roster_nor_the_frozen_seed(self) -> None:
        from scripts.checks.hygiene.validate_check_accounting import _BASELINE_SEED  # noqa: PLC0415

        roster = yaml.safe_load((_common.ROOT / "config" / "check_accounting_baseline.yaml").read_text(encoding="utf-8"))

        assert "check_roadmap_regrowth" not in _BASELINE_SEED
        assert "check_roadmap_regrowth" not in roster["entries"]

    def test_the_manifest_entry_dispatches_the_modules_own_callable(self) -> None:
        entry = next(e for e in ENTRIES if e.name == "check_roadmap_regrowth")

        assert entry.module == "scripts.checks.roadmap.check_roadmap_regrowth"
        assert entry.attr == "check_roadmap_regrowth"
        assert registry.resolve("check_roadmap_regrowth") is module.check_roadmap_regrowth

    def test_the_entry_gates_on_the_roadmap_and_its_own_import_closure(self) -> None:
        entry = next(e for e in ENTRIES if e.name == "check_roadmap_regrowth")

        closure = {"docs/ROADMAP-*", "scripts/checks/roadmap/**", "scripts/checks/_common.py", "scripts/checks/registry.py"}

        assert entry.pre is True
        assert closure <= set(entry.pre_globs)
        assert entry.full_segment == "full_after_lint"

    def test_the_check_is_dispatched_in_both_tiers(self) -> None:
        assert "check_roadmap_regrowth" in {step.name for step in registry.pre_sequence()}
        assert "check_roadmap_regrowth" in {step.name for step in registry.full_sequence()}

    def test_the_taxonomy_assigns_a_declared_failure_category(self) -> None:
        taxonomy = yaml.safe_load((_common.ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))

        assert taxonomy["function_to_category"]["check_roadmap_regrowth"] == "schema_drift"
        assert taxonomy["function_to_category"]["check_roadmap_regrowth"] in taxonomy["failure_categories"]
