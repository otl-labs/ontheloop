"""Tests for validate_reversal_stanzas() -- the SEQ-02 stanza well-formedness gate.

Self-contained (Decision 131 no-cross-test-import): patches
scripts.checks.ops_governance.validate_reversal_stanzas.evaluate directly with canned
DecisionConditionState results rather than importing another test module's fixtures.
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.checks import registry, validation_result
from scripts.checks.ops_governance.validate_reversal_stanzas import validate_reversal_stanzas
from scripts.preflight.decision_conditions import DecisionConditionState


class TestValidateReversalStanzasWellFormed:
    def test_well_formed_tree_appends_no_failure(self) -> None:
        canned = [
            DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30"),
            DecisionConditionState(decision_id=901, state="manual-review-due", review_by="2020-01-01"),
        ]
        with patch(
            "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate",
            return_value=canned,
        ):
            failed: list[str] = []
            validate_reversal_stanzas(failed)
        assert failed == [], f"Expected no failure on a well-formed tree, got: {failed}"

    def test_manual_condition_without_predicate_params_is_not_flagged(self) -> None:
        """A kind: manual state (no predicate/params, mirroring Decision 133's
        platform-mvp-closes condition) must never itself be treated as malformed by this gate --
        the manual/repo_state optionality is validated inside evaluate(), and a non-MALFORMED
        state from evaluate() must pass through untouched here."""
        canned = [DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30")]
        with patch(
            "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate",
            return_value=canned,
        ):
            failed: list[str] = []
            validate_reversal_stanzas(failed)
        assert failed == []


class TestValidateReversalStanzasMalformed:
    def test_single_malformed_entry_appends_one_failure_naming_the_decision(self) -> None:
        canned = [
            DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30"),
            DecisionConditionState(decision_id=905, state="MALFORMED", error="stanza 'decision: 999' mismatch"),
        ]
        with patch(
            "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate",
            return_value=canned,
        ):
            failed: list[str] = []
            validate_reversal_stanzas(failed)
        assert len(failed) == 1
        assert "905" in failed[0]
        assert "MALFORMED" in failed[0]

    def test_one_failure_per_malformed_variant(self) -> None:
        canned = [
            DecisionConditionState(decision_id=906, state="MALFORMED", error="unclosed fence"),
            DecisionConditionState(decision_id=907, state="MALFORMED", error="unregistered predicate"),
            DecisionConditionState(decision_id=908, state="MALFORMED", error="unknown kind"),
        ]
        with patch(
            "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate",
            return_value=canned,
        ):
            failed: list[str] = []
            validate_reversal_stanzas(failed)
        assert len(failed) == 3
        assert "906" in failed[0]
        assert "907" in failed[1]
        assert "908" in failed[2]

    def test_evaluate_raising_fails_loud_not_silently(self) -> None:
        """evaluate() raising must append a failure -- never silently pass a broken tree
        (Decision 55: fail loud at the call site)."""
        with patch(
            "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate",
            side_effect=RuntimeError("boom"),
        ):
            failed: list[str] = []
            validate_reversal_stanzas(failed)
        assert len(failed) == 1
        assert "boom" in failed[0]


class TestValidateReversalStanzasRealTree:
    def test_real_decisions_md_is_well_formed(self) -> None:
        """No mocking -- runs the real evaluate() over the real DECISIONS.md/DECISIONS_ARCHIVE.md.
        Confirms the current committed tree (including Decision 133 and Decision 134) is clean."""
        failed: list[str] = []
        validate_reversal_stanzas(failed)
        assert failed == [], f"Real DECISIONS.md tree has malformed reversal-conditions stanza(s): {failed}"


class TestReversalStanzasAccountingDeclaration:
    """The check declares how many stanzas evaluate() returned, so a run is never recorded as undeclared."""

    _EVALUATE = "scripts.checks.ops_governance.validate_reversal_stanzas.evaluate"

    @classmethod
    def _run(cls, canned: list[DecisionConditionState]) -> tuple[list[str], registry._Declaration | None]:
        registry.pop_declaration()
        failed: list[str] = []
        with patch(cls._EVALUATE, return_value=canned):
            validate_reversal_stanzas(failed)
        return failed, registry.pop_declaration()

    @classmethod
    def _outcome(cls, canned: list[DecisionConditionState]) -> registry.CheckOutcome:
        validation_result._OUTCOMES.clear()
        try:
            with patch(cls._EVALUATE, return_value=canned):
                validation_result.dispatch_recording("validate_reversal_stanzas", [], validate_reversal_stanzas)
            (outcome,) = validation_result._OUTCOMES
        finally:
            validation_result._OUTCOMES.clear()
        return outcome

    def test_declares_every_evaluated_stanza(self) -> None:
        canned = [
            DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30"),
            DecisionConditionState(decision_id=901, state="manual-review-due", review_by="2020-01-01"),
            DecisionConditionState(decision_id=902, state="not-due", review_by="2027-01-01"),
        ]
        failed, declaration = self._run(canned)
        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 3, "reversal_stanzas")

    def test_declared_count_tracks_the_input(self) -> None:
        one = [DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30")]
        two = one + [DecisionConditionState(decision_id=134, state="not-due", review_by="2026-09-30")]
        _, first = self._run(one)
        _, second = self._run(two)
        assert first is not None and second is not None
        assert (first.count, second.count) == (1, 2)

    def test_malformed_stanzas_are_counted_as_examined(self) -> None:
        canned = [
            DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30"),
            DecisionConditionState(decision_id=905, state="MALFORMED", error="unclosed fence"),
        ]
        failed, declaration = self._run(canned)
        assert len(failed) == 1
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 2, "reversal_stanzas")

    def test_no_stanzas_declares_zero_examined(self) -> None:
        failed, declaration = self._run([])
        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 0, "reversal_stanzas")

    def test_clean_run_is_recorded_enforced(self) -> None:
        canned = [DecisionConditionState(decision_id=133, state="not-due", review_by="2026-09-30")]
        outcome = self._outcome(canned)
        assert (outcome.status, outcome.examined_count, outcome.examined_unit) == ("enforced", 1, "reversal_stanzas")

    def test_no_stanzas_is_recorded_vacuous(self) -> None:
        outcome = self._outcome([])
        assert (outcome.status, outcome.examined_count, outcome.examined_unit) == ("vacuous", 0, "reversal_stanzas")


class TestGateModeAndCeiling:
    @staticmethod
    def _md(tmp_path, stanzas: list[tuple[int, str, str]]):
        blocks = "".join(
            f"## Decision {n}: F\n\n```yaml reversal-conditions\ndecision: {n}\nreview_by: {d}\non_trigger: x\n"
            f"conditions:\n{cond}```\n\n"
            for n, d, cond in stanzas
        )
        path = tmp_path / "DECISIONS.md"
        path.write_text(blocks, encoding="utf-8")
        return path

    def test_gate_skips_history_predicates_and_runs_the_rest(self, tmp_path, monkeypatch) -> None:
        from scripts.preflight import decision_conditions as dc

        calls: list[str] = []

        def hist(window_days: int, max_entries_per_plan: float, as_of=None) -> bool:
            calls.append("hist")
            raise RuntimeError("shallow clone")

        def safe(n: int) -> bool:
            calls.append("safe")
            raise RuntimeError("gate-safe predicate raised")

        monkeypatch.setitem(dc._PREDICATE_REGISTRY, "decision_entries_per_plan_exceed", hist)
        monkeypatch.setitem(dc._PREDICATE_REGISTRY, "gate_safe_fixture", safe)
        hist_cond = (
            "  - id: h\n    kind: repo_state\n    description: d\n    predicate: decision_entries_per_plan_exceed\n"
            "    params: {window_days: 90, max_entries_per_plan: 0.3}\n    fires: at_review\n"
        )
        path = self._md(tmp_path, [(901, "2020-01-01", hist_cond)])
        monkeypatch.setattr(dc, "_DECISIONS_MD_PATHS", [path])
        failed: list[str] = []
        validate_reversal_stanzas(failed)
        assert failed == [] and calls == []

        safe_cond = (
            "  - id: s\n    kind: repo_state\n    description: d\n    predicate: gate_safe_fixture\n    params: {n: 1}\n"
        )
        path = self._md(tmp_path, [(902, "2999-01-01", safe_cond)])
        monkeypatch.setattr(dc, "_DECISIONS_MD_PATHS", [path])
        failed = []
        validate_reversal_stanzas(failed)
        assert calls == ["safe"] and len(failed) == 1 and "902" in failed[0] and "MALFORMED" in failed[0]

    def test_date_over_the_ceiling_fails_and_names_the_command(self, tmp_path, monkeypatch) -> None:
        from scripts.preflight import decision_conditions as dc

        manual = "  - id: c\n    kind: manual\n    description: d\n"
        ok = self._md(tmp_path, [(n, "2027-01-01", manual) for n in range(1, 11)])
        monkeypatch.setattr(dc, "_DECISIONS_MD_PATHS", [ok])
        failed: list[str] = []
        validate_reversal_stanzas(failed)
        assert failed == []

        over = self._md(tmp_path, [(n, "2027-01-01", manual) for n in range(1, 12)])
        monkeypatch.setattr(dc, "_DECISIONS_MD_PATHS", [over])
        failed = []
        validate_reversal_stanzas(failed)
        assert len(failed) == 1 and "2027-01-01" in failed[0] and "11 stanzas" in failed[0]
        assert "scripts.preflight.review_dates --next" in failed[0]
