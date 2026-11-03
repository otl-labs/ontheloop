"""Mirror for scripts/checks/verification/_vp_replay_budget.py (PLAN-vp-replay-deadline-from-
shared-budget, Decision 131 -- carries its own fixtures, imports only from the source module).

validate_test_coverage measures THIS FILE ALONE against the source module (no
config/coverage_baseline.yaml entry exists for scripts/checks/verification/**, so the threshold is
100%): every defensive branch of run_bounded (the happy path, the timeout-kill path and the
BaseException-kill path) is covered here -- the shared kill-and-reap it calls is covered by
tests/checks/test__process_group.py -- alongside the pure telemetry helpers and the deadline-
kill message builders.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks.verification import _vp_replay_budget as b
from tests.fixtures.process_probes import pid_is_dead as _pid_is_dead
from tests.fixtures.process_probes import read_published_pid as _read_published_pid

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestReadPublishedPid:
    """rec-4182: the publishing write is staged inside the first poll sleep, so each case lands in
    the created-but-incomplete window deterministically instead of racing a real shell."""

    @pytest.mark.parametrize("staged", ["", "42"], ids=["created-not-written", "written-without-newline"])
    def test_an_incomplete_pidfile_is_polled_until_the_full_record_lands(self, tmp_path: Path, staged: str) -> None:
        pidfile = tmp_path / "grandchild.pid"
        pidfile.write_text(staged, encoding="utf-8")

        def _publish(_seconds: float) -> None:
            pidfile.write_text("4242\n", encoding="utf-8")

        with patch.object(time, "sleep", side_effect=_publish) as sleep:
            assert _read_published_pid(pidfile) == 4242
        assert sleep.call_count == 1

    def test_no_complete_record_fails_naming_the_last_read(self, tmp_path: Path) -> None:
        with pytest.raises(pytest.fail.Exception, match=r"never held a complete PID record within 0\.1s \(last read ''\)"):
            _read_published_pid(tmp_path / "never-written.pid", timeout=0.1)


class TestRunBoundedHappyPath:
    def test_completing_command_returns_output_and_elapsed(self, tmp_path: Path) -> None:
        result = b.run_bounded("echo hello-bounded", cwd=tmp_path, timeout=5.0)
        assert not result.timed_out
        assert result.returncode == 0
        assert "hello-bounded" in result.output
        assert result.elapsed >= 0.0


class TestRunBoundedTimeoutKillsTheProcessGroup:
    def test_the_orphaned_grandchild_is_dead(self, tmp_path: Path) -> None:
        """The command backgrounds a grandchild and waits on it -- job control is off in a
        non-interactive shell, so the grandchild shares the parent's process group, and
        start_new_session makes that group killable as a unit."""
        pidfile = tmp_path / "grandchild.pid"
        command = f"sleep 30 & echo $! > {pidfile}; wait"
        result = b.run_bounded(command, cwd=tmp_path, timeout=0.3)
        assert result.timed_out
        assert result.returncode is None

        # run_bounded has already killed and reaped the shell, so the file is final: read it once.
        pid = _read_published_pid(pidfile, timeout=0.0)
        deadline = time.monotonic() + 5.0
        dead = False
        while time.monotonic() < deadline:
            if _pid_is_dead(pid):
                dead = True
                break
            time.sleep(0.1)
        assert dead, f"grandchild pid {pid} survived the process-group kill"


class TestRunBoundedBaseExceptionPath:
    def test_kills_the_group_and_reraises(self, tmp_path: Path) -> None:
        real_communicate = subprocess.Popen.communicate
        calls = {"n": 0}

        def _fake_communicate(self, timeout=None):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt()
            return real_communicate(self, timeout=timeout)

        with patch.object(subprocess.Popen, "communicate", _fake_communicate):
            with pytest.raises(KeyboardInterrupt):
                b.run_bounded("sleep 5", cwd=tmp_path, timeout=5.0)
        assert calls["n"] == 2, "the reap call must reach the real communicate() exactly once"


class TestDurationSuffix:
    def test_formats_seconds_and_percentage(self) -> None:
        assert b.duration_suffix(1.23, 120.0) == "in 1.2s (1% of 120s)"

    def test_zero_aggregate_is_zero_percent_not_a_division_error(self) -> None:
        assert b.duration_suffix(1.0, 0.0) == "in 1.0s (0% of 0s)"


class TestSummaryLine:
    def test_formats_with_a_peak_under_the_authoring_target(self) -> None:
        line = b.summary_line(10.0, 120.0, 3, ("docs/plans/PLAN-x.yaml", 2, 5.0))
        assert line == "vp-replay budget: 10.0s of 120s across 3 step(s); peak docs/plans/PLAN-x.yaml:2 5.0s"

    def test_appends_the_authoring_target_note_when_the_peak_step_exceeds_it(self) -> None:
        line = b.summary_line(40.0, 120.0, 1, ("docs/plans/PLAN-x.yaml", 1, 40.0))
        assert line.endswith("(over the 25% authoring target)")

    def test_no_peak_omits_the_peak_clause(self) -> None:
        line = b.summary_line(0.0, 120.0, 0, None)
        assert "peak" not in line


class TestWarnBand:
    def test_no_warn_below_both_thresholds(self) -> None:
        assert b.warn_band(peak_elapsed=59.9, total_elapsed=95.9, aggregate=120.0) is None

    def test_warns_when_a_single_step_exceeds_half_the_aggregate(self) -> None:
        warning = b.warn_band(peak_elapsed=61.0, total_elapsed=61.0, aggregate=120.0)
        assert warning is not None
        assert "WARN" in warning

    def test_does_not_warn_at_exactly_half(self) -> None:
        assert b.warn_band(peak_elapsed=60.0, total_elapsed=60.0, aggregate=120.0) is None

    def test_warns_when_cumulative_spend_exceeds_eighty_percent(self) -> None:
        warning = b.warn_band(peak_elapsed=10.0, total_elapsed=97.0, aggregate=120.0)
        assert warning is not None
        assert "WARN" in warning

    def test_does_not_warn_at_exactly_eighty_percent_cumulative(self) -> None:
        assert b.warn_band(peak_elapsed=10.0, total_elapsed=96.0, aggregate=120.0) is None

    def test_zero_aggregate_never_warns(self) -> None:
        assert b.warn_band(peak_elapsed=10.0, total_elapsed=10.0, aggregate=0.0) is None


class TestDeadlineKillMessageBuilders:
    def test_green_names_timeout_and_aggregate_deadline_with_prior_spend(self) -> None:
        msg = b.format_deadline_kill_green("docs/plans/PLAN-x.yaml", 2, 1.5, 3.0, 1, 4.5)
        assert "TIMEOUT" in msg
        assert "aggregate deadline" in msg
        assert "1.5" in msg
        assert "1 prior step(s)" in msg

    def test_green_names_step_alone_when_nothing_ran_before_it(self) -> None:
        msg = b.format_deadline_kill_green("docs/plans/PLAN-x.yaml", 1, 5.0, 0.0, 0, 4.5)
        assert "step alone exceeds" in msg

    def test_red_before_classifies_unmeasurable_and_names_aggregate_deadline(self) -> None:
        msg = b.format_deadline_kill_red_before("docs/plans/PLAN-x.yaml", 1, 1.0, 0.0, 0, 2.0)
        assert "actual=unmeasurable" in msg
        assert "aggregate deadline" in msg
        assert "never counted as red" in msg


class TestDivergenceTextBuilders:
    """Every pinned substring survives the move into this module, "after <N>s" is present, and
    the green exit-code builder's label is correct for every exit/output shape measured at
    planning: exit 127 names the command, exit 126 reads not executable, an unmeasurable arm with
    no parsed command name, a pytest exit 4 with each collection hint (including the exit-4
    import-error shape getting collection_error, never the skip hint), a pytest exit 5 whole-file
    skip with the module-level-skip hint, a non-pytest exit 4 as target_absent with no hint, and a
    plain exit 1 as assertion_failed. No green label contains "actual=<outcome>"; the
    missing-literal and red-before builders carry the duration."""

    def test_green_exit_code_pinned_substrings_and_duration(self) -> None:
        msg = b.format_green_exit_code_divergence("docs/plans/PLAN-x.yaml", 1, "exit 1", 1, "some output", "Exit 0.", 1.5)
        assert "vp-replay docs/plans/PLAN-x.yaml:1:" in msg
        assert "actual=exit 1" in msg
        assert "!= expected=exit 0" in msg
        assert "output tail=" in msg
        assert "after 1.5s" in msg
        assert "actual=<outcome>" not in msg

    def test_green_exit_127_names_the_command(self) -> None:
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "nonexistent-tool", 127, "/bin/sh: 1: nonexistent-tool: not found\n", "Exit 0.", 0.1
        )
        assert "raw outcome: unmeasurable (exit_127_command_not_found: nonexistent-tool)" in msg

    def test_green_exit_126_reads_not_executable_never_missing(self) -> None:
        msg = b.format_green_exit_code_divergence("docs/plans/PLAN-x.yaml", 1, "./x", 126, "", "Exit 0.", 0.1)
        assert "raw outcome: unmeasurable (exit_126_not_executable)" in msg
        assert "missing" not in msg

    def test_green_unmeasurable_arm_with_no_parsed_command_name(self) -> None:
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "grep p missing.py", 2, "grep: missing.py: No such file", "Exit 0.", 0.1
        )
        assert "raw outcome: unmeasurable (rg_or_grep_error_exit_2)" in msg

    def test_green_credential_absence_arm(self) -> None:
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "aws s3 ls", 1, "Unable to locate credentials", "Exit 0.", 0.1
        )
        assert "raw outcome: unmeasurable (credential_absence)" in msg

    def test_green_pytest_exit_4_collection_error_never_the_skip_hint(self) -> None:
        out = "1 error in 0.02s\nERROR: found no collectors for x::test_thing\n"
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "pytest x::test_thing -q", 4, out, "Exit 0.", 0.1
        )
        assert "raw outcome: target_absent (collection error -- see output tail)" in msg

    def test_green_pytest_exit_4_module_level_skip_hint(self) -> None:
        out = "ERROR: found no collectors for x::test_thing\n\n1 skipped in 0.00s\n"
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "pytest x::test_thing -q", 4, out, "Exit 0.", 0.1
        )
        assert "module-level skip -- e.g. a dependency absent from requirements-fast.txt (rec-2809)" in msg

    def test_green_pytest_exit_5_whole_file_skip_hint(self) -> None:
        out = "1 skipped in 0.01s\n"
        msg = b.format_green_exit_code_divergence("docs/plans/PLAN-x.yaml", 1, "pytest x.py -q", 5, out, "Exit 0.", 0.1)
        assert "module-level skip -- e.g. a dependency absent from requirements-fast.txt (rec-2809)" in msg

    def test_green_non_pytest_exit_4_is_target_absent_with_no_hint(self) -> None:
        msg = b.format_green_exit_code_divergence(
            "docs/plans/PLAN-x.yaml", 1, "some-other-tool", 4, "found no collectors\n1 error in 0.0s", "Exit 0.", 0.1
        )
        assert "raw outcome: target_absent" in msg
        assert "raw outcome: target_absent (" not in msg

    def test_green_plain_exit_1_is_assertion_failed(self) -> None:
        msg = b.format_green_exit_code_divergence("docs/plans/PLAN-x.yaml", 1, "exit 1", 1, "", "Exit 0.", 0.1)
        assert "raw outcome: assertion_failed" in msg

    def test_green_missing_literal_pinned_substrings_and_duration(self) -> None:
        msg = b.format_green_missing_literal_divergence(
            "docs/plans/PLAN-x.yaml", 1, ["expected-literal"], "stdout contains `expected-literal`.", "output", 2.3
        )
        assert "vp-replay docs/plans/PLAN-x.yaml:1:" in msg
        assert "actual=missing literal(s)" in msg
        assert "output tail=" in msg
        assert "after 2.3s" in msg

    def test_red_before_pinned_substrings_and_duration(self) -> None:
        msg = b.format_red_before_divergence("docs/plans/PLAN-x.yaml", 1, "tautological", 0, "output", 0.4)
        assert "vp-red-before" in msg
        assert "actual=tautological (exit 0)" in msg
        assert "must be genuinely red" in msg
        assert "output tail=" in msg
        assert "after 0.4s" in msg


class TestContractPins:
    def test_replay_bound_matches_the_contract(self) -> None:
        contract_path = _REPO_ROOT / "docs" / "contracts" / "vp-red-before.yaml"
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        replay_bound = data["replay_bound"]
        assert replay_bound["per_step_warn_fraction"] == b.PER_STEP_WARN_FRACTION
        assert replay_bound["cumulative_warn_fraction"] == b.CUMULATIVE_WARN_FRACTION
        assert replay_bound["authoring_max_fraction"] == b.AUTHORING_MAX_FRACTION
