"""Tests for scripts/ci_rca/vacuous_pass.py (100% coverage)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent

from scripts.ci_rca.vacuous_pass import (  # noqa: E402
    _TEST_FILE_RE,
    _UNDETERMINED,
    compute_coverage_regression,
    compute_escape_mode,
    compute_merge_gate_test_coverage,
    deleted_test_files,
    merged_diff_files,
    parse_vacuous_pass,
)


class TestParseVacuousPass:
    def test_collected_zero_no_deselection_returns_true(self):
        log = "collected 0 items\n========================= no tests ran in 0.01s ========================="
        assert parse_vacuous_pass(log) is True

    def test_collected_zero_with_deselection_returns_false(self):
        log = "collected 1 item / 1 deselected / 0 selected\n========================= no tests ran ="
        assert parse_vacuous_pass(log) is False

    def test_multiple_items_collected_returns_false(self):
        log = "collected 5 items\n5 passed in 0.12s"
        assert parse_vacuous_pass(log) is False

    def test_no_pytest_summary_returns_undetermined(self):
        log = "Unexpected error occurred\nProcess exited with code 1"
        assert parse_vacuous_pass(log) == _UNDETERMINED

    def test_no_tests_ran_without_deselected_returns_true(self):
        log = "no tests ran in 0.00s"
        assert parse_vacuous_pass(log) is True

    def test_no_tests_ran_with_deselected_returns_false(self):
        log = "collected 2 items / 2 deselected\nno tests ran"
        assert parse_vacuous_pass(log) is False

    def test_empty_log_returns_undetermined(self):
        assert parse_vacuous_pass("") == _UNDETERMINED

    def test_case_insensitive(self):
        log = "Collected 0 Items"
        assert parse_vacuous_pass(log) is True

    def test_large_deselected_count_returns_false(self):
        log = "collected 10 items / 10 deselected / 0 selected"
        assert parse_vacuous_pass(log) is False

    def test_never_silently_false_on_ambiguous(self):
        """Non-parseable log must return undetermined, never False (Decision 55 fail-loud)."""
        result = parse_vacuous_pass("some unrelated output\nrandom lines")
        assert result == _UNDETERMINED

    def test_fixture_vacuous_collected0(self):
        log = (ROOT / "tests" / "fixtures" / "ci_rca" / "vacuous_collected0.log").read_text()
        assert parse_vacuous_pass(log) is True

    def test_fixture_all_integration_deselected(self):
        log = (ROOT / "tests" / "fixtures" / "ci_rca" / "all_integration_deselected.log").read_text()
        assert parse_vacuous_pass(log) is False

    def test_fixture_unparseable(self):
        log = (ROOT / "tests" / "fixtures" / "ci_rca" / "unparseable.log").read_text()
        assert parse_vacuous_pass(log) == _UNDETERMINED


class TestMergedDiffFiles:
    def test_returns_list_on_success(self, tmp_path):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "scripts/foo.py\ntests/test_foo.py\n"
            result = merged_diff_files()
        assert isinstance(result, list)
        assert "scripts/foo.py" in result
        assert "tests/test_foo.py" in result

    def test_returns_undetermined_on_nonzero_exit(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "fatal: ambiguous argument 'HEAD^'"
            result = merged_diff_files()
        assert result == _UNDETERMINED

    def test_returns_undetermined_on_exception(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = merged_diff_files()
        assert result == _UNDETERMINED

    def test_empty_diff_returns_empty_list(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            result = merged_diff_files()
        assert result == []


class TestDeletedTestFiles:
    def test_returns_only_test_files(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "tests/test_foo.py\nscripts/bar.py\n"
            result = deleted_test_files()
        assert isinstance(result, list)
        assert "tests/test_foo.py" in result
        assert "scripts/bar.py" not in result

    def test_returns_undetermined_on_failure(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "fatal: ambiguous argument 'HEAD^'"
            result = deleted_test_files()
        assert result == _UNDETERMINED

    def test_empty_when_no_deleted_tests(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "scripts/bar.py\n"
            result = deleted_test_files()
        assert result == []

    def test_uses_diff_filter_D(self):
        with patch("scripts.ci_rca.vacuous_pass.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            deleted_test_files()
        cmd = mock_run.call_args[0][0]
        assert "--diff-filter=D" in cmd


class TestComputeMergeGateTestCoverage:
    def test_source_only_diff_returns_not_selected(self):
        merged = ["scripts/foo.py", "scripts/bar.py"]
        result = compute_merge_gate_test_coverage("validate_sloc_limits", merged)
        assert result == "not_selected"

    def test_changed_test_file_returns_selected(self):
        merged = ["scripts/foo.py", "tests/test_foo.py"]
        result = compute_merge_gate_test_coverage("validate_sloc_limits", merged)
        assert result == "selected"

    def test_undetermined_input_returns_undetermined(self):
        result = compute_merge_gate_test_coverage("validate_sloc_limits", _UNDETERMINED)
        assert result == _UNDETERMINED

    def test_empty_diff_returns_not_selected(self):
        result = compute_merge_gate_test_coverage("validate_sloc_limits", [])
        assert result == "not_selected"

    def test_test_file_regex_matches_correctly(self):
        assert _TEST_FILE_RE.match("tests/test_foo.py")
        assert _TEST_FILE_RE.match("tests/subdir/test_bar.py")
        assert not _TEST_FILE_RE.match("scripts/test_foo.py")
        assert not _TEST_FILE_RE.match("tests/conftest.py")
        assert not _TEST_FILE_RE.match("tests/test_foo/helper.py")

    def test_integration_test_file_still_selected(self):
        # Path selection is orthogonal to -m "not integration"; selection is by path only
        merged = ["tests/test_integration_foo.py"]
        result = compute_merge_gate_test_coverage("any_check", merged)
        assert result == "selected"


class TestComputeCoverageRegression:
    def test_deleted_test_returns_true(self):
        deleted = ["tests/test_foo.py"]
        assert compute_coverage_regression(deleted) is True

    def test_no_deleted_tests_returns_false(self):
        assert compute_coverage_regression([]) is False

    def test_undetermined_returns_undetermined(self):
        assert compute_coverage_regression(_UNDETERMINED) == _UNDETERMINED

    def test_multiple_deleted_tests_returns_true(self):
        deleted = ["tests/test_a.py", "tests/test_b.py"]
        assert compute_coverage_regression(deleted) is True


class TestComputeEscapeMode:
    """c9: compute_escape_mode() returns escape_mode enum string."""

    def test_any_undetermined_input_returns_undetermined(self):
        assert compute_escape_mode(_UNDETERMINED, "selected", False, False) == _UNDETERMINED
        assert compute_escape_mode(False, _UNDETERMINED, False, False) == _UNDETERMINED
        assert compute_escape_mode(False, "selected", _UNDETERMINED, False) == _UNDETERMINED

    def test_postmerge_canary_returns_tier_misplaced(self):
        result = compute_escape_mode(False, "selected", True, False)
        assert result == "tier_misplaced"

    def test_not_selected_returns_no_premerge_gate_by_design(self):
        result = compute_escape_mode(False, "not_selected", False, False)
        assert result == "no_premerge_gate_by_design"

    def test_vacuous_pass_and_selected_returns_check_ran_vacuously(self):
        result = compute_escape_mode(True, "selected", False, False)
        assert result == "check_ran_vacuously"

    def test_false_vacuous_and_selected_returns_undetermined(self):
        result = compute_escape_mode(False, "selected", False, False)
        assert result == _UNDETERMINED

    def test_postmerge_canary_takes_precedence_over_not_selected(self):
        result = compute_escape_mode(False, "not_selected", True, False)
        assert result == "tier_misplaced"

    # -----------------------------------------------------------------
    # Precedence-hoist repair (T1.13:c9): the four rows below all fail on the pre-repair
    # ordering -- three return the undetermined sentinel (rows 1, 2, and the raw-tier row),
    # one returns tier_misplaced (row 3, since the pre-repair canary rule sat above the
    # vacuous-pass rule). The pre-existing rows above are the no-regression half and stay
    # untouched.
    # -----------------------------------------------------------------

    def test_canary_true_overrides_undetermined_vacuous_pass(self):
        """AC1: gate_is_postmerge_canary=True wins even when vacuous_pass is undetermined."""
        result = compute_escape_mode(_UNDETERMINED, "selected", True, False)
        assert result == "tier_misplaced"

    def test_canary_true_overrides_undetermined_coverage(self):
        """AC1: gate_is_postmerge_canary=True wins even when merge_gate_test_coverage is undetermined."""
        result = compute_escape_mode(False, _UNDETERMINED, True, False)
        assert result == "tier_misplaced"

    def test_vacuous_pass_preferred_over_canary_when_both_determinate(self):
        """AC2: check_ran_vacuously is preferred over tier_misplaced when both rules could fire --
        the most specific diagnosis must not be shadowed by the canary rule."""
        result = compute_escape_mode(True, "selected", True, False)
        assert result == "check_ran_vacuously"

    def test_raw_tier_not_a_gate_returns_no_premerge_gate_by_design(self):
        """AC3: a raw not_a_gate workflow tier yields the by-design value without consulting
        merge_gate_test_coverage, even when both other tri-state inputs are undetermined."""
        result = compute_escape_mode(_UNDETERMINED, _UNDETERMINED, False, False, workflow_tier_raw="not_a_gate")
        assert result == "no_premerge_gate_by_design"

    # -----------------------------------------------------------------
    # Rule-5 precondition (no-regression half): narrowing the abstention guard to
    # vacuous_pass/merge_gate_test_coverage only must NOT let an undetermined canary be
    # silently answered as if it were known false. Neither existing row covers this cell --
    # a guard-narrowing alone (without threading `gate_is_postmerge_canary is False` onto rule
    # 5's own precondition) would flip both of these from abstain to a confident, unfounded
    # no_premerge_gate_by_design.
    # -----------------------------------------------------------------

    def test_rule5_precondition_still_abstains_with_undetermined_canary_vacuous_true(self):
        result = compute_escape_mode(True, "not_selected", _UNDETERMINED, False)
        assert result == _UNDETERMINED

    def test_rule5_precondition_still_abstains_with_undetermined_canary_vacuous_false(self):
        result = compute_escape_mode(False, "not_selected", _UNDETERMINED, False)
        assert result == _UNDETERMINED


def _range_repo(tmp_path):
    import subprocess

    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8").stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    shas = []
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(name, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", name)
        shas.append(git("rev-parse", "HEAD"))
    return shas


def test_two_commit_range_diff_lists_both(tmp_path, monkeypatch):
    shas = _range_repo(tmp_path)
    monkeypatch.setenv("CI_RCA_RANGE_PREV", shas[0])
    with patch("scripts.ci_rca.vacuous_pass.ROOT", tmp_path):
        assert sorted(merged_diff_files()) == ["b.py", "c.py"]


def test_set_but_empty_range_is_undetermined(tmp_path, monkeypatch):
    _range_repo(tmp_path)
    monkeypatch.setenv("CI_RCA_RANGE_PREV", "")
    with patch("scripts.ci_rca.vacuous_pass.ROOT", tmp_path):
        assert merged_diff_files() == _UNDETERMINED
        assert deleted_test_files() == _UNDETERMINED


def test_head_caret_value_diffs_as_today(tmp_path, monkeypatch):
    _range_repo(tmp_path)
    monkeypatch.setenv("CI_RCA_RANGE_PREV", "HEAD^")
    with patch("scripts.ci_rca.vacuous_pass.ROOT", tmp_path):
        assert merged_diff_files() == ["c.py"]


def test_unset_keeps_head_caret(tmp_path):
    _range_repo(tmp_path)
    with patch("scripts.ci_rca.vacuous_pass.ROOT", tmp_path):
        assert merged_diff_files() == ["c.py"]


def test_no_tests_ran_without_deselection_is_vacuous():
    assert parse_vacuous_pass("========== no tests ran in 0.01s ==========") is True


def test_no_tests_ran_with_deselection_is_not_vacuous():
    assert parse_vacuous_pass("no tests ran; 3 deselected") is False
