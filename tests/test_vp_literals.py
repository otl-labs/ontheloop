"""Tests for scripts/roadmap/vp_literals.py -- the sole home for VP-command shape analysis
(docs/contracts/vp-red-before.yaml's carrier_rule / self_satisfying_lint / unsat_guard sections).

Mirror path: flat tests/test_vp_literals.py, matching the tests/test_find_plan.py and
tests/test_plan_obligations.py convention for scripts/roadmap/*.py modules.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import yaml

from scripts.checks.verification._vp_replay_classify import OUTCOME_CLASSES
from scripts.roadmap import vp_literals

_ROOT = Path(__file__).resolve().parents[1]
_PLANS_DIR = _ROOT / "docs" / "plans"
_CONTRACT_PATH = _ROOT / "docs" / "contracts" / "vp-red-before.yaml"

# The historical PLAN-g4-byte-source-and-post-expiry-bound VP1 shape (all four grep calls
# quiet-suppressed) and its PR #1202 fix (the fourth call swapped to `-o -m1`, printing its match).
_HISTORICAL_UNSAT_COMMAND = (
    'grep -q "candidate_sizes" src/common/ducklake_maintenance_ops.py && '
    'grep -q "strict_sizes" src/common/ducklake_maintenance_ops.py && '
    'grep -q "post_expiry" src/common/ducklake_maintenance_ops.py && '
    'grep -q "drain" src/common/ducklake_maintenance_ops.py'
)
_PR_1202_FIXED_COMMAND = (
    'grep -q "candidate_sizes" src/common/ducklake_maintenance_ops.py && '
    'grep -q "strict_sizes" src/common/ducklake_maintenance_ops.py && '
    'grep -q "post_expiry" src/common/ducklake_maintenance_ops.py && '
    'grep -o -m1 "drain" src/common/ducklake_maintenance_ops.py'
)


class TestSelfSatisfyingLint:
    """ADVISORY only -- find_self_satisfying_literals never rejects, it only reports."""

    def test_literal_verbatim_in_command_is_flagged(self) -> None:
        assert vp_literals.find_self_satisfying_literals("echo MARKER", ["MARKER"]) == ["MARKER"]

    def test_literal_absent_from_command_is_not_flagged(self) -> None:
        assert vp_literals.find_self_satisfying_literals("echo MARKER", ["OTHER"]) == []

    def test_mixed_literals_report_only_the_matching_subset(self) -> None:
        found = vp_literals.find_self_satisfying_literals("echo MARKER", ["MARKER", "OTHER"])
        assert found == ["MARKER"]

    def test_empty_literals_is_a_noop(self) -> None:
        assert vp_literals.find_self_satisfying_literals("anything", []) == []

    def test_pr_1202_shape_is_self_satisfying(self) -> None:
        """The PR #1202 fix's literal 'drain' appears verbatim in its own command -- the mirror
        of TestUnsatGuard's rejection case on the pre-fix shape."""
        found = vp_literals.find_self_satisfying_literals(_PR_1202_FIXED_COMMAND, ["drain"])
        assert found == ["drain"]
        # And it is genuinely ACCEPTED (never rejected) at v5 -- the unsat guard stays silent.
        assert vp_literals.find_unsatisfiable_literals(_PR_1202_FIXED_COMMAND, ["drain"]) == []

    def test_verdict_name_is_self_satisfying_never_tautological(self) -> None:
        """The static verdict name is `self_satisfying`, deliberately NOT `tautological` --
        vp-red-before.yaml#outcome_classes freezes the latter as one of four DYNAMIC replay
        outcome classes; reusing it for this unrelated STATIC property would be drift."""
        assert vp_literals.SELF_SATISFYING_VERDICT == "self_satisfying"
        assert vp_literals.SELF_SATISFYING_VERDICT not in OUTCOME_CLASSES
        assert "tautological" in OUTCOME_CLASSES


class TestUnsatGuard:
    """HARD-FAILING -- find_unsatisfiable_literals rejects only a confidently all-suppressed
    grep/rg chain; the PR #1202 fix (one `-o` call) is never flagged."""

    def test_historical_all_suppressed_chain_is_rejected(self) -> None:
        assert vp_literals.command_is_output_suppressed(_HISTORICAL_UNSAT_COMMAND) is True
        assert vp_literals.find_unsatisfiable_literals(_HISTORICAL_UNSAT_COMMAND, ["drain"]) == ["drain"]

    def test_pr_1202_fix_is_not_rejected(self) -> None:
        assert vp_literals.command_is_output_suppressed(_PR_1202_FIXED_COMMAND) is False
        assert vp_literals.find_unsatisfiable_literals(_PR_1202_FIXED_COMMAND, ["drain"]) == []

    def test_dev_null_redirect_is_confidently_silent(self) -> None:
        assert vp_literals.command_is_output_suppressed('grep "x" file.py > /dev/null') is True

    def test_non_grep_command_fails_open(self) -> None:
        """A command this guard cannot reason about (python -c, module invocations) is never
        flagged -- a narrow regression guard for one historical shape, not a general prover."""
        assert vp_literals.command_is_output_suppressed('python -c "print(1)"') is False
        assert vp_literals.find_unsatisfiable_literals('python -c "print(1)"', ["1"]) == []

    def test_unparseable_segment_fails_open(self) -> None:
        assert vp_literals.command_is_output_suppressed('grep -q "unterminated file.py') is False

    def test_empty_literals_is_a_noop(self) -> None:
        assert vp_literals.find_unsatisfiable_literals(_HISTORICAL_UNSAT_COMMAND, []) == []


class TestExpectedLiteralEmittability:
    """rec-3900's filed acceptance probe pins this exact identifier -- exercises the combined
    unsat-guard + self-satisfying-lint truth table the module's docstring calls 'emittable'."""

    def test_suppressed_chain_literal_that_never_appears_in_the_command_is_unsatisfiable_only(self) -> None:
        """A literal absent from the command's own text is never self-satisfying by construction
        -- distinct from `drain`, which appears in BOTH the historical and PR #1202 command
        strings and would trip the self-satisfying check regardless of output suppression."""
        assert vp_literals.find_unsatisfiable_literals(_HISTORICAL_UNSAT_COMMAND, ["SIZES_VALID"]) == ["SIZES_VALID"]
        assert vp_literals.find_self_satisfying_literals(_HISTORICAL_UNSAT_COMMAND, ["SIZES_VALID"]) == []

    def test_pr_1202_literal_is_emittable_and_self_satisfying(self) -> None:
        assert vp_literals.find_unsatisfiable_literals(_PR_1202_FIXED_COMMAND, ["drain"]) == []
        assert vp_literals.find_self_satisfying_literals(_PR_1202_FIXED_COMMAND, ["drain"]) == ["drain"]

    def test_emittable_but_not_self_satisfying_literal(self) -> None:
        """A literal that is genuinely emittable (the command is not output-suppressed) but does
        not appear verbatim in the command text -- neither guard fires."""
        command = "python -m pytest tests/test_x.py -q"
        assert vp_literals.find_unsatisfiable_literals(command, ["1 passed"]) == []
        assert vp_literals.find_self_satisfying_literals(command, ["1 passed"]) == []


class TestV4CorpusInvariance:
    """Regression: every plan at schema_version 4 or below extracts a literal set identical to an
    INLINE re-implementation of the pre-relocation _BACKTICK_LITERAL_RE scan, over the live
    docs/plans corpus. Asserted SET-wise (never a count) -- the corpus grows daily."""

    _REFERENCE_BACKTICK_RE = re.compile(r"`([^`]+)`")

    def _v4_and_below_steps(self):
        for path in sorted(_PLANS_DIR.glob("PLAN-*.yaml")):
            try:
                data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict) or data.get("schema_version", 1) > 4:
                continue
            for step in data.get("verification_plan") or []:
                if isinstance(step, dict):
                    yield path.name, step

    def test_extracted_literal_sets_match_inline_reference(self) -> None:
        checked = 0
        for name, step in self._v4_and_below_steps():
            expected = step.get("expected") or ""
            reference = set(self._REFERENCE_BACKTICK_RE.findall(expected))
            actual = set(vp_literals._extract_literals(expected))
            assert actual == reference, f"{name} step {step.get('step')}: {actual} != {reference}"
            checked += 1
        assert checked > 0, "corpus scan found no verification_plan steps -- test is vacuous"


class TestPartitionCommandUnit:
    """Direct coverage for the relocated shell-command tokenizer (Decision 104 sole-home) --
    plan_document.py exercises it too, but the per-file coverage mapping for this module expects
    tests/test_vp_literals.py alone to cover vp_literals.py."""

    def test_selectable_arguments_pass_through(self) -> None:
        selectable, excluded = vp_literals._partition_command("pytest tests/test_x.py -q")
        assert selectable == ["pytest", "tests/test_x.py", "-q"]
        assert excluded == []

    def test_ignore_flag_with_equals_is_excluded(self) -> None:
        selectable, excluded = vp_literals._partition_command("pytest --ignore=tests/test_x.py tests/")
        assert excluded == ["tests/test_x.py"]
        assert selectable == ["pytest", "tests/"]

    def test_ignore_flag_with_separate_value_is_excluded(self) -> None:
        selectable, excluded = vp_literals._partition_command("pytest --deselect tests/test_x.py::test_y")
        assert excluded == ["tests/test_x.py::test_y"]
        assert selectable == ["pytest"]

    def test_unparseable_command_falls_back_to_whitespace_split(self) -> None:
        selectable, excluded = vp_literals._partition_command('pytest "unterminated')
        assert excluded == []
        assert selectable == ["pytest", '"unterminated']


class TestSelectLiteralsAndFormatPrintUnit:
    """Direct coverage for the carrier-selection helper and the red-before leg's audit-print
    formatter, both otherwise exercised only through validate_vp_replay's own test suite."""

    def test_select_literals_v5_uses_expected_literals(self) -> None:
        step = SimpleNamespace(expected_literals=["ok"], expected="prints `MISSING`")
        assert vp_literals.select_literals(step, schema_version=5) == ["ok"]

    def test_select_literals_v5_absent_expected_literals_is_empty(self) -> None:
        step = SimpleNamespace(expected_literals=None, expected="prints ok")
        assert vp_literals.select_literals(step, schema_version=5) == []

    def test_select_literals_v4_uses_backtick_scan(self) -> None:
        step = SimpleNamespace(expected_literals=None, expected="prints `ok`")
        assert vp_literals.select_literals(step, schema_version=4) == ["ok"]

    def test_format_literal_print(self) -> None:
        line = vp_literals.format_literal_print("docs/plans/PLAN-x.yaml", 3, ["ok"], 5)
        assert line == "  LITERALS: docs/plans/PLAN-x.yaml:3 expected_literals=['ok'] (schema_version 5)"


class TestSegmentAnalysisUnit:
    """Unit coverage for the segment-level helpers underneath command_is_output_suppressed."""

    def test_empty_segment_between_operators_is_dropped(self) -> None:
        assert vp_literals._segment_tokens("true && && true") == [["true"], ["true"]]

    def test_empty_tokens_is_not_confidently_silent(self) -> None:
        assert vp_literals._segment_is_confidently_silent([]) is False

    def test_grep_with_unrecognized_flag_falls_through_to_not_silent(self) -> None:
        """A grep/rg call whose flags include neither a silencing nor an unsilencing one, and no
        /dev/null redirect, is not confidently silent -- the final fallback branch."""
        assert vp_literals._segment_is_confidently_silent(["grep", "-v", "x", "file.py"]) is False
        assert vp_literals.command_is_output_suppressed('grep -v "x" file.py') is False


class TestCensusAndCli:
    """Coverage for _iter_hermetic_pre_deploy_steps_with_literals, run_census, and the --census
    CLI entry point."""

    def test_census_over_a_small_mixed_corpus(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "PLAN-a.yaml").write_text(
            yaml.dump(
                {
                    "verification_plan": [
                        {
                            "step": 1,
                            "phase": "pre-deploy",
                            "hermetic": True,
                            "command": "echo ok",
                            "expected": "prints `ok`",
                        },
                        {  # not pre-deploy -- skipped
                            "step": 2,
                            "phase": "post-deploy",
                            "hermetic": True,
                            "command": "echo ok",
                            "expected": "prints `ok`",
                        },
                        {  # no literal in expected -- skipped
                            "step": 3,
                            "phase": "pre-deploy",
                            "hermetic": True,
                            "command": "echo ok",
                            "expected": "prints ok",
                        },
                        {  # unsat: an all-suppressed grep chain
                            "step": 4,
                            "phase": "pre-deploy",
                            "hermetic": True,
                            "command": _HISTORICAL_UNSAT_COMMAND,
                            "expected": "prints `SIZES_VALID`",
                        },
                        {  # residual: emittable, but the literal never appears verbatim
                            "step": 5,
                            "phase": "pre-deploy",
                            "hermetic": True,
                            "command": "python -m pytest tests/test_x.py -q",
                            "expected": "prints `1 passed`",
                        },
                        "not-a-dict",  # malformed step entry -- skipped
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "PLAN-b.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
        (tmp_path / "PLAN-c-malformed.yaml").write_text("not: [valid, yaml,\n", encoding="utf-8")

        vp_literals.run_census(tmp_path)
        out = capsys.readouterr().out
        assert "CENSUS steps=3 literals=3 self_satisfying=1 unsat=1 residual=1" in out

    def test_main_census_flag_exits_zero(self, tmp_path: Path, capsys) -> None:
        assert vp_literals.main(["--census", str(tmp_path)]) == 0
        assert "CENSUS" in capsys.readouterr().out

    def test_main_without_args_prints_help_and_exits_one(self, capsys) -> None:
        assert vp_literals.main([]) == 1
        assert "usage" in capsys.readouterr().out.lower()


class TestContractSuppressorParity:
    """The suppressor set vp_literals declares as code and the copy docs/contracts/
    vp-red-before.yaml declares as data must stay equal -- mirrors _vp_replay_classify's own
    derive-and-assert precedent so the contract cannot drift from the code."""

    def test_contract_suppressor_set_matches_code(self) -> None:
        data = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
        contract_flags = tuple(data["unsat_guard_suppressor_flags"])
        assert contract_flags == vp_literals.SUPPRESSOR_FLAGS
