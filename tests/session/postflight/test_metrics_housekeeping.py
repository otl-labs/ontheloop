"""metrics + telemetry-log pruning + document-derived-tables concern:
tests/session/postflight/test_metrics_housekeeping.py (rec-2709 Wave 10).

Split from the former tests/test_session_postflight.py monolith: TestMetricsMode,
TestPruneTelemetryLogs, TestStageDocumentDerivedTables. Uses patch.object(_common, "LOGS_DIR"/
"ARCHIVE_DIR"/"ROOT") -- imports the REAL scripts.postflight._common.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.postflight import _common
from tests.fixtures.session_postflight_module import postflight as _postflight


class TestMetricsMode:
    def test_returns_combined_json(self, capsys: pytest.CaptureFixture) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "metrics output"
        mock_result.stderr = ""
        with (
            patch("scripts.postflight._common._run", return_value=mock_result),
            patch("scripts.postflight.housekeeping.prune_telemetry_logs", return_value={"pruned": [], "skipped": []}),
        ):
            rc = _postflight.run_metrics()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "metrics" in data
        assert "plan_audit" in data
        assert rc == 0


class TestPruneTelemetryLogs:
    """Tests for prune_telemetry_logs and helpers."""

    def test_prune_moves_old_entries(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        archive = logs / "archive"
        old_line = json.dumps({"date": "2020-01-01", "msg": "old"})
        new_line = json.dumps({"date": "2099-12-31", "msg": "new"})
        (logs / ".test-log.jsonl").write_text(f"{old_line}\n{new_line}\n", encoding="utf-8")
        with (
            patch.object(_common, "LOGS_DIR", logs),
            patch.object(_common, "ARCHIVE_DIR", archive),
        ):
            result = _postflight.prune_telemetry_logs(max_age_days=90)
        assert ".test-log.jsonl" in result["pruned"]
        remaining = (logs / ".test-log.jsonl").read_text(encoding="utf-8")
        assert "2099-12-31" in remaining
        assert "2020-01-01" not in remaining
        archived = list(archive.glob("*.jsonl"))
        assert len(archived) == 1
        assert "2020-01-01" in archived[0].read_text(encoding="utf-8")

    def test_prune_skips_directories(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "archive").mkdir()
        (logs / "transcripts").mkdir()
        (logs / ".keep.jsonl").write_text(
            json.dumps({"date": "2099-01-01"}) + "\n",
            encoding="utf-8",
        )
        with (
            patch.object(_common, "LOGS_DIR", logs),
            patch.object(_common, "ARCHIVE_DIR", logs / "archive"),
        ):
            result = _postflight.prune_telemetry_logs(max_age_days=90)
        assert ".keep.jsonl" in result["skipped"]

    def test_prune_handles_no_logs_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        with patch.object(_common, "LOGS_DIR", missing):
            result = _postflight.prune_telemetry_logs(max_age_days=30)
        assert result == {"pruned": [], "skipped": []}

    def test_prune_uses_timestamp_field(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        archive = logs / "archive"
        old = json.dumps({"timestamp": "2020-06-15T12:00:00Z", "v": 1})
        new = json.dumps({"timestamp": "2099-06-15T12:00:00Z", "v": 2})
        (logs / ".ts-log.jsonl").write_text(f"{old}\n{new}\n", encoding="utf-8")
        with (
            patch.object(_common, "LOGS_DIR", logs),
            patch.object(_common, "ARCHIVE_DIR", archive),
        ):
            result = _postflight.prune_telemetry_logs(max_age_days=90)
        assert ".ts-log.jsonl" in result["pruned"]

    def test_prune_preserves_malformed_lines(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        archive = logs / "archive"
        bad_line = "NOT JSON"
        old = json.dumps({"date": "2020-01-01", "x": 1})
        (logs / ".bad.jsonl").write_text(f"{bad_line}\n{old}\n", encoding="utf-8")
        with (
            patch.object(_common, "LOGS_DIR", logs),
            patch.object(_common, "ARCHIVE_DIR", archive),
        ):
            result = _postflight.prune_telemetry_logs(max_age_days=90)
        assert ".bad.jsonl" in result["pruned"]
        remaining = (logs / ".bad.jsonl").read_text(encoding="utf-8")
        assert "NOT JSON" in remaining

    def test_load_max_age_days_default(self) -> None:
        with patch.object(_common, "ROOT", Path("/nonexistent")):
            val = _postflight._load_max_age_days()
        assert val == _postflight.DEFAULT_MAX_AGE_DAYS

    def test_load_max_age_days_from_yaml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        cfg = config_dir / "config.yaml"
        cfg.write_text(
            "telemetry:\n  max_age_days: 45\n",
            encoding="utf-8",
        )
        with patch.object(_common, "ROOT", tmp_path):
            val = _postflight._load_max_age_days()
        assert val == 45

    def test_run_metrics_calls_prune(self, capsys: pytest.CaptureFixture) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "{}"
        mock_result.stderr = ""
        with (
            patch(
                "scripts.postflight._common._run",
                return_value=mock_result,
            ),
            patch(
                "scripts.postflight.housekeeping.prune_telemetry_logs",
                return_value={
                    "pruned": ["a.jsonl"],
                    "skipped": [],
                },
            ) as mock_prune,
        ):
            rc = _postflight.run_metrics()
        assert rc == 0
        mock_prune.assert_called_once()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "telemetry_pruning" in data


class TestStageDocumentDerivedTables:
    """Tests for _stage_document_derived_tables() (neutered in Phase 0+1)."""

    def test_does_not_raise(self) -> None:
        """Neutered stub completes without raising."""
        _postflight._stage_document_derived_tables()

    def test_does_not_raise_on_any_input(self, capsys: pytest.CaptureFixture) -> None:
        """Neutered stub ignores all context and does not raise."""
        _postflight._stage_document_derived_tables()

    def test_auto_mode_does_not_call_stage_documents(self, capsys: pytest.CaptureFixture) -> None:
        """run_auto() does not call _stage_document_derived_tables (ETL bypass removed)."""
        close_out = json.dumps(
            {
                "sanity_status": "PASS",
                "details": {},
            }
        )
        push_out = json.dumps({"status": "merged", "pr_url": "https://github.com/pr/1"})

        def fake_close() -> int:
            print(close_out)
            return 0

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
            patch("scripts.postflight.housekeeping._stage_document_derived_tables") as mock_stage,
            patch("scripts.session.postflight_evidence.EVIDENCE_PATH") as evidence_path,
        ):
            evidence_path.read_text.return_value = json.dumps(
                {category: [] for category in _postflight.postflight_evidence.CATEGORIES}
            )
            rc = _postflight.run_auto("feat: test")

        assert rc == 0
        mock_stage.assert_not_called()
