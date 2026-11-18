"""Tests for validate_decisions_size(). Mirror of
scripts/checks/decisions/validate_decisions_size.py (Decision 134 / Decision 114 parity;
PLAN-decision-entry-flow-governance / Decision 167 adds the per-entry authoring cap, hard-fail
tier flipped by migration-step-3-grandfathering / T2.56 migration step 3).

Decision 179 retires the module's three mechanical stock ceilings (the live '## Decision' header
count, the live-byte-only ceiling, and the live+archive combined byte ceiling) -- bounded
decision-scout retrieval means no consumer reads the live corpus wholesale anymore, so the guards
that sized that read no longer have a referent. This file covers the surviving
_PER_ENTRY_CAP_BYTES (6_144) hard-fail per-NEW-entry cap (Decision 167 clause 3, fired by
migration step 3), TestStockCeilingsRetired's standing guard against the retired ceilings'
return, and (rec-3243) the WARN-tier-only amendment-delta and standing-pressure accretion legs
(TestAmendmentDeltaCap, TestWarnNeverFails, TestStandingPressure)."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.decisions.validate_decisions_size import (
    _PER_ENTRY_CAP_BYTES,
    _new_entries_examined_count,
    _per_entry_cap_failures,
    validate_decisions_size,
)
from tests.fixtures.decision_entry import ENTRY, FULL, OPTS, gov, make_root


class TestValidateDecisionsSizeRegisteredCheck:
    """Exercises the REGISTERED validate_decisions_size(failed) function itself (not just the
    pure helper), via patch("scripts.checks._common.ROOT", tmp_path) over a synthetic
    docs/DECISIONS.md + docs/DECISIONS_ARCHIVE.md tree -- mirrors
    tests/checks/roadmap/test_validate_platform_roadmap.py's TestPlatformRoadmapCriteriaIntegrity
    pattern, so validate_test_coverage's 100%-of-new-code gate covers the check function's own
    file-read / count / print / failed.append branches."""

    def _write_docs(self, tmp_path: Path, live_text: str, archive_text: str) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "DECISIONS.md").write_text(live_text, encoding="utf-8")
        (docs_dir / "DECISIONS_ARCHIVE.md").write_text(archive_text, encoding="utf-8")

    def test_pass_case(self, tmp_path: Path) -> None:
        self._write_docs(tmp_path, "## Decision 1: Small entry\n\nBody.\n\n---\n\n", "Archive body.\n")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        assert failed == []

    def test_missing_live_file_fails(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "DECISIONS_ARCHIVE.md").write_text("", encoding="utf-8")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        assert "DECISIONS size governance" in failed

    def test_missing_archive_file_fails(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "DECISIONS.md").write_text("## Decision 1: X\n", encoding="utf-8")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        assert "DECISIONS size governance" in failed


class TestStockCeilingsRetired:
    """VP step 1 / graduated check_id 'decisions-stock-ceilings-retired' (Decision 179): the
    module's retired stock-ceiling symbols stay absent, and the contract's parsed
    size_governance carries none of the four retired keys while retaining per_entry_size_norm.
    _DECISIONS_LIVE_MAX_BYTES is included in assertion (i) so this class subsumes the deleted
    TestLiveByteCeilingRetired's Decision 145/160 regression guard. Deliberately structural, not
    free-text grep -- this class must name the retired tokens to assert their absence, so it
    lives inside VP step 2's residual-sweep pathspec exclusion rather than duplicating that
    sweep."""

    def test_retired_symbols_absent_from_module(self) -> None:
        import scripts.checks.decisions.validate_decisions_size as m

        gone = (
            "_DECISIONS_LIVE_MAX_H2",
            "_DECISIONS_COMBINED_MAX_BYTES",
            "_DECISIONS_LIVE_MAX_BYTES",
            "_decisions_size_issues",
            "_LIVE_H2_RE",
            "_RELIEF_VALVES",
        )
        still = [name for name in gone if hasattr(m, name)]
        assert still == [], still

    def test_contract_size_governance_drops_retired_keys(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        contract_path = repo_root / "docs" / "contracts" / "decision-entry.yaml"
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        size_governance = contract["size_governance"]
        stale = {"live_max_h2_headers", "combined_max_bytes", "index_max_bytes", "lever_by_ceiling_matrix"}
        assert not (stale & set(size_governance)), sorted(size_governance)
        assert "per_entry_size_norm" in size_governance


def _make_block(number: int, target_bytes: int) -> str:
    """A synthetic '## Decision N:' heading-inclusive block whose UTF-8 byte length is exactly
    target_bytes -- header/filler/trailing-newline are all ASCII, so char count == byte count."""
    header = f"## Decision {number}: Test entry\n\n"
    footer = "\n"
    filler_len = target_bytes - len((header + footer).encode("utf-8"))
    assert filler_len >= 0, "target_bytes too small for the fixed header/footer"
    return header + ("x" * filler_len) + footer


def _write_docs(tmp_path: Path, live_text: str, archive_text: str = "") -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "DECISIONS.md").write_text(live_text, encoding="utf-8")
    (docs_dir / "DECISIONS_ARCHIVE.md").write_text(archive_text, encoding="utf-8")


class TestPerEntryCap:
    """test_obligations selector alias for the hard-fail flip -- see
    TestPerEntryCapFailuresPureFunction below for the full boundary coverage."""

    def test_new_over_cap_entry_fails(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers=set())
        assert len(failures) == 1
        assert "Decision 5" in failures[0]

        historical_failures = _per_entry_cap_failures(tmp_path, baseline_numbers={5})
        assert historical_failures == [], "a historical entry must still never be measured"


class TestPerEntryCapFailuresPureFunction:
    """_per_entry_cap_failures(root, baseline_numbers) -- hard-fail tier, forward-only.
    Renamed from _per_entry_cap_warnings / TestPerEntryCapWarningsPureFunction
    (migration-step-3-grandfathering: Decision 167 clause 3's WARN pre-commitment fires)."""

    def test_over_cap_new_entry_fails(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers=set())
        assert len(failures) == 1
        assert "Decision 5" in failures[0]
        assert str(_PER_ENTRY_CAP_BYTES) in failures[0]

    def test_over_cap_historical_entry_is_silent(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers={5})
        assert failures == []

    def test_new_entry_at_exactly_cap_is_silent(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers=set())
        assert failures == []

    def test_new_entry_under_cap_is_silent(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES - 100))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers=set())
        assert failures == []

    def test_archive_over_cap_new_entry_fails_too(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, "", archive_text=_make_block(9, _PER_ENTRY_CAP_BYTES + 1))
        failures = _per_entry_cap_failures(tmp_path, baseline_numbers=set())
        assert len(failures) == 1
        assert "Decision 9" in failures[0]

    def test_missing_files_yield_no_failures(self, tmp_path: Path) -> None:
        assert _per_entry_cap_failures(tmp_path, baseline_numbers=set()) == []


class TestPerEntryCapThroughRegisteredCheck:
    """End-to-end through validate_decisions_size(failed, root=..., baseline_reader=...) -- a
    non-default root always exercises the per-entry sub-check (deterministic for tests,
    bypassing the production changed-files short-circuit; see the module docstring)."""

    def test_over_cap_new_entry_fails_and_prints_fail(self, tmp_path: Path, capsys) -> None:
        """INVERTED from the pre-flip test_over_cap_new_entry_prints_warning_and_never_fails
        (which asserted failed == []) -- migration-step-3-grandfathering fires Decision 167
        clause 3's hard-fail flip; a red result here IS the flip working."""
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failed: list[str] = []
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert "DECISIONS size governance" in failed
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Decision 5" in out

    def test_over_cap_historical_entry_produces_no_per_entry_output(self, tmp_path: Path, capsys) -> None:
        """Re-anchored on the new failure surface: a "WARN not in out" assertion would pass
        VACUOUSLY post-flip (WARN is never printed by this module at all anymore) -- the real
        claim is that a historical (baseline-present) entry never appears in the per-entry
        output or in `failed`, regardless of its size."""
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failed: list[str] = []
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: {5})
        assert failed == []
        assert "Decision 5" not in capsys.readouterr().out

    def test_boundary_at_exactly_cap_is_silent(self, tmp_path: Path, capsys) -> None:
        """Re-anchored (see test_over_cap_historical_entry_produces_no_per_entry_output above)."""
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES))
        failed: list[str] = []
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []
        assert "Decision 5" not in capsys.readouterr().out

    def test_unreachable_baseline_advisory_skips_per_entry_cap(self, tmp_path: Path, capsys) -> None:
        """A non-default root with NO injected baseline_reader and no .git directory -- the
        default reader's own reachability check returns the None sentinel, so the per-entry cap
        sub-check never runs. Also asserts the SKIP message printed, so this proves the skip
        path fired rather than merely that no over-cap entry happened to be measured."""
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES + 1))
        failed: list[str] = []
        validate_decisions_size(failed, root=tmp_path)
        assert failed == []
        assert "SKIP" in capsys.readouterr().out

    def test_multiple_per_entry_cap_failures_all_reported(self, tmp_path: Path, capsys) -> None:
        """Reworked from the pre-retirement test_stock_ceiling_breach_and_per_entry_failure_can_coexist
        (which paired a combined-ceiling breach with a per-entry failure) -- that ceiling is gone,
        so the per-entry-only analogue is two independent new over-cap entries, one live and one
        archived: both fail and both print their own FAIL line; `failed` carries the check's label
        (membership is what matters, not count)."""
        _write_docs(
            tmp_path,
            _make_block(5, _PER_ENTRY_CAP_BYTES + 1),
            archive_text=_make_block(9, _PER_ENTRY_CAP_BYTES + 1),
        )
        failed: list[str] = []
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert "DECISIONS size governance" in failed
        out = capsys.readouterr().out
        assert "Decision 5" in out
        assert "Decision 9" in out


class TestDeclarationAdoption:
    """Decision 170 touch-it-fix-it: this check was baselined (no declaration) before this
    plan; editing it now obligates a registry.examined()/skipped() declaration on ALL FIVE of
    its reachable exit paths."""

    def test_missing_live_file_skips(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS_ARCHIVE.md").write_text("", encoding="utf-8")
        failed: list[str] = []
        registry.pop_declaration()
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"
        assert "DECISIONS.md" in declaration.reason

    def test_missing_archive_file_skips(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 1: X\n", encoding="utf-8")
        failed: list[str] = []
        registry.pop_declaration()
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"
        assert "DECISIONS_ARCHIVE.md" in declaration.reason

    def test_cost_gated_early_return_examines_zero(self, tmp_path: Path) -> None:
        """using_default_root AND neither DECISIONS file changed (an empty-.git-less tmp_path
        naturally yields get_changed_files() == []) -- definitively zero new entries to check,
        not an unmet precondition, so this declares examined(0), never skipped()."""
        _write_docs(tmp_path, "## Decision 1: Small entry\n\nBody.\n\n---\n\n", "Archive body.\n")
        failed: list[str] = []
        registry.pop_declaration()
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_decisions_size(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 0
        assert declaration.unit == "new_decision_entries"

    def test_unreachable_baseline_skips(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, "## Decision 1: X\n\nBody.\n\n---\n\n")
        failed: list[str] = []
        registry.pop_declaration()
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: None)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"
        assert "origin/main" in declaration.reason
        assert failed == []

    def test_fallthrough_examines_new_entry_count(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, 200))
        failed: list[str] = []
        registry.pop_declaration()
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: set())
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 1
        assert declaration.unit == "new_decision_entries"

    def test_fallthrough_excludes_baselined_historical_entries(self, tmp_path: Path) -> None:
        _write_docs(tmp_path, _make_block(5, 200))
        failed: list[str] = []
        registry.pop_declaration()
        validate_decisions_size(failed, root=tmp_path, baseline_reader=lambda r: {5})
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 0

    def test_new_entries_examined_count_missing_files_is_zero(self, tmp_path: Path) -> None:
        """Neither docs/DECISIONS.md nor docs/DECISIONS_ARCHIVE.md exists under tmp_path --
        mirrors _per_entry_cap_failures's own missing-files tolerance."""
        assert _new_entries_examined_count(tmp_path, baseline_numbers=set()) == 0


_NO_BASELINE_BODIES = {"docs/DECISIONS.md": {}, "docs/DECISIONS_ARCHIVE.md": {}}


class TestAmendmentDeltaCap:
    """rec-3243's acceptance selector (exact class name, per the plan). Drives the module
    through the injected baseline_body_reader seam with tmp_path -- never real git -- covering
    the amendment-delta leg's WARN/silence boundary, and confirming the pre-existing per-entry
    hard-fail cap is unaffected by this leg's addition."""

    def test_changed_past_cap_warns(self, tmp_path: Path, capsys) -> None:
        base_block = _make_block(5, 5_000)
        head_block = _make_block(5, 6_300)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        baseline_bodies = {"docs/DECISIONS.md": {5: base_block}, "docs/DECISIONS_ARCHIVE.md": {}}
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: baseline_bodies,
        )
        assert failed == [], "a historical (baseline) entry must never hard-fail"
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "Decision 5" in out
        assert "+1300 bytes" in out

    def test_changed_but_under_cap_is_silent(self, tmp_path: Path, capsys) -> None:
        base_block = _make_block(5, 5_000)
        head_block = _make_block(5, 5_500)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        baseline_bodies = {"docs/DECISIONS.md": {5: base_block}, "docs/DECISIONS_ARCHIVE.md": {}}
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: baseline_bodies,
        )
        assert failed == []
        assert "WARN" not in capsys.readouterr().out

    def test_unchanged_baseline_is_silent_even_over_cap(self, tmp_path: Path, capsys) -> None:
        block = _make_block(5, 7_000)
        _write_docs(tmp_path, block)
        failed: list[str] = []
        baseline_bodies = {"docs/DECISIONS.md": {5: block}, "docs/DECISIONS_ARCHIVE.md": {}}
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: baseline_bodies,
        )
        assert failed == []
        assert "WARN" not in capsys.readouterr().out

    def test_shrunk_over_cap_still_warns(self, tmp_path: Path, capsys) -> None:
        """Decision 178 retired validate_live_entry_immutability -- a baseline body can now be
        rewritten in place, so a SHRUNK-but-still-over-cap entry must still WARN (delta != 0,
        not delta > 0)."""
        base_block = _make_block(5, 8_000)
        head_block = _make_block(5, 7_000)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        baseline_bodies = {"docs/DECISIONS.md": {5: base_block}, "docs/DECISIONS_ARCHIVE.md": {}}
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: baseline_bodies,
        )
        assert failed == []
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "-1000 bytes" in out

    def test_none_body_baseline_advisory_skips(self, tmp_path: Path, capsys) -> None:
        """origin/main unreachable for the BODY baseline specifically -- print nothing, raise
        nothing, leave the existing terminal declarations untouched."""
        head_block = _make_block(5, 6_300)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: None,
        )
        assert failed == []
        assert "WARN" not in capsys.readouterr().out

    def test_new_in_diff_entry_still_hard_fails(self, tmp_path: Path, capsys) -> None:
        """Regression guard: a genuinely new-in-diff over-cap entry still HARD-FAILS through the
        existing per-entry cap, unaffected by the new accretion legs."""
        head_block = _make_block(5, _PER_ENTRY_CAP_BYTES + 1)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: set(),
            baseline_body_reader=lambda r: _NO_BASELINE_BODIES,
        )
        assert "DECISIONS size governance" in failed
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Decision 5" in out


class TestAccretionHelperMissingFileBranches:
    """Direct coverage of _amendment_delta_warnings / _standing_pressure_warning's
    file-missing and entry-absent-from-a-nonempty-baseline branches -- reachable only when
    called directly, since the registered validate_decisions_size() entry point already fails
    closed on a missing DECISIONS file before either helper runs."""

    def test_amendment_delta_warnings_skips_a_missing_file_but_still_warns_on_the_present_one(self, tmp_path: Path) -> None:
        from scripts.checks.decisions.validate_decisions_size import _amendment_delta_warnings

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS.md").write_text(_make_block(5, 6_300), encoding="utf-8")
        # docs/DECISIONS_ARCHIVE.md intentionally absent.
        baseline_bodies = {
            "docs/DECISIONS.md": {5: _make_block(5, 5_000)},
            "docs/DECISIONS_ARCHIVE.md": {9: _make_block(9, 5_000)},
        }
        warnings = _amendment_delta_warnings(tmp_path, baseline_bodies)
        assert any("Decision 5" in w for w in warnings)

    def test_amendment_delta_warnings_entry_absent_from_nonempty_baseline_is_silent(self, tmp_path: Path) -> None:
        from scripts.checks.decisions.validate_decisions_size import _amendment_delta_warnings

        _write_docs(tmp_path, _make_block(5, 6_300))
        baseline_bodies = {"docs/DECISIONS.md": {99: _make_block(99, 5_000)}, "docs/DECISIONS_ARCHIVE.md": {}}
        assert _amendment_delta_warnings(tmp_path, baseline_bodies) == []

    def test_standing_pressure_warning_skips_a_missing_file_but_still_counts_the_present_one(self, tmp_path: Path) -> None:
        from scripts.checks.decisions.validate_decisions_size import _standing_pressure_warning

        (tmp_path / "docs").mkdir()
        threshold = int(_PER_ENTRY_CAP_BYTES * 1.2)
        (tmp_path / "docs" / "DECISIONS.md").write_text(_make_block(5, threshold + 100), encoding="utf-8")
        # docs/DECISIONS_ARCHIVE.md intentionally absent.
        warnings = _standing_pressure_warning(tmp_path)
        assert any("1.2x the" in w for w in warnings)


class TestWarnNeverFails:
    """VP step 1 / graduated check_id 'decisions-size-warn-never-fails': the load-bearing safety
    property of the whole accretion signal -- an accretion condition severe enough to WARN must
    leave `failed` empty."""

    def test_warn_never_fails(self, tmp_path: Path, capsys) -> None:
        base_block = _make_block(5, 5_000)
        head_block = _make_block(5, 6_300)
        _write_docs(tmp_path, head_block)
        failed: list[str] = []
        baseline_bodies = {"docs/DECISIONS.md": {5: base_block}, "docs/DECISIONS_ARCHIVE.md": {}}
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: baseline_bodies,
        )
        assert failed == []
        assert "WARN" in capsys.readouterr().out


class TestStandingPressure:
    """VP step 3 / graduated check_id 'decisions-size-standing-pressure-single-line': the
    standing-pressure aggregate is exactly ONE line, not one per entry."""

    def test_standing_pressure_emits_a_single_aggregate_line(self, tmp_path: Path, capsys) -> None:
        threshold = int(_PER_ENTRY_CAP_BYTES * 1.2)
        live_text = _make_block(5, threshold + 100) + _make_block(6, threshold + 200)
        archive_text = _make_block(9, threshold + 50)
        _write_docs(tmp_path, live_text, archive_text)
        failed: list[str] = []
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5, 6, 9},
            baseline_body_reader=lambda r: _NO_BASELINE_BODIES,
        )
        assert failed == []
        out = capsys.readouterr().out
        pressure_lines = [line for line in out.splitlines() if "1.2x the" in line]
        assert len(pressure_lines) == 1, pressure_lines
        assert "3 decision entries" in pressure_lines[0]

    def test_standing_pressure_is_silent_when_nothing_is_over_threshold(self, tmp_path: Path, capsys) -> None:
        _write_docs(tmp_path, _make_block(5, _PER_ENTRY_CAP_BYTES))
        failed: list[str] = []
        validate_decisions_size(
            failed,
            root=tmp_path,
            baseline_reader=lambda r: {5},
            baseline_body_reader=lambda r: _NO_BASELINE_BODIES,
        )
        assert failed == []
        assert "1.2x the" not in capsys.readouterr().out


def _run(root: Path, base_numbers: set[int] | None = None, bodies: dict | None = None, **kwargs: object) -> list[str]:
    failed: list[str] = []
    registry.pop_declaration()
    validate_decisions_size(
        failed, root=root, baseline_reader=lambda r: base_numbers or set(), baseline_body_reader=lambda r: bodies, **kwargs
    )
    return failed


class TestRejectedAlternativesRequired:
    @pytest.mark.parametrize(
        ("body", "contract", "exempt", "needle"),
        [
            ("", FULL, None, "carries no"),
            (OPTS, FULL, None, ""),
            ("", FULL, "exempt_sidecars:\n- {slug: s, title: Persona entry}\n", ""),
            ("", None, None, ""),
            ("", "a: [", None, "unreadable"),
        ],
    )
    def test_the_options_rule(
        self, tmp_path: Path, capsys, body: str, contract: str | None, exempt: str | None, needle: str
    ) -> None:
        assert bool(_run(make_root(tmp_path, body, contract, exempt))) is bool(needle)
        assert needle in capsys.readouterr().out


class TestProductionCostGate:
    @pytest.mark.parametrize(
        "head",
        [gov(measured_size_norm={"cap_bytes": 1}), gov(options_considered={"cap_bytes": 1}), gov(measured_size_norm={})],
    )
    def test_dropping_a_cap_key_fails(self, tmp_path: Path, head: str) -> None:
        make_root(tmp_path, OPTS, head)
        failed: list[str] = []
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.get_changed_files", return_value=["docs/contracts/decision-entry.yaml"]),
        ):
            validate_decisions_size(
                failed, baseline_reader=lambda r: {5}, base_text_reader=lambda r, rel: FULL if "entry" in rel else None
            )
        assert failed and registry.pop_declaration().kind == "examined"


class TestInPlaceAndExemptionWiring:
    def test_growth_and_exemption_findings_reach_the_run(self, tmp_path: Path, capsys) -> None:
        base = ENTRY.format("x" * 5100 + "\n\n" + OPTS)
        root = make_root(tmp_path, "x" * 5102 + "\n\n" + OPTS, exempt="exempt_sidecars:\n- {slug: gone, title: T}\n")
        assert _run(
            root, {5}, {"docs/DECISIONS.md": {5: base}}, base_text_reader=lambda r, rel: FULL if "entry" in rel else None
        )
        out = capsys.readouterr().out
        assert "clause 6" in out and "has no approved sidecar" in out
