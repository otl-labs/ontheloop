"""Tests for validate_structural_size_budget_raises() -- the raise-guard consumer of
scripts.checks._marker_guard (SGE-01/SGE-02/SGE-04, Decision 166).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.structural import _byte_raises
from scripts.checks.structural.budget_raises import (
    _BUDGET_SPEC,
    _LONG_LINE_SPEC,
    frozen_base_violations,
    validate_structural_size_budget_raises,
)


def _write_current(tmp_path: Path, body: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "structural_size_budgets.yaml").write_text(body, encoding="utf-8")


def _write_decisions(tmp_path: Path, decision_numbers: list[int], mentions: dict[int, str] | None = None) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(exist_ok=True)
    mentions = mentions or {}
    parts = []
    for n in decision_numbers:
        header = f"## Decision {n}: Some title (Decided)\n"
        mention = mentions.get(n)
        parts.append(header if not mention else f"{header}\n**Decision:** Authorizes {mention}.\n")
    (docs_dir / "DECISIONS.md").write_text("\n".join(parts), encoding="utf-8")


_BASE_CLASSES_BLOCK = (
    "schema_version: 1\n"
    "classes:\n"
    "  - slug: config\n"
    '    include: ["config/*.yaml"]\n'
    "    governed: true\n"
    "    unit: effective-lines\n"
    "    limit: 500\n"
    "    max_line_chars: 2000\n"
)


class TestTheCriticalSeam:
    """A `classes:` block's `limit`/`max_line_chars` scalars must never leak into either
    section's extraction -- proving the seam, not merely observing it."""

    def test_class_table_scalars_absent_from_both_sections(self, tmp_path: Path) -> None:
        body = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 800\nlong_line_budgets:\n  config/long.yaml: 2200\n"
        _write_current(tmp_path, body)
        _write_decisions(tmp_path, [])

        text = (tmp_path / "config" / "structural_size_budgets.yaml").read_text(encoding="utf-8")
        budgets = _BUDGET_SPEC.extractor(text)
        long_line = _LONG_LINE_SPEC.extractor(text)

        assert "limit" not in budgets and "limit" not in long_line
        assert "max_line_chars" not in budgets and "max_line_chars" not in long_line

    def test_sections_do_not_cross_leak(self, tmp_path: Path) -> None:
        body = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 800\nlong_line_budgets:\n  config/long.yaml: 2200\n"
        _write_current(tmp_path, body)
        text = (tmp_path / "config" / "structural_size_budgets.yaml").read_text(encoding="utf-8")
        budgets = _BUDGET_SPEC.extractor(text)
        long_line = _LONG_LINE_SPEC.extractor(text)

        assert "config/heavy.yaml" in budgets and "config/heavy.yaml" not in long_line
        assert "config/long.yaml" in long_line and "config/long.yaml" not in budgets


class TestValidateStructuralSizeBudgetRaises:
    def test_fails_on_unmarked_increase_in_budgets(self, tmp_path: Path) -> None:
        current = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 800\nlong_line_budgets: {}\n"
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [])
        base_text = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 600\nlong_line_budgets: {}\n"
        base_reader = lambda rel: base_text  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_fails_on_unmarked_increase_in_long_line_budgets(self, tmp_path: Path) -> None:
        current = _BASE_CLASSES_BLOCK + "budgets: {}\nlong_line_budgets:\n  config/long.yaml: 2500\n"
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [])
        base_text = _BASE_CLASSES_BLOCK + "budgets: {}\nlong_line_budgets:\n  config/long.yaml: 2100\n"
        base_reader = lambda rel: base_text  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_marked_increase_citing_authorizing_decision_passes(self, tmp_path: Path) -> None:
        current = (
            _BASE_CLASSES_BLOCK
            + "budgets:\n  config/heavy.yaml: 800  # raise-approved: dec-166 module growth\nlong_line_budgets: {}\n"
        )
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [166], mentions={166: "config/heavy.yaml"})
        base_text = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 600\nlong_line_budgets: {}\n"
        base_reader = lambda rel: base_text  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert failed == []

    def test_marked_increase_citing_non_authorizing_decision_fails(self, tmp_path: Path) -> None:
        """Authorization, not existence: a marker citing a Decision that exists but never
        mentions the key must still fail."""
        current = (
            _BASE_CLASSES_BLOCK
            + "budgets:\n  config/heavy.yaml: 800  # raise-approved: dec-166 module growth\nlong_line_budgets: {}\n"
        )
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [166])  # header-only body -- never mentions the key
        base_text = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 600\nlong_line_budgets: {}\n"
        base_reader = lambda rel: base_text  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_retroactive_leg_fails_unchanged_unbacked_marker(self, tmp_path: Path) -> None:
        """The present-markers leg re-scans every marker on every run -- an entry that did
        NOT change in this diff still fails if its marker is unbacked."""
        current = (
            _BASE_CLASSES_BLOCK
            + "budgets:\n  config/heavy.yaml: 800  # raise-approved: dec-166 module growth\nlong_line_budgets: {}\n"
        )
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [166])  # never mentions the key
        # base == current: nothing changed in this diff.
        base_reader = lambda rel: current  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_passes_on_decrease(self, tmp_path: Path) -> None:
        current = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 600\nlong_line_budgets: {}\n"
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [])
        base_text = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 800\nlong_line_budgets: {}\n"
        base_reader = lambda rel: base_text  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert failed == []

    def test_spec_tokens_and_direction(self) -> None:
        assert _BUDGET_SPEC.token == "raise-approved"
        assert _BUDGET_SPEC.gated_direction == "up"
        assert _LONG_LINE_SPEC.token == "raise-approved"
        assert _LONG_LINE_SPEC.gated_direction == "up"
        assert _BUDGET_SPEC.rel_path == _LONG_LINE_SPEC.rel_path


_FROZEN_LINE = "  docs/ROADMAP-SEMANTO.yaml: 4707  # raise-approved: dec-166 grandfather at measured size\n"
_PLATFORM_DEFERRAL = (
    "    defer_to_incumbent:\n"
    "      docs/ROADMAP-PLATFORM.yaml:\n"
    "        incumbent: validate_platform_roadmap\n"
    "        authorized_by: dec-166\n"
)
_FROZEN_NOTE = (
    "budget_notes:\n"
    "  docs/ROADMAP-SEMANTO.yaml:\n"
    "    frozen: true\n"
    "    rationale: operator freeze pending the warehouse-graph migration\n"
    "    retires_when: the migration has shrunk or deleted the file\n"
)
_FROZEN_BASE = (
    "schema_version: 1\n"
    "classes:\n"
    "  - slug: roadmaps\n"
    '    include: ["docs/ROADMAP-*.yaml"]\n'
    "    governed: true\n"
    "    limit: 500\n"
    "    max_line_chars: 2000\n"
    + _PLATFORM_DEFERRAL
    + "budgets:\n"
    + _FROZEN_LINE
    + "  config/heavy.yaml: 800\n"
    + _FROZEN_NOTE
    + "long_line_budgets: {}\n"
)


def _e4(base: str, current: str) -> list[str]:
    return [v for v in frozen_base_violations(base, current) if v.startswith("E4:")]


class TestFrozenBaseViolations:
    """Mirror tests for the base-aware half of E4. Every leg reads frozen-ness from BASE."""

    def test_no_op_diff_is_clean(self) -> None:
        assert frozen_base_violations(_FROZEN_BASE, _FROZEN_BASE) == []

    def test_increase_on_a_frozen_entry_reds_despite_its_committed_marker(self) -> None:
        """Proof B: _marker_guard.authorization_failure matches the entry KEY, never its
        VALUE, so one committed marker would otherwise authorize every future raise."""
        current = _FROZEN_BASE.replace("4707  #", "9999  #")
        assert _e4(_FROZEN_BASE, current)

    def test_shrink_on_a_frozen_entry_passes(self) -> None:
        """Direction, not equality: compaction is this class's own first-listed relief valve
        and the engine has no --update-sloc-budgets equivalent to absorb a re-pin."""
        current = _FROZEN_BASE.replace("4707  #", "4000  #")
        assert frozen_base_violations(_FROZEN_BASE, current) == []

    def test_non_frozen_entry_is_unaffected_by_the_frozen_legs(self) -> None:
        current = _FROZEN_BASE.replace("config/heavy.yaml: 800", "config/heavy.yaml: 9000")
        assert frozen_base_violations(_FROZEN_BASE, current) == []

    def test_one_commit_unfreeze_and_raise_reds(self) -> None:
        """Leg 1 reads frozen-ness from BASE, so deleting the freeze in the same commit that
        raises the pin does not make the raise invisible."""
        current = _FROZEN_BASE.replace("frozen: true", "frozen: false").replace("4707  #", "9999  #")
        assert _e4(_FROZEN_BASE, current)

    def test_unfreeze_at_an_unchanged_value_reds_while_the_entry_survives(self) -> None:
        """Leg 2 closes the two-PR escape: PR 1 unfreezes at an unchanged value (no increase,
        clean under leg 1 alone), PR 2 then raises against an already-innocent base."""
        current = _FROZEN_BASE.replace("frozen: true", "frozen: false")
        violations = _e4(_FROZEN_BASE, current)
        assert any("un-freezing is itself the escape" in v for v in violations)

    def test_unfreeze_with_the_entry_removed_together_is_the_clean_retirement(self) -> None:
        """The prescribed retirement shape. Deliberately NOT collapsed into the leg-2 test:
        the two are the same edit, distinguished only by whether the entry survives."""
        current = _FROZEN_BASE.replace("frozen: true", "frozen: false").replace(_FROZEN_LINE, "")
        assert frozen_base_violations(_FROZEN_BASE, current) == []

    def test_frozen_in_base_and_newly_deferred_reds_even_with_the_note_deleted(self) -> None:
        """Leg 3 closes the MEASURED bypass: a deferred path is dropped before measurement,
        so deferring a frozen file to a size-less incumbent un-governs it and reports green."""
        added = _PLATFORM_DEFERRAL + (
            "      docs/ROADMAP-SEMANTO.yaml:\n        incumbent: validate_platform_roadmap\n        authorized_by: dec-166\n"
        )
        current = _FROZEN_BASE.replace(_PLATFORM_DEFERRAL, added).replace(_FROZEN_NOTE, "").replace(_FROZEN_LINE, "")
        violations = _e4(_FROZEN_BASE, current)
        assert any("NEWLY deferred" in v for v in violations)

    def test_already_deferred_in_base_is_not_reported_as_newly_deferred(self) -> None:
        assert frozen_base_violations(_FROZEN_BASE, _FROZEN_BASE) == []

    def test_unparseable_current_text_reports_never_raises(self) -> None:
        violations = frozen_base_violations(_FROZEN_BASE, "budgets:\n  - [unbalanced\n")
        assert violations and violations[0].startswith("E4:")

    def test_non_mapping_registry_text_yields_no_frozen_state(self) -> None:
        """A registry that parses to a list or a scalar carries no budgets and no notes, so
        there is nothing frozen to guard -- report nothing rather than raising on .get()."""
        assert frozen_base_violations("- a\n- b\n", _FROZEN_BASE) == []

    def test_absent_registry_file_evaluates_no_frozen_leg(self, tmp_path: Path) -> None:
        """Mirrors check_diff's own not-found contract: with no current registry on disk
        there is nothing to diff, and the base_reader is never consulted."""
        _write_decisions(tmp_path, [166], mentions={166: "docs/ROADMAP-SEMANTO.yaml"})
        calls: list[str] = []

        def _recording_reader(rel: str) -> str:
            calls.append(rel)
            return _FROZEN_BASE

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            with contextlib.redirect_stdout(io.StringIO()):
                validate_structural_size_budget_raises(failed, base_reader=_recording_reader)

        assert failed == []
        assert calls == []

    def test_wired_into_the_check_and_reds_the_gate(self, tmp_path: Path) -> None:
        current = _FROZEN_BASE.replace("4707  #", "9999  #")
        _write_current(tmp_path, current)
        _write_decisions(tmp_path, [166], mentions={166: "docs/ROADMAP-SEMANTO.yaml"})
        base_reader = lambda rel: _FROZEN_BASE  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_budget_raises(failed, base_reader=base_reader)

        assert len(failed) == 1
        assert "is frozen in the base config" in buf.getvalue()


class TestBaseUnreachableContract:
    def test_unreachable_base_skips_and_evaluates_no_frozen_leg(self, tmp_path: Path) -> None:
        """The pure-text signature moves check_diff's base_text-is-None contract onto the
        caller, so the caller must propagate None as a SKIP rather than coercing it to "".

        THE THIRD ASSERTION IS THE ONE THAT BITES. check_diff prints its SKIP line
        unconditionally off its own base_text-is-None branch, so the SKIP-line assertion
        still passes under a `reader(rel) or ""` implementation -- and an empty base text
        yields an empty base map, which makes every frozen leg silently vacuous and leaves
        `failed` empty too. Only proving that NO frozen leg ran distinguishes an unreachable
        base from a genuine pass.
        """
        _write_current(tmp_path, _FROZEN_BASE)
        _write_decisions(tmp_path, [166], mentions={166: "docs/ROADMAP-SEMANTO.yaml"})

        calls: list[tuple[str, str]] = []

        def _sentinel(base_text: str, current_text: str) -> list[str]:
            # Returns CLEAN deliberately, so `failed` stays empty in both worlds and the
            # call record is the sole discriminator -- exactly the asymmetry being pinned.
            calls.append((base_text, current_text))
            return []

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks.structural.budget_raises.frozen_base_violations", _sentinel),
        ):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_budget_raises(failed, base_reader=lambda rel: None)

        output = buf.getvalue()
        assert "SKIP: origin/main unreachable" in output
        assert failed == []
        assert calls == [], "a frozen leg was evaluated on an unreachable base"

    def test_a_genuine_clean_pass_prints_no_skip_line(self, tmp_path: Path) -> None:
        """Both outcomes leave `failed` empty, so the SKIP line is the only thing that tells
        an unreachable base apart from a real pass."""
        _write_current(tmp_path, _FROZEN_BASE)
        _write_decisions(tmp_path, [166], mentions={166: "docs/ROADMAP-SEMANTO.yaml"})
        base_reader = lambda rel: _FROZEN_BASE  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_budget_raises(failed, base_reader=base_reader)

        output = buf.getvalue()
        assert failed == []
        assert "SKIP" not in output
        assert "No unauthorized structural-size budget raises." in output


def _declared(tmp_path: Path, base_text: str | None) -> tuple[list[str], registry._Declaration | None]:
    registry.pop_declaration()
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path), contextlib.redirect_stdout(io.StringIO()):
        validate_structural_size_budget_raises(failed, base_reader=lambda _rel: base_text)
    return failed, registry.pop_declaration()


_TWO_SECTION_BODY = (
    _BASE_CLASSES_BLOCK
    + "budgets:\n  config/heavy.yaml: 800\n  config/other.yaml: 700\n"
    + "long_line_budgets:\n  config/long.yaml: 2200\n"
)


class TestExaminedDeclaration:
    """Decision 170: skipped when the registry or its base is unavailable, else examined over
    the entries of both sections the diff legs judged."""

    def test_missing_registry_declares_skipped(self, tmp_path: Path) -> None:
        failed, decl = _declared(tmp_path, _TWO_SECTION_BODY)
        assert failed == []
        assert decl is not None
        assert decl.kind == "skipped"
        assert decl.reason == "config/structural_size_budgets.yaml not found"

    def test_unreachable_base_declares_skipped(self, tmp_path: Path) -> None:
        _write_current(tmp_path, _TWO_SECTION_BODY)
        _write_decisions(tmp_path, [])
        failed, decl = _declared(tmp_path, None)
        assert failed == []
        assert decl is not None
        assert decl.kind == "skipped"
        assert decl.reason == "origin/main unreachable"
        outcome = registry.build_outcome("validate_structural_size_budget_raises", "check", decl, appended_to_failed=False)
        assert outcome.status == "skipped"

    def test_unreachable_base_still_fails_on_unauthorized_present_marker(self, tmp_path: Path) -> None:
        body = _BASE_CLASSES_BLOCK + "budgets:\n  config/heavy.yaml: 900  # raise-approved: dec-166 x\nlong_line_budgets: {}\n"
        _write_current(tmp_path, body)
        _write_decisions(tmp_path, [166])
        failed, decl = _declared(tmp_path, None)
        assert len(failed) == 1
        assert decl is not None
        assert decl.kind == "skipped"
        outcome = registry.build_outcome("validate_structural_size_budget_raises", "check", decl, appended_to_failed=True)
        assert outcome.status == "failed"

    def test_clean_registry_counts_entries_of_both_sections(self, tmp_path: Path) -> None:
        _write_current(tmp_path, _TWO_SECTION_BODY)
        _write_decisions(tmp_path, [])
        failed, decl = _declared(tmp_path, _TWO_SECTION_BODY)
        assert failed == []
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == 3
        assert decl.unit == "entries"
        outcome = registry.build_outcome("validate_structural_size_budget_raises", "check", decl, appended_to_failed=False)
        assert outcome.status == "enforced"

    def test_failing_diff_still_declares_examined_count(self, tmp_path: Path) -> None:
        _write_current(tmp_path, _TWO_SECTION_BODY.replace("config/long.yaml: 2200", "config/long.yaml: 2900"))
        _write_decisions(tmp_path, [])
        failed, decl = _declared(tmp_path, _TWO_SECTION_BODY)
        assert len(failed) == 1
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == 3

    def test_empty_sections_declare_vacuous(self, tmp_path: Path) -> None:
        _write_current(tmp_path, _BASE_CLASSES_BLOCK + "budgets: {}\nlong_line_budgets: {}\n")
        _write_decisions(tmp_path, [])
        failed, decl = _declared(tmp_path, _TWO_SECTION_BODY)
        assert failed == []
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == 0
        outcome = registry.build_outcome("validate_structural_size_budget_raises", "check", decl, appended_to_failed=False)
        assert outcome.status == "vacuous"

    def test_base_is_read_once_for_every_leg(self, tmp_path: Path) -> None:
        _write_current(tmp_path, _TWO_SECTION_BODY)
        _write_decisions(tmp_path, [])
        calls: list[str] = []

        def _recording_reader(rel: str) -> str:
            calls.append(rel)
            return _TWO_SECTION_BODY

        with patch("scripts.checks._common.ROOT", tmp_path), contextlib.redirect_stdout(io.StringIO()):
            validate_structural_size_budget_raises([], base_reader=_recording_reader)
        assert calls.count("config/structural_size_budgets.yaml") == 1
        assert set(calls) <= {"config/structural_size_budgets.yaml", "docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"}

    def test_live_registry_examines_every_entry(self) -> None:
        live = (Path(__file__).resolve().parents[3] / "config" / "structural_size_budgets.yaml").read_text(encoding="utf-8")
        registry.pop_declaration()
        failed: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            validate_structural_size_budget_raises(failed, base_reader=lambda _rel: live)
        decl = registry.pop_declaration()
        assert failed == []
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == (
            len(_BUDGET_SPEC.extractor(live)) + len(_LONG_LINE_SPEC.extractor(live)) + _byte_raises.entry_count(live)
        )
        assert decl.count > 0
