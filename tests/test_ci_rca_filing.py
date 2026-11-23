"""Tests for scripts/ci_rca/filing.py -- extract_filed_rec_id and CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_rca.filing import extract_filed_rec_id, extract_filed_recs, main


def _write(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "output.json"
    f.write_text(content, encoding="utf-8")
    return f


class TestExtractFiledRecId:
    def test_mention_only_returns_none(self, tmp_path: Path) -> None:
        content = "Agent processed rec-123 and rec-456 from the backlog."
        assert extract_filed_rec_id(_write(tmp_path, content)) is None

    def test_filed_marker_returns_id(self, tmp_path: Path) -> None:
        content = "Some output\nFILED: rec-123\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-123"

    def test_filed_none_returns_none(self, tmp_path: Path) -> None:
        content = "Could not file.\nFILED: none\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) is None

    def test_filed_none_case_insensitive(self, tmp_path: Path) -> None:
        content = "FILED: NONE\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) is None

    def test_multiple_markers_returns_last(self, tmp_path: Path) -> None:
        content = "FILED: rec-100\nSome stuff\nFILED: rec-200\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-200"

    def test_filed_none_then_real_marker_returns_id(self, tmp_path: Path) -> None:
        # An earlier FILED: none must NOT suppress a later real marker (last wins).
        content = "FILED: none\nReconsidered after retry.\nFILED: rec-123\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-123"

    def test_real_marker_then_filed_none_returns_none(self, tmp_path: Path) -> None:
        # A trailing FILED: none is the authoritative final signal -> absence.
        content = "FILED: rec-123\nFiling rolled back.\nFILED: none\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) is None

    def test_marker_with_leading_whitespace(self, tmp_path: Path) -> None:
        content = "  FILED: rec-456  \n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-456"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_filed_rec_id(tmp_path / "nonexistent.json") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_filed_rec_id(_write(tmp_path, "")) is None

    def test_malformed_json_fallback_finds_marker(self, tmp_path: Path) -> None:
        content = "Some stderr line\n{invalid json\nFILED: rec-789\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-789"

    def test_json_envelope_result_field_searched(self, tmp_path: Path) -> None:
        result_text = "I analysed the CI run.\nRoot cause: missing dependency.\nFILED: rec-333\n"
        envelope = json.dumps({"result": result_text, "type": "result"})
        assert extract_filed_rec_id(_write(tmp_path, envelope)) == "rec-333"

    def test_json_envelope_with_escaped_newlines_in_result(self, tmp_path: Path) -> None:
        result_text = "Step 1: read logs\nFILED: rec-999\n"
        envelope = json.dumps({"result": result_text})
        assert extract_filed_rec_id(_write(tmp_path, envelope)) == "rec-999"

    def test_filed_none_in_json_result(self, tmp_path: Path) -> None:
        result_text = "Nothing to file.\nFILED: none\n"
        envelope = json.dumps({"result": result_text})
        assert extract_filed_rec_id(_write(tmp_path, envelope)) is None

    def test_bare_rec_mention_in_json_result_returns_none(self, tmp_path: Path) -> None:
        result_text = "Reviewed existing rec-500 for context."
        envelope = json.dumps({"result": result_text})
        assert extract_filed_rec_id(_write(tmp_path, envelope)) is None

    def test_realistic_json_envelope_with_multi_turn_output(self, tmp_path: Path) -> None:
        result_text = (
            "Turn 1: reading logs\n"
            "Turn 5: classifying failure as dependency gap\n"
            "rec-850 is similar but not the same\n"
            "Turn 29: filing recommendation\n"
            "FILED: rec-2025\n"
        )
        envelope = json.dumps({"result": result_text, "stop_reason": "end_turn", "usage": {}})
        assert extract_filed_rec_id(_write(tmp_path, envelope)) == "rec-2025"

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "FILED: rec-111\n")
        assert extract_filed_rec_id(path) == "rec-111"

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "FILED: rec-222\n")
        assert extract_filed_rec_id(str(path)) == "rec-222"


class TestExtractFiledRecIdWithCategoryMarker:
    """CIRCA-07: extract_filed_rec_id() still returns just the rec id when the marker
    carries a trailing category token."""

    def test_single_marker_with_category(self, tmp_path: Path) -> None:
        content = "FILED: rec-123 sloc_violation\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-123"

    def test_last_of_multiple_category_markers(self, tmp_path: Path) -> None:
        content = "FILED: rec-100 sloc_violation\nFILED: rec-200 iam_policy_gap\n"
        assert extract_filed_rec_id(_write(tmp_path, content)) == "rec-200"


class TestExtractFiledRecs:
    """CIRCA-07: extract_filed_recs() returns ALL (rec_id, category) pairs (N-1 filings no longer lost)."""

    def test_multi_marker_returns_all_pairs(self, tmp_path: Path) -> None:
        content = "FILED: rec-100 sloc_violation\nFILED: rec-200 iam_policy_gap\n"
        assert extract_filed_recs(_write(tmp_path, content)) == [
            ("rec-100", "sloc_violation"),
            ("rec-200", "iam_policy_gap"),
        ]

    def test_single_marker_with_category_still_parses(self, tmp_path: Path) -> None:
        content = "FILED: rec-42 dependency_gap\n"
        assert extract_filed_recs(_write(tmp_path, content)) == [("rec-42", "dependency_gap")]

    def test_single_marker_without_category_backward_compat(self, tmp_path: Path) -> None:
        content = "FILED: rec-42\n"
        assert extract_filed_recs(_write(tmp_path, content)) == [("rec-42", "")]

    def test_filed_none_returns_empty_list(self, tmp_path: Path) -> None:
        content = "Could not file.\nFILED: none\n"
        assert extract_filed_recs(_write(tmp_path, content)) == []

    def test_filed_none_does_not_suppress_earlier_real_marker(self, tmp_path: Path) -> None:
        """Unlike extract_filed_rec_id (last-wins), extract_filed_recs collects ALL real filings --
        a trailing FILED: none is simply skipped, not authoritative over earlier markers."""
        content = "FILED: rec-1 cat_a\nFILED: none\n"
        assert extract_filed_recs(_write(tmp_path, content)) == [("rec-1", "cat_a")]

    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        assert extract_filed_recs(tmp_path / "nonexistent.json") == []

    def test_no_marker_returns_empty_list(self, tmp_path: Path) -> None:
        content = "Agent mentioned rec-100 but did not file."
        assert extract_filed_recs(_write(tmp_path, content)) == []

    def test_json_envelope_result_field_searched(self, tmp_path: Path) -> None:
        result_text = "FILED: rec-1 cat_a\nFILED: rec-2 cat_b\n"
        envelope = json.dumps({"result": result_text})
        assert extract_filed_recs(_write(tmp_path, envelope)) == [("rec-1", "cat_a"), ("rec-2", "cat_b")]


class TestCLI:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.ci_rca.filing", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_cli_exits_0_and_prints_id(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: rec-42\n", encoding="utf-8")
        result = self._run(str(f))
        assert result.returncode == 0
        assert result.stdout.strip() == "rec-42"
        assert result.stderr == ""

    def test_cli_exits_1_when_no_marker(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("Agent mentioned rec-100 but did not file.", encoding="utf-8")
        result = self._run(str(f))
        assert result.returncode == 1
        assert result.stdout.strip() == ""

    def test_cli_exits_1_for_filed_none(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: none\n", encoding="utf-8")
        result = self._run(str(f))
        assert result.returncode == 1
        assert result.stdout.strip() == ""

    def test_cli_exits_1_when_no_args(self) -> None:
        result = self._run()
        assert result.returncode == 1

    def test_cli_prints_only_id_no_extra_whitespace(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: rec-77\n", encoding="utf-8")
        result = self._run(str(f))
        assert result.returncode == 0
        assert result.stdout == "rec-77\n"

    def test_cli_all_mode_prints_one_line_per_rec(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: rec-100 sloc_violation\nFILED: rec-200 iam_policy_gap\n", encoding="utf-8")
        result = self._run("--all", str(f))
        assert result.returncode == 0
        assert result.stdout == "rec-100,sloc_violation\nrec-200,iam_policy_gap\n"

    def test_cli_all_mode_no_markers_exits_0_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: none\n", encoding="utf-8")
        result = self._run("--all", str(f))
        assert result.returncode == 0
        assert result.stdout == ""

    def test_cli_all_mode_legacy_marker_prints_empty_category(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        f.write_text("FILED: rec-55\n", encoding="utf-8")
        result = self._run("--all", str(f))
        assert result.returncode == 0
        assert result.stdout == "rec-55,\n"


class TestMainInProcess:
    def _main(self, monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
        monkeypatch.setattr(sys, "argv", ["filing", *args])
        main()

    def test_usage_error(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch)
        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err

    def test_all_prints_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._main(monkeypatch, "--all", str(_write(tmp_path, "FILED: rec-1 environment\nFILED: rec-2 code\n")))
        out = capsys.readouterr()
        assert out.out.splitlines() == ["rec-1,environment", "rec-2,code"]
        assert out.err == ""

    def test_single_prints_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._main(monkeypatch, str(_write(tmp_path, "FILED: rec-42\n")))
        out = capsys.readouterr()
        assert out.out.strip() == "rec-42"
        assert out.err == ""

    def test_single_no_marker_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch, str(_write(tmp_path, "nothing here")))
        assert exc.value.code == 1
        assert "No FILED" in capsys.readouterr().err

    def test_non_envelope_output_names_the_cause_on_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        envelope = json.dumps({"result": "FILED: rec-5 environment"})
        bad = _write(tmp_path, "Warning: workspace has not been trusted\n" + envelope + "\n")
        self._main(monkeypatch, "--all", str(bad))
        out = capsys.readouterr()
        assert out.out == ""
        assert "is not a single claude -p JSON envelope" in out.err
        good = tmp_path / "good.json"
        good.write_text(envelope, encoding="utf-8")
        self._main(monkeypatch, "--all", str(good))
        out = capsys.readouterr()
        assert out.out.strip() == "rec-5,environment"
        assert out.err == ""

    def test_missing_file_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._main(monkeypatch, "--all", str(tmp_path / "absent.json"))
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""

    def test_non_string_result_falls_back_to_raw(self, tmp_path: Path) -> None:
        assert extract_filed_recs(_write(tmp_path, json.dumps({"result": ""}))) == []
