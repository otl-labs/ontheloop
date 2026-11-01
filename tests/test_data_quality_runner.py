import json
from pathlib import Path
from unittest.mock import patch

from scripts.data_quality_runner import (
    Check,
    CheckResult,
    RunResult,
    _print_results,
    _save_latest_result,
    main,
)


def test_print_results_with_issues(capsys):
    """Covers lines 518-524: Print results issues loop."""
    check = Check(table="T", column="C", test_type="not_null", kind="not_null", description="N1", severity="error")
    results = [CheckResult(check=check, verdict="FAIL", violation_count=10, detail="10 violations")]
    rr = RunResult(results=results, verdict="FAIL", duration_seconds=1.2)
    _print_results(rr)
    captured = capsys.readouterr().out
    assert "[FAIL] N1" in captured
    assert "10 violations" in captured


@patch("scripts.data_quality_runner.load_tombstones", return_value=[])
@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
def test_dry_run_prints_dq_check_requests(mock_load, mock_dq_dir, _mock_tombstones, capsys):
    mock_dq_dir.glob.return_value = [Path("test.yaml")]
    check = Check("ops_recommendations", "title", "not_null", "not_null", "desc", params={"column": "title"})
    mock_load.return_value = ([check], {"database": "db"})
    with (
        patch("sys.argv", ["runner.py", "--dry-run"]),
        patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
    ):
        assert main() == 0
    out = capsys.readouterr().out
    request = json.loads(out.strip().splitlines()[-1])
    assert request == {
        "action": "dq_check",
        "check": "not_null",
        "table": "ops_recommendations",
        "params": {"column": "title"},
    }
    assert "SELECT" not in out


def test_main_no_matching_filters():
    """Covers lines 589-590: No checks match filters."""
    with patch("scripts.data_quality_runner._DQ_DIR") as mock_dq_dir:
        mock_dq_dir.glob.return_value = [Path("test.yaml")]
        with patch("scripts.data_quality_runner.load_checks", return_value=([], {"database": "db"})):
            with (
                patch("sys.argv", ["runner.py", "--table", "non_existent"]),
                patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
            ):
                assert main() == 0


def test_print_results_json(capsys):
    c = Check("t", "c", "type", "not_null", "desc")
    results = [CheckResult(c, "PASS")]
    rr = RunResult(results=results, verdict="PASS", duration_seconds=1.2)
    _print_results(rr, as_json=True)
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["verdict"] == "PASS"


def test_save_latest_result(tmp_path):
    with patch("scripts.data_quality_runner._ROOT", tmp_path):
        c = Check("t", "c", "type", "not_null", "desc")
        rr = RunResult(results=[CheckResult(c, "PASS")], verdict="PASS", duration_seconds=1.2)
        _save_latest_result(rr)
        assert (tmp_path / "logs" / "debug" / "dq-latest.json").exists()


@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
@patch("scripts.data_quality_runner.run_checks")
def test_main_full(mock_run, mock_load, mock_dq_dir):
    mock_dq_dir.glob.return_value = [Path("test.yaml")]
    mock_load.return_value = ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"})
    mock_run.return_value = RunResult(verdict="PASS")
    # Stub routing: it reads _DQ_DIR/"ops.yaml" which is a MagicMock here; routing has dedicated tests.
    with (
        patch("sys.argv", ["runner.py"]),
        patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
    ):
        assert main() == 0


@patch("scripts.data_quality_runner._DQ_DIR")
def test_main_no_files(mock_dq_dir):
    mock_dq_dir.glob.return_value = []
    with patch("sys.argv", ["runner.py"]):
        assert main() == 1


@patch("scripts.data_quality_runner.load_tombstones", return_value=[])
@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
def test_main_severity_error(mock_load, mock_dq_dir, _mock_tombstones):
    mock_dq_dir.glob.return_value = [Path("test.yaml")]
    checks = [
        Check("t", "c", "type", "not_null", "desc", "error"),
        Check("t", "c", "type", "not_null", "desc", "warn"),
    ]
    mock_load.return_value = (checks, {"database": "db"})
    with (
        patch("sys.argv", ["runner.py", "--severity", "error", "--dry-run"]),
        patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
    ):
        with patch("builtins.print") as mock_print:
            main()
            # Only error check printed (2 lines: desc and sql)
            assert mock_print.call_count == 2


@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
def test_main_severity_warn(mock_load, mock_dq_dir):
    mock_dq_dir.glob.return_value = [Path("test.yaml")]
    checks = [
        Check("t", "c", "type", "not_null", "desc", "error"),
        Check("t", "c", "type", "not_null", "desc", "warn"),
    ]
    mock_load.return_value = (checks, {"database": "db"})
    with (
        patch("sys.argv", ["runner.py", "--severity", "warn", "--dry-run"]),
        patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
    ):
        with patch("builtins.print") as mock_print:
            main()
            assert mock_print.call_count == 2


@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
def test_main_json(mock_load, mock_dq_dir):
    mock_dq_dir.glob.return_value = [Path("test.yaml")]
    mock_load.return_value = ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"})
    with patch("scripts.data_quality_runner.run_checks") as mock_run:
        mock_run.return_value = RunResult(verdict="PASS")
        with (
            patch("sys.argv", ["runner.py", "--json"]),
            patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
        ):
            with patch("builtins.print") as mock_print:
                main()
                # Check that print was called with JSON (one of the calls should be the JSON string)
                assert any("verdict" in str(args[0]) for args, kwargs in mock_print.call_args_list)


def test_main_cli_file_path():
    """Covers line 564: CLI --file argument path."""
    with patch("scripts.data_quality_runner.load_checks", return_value=([], {"database": "db"})) as mock_load:
        with patch("sys.argv", ["runner.py", "--file", "custom.yaml"]):
            main()
        mock_load.assert_called_once()
        assert "custom.yaml" in str(mock_load.call_args[0][0])


def test_save_latest_result_zero_results_no_write(tmp_path):
    """Guard: _save_latest_result must not write when no checks were attempted."""
    with patch("scripts.data_quality_runner._ROOT", tmp_path):
        rr = RunResult(results=[], verdict="PASS", duration_seconds=0.0)
        _save_latest_result(rr)
    assert not (tmp_path / "logs" / "debug" / "dq-latest.json").exists()


def test_save_latest_result_checks_array(tmp_path):
    """_save_latest_result writes checks array with correct schema: table, column, test, verdict."""
    with patch("scripts.data_quality_runner._ROOT", tmp_path):
        c_table = Check("tbl", None, "row_count", "not_null", "desc")
        c_col = Check("tbl", "col", "not_null", "not_null", "desc")
        results = [
            CheckResult(c_table, "PASS"),
            CheckResult(c_col, "FAIL"),
        ]
        rr = RunResult(results=results, verdict="FAIL", duration_seconds=1.0)
        _save_latest_result(rr)

    data = json.loads((tmp_path / "logs" / "debug" / "dq-latest.json").read_text())
    assert "checks" in data
    assert len(data["checks"]) == 2

    table_entry = next(e for e in data["checks"] if e["test"] == "row_count")
    assert table_entry["table"] == "tbl"
    assert table_entry["column"] is None
    assert table_entry["verdict"] == "PASS"

    col_entry = next(e for e in data["checks"] if e["test"] == "not_null")
    assert col_entry["table"] == "tbl"
    assert col_entry["column"] == "col"
    assert col_entry["verdict"] == "FAIL"


@patch("scripts.data_quality_runner._DQ_DIR")
@patch("scripts.data_quality_runner.load_checks")
def test_main_conflicting_workgroup_returns_1(mock_load, mock_dq_dir):
    mock_dq_dir.glob.return_value = [Path("a.yaml"), Path("b.yaml")]
    mock_load.side_effect = [
        ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"}),
        ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"}),
    ]
    with patch("sys.argv", ["runner.py"]):
        assert main() == 1


def test_graduation_guard_unenforced_fail_recorded_in_latest_json(tmp_path):
    """_save_latest_result records UNENFORCED_FAIL per-check -- graduation guard reads this to block flip."""
    with patch("scripts.data_quality_runner._ROOT", tmp_path):
        c = Check("ops_recommendations", "file", "not_null", "not_null", "desc", "error", enforced=False)
        rr = RunResult(results=[CheckResult(c, "UNENFORCED_FAIL")], verdict="PASS")
        _save_latest_result(rr)

    data = json.loads((tmp_path / "logs" / "debug" / "dq-latest.json").read_text())
    file_check = next(ch for ch in data["checks"] if ch["column"] == "file")
    assert file_check["verdict"] == "UNENFORCED_FAIL"
    assert file_check["verdict"] != "PASS"


def test_save_latest_result_includes_unenforced_fail_count(tmp_path):
    """dq-latest.json aggregate includes unenforced_fail key; failed excludes unenforced."""
    with patch("scripts.data_quality_runner._ROOT", tmp_path):
        c_enforced = Check("t", "c1", "not_null", "not_null", "desc", "error", enforced=True)
        c_unenforced = Check("t", "c2", "not_null", "not_null", "desc", "error", enforced=False)
        results = [
            CheckResult(c_enforced, "FAIL"),
            CheckResult(c_unenforced, "UNENFORCED_FAIL"),
            CheckResult(c_enforced, "PASS"),
        ]
        rr = RunResult(results=results, verdict="FAIL", duration_seconds=1.0)
        _save_latest_result(rr)

    data = json.loads((tmp_path / "logs" / "debug" / "dq-latest.json").read_text())
    assert "unenforced_fail" in data
    assert data["unenforced_fail"] == 1
    assert data["failed"] == 1  # only enforced FAIL
    assert data["passed"] == 1


def test_print_results_json_includes_unenforced_fail(capsys):
    """JSON output from _print_results includes unenforced_fail field."""
    c = Check("t", "c", "not_null", "not_null", "desc", "error", enforced=False)
    results = [CheckResult(c, "UNENFORCED_FAIL")]
    rr = RunResult(results=results, verdict="PASS", duration_seconds=1.0)
    _print_results(rr, as_json=True)
    data = json.loads(capsys.readouterr().out)
    assert "unenforced_fail" in data
    assert data["unenforced_fail"] == 1
    assert data["failed"] == 0


class TestMainExitCodeDegraded:
    """main() must exit 0 on DEGRADED and 1 on real FAIL."""

    @patch("scripts.data_quality_runner._DQ_DIR")
    @patch("scripts.data_quality_runner.load_checks")
    @patch("scripts.data_quality_runner.run_checks")
    def test_main_exits_0_on_degraded(self, mock_run, mock_load, mock_dq_dir):
        mock_dq_dir.glob.return_value = [Path("test.yaml")]
        mock_load.return_value = ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"})
        mock_run.return_value = RunResult(verdict="DEGRADED")
        with (
            patch("sys.argv", ["runner.py"]),
            patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
        ):
            assert main() == 0

    @patch("scripts.data_quality_runner._DQ_DIR")
    @patch("scripts.data_quality_runner.load_checks")
    @patch("scripts.data_quality_runner.run_checks")
    def test_main_exits_1_on_fail(self, mock_run, mock_load, mock_dq_dir):
        mock_dq_dir.glob.return_value = [Path("test.yaml")]
        mock_load.return_value = ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"})
        mock_run.return_value = RunResult(verdict="FAIL")
        with (
            patch("sys.argv", ["runner.py"]),
            patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
        ):
            assert main() == 1

    @patch("scripts.data_quality_runner._DQ_DIR")
    @patch("scripts.data_quality_runner.load_checks")
    @patch("scripts.data_quality_runner.run_checks")
    def test_main_exits_1_on_hard_gate(self, mock_run, mock_load, mock_dq_dir):
        mock_dq_dir.glob.return_value = [Path("test.yaml")]
        mock_load.return_value = ([Check("t", "c", "type", "not_null", "desc")], {"database": "db"})
        mock_run.return_value = RunResult(verdict="HARD_GATE")
        with (
            patch("sys.argv", ["runner.py"]),
            patch("scripts.data_quality_runner.apply_backend_routing", side_effect=lambda c, d, **k: c),
        ):
            assert main() == 1
