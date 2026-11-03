"""Tests for scripts/checks/misc/coverage_baseline.py: load/compare, the measurement engine,
and validate_coverage_baseline_edits() (Decision 128 mirror tamper guard)."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.checks import _scaffolding
from scripts.checks.misc import coverage_baseline
from scripts.checks.misc.coverage_baseline import _SPEC


def _probe_files(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "scripts" / "probe.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_probe.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("# test\n", encoding="utf-8")
    return source, test_file


def _mock_subprocess() -> MagicMock:
    mock_subprocess = MagicMock()
    proc = MagicMock()
    proc.__enter__.return_value = proc
    proc.__exit__.return_value = False
    proc.communicate.return_value = ("", "")
    proc.returncode = 0
    mock_subprocess.Popen.return_value = proc
    return mock_subprocess


class TestLoadBaseline:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert coverage_baseline.load_baseline(tmp_path / "nope.yaml") == {}

    def test_loads_entries_rounded_to_one_decimal(self, tmp_path: Path) -> None:
        p = tmp_path / "coverage_baseline.yaml"
        p.write_text(
            "entries:\n  scripts/ops_data_portal.py: 75.4601  # grandfathered dec-159 (one-time roster)\n", encoding="utf-8"
        )
        assert coverage_baseline.load_baseline(p) == {"scripts/ops_data_portal.py": 75.5}

    def test_empty_entries_key_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "coverage_baseline.yaml"
        p.write_text("entries: {}\n", encoding="utf-8")
        assert coverage_baseline.load_baseline(p) == {}


class TestCompare:
    def test_passes_at_or_above_baseline(self) -> None:
        assert coverage_baseline.compare(75.5, "scripts/ops_data_portal.py", {"scripts/ops_data_portal.py": 75.4})
        assert coverage_baseline.compare(75.4, "scripts/ops_data_portal.py", {"scripts/ops_data_portal.py": 75.4})

    def test_fails_below_baseline(self) -> None:
        assert not coverage_baseline.compare(75.0, "scripts/ops_data_portal.py", {"scripts/ops_data_portal.py": 75.4})

    def test_unbaselined_file_requires_100(self) -> None:
        assert not coverage_baseline.compare(99.9, "scripts/unbaselined.py", {})
        assert coverage_baseline.compare(100.0, "scripts/unbaselined.py", {})

    def test_uses_load_baseline_when_table_omitted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "coverage_baseline.yaml").write_text("entries:\n  scripts/x.py: 50.0\n", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert coverage_baseline.compare(50.0, "scripts/x.py")
            assert not coverage_baseline.compare(49.0, "scripts/x.py")


class TestMeasureAndCheck:
    """Drives the measurement engine directly with injected root/map_source_to_test/is_empty_dir/
    subprocess_module -- this is what lets scripts/test_coverage_checker.py's own module-globals
    (patchable via `patch("test_coverage_checker.<name>")`) remain the sole source of truth for
    the legacy split test suite while the heavy loop body lives here (SLOC decompose)."""

    def test_returns_none_when_test_target_unmapped(self, tmp_path: Path) -> None:
        source = tmp_path / "scripts" / "orphan.py"
        source.parent.mkdir(parents=True)
        source.write_text("x = 1\n", encoding="utf-8")

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: None,
            is_empty_dir=lambda p: False,
            subprocess_module=MagicMock(),
        )
        assert pcts == {}
        assert errors == []

    def test_returns_none_for_empty_directory_target(self, tmp_path: Path) -> None:
        source = tmp_path / "scripts" / "ops_data_portal.py"
        source.parent.mkdir(parents=True)
        source.write_text("x = 1\n", encoding="utf-8")
        test_dir = tmp_path / "tests" / "ops_data_portal"
        test_dir.mkdir(parents=True)

        mock_subprocess = MagicMock()
        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_dir,
            is_empty_dir=lambda p: True,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {}
        assert errors == []
        mock_subprocess.Popen.assert_not_called()

    def test_measures_matched_file_pct(self, tmp_path: Path) -> None:
        source, test_file = _probe_files(tmp_path)

        coverage_json = tmp_path / ".coverage.json"
        coverage_json.write_text('{"files": {"scripts/probe.py": {"summary": {"percent_covered": 87.7}}}}', encoding="utf-8")
        # Baselined at 80% so 87.7% passes -- this test pins pct measurement, not the sub-
        # threshold diagnostic surface (see TestSubthresholdDiagnostics for that).
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "coverage_baseline.yaml").write_text("entries:\n  scripts/probe.py: 80.0\n", encoding="utf-8")

        mock_subprocess = _mock_subprocess()

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): 87.7}
        assert errors == []
        called_cmd = mock_subprocess.Popen.call_args.args[0]
        cov_args = [a for a in called_cmd if isinstance(a, str) and a.startswith("--cov=")]
        assert cov_args == ["--cov=scripts"], called_cmd

    def test_child_selects_the_full_tier_marker_expression(self, tmp_path: Path) -> None:
        full_tier = _scaffolding._build_unit_test_cmd()
        expected = full_tier[full_tier.index("-m", full_tier.index("pytest")) + 1]
        source, test_file = _probe_files(tmp_path)
        mock_subprocess = _mock_subprocess()

        coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        argv = mock_subprocess.Popen.call_args.args[0]
        after_pytest = argv[argv.index("pytest") :]
        assert after_pytest[after_pytest.index("-m") + 1] == expected, argv

    def test_child_runs_on_the_sysmon_core(self, tmp_path: Path) -> None:
        """rec-4365: the per-file coverage child runs with COVERAGE_CORE=sysmon, read from
        coverage_baseline.COVERAGE_CORE rather than the literal -- so a renamed constant value
        is still what the child actually sees."""
        source, test_file = _probe_files(tmp_path)
        mock_subprocess = _mock_subprocess()

        coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        child_env = mock_subprocess.Popen.call_args.kwargs["env"]
        assert child_env["COVERAGE_CORE"] == coverage_baseline.COVERAGE_CORE
        assert coverage_baseline.COVERAGE_CORE == "sysmon"

    def test_unmatched_report_yields_none_and_error(self, tmp_path: Path) -> None:
        source, test_file = _probe_files(tmp_path)

        coverage_json = tmp_path / ".coverage.json"
        coverage_json.write_text(
            '{"files": {"scripts/unrelated.py": {"summary": {"percent_covered": 100.0}}}}', encoding="utf-8"
        )

        mock_subprocess = _mock_subprocess()

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): None}
        assert any("0% coverage" in e for e in errors)

    def test_missing_coverage_json_yields_none_no_error(self, tmp_path: Path) -> None:
        source, test_file = _probe_files(tmp_path)

        mock_subprocess = _mock_subprocess()

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): None}
        assert errors == []

    def test_source_path_outside_root_is_skipped(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "not_under_root.py"
        outside.write_text("x = 1\n", encoding="utf-8")

        pcts, errors = coverage_baseline.measure_and_check(
            [outside],
            root=tmp_path,
            map_source_to_test=lambda p: None,
            is_empty_dir=lambda p: False,
            subprocess_module=MagicMock(),
        )
        assert pcts == {}
        assert errors == []

    def test_malformed_coverage_json_yields_none(self, tmp_path: Path) -> None:
        source, test_file = _probe_files(tmp_path)

        (tmp_path / ".coverage.json").write_text("{not valid json", encoding="utf-8")

        mock_subprocess = _mock_subprocess()

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): None}
        assert errors == []

    def test_unlink_oserror_is_swallowed(self, tmp_path: Path) -> None:
        source, test_file = _probe_files(tmp_path)

        (tmp_path / ".coverage.json").write_text(
            '{"files": {"scripts/probe.py": {"summary": {"percent_covered": 100.0}}}}', encoding="utf-8"
        )

        mock_subprocess = _mock_subprocess()

        with patch("pathlib.Path.unlink", side_effect=OSError("locked")):
            pcts, errors = coverage_baseline.measure_and_check(
                [source],
                root=tmp_path,
                map_source_to_test=lambda p: test_file,
                is_empty_dir=lambda p: False,
                subprocess_module=mock_subprocess,
            )
        assert pcts == {str(source.resolve()): 100.0}
        assert errors == []

    def _timeout_scaffold(self, tmp_path: Path, side_effect: BaseException) -> tuple:
        import subprocess as real_subprocess

        source, test_file = _probe_files(tmp_path)

        mock_subprocess = MagicMock()
        mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired
        mock_subprocess.PIPE = real_subprocess.PIPE
        proc = MagicMock()
        proc.__enter__.return_value = proc
        proc.__exit__.return_value = False
        proc.communicate.side_effect = side_effect
        mock_subprocess.Popen.return_value = proc
        return source, test_file, mock_subprocess, proc

    def test_timeout_kills_the_child_group_and_records_the_cap(self, tmp_path: Path, capsys) -> None:
        import subprocess as real_subprocess

        source, test_file, mock_subprocess, proc = self._timeout_scaffold(
            tmp_path, real_subprocess.TimeoutExpired(cmd="pytest", timeout=7)
        )

        with (
            patch.object(coverage_baseline, "COVERAGE_TIMEOUT_SECONDS", 7),
            patch("scripts.checks._process_group.kill_and_reap", return_value=("", "tail")) as mock_kill,
        ):
            pcts, errors = coverage_baseline.measure_and_check(
                [source],
                root=tmp_path,
                map_source_to_test=lambda p: test_file,
                is_empty_dir=lambda p: False,
                subprocess_module=mock_subprocess,
            )
        assert mock_subprocess.Popen.call_args.kwargs["start_new_session"] is True
        assert proc.communicate.call_args.kwargs["timeout"] == 7
        mock_kill.assert_called_once_with(proc)
        assert errors == ["scripts/probe.py: coverage check timed out (7s)"]
        assert pcts == {str(source.resolve()): None}
        assert "killed at 7s" in capsys.readouterr().out

    def test_interrupt_while_waiting_kills_the_child_group_and_reraises(self, tmp_path: Path) -> None:
        source, test_file, mock_subprocess, proc = self._timeout_scaffold(tmp_path, KeyboardInterrupt())

        with patch("scripts.checks._process_group.kill_and_reap") as mock_kill:
            with pytest.raises(KeyboardInterrupt):
                coverage_baseline.measure_and_check(
                    [source],
                    root=tmp_path,
                    map_source_to_test=lambda p: test_file,
                    is_empty_dir=lambda p: False,
                    subprocess_module=mock_subprocess,
                )
        mock_kill.assert_called_once_with(proc)


class TestSubthresholdDiagnostics:
    """Pins the new diagnostic surface: a sub-threshold result must report the missing line
    numbers, the coverage subprocess returncode, and an output tail -- including the
    non-zero-returncode case, which distinguishes a genuinely failing coverage subprocess from a
    plain coverage gap that the old code folded silently into the bare percentage."""

    def test_subthreshold_result_reports_diagnostics(self, tmp_path: Path, capsys) -> None:
        """Printed, never appended to `errors` -- errors is consumed 2-tuple-unpacked by
        scripts/test_coverage_checker.py (out of scope, Decision 59), whose own
        check_per_file_coverage() separately appends its own baseline-aware message to the same
        list; a second errors entry here would silently double that list instead of adding
        value."""
        source, test_file = _probe_files(tmp_path)

        coverage_json = tmp_path / ".coverage.json"
        coverage_json.write_text(
            '{"files": {"scripts/probe.py": {"summary": {"percent_covered": 50.0}, "missing_lines": [3, 7, 9]}}}',
            encoding="utf-8",
        )

        mock_subprocess = MagicMock()
        proc = MagicMock()
        proc.__enter__.return_value = proc
        proc.__exit__.return_value = False
        proc.communicate.return_value = ("child stdout content", "child stderr content")
        proc.returncode = 3
        mock_subprocess.Popen.return_value = proc

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): 50.0}
        assert errors == []
        diagnostic = capsys.readouterr().out
        assert "[3, 7, 9]" in diagnostic
        assert "exit 3" in diagnostic
        assert "child stdout content" in diagnostic
        assert "child stderr content" in diagnostic

    def test_baselined_passing_file_reports_no_diagnostics(self, tmp_path: Path, capsys) -> None:
        """'sub-threshold' is decided by load_baseline/compare, never by 'has missing lines' -- a
        baselined file with missing lines that still clears its own threshold must not get a
        spurious diagnostic (11+ live sub-100 baseline entries pass this way today)."""
        source, test_file = _probe_files(tmp_path)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "coverage_baseline.yaml").write_text("entries:\n  scripts/probe.py: 70.0\n", encoding="utf-8")

        coverage_json = tmp_path / ".coverage.json"
        coverage_json.write_text(
            '{"files": {"scripts/probe.py": {"summary": {"percent_covered": 75.0}, "missing_lines": [3, 7, 9]}}}',
            encoding="utf-8",
        )

        mock_subprocess = _mock_subprocess()

        pcts, errors = coverage_baseline.measure_and_check(
            [source],
            root=tmp_path,
            map_source_to_test=lambda p: test_file,
            is_empty_dir=lambda p: False,
            subprocess_module=mock_subprocess,
        )
        assert pcts == {str(source.resolve()): 75.0}
        assert errors == []
        assert "[diagnostic]" not in capsys.readouterr().out


class TestValidateCoverageBaselineEdits:
    def _write_current(self, tmp_path: Path, body: str) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "coverage_baseline.yaml").write_text(body, encoding="utf-8")

    def _write_decisions(self, tmp_path: Path, decision_numbers: list[int], mentions: dict[int, str] | None = None) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(exist_ok=True)
        mentions = mentions or {}
        parts = []
        for n in decision_numbers:
            header = f"## Decision {n}: Some title (Decided)\n"
            mention = mentions.get(n)
            parts.append(header if not mention else f"{header}\n**Decision:** Authorizes {mention}.\n")
        (docs_dir / "DECISIONS.md").write_text("\n".join(parts), encoding="utf-8")

    def test_skips_when_base_absent(self, tmp_path: Path) -> None:
        """Missing-base (e.g. this plan's own seeding PR) SKIPs -- never 'missing == empty'."""
        self._write_current(tmp_path, "entries:\n  scripts/new.py: 80.0  # grandfathered dec-159 (one-time roster)\n")
        self._write_decisions(tmp_path, [])

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=lambda rel: None)

        assert failed == []

    def test_fails_on_unmarked_lowering(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/x.py: 60.0\n")
        self._write_decisions(tmp_path, [])
        base_reader = lambda rel: "entries:\n  scripts/x.py: 80.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert len(failed) == 1
        assert "Coverage baseline tamper guard" in failed[0]

    def test_passes_lowering_with_valid_marker(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/x.py: 60.0  # baseline-lowered: dec-159 regression, tracked\n")
        self._write_decisions(tmp_path, [159], mentions={159: "scripts/x.py"})
        base_reader = lambda rel: "entries:\n  scripts/x.py: 80.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert failed == []

    def test_fails_lowering_marker_cites_nonexistent_decision(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/x.py: 60.0  # baseline-lowered: dec-999 bogus\n")
        self._write_decisions(tmp_path, [159])
        base_reader = lambda rel: "entries:\n  scripts/x.py: 80.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_fails_new_sub100_registration_without_marker(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/new.py: 90.0\n")
        self._write_decisions(tmp_path, [])
        base_reader = lambda rel: "entries: {}\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert len(failed) == 1

    def test_new_entry_at_exactly_100_requires_no_marker(self, tmp_path: Path) -> None:
        """A new entry is only a violation when it registers SUB-100 debt -- a (pointless but
        harmless) new entry at exactly 100.0 is neither a lowering nor a sub-100 registration."""
        self._write_current(tmp_path, "entries:\n  scripts/new.py: 100.0\n")
        self._write_decisions(tmp_path, [])
        base_reader = lambda rel: "entries: {}\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert failed == []

    def test_passes_new_sub100_registration_with_marker(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/new.py: 90.0  # baseline-lowered: dec-159 newly discovered debt\n")
        self._write_decisions(tmp_path, [159], mentions={159: "scripts/new.py"})
        base_reader = lambda rel: "entries: {}\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert failed == []

    def test_passes_on_raise(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/x.py: 90.0\n")
        self._write_decisions(tmp_path, [])
        base_reader = lambda rel: "entries:\n  scripts/x.py: 60.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert failed == []

    def test_passes_on_removal(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries: {}\n")
        self._write_decisions(tmp_path, [])
        base_reader = lambda rel: "entries:\n  scripts/x.py: 60.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert failed == []

    def test_no_current_file_is_noop(self, tmp_path: Path) -> None:
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=lambda rel: "entries: {}\n")

        assert failed == []

    def test_spec_direction_and_new_entry_predicate(self) -> None:
        assert _SPEC.gated_direction == "down"
        assert _SPEC.token == "baseline-lowered"
        assert _SPEC.gates_new_entry(99.9) is True
        assert _SPEC.gates_new_entry(100.0) is False

    def test_unauthorized_marker_fails(self, tmp_path: Path) -> None:
        self._write_current(tmp_path, "entries:\n  scripts/x.py: 60.0  # baseline-lowered: dec-159 regression, tracked\n")
        self._write_decisions(tmp_path, [159])  # header-only body -- never mentions the key
        base_reader = lambda rel: "entries:\n  scripts/x.py: 80.0\n"  # noqa: E731

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            coverage_baseline.validate_coverage_baseline_edits(failed, base_reader=base_reader)

        assert len(failed) == 1


class TestValidatePyEntryCarriesAnAuthorizedMarker:
    """Decision 169 (amends Decision 104): the lowered scripts/validate.py entry must carry a
    `baseline-lowered` marker naming a real, AUTHORIZING Decision -- a real assertion, since the
    module mirror test alone would pass whether or not the marker exists at all."""

    def test_entry_carries_a_baseline_lowered_marker(self) -> None:
        from scripts.checks import _common

        text = (_common.ROOT / "config" / "coverage_baseline.yaml").read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if line.strip().startswith("scripts/validate.py:"))
        match = re.search(r"#\s*baseline-lowered:\s*dec-(\d+)", line)
        assert match, f"scripts/validate.py's coverage_baseline.yaml entry carries no baseline-lowered marker: {line!r}"

        decision_number = int(match.group(1))
        decisions_text = (_common.ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
        assert re.search(rf"^## Decision {decision_number}:", decisions_text, re.M), (
            f"marker cites dec-{decision_number}, but no such Decision header exists"
        )

    def test_marker_is_authorized_by_its_cited_decision(self) -> None:
        from scripts.checks import _common, _marker_guard
        from scripts.checks.misc.coverage_baseline import _SPEC

        text = (_common.ROOT / "config" / "coverage_baseline.yaml").read_text(encoding="utf-8")
        entries = _SPEC.extractor(text)
        entry = entries.get("scripts/validate.py")
        assert entry is not None and entry.marker is not None, "scripts/validate.py entry has no parsed marker"

        bodies = _marker_guard.load_decision_bodies()
        assert not _marker_guard.authorization_failure(entry.marker, "scripts/validate.py", bodies), (
            f"marker {entry.marker!r} does not authorize the scripts/validate.py entry -- its Decision body "
            "must contain the literal string 'scripts/validate.py'"
        )
