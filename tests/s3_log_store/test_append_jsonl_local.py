"""Local-mode tests for scripts.s3_log_store.append_jsonl."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import scripts.s3_log_store as s3_mod


class TestAppendJsonlLocalMode:
    """Tests for append_jsonl() in local mode."""

    def test_appends_to_local_log(self, tmp_path: Path, monkeypatch) -> None:
        import json

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        with patch.object(s3_mod, "_LOGS_DIR", log_dir):
            with patch.object(s3_mod, "_BOTO3_AVAILABLE", False):
                from scripts.s3_log_store import append_jsonl

                result = append_jsonl(".north-star-log.jsonl", {"timestamp": "2026-01-01", "score": 5})
        assert result is True
        log_file = log_dir / ".north-star-log.jsonl"
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert data["score"] == 5

    def test_creates_parent_dirs_if_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        log_dir = tmp_path / "logs"
        # Do NOT create the directory — append_jsonl should create it
        with patch.object(s3_mod, "_LOGS_DIR", log_dir):
            with patch.object(s3_mod, "_BOTO3_AVAILABLE", False):
                from scripts.s3_log_store import append_jsonl

                result = append_jsonl(".north-star-log.jsonl", {"score": 1})
        assert result is True
        assert (log_dir / ".north-star-log.jsonl").exists()
