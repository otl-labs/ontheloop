"""Tests for scripts/preflight/decision_conditions.py (audit SEQ-02, Decision 133 follow-on).

Covers: the parser + state machine (fired > manual-review-due > not-due, MALFORMED on any
structural error), the injected-predicate 'fired' path (never mutating the production registry),
fail-loud-at-the-call-site behaviour for every malformed variant, prose-only decisions staying
unmonitored, preflight_bucket()'s resilience contract, and the CLI's exit-code behaviour.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.preflight import decision_conditions as dc

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "decision_conditions"


class TestFenceExtraction:
    def test_no_fence_is_not_found(self) -> None:
        found, text, error = dc._extract_stanza("no fence here at all")
        assert found is False
        assert text is None
        assert error is None

    def test_closed_fence_extracts_text(self) -> None:
        block = "prose\n```yaml reversal-conditions\ndecision: 1\n```\nmore prose"
        found, text, error = dc._extract_stanza(block)
        assert found is True
        assert error is None
        assert text is not None
        assert "decision: 1" in text

    def test_unclosed_fence_reports_error(self) -> None:
        block = "prose\n```yaml reversal-conditions\ndecision: 1\nno closing marker"
        found, text, error = dc._extract_stanza(block)
        assert found is True
        assert text is None
        assert error is not None
        assert "unclosed" in error.lower()

    def test_indented_closing_fence_is_not_recognized_bad_fence_variant(self) -> None:
        """A closing ``` that is not at column 0 is invisible to the column-0-anchored scan --
        the parser contract requires column-0 fences, so this degrades to 'unclosed', not a
        silently-accepted stanza with trailing garbage folded in."""
        block = "```yaml reversal-conditions\ndecision: 1\n  ```\nmore prose, never closed properly"
        found, text, error = dc._extract_stanza(block)
        assert found is True
        assert text is None
        assert error is not None
        assert "unclosed" in error.lower()

    def test_non_matching_info_string_is_not_a_stanza(self) -> None:
        """A fence with a different info-string (typo, wrong language tag) is not detected at
        all -- it is simply not monitored, the same as having no fence."""
        block = "```yaml reversal-conditions-typo\ndecision: 1\n```\n"
        found, text, error = dc._extract_stanza(block)
        assert found is False
        assert text is None
        assert error is None


class TestNormalizeReviewBy:
    def test_unquoted_date_passes_through(self) -> None:
        assert dc._normalize_review_by(date(2026, 9, 30)) == date(2026, 9, 30)

    def test_datetime_value_is_downcast_to_date(self) -> None:
        """yaml.safe_load coerces an unquoted 'review_by: 2026-09-30 10:00:00' (date + time) to a
        datetime.date, not datetime.date -- must be downcast to .date() before compare."""
        from datetime import datetime

        assert dc._normalize_review_by(datetime(2026, 9, 30, 10, 0, 0)) == date(2026, 9, 30)

    def test_quoted_string_parses(self) -> None:
        assert dc._normalize_review_by("2026-09-30") == date(2026, 9, 30)

    def test_unparseable_string_returns_none(self) -> None:
        assert dc._normalize_review_by("not-a-date") is None

    def test_wrong_type_returns_none(self) -> None:
        assert dc._normalize_review_by(12345) is None


class TestStateMachine:
    def test_past_review_by_fixture_is_manual_review_due(self) -> None:
        results = dc.evaluate(paths=[_FIXTURES / "past_review.txt"])
        assert len(results) == 1
        assert results[0].decision_id == 901
        assert results[0].state == "manual-review-due"

    def test_future_review_by_is_not_due(self) -> None:
        results = dc.evaluate(paths=[_FIXTURES / "not_due_133_like.txt"])
        assert len(results) == 1
        assert results[0].decision_id == 903
        assert results[0].state == "not-due"

    def test_injected_predicate_fires(self) -> None:
        """The fired_predicate.txt fixture's predicate is injected here -- it is never present
        in the production _PREDICATE_REGISTRY, so this proves the predicates= override path
        without mutating production state."""
        assert "always-true-fixture-predicate" not in dc._PREDICATE_REGISTRY
        results = dc.evaluate(
            paths=[_FIXTURES / "fired_predicate.txt"],
            predicates={"always-true-fixture-predicate": lambda: True},
        )
        assert len(results) == 1
        assert results[0].state == "fired"
        assert results[0].fired_condition_ids == ["always-fires"]
        assert "always-true-fixture-predicate" not in dc._PREDICATE_REGISTRY

    def test_fired_outranks_manual_review_due(self) -> None:
        """A stanza whose review_by is already past AND has a firing repo_state condition
        reports 'fired', not 'manual-review-due' -- fired takes state-machine priority."""
        content = (
            "## Decision 950: Fixture -- fired outranks manual-review-due (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 950\n"
            "review_by: 2020-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: firing-condition\n"
            "    kind: repo_state\n"
            "    predicate: always-fires\n"
            "    params: {}\n"
            '    description: "fires unconditionally"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path], predicates={"always-fires": lambda: True})
        assert results[0].state == "fired"

    def test_manual_and_null_predicate_conditions_never_auto_fire(self) -> None:
        results = dc.evaluate(paths=[_FIXTURES / "not_due_133_like.txt"])
        assert results[0].fired_condition_ids == []
        kinds = {c.kind for c in results[0].conditions}
        assert kinds == {"manual", "repo_state"}
        for cond in results[0].conditions:
            if cond.kind == "repo_state":
                assert cond.predicate is None
                assert cond.fired is False

    def test_manual_condition_with_only_id_kind_description_is_well_formed(self) -> None:
        """Mirrors Decision 133's platform-mvp-closes condition (id/kind/description only, no
        predicate/params keys at all) -- must NOT be flagged malformed."""
        results = dc.evaluate(paths=[_FIXTURES / "not_due_133_like.txt"])
        assert results[0].state != "MALFORMED"
        manual_conditions = [c for c in results[0].conditions if c.kind == "manual"]
        assert len(manual_conditions) == 1
        assert manual_conditions[0].predicate is None


class TestProseOnlyNotMonitored:
    def test_prose_only_decision_yields_no_result(self) -> None:
        results = dc.evaluate(paths=[_FIXTURES / "prose_only.txt"])
        assert results == []


class TestMalformedFixtures:
    @pytest.mark.parametrize(
        "fixture_name,decision_id",
        [
            ("malformed_bad_decision_key.txt", 905),
            ("malformed_unclosed_fence.txt", 906),
            ("malformed_unknown_predicate.txt", 907),
            ("malformed_bad_kind.txt", 908),
        ],
    )
    def test_committed_malformed_fixtures_fail_loud(self, fixture_name: str, decision_id: int) -> None:
        results = dc.evaluate(paths=[_FIXTURES / fixture_name])
        assert len(results) == 1
        assert results[0].decision_id == decision_id
        assert results[0].state == "MALFORMED"
        assert results[0].error

    def test_bad_fence_inline_variant(self) -> None:
        """Non-column-0 closing marker -- a 'bad fence' shape that degrades to the same
        unclosed-fence MALFORMED path (the parser contract requires column-0 fences)."""
        content = (
            "## Decision 951: Fixture -- bad (indented) closing fence (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 951\n"
            "review_by: 2099-01-01\n"
            "  ```\n"
            "trailing content never recognized as closing the stanza\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert len(results) == 1
        assert results[0].state == "MALFORMED"
        assert "unclosed" in (results[0].error or "").lower()

    def test_non_loadable_yaml_inline_variant(self) -> None:
        content = (
            "## Decision 952: Fixture -- non-loadable YAML inside the fence (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 952\n"
            "review_by: [unterminated flow sequence\n"
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert len(results) == 1
        assert results[0].state == "MALFORMED"
        assert "yaml" in (results[0].error or "").lower()

    def test_malformed_never_raises_at_import_or_module_load(self) -> None:
        """Re-importing the module and calling evaluate() on a malformed fixture must never
        raise -- the failure is collected as a MALFORMED result at the call site."""
        import importlib

        importlib.reload(dc)
        results = dc.evaluate(paths=[_FIXTURES / "malformed_bad_decision_key.txt"])
        assert results[0].state == "MALFORMED"

    def test_missing_conditions_list_is_malformed(self) -> None:
        content = (
            "## Decision 953: Fixture -- missing conditions list (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 953\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"

    def test_missing_top_level_key_is_malformed(self) -> None:
        content = (
            "## Decision 954: Fixture -- missing top-level review_by key (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 954\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: manual\n"
            '    description: "test"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"

    def test_condition_missing_required_key_is_malformed(self) -> None:
        content = (
            "## Decision 955: Fixture -- condition missing description (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 955\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: manual\n"
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"

    def test_repo_state_missing_predicate_key_is_malformed(self) -> None:
        content = (
            "## Decision 956: Fixture -- repo_state condition with no predicate key at all (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 956\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: repo_state\n"
            '    description: "repo_state condition missing the predicate key entirely"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"

    def test_stanza_parses_to_non_mapping_is_malformed(self) -> None:
        """A stanza that is syntactically valid YAML but not a mapping at all (a bare scalar or
        a bare list) must be rejected before any top-level-key lookup is attempted."""
        content = (
            "## Decision 961: Fixture -- stanza parses to a bare scalar, not a mapping (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "just a bare scalar string, not a mapping\n"
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "mapping" in (results[0].error or "").lower()

    def test_stanza_parses_to_a_list_is_malformed(self) -> None:
        content = (
            "## Decision 962: Fixture -- stanza parses to a bare list, not a mapping (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "- item-one\n"
            "- item-two\n"
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "mapping" in (results[0].error or "").lower()

    def test_unparseable_review_by_value_is_malformed(self) -> None:
        content = (
            "## Decision 963: Fixture -- review_by is not a date-shaped value at all (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 963\n"
            "review_by: not-a-date\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: manual\n"
            '    description: "test"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "review_by" in (results[0].error or "")

    def test_conditions_present_but_not_a_list_is_malformed(self) -> None:
        content = (
            "## Decision 964: Fixture -- conditions key present but not a list (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 964\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            'conditions: "not-a-list"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "conditions" in (results[0].error or "").lower()

    def test_conditions_present_but_empty_list_is_malformed(self) -> None:
        content = (
            "## Decision 965: Fixture -- conditions key present but an empty list (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 965\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions: []\n"
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "conditions" in (results[0].error or "").lower()

    def test_condition_entry_not_a_mapping_is_malformed(self) -> None:
        """A conditions[] list whose entries are bare strings, not mappings, must be rejected by
        _evaluate_condition() before any key lookup."""
        content = (
            "## Decision 966: Fixture -- a condition list entry is a bare string, not a mapping (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 966\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            '  - "not-a-mapping"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path])
        assert results[0].state == "MALFORMED"
        assert "mapping" in (results[0].error or "").lower()


class TestEvaluatePathHandling:
    def test_nonexistent_path_is_skipped_not_an_error(self, tmp_path: Path) -> None:
        """A path that does not exist is silently skipped (not every configured DECISIONS.md-
        shaped path needs to exist -- DECISIONS_ARCHIVE.md itself is optional in some contexts)."""
        missing = tmp_path / "does-not-exist.txt"
        assert not missing.exists()
        results = dc.evaluate(paths=[missing])
        assert results == []

    def test_nonexistent_path_alongside_a_real_one_still_evaluates_the_real_one(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.txt"
        results = dc.evaluate(paths=[missing, _FIXTURES / "past_review.txt"])
        assert len(results) == 1
        assert results[0].decision_id == 901


class TestPredicateException:
    def test_predicate_raising_is_malformed_not_propagated(self) -> None:
        """A registered predicate that raises must fail loud at the call site (collected into
        the MALFORMED result), never propagate as an uncaught exception."""

        def _boom() -> bool:
            raise RuntimeError("boom")

        content = (
            "## Decision 957: Fixture -- predicate raises (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 957\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: repo_state\n"
            "    predicate: boom-predicate\n"
            "    params: {}\n"
            '    description: "predicate that raises"\n'
            "```\n"
        )
        path = tmp_decisions_file(content)
        results = dc.evaluate(paths=[path], predicates={"boom-predicate": _boom})
        assert results[0].state == "MALFORMED"
        assert "boom" in (results[0].error or "")


class TestPreflightBucket:
    def test_bucket_shape_on_real_decisions_md(self) -> None:
        bucket = dc.preflight_bucket()
        assert set(bucket) == {"monitored", "surfaced", "malformed"}
        assert bucket["monitored"] and 133 not in bucket["monitored"] and "133" not in str(bucket["surfaced"])
        assert bucket["malformed"] == []

    def test_bucket_never_raises_on_a_broken_paths_arg(self) -> None:
        """preflight_bucket() must degrade gracefully (never raise) even on a pathological
        input -- e.g. a path object that raises when read."""

        class _ExplodingPath:
            def exists(self) -> bool:
                return True

            def read_text(self, *args: object, **kwargs: object) -> str:
                raise OSError("disk exploded")

        bucket = dc.preflight_bucket(paths=[_ExplodingPath()])  # type: ignore[list-item]
        assert "error" in bucket
        assert bucket["monitored"] == []
        assert bucket["surfaced"] == []
        assert bucket["malformed"] == []

    def test_surfaced_ranks_fired_before_manual_review_due(self) -> None:
        content_fired = (
            "## Decision 960: Fixture -- fired for ranking test (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 960\n"
            "review_by: 2099-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: repo_state\n"
            "    predicate: always-fires-960\n"
            "    params: {}\n"
            '    description: "fires"\n'
            "```\n"
        )
        content_due = (
            "## Decision 959: Fixture -- manual-review-due for ranking test (Decided)\n\n"
            "```yaml reversal-conditions\n"
            "decision: 959\n"
            "review_by: 2020-01-01\n"
            'on_trigger: "re-decide via /plan"\n'
            "conditions:\n"
            "  - id: c1\n"
            "    kind: manual\n"
            '    description: "context only"\n'
            "```\n"
        )
        path = tmp_decisions_file(content_due + "\n" + content_fired)
        bucket = dc.preflight_bucket(paths=[path], predicates={"always-fires-960": lambda: True})
        assert [row["state"] for row in bucket["surfaced"]] == ["fired", "manual-review-due"]

    def test_print_decision_conditions_handles_degraded_bucket(self, capsys: pytest.CaptureFixture) -> None:
        dc.print_decision_conditions({"error": "boom"})
        out = capsys.readouterr().out
        assert "DEGRADED" in out

    def test_print_decision_conditions_handles_empty_bucket(self, capsys: pytest.CaptureFixture) -> None:
        dc.print_decision_conditions({"monitored": [], "surfaced": [], "malformed": []})
        out = capsys.readouterr().out
        assert "(none)" in out

    def test_print_decision_conditions_renders_fired_and_malformed_rows(self, capsys: pytest.CaptureFixture) -> None:
        """The three highest-signal operator-facing render branches: a FIRED surfaced row (naming
        its fired_condition_ids), a manual-review-due surfaced row (REVIEW DUE), and a MALFORMED
        row (naming the decision + error)."""
        bucket = {
            "monitored": [969, 970, 971],
            "surfaced": [
                {"decision": 969, "state": "manual-review-due", "review_by": "2020-01-01", "fired_condition_ids": []},
                {"decision": 970, "state": "fired", "review_by": "2099-01-01", "fired_condition_ids": ["cond-a", "cond-b"]},
            ],
            "malformed": [{"decision": 971, "error": "unclosed fence"}],
        }
        dc.print_decision_conditions(bucket)
        out = capsys.readouterr().out
        assert "Decision 969: REVIEW DUE (review_by 2020-01-01)" in out
        assert "Decision 970: FIRED (cond-a, cond-b)" in out
        assert "Decision 971: MALFORMED -- unclosed fence" in out


class TestPredicateRegistry:
    def test_register_predicate_decorator_adds_to_registry(self) -> None:
        """register_predicate() is the extension point for a future repo_state predicate --
        confirm the decorator actually registers the function under the given name and returns
        it unchanged (usable directly, not just as a side effect)."""
        assert "test-only-registered-predicate" not in dc._PREDICATE_REGISTRY
        try:

            @dc.register_predicate("test-only-registered-predicate")
            def _always_true() -> bool:
                return True

            assert dc._PREDICATE_REGISTRY["test-only-registered-predicate"] is _always_true
            assert dc._PREDICATE_REGISTRY["test-only-registered-predicate"]() is True
        finally:
            dc._PREDICATE_REGISTRY.pop("test-only-registered-predicate", None)


class TestCli:
    def test_cli_exits_zero_on_well_formed_fixture(self) -> None:
        exit_code = dc._cli([str(_FIXTURES / "past_review.txt")])
        assert exit_code == 0

    def test_cli_exits_nonzero_on_malformed_fixture(self) -> None:
        exit_code = dc._cli([str(_FIXTURES / "malformed_bad_kind.txt")])
        assert exit_code == 1

    def test_cli_prints_malformed_row(self, capsys: pytest.CaptureFixture) -> None:
        dc._cli([str(_FIXTURES / "malformed_bad_kind.txt")])
        out = capsys.readouterr().out
        assert "MALFORMED" in out
        assert "908" in out

    def test_cli_prints_not_due_row_and_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        exit_code = dc._cli([str(_FIXTURES / "not_due_133_like.txt")])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Decision 903: not-due (review_by 2099-09-30)" in out

    def test_cli_prints_fired_row_and_exits_zero(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """The 'fired' CLI branch requires a registered predicate (the CLI calls evaluate() with
        no predicates= override, i.e. the production registry) -- register one for the duration
        of this test only, via the public register_predicate() extension point, then remove it."""
        monkeypatch.setitem(dc._PREDICATE_REGISTRY, "always-true-fixture-predicate", lambda: True)
        exit_code = dc._cli([str(_FIXTURES / "fired_predicate.txt")])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Decision 902: fired (always-fires)" in out


def tmp_decisions_file(content: str) -> Path:
    """Write `content` to a fresh temp file under the pytest tmp dir and return its Path.

    A module-level helper (not a fixture) so it is usable from both test methods and
    parametrized cases without pytest fixture-injection ceremony; each call gets a unique file
    via tempfile so parallel test methods never collide.
    """
    import tempfile

    fd, path_str = tempfile.mkstemp(suffix=".txt", prefix="decision_conditions_test_")
    path = Path(path_str)
    path.write_text(content, encoding="utf-8")
    return path
