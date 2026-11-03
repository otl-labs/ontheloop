"""Tests for validate_executor_boundary() -- Decision 44 enforcement (mirror of
scripts/checks/executor/validate_executor_boundary.py, rec-2709 Wave 1)."""

from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.executor.validate_executor_boundary import validate_executor_boundary


class TestValidateExecutorBoundary:
    """Tests for validate_executor_boundary() -- Decision 44 enforcement."""

    _VALID_REC = {
        "id": "rec-001",
        "date": "2026-01-01",
        "title": "Test boundary rec",
        "source": "executor-supervision",
        "effort": "XS",
        "priority": "High",
        "status": "open",
        "automatable": True,
        "risk": "low",
        "file": "scripts/executor/plan.py",
        "context": "Some context about the executor plan module.",
        "acceptance": "`python -m pytest tests/test_executor_plan.py -x -q`",
    }

    def _write_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        recs_path = log_dir / ".recommendations-log.jsonl"
        recs_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return recs_path

    def test_boundary_violation_detected(self, tmp_path: Path) -> None:
        """Open rec with boundary file + automatable:true is reported as a violation."""
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        # file matches "scripts/executor/" pattern, automatable:True, status:open
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert "Executor boundary validation" in failed

    def test_boundary_compliant_passes(self, tmp_path: Path) -> None:
        """Open rec with boundary file but automatable:false passes."""
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        rec["automatable"] = False
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == []

    def test_non_boundary_file_ignored(self, tmp_path: Path) -> None:
        """Open rec targeting a non-boundary file with automatable:true passes."""
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        rec["file"] = "scripts/session/postflight.py"
        rec["acceptance"] = "`python -m pytest tests/test_session_postflight.py -x -q`"
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == []

    def test_closed_rec_ignored(self, tmp_path: Path) -> None:
        """Closed rec with boundary file + automatable:true is not flagged."""
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        rec["status"] = "closed"
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == []

    def test_missing_jsonl_skips_gracefully(self, tmp_path: Path) -> None:
        """Missing JSONL file does not raise, does not append to failed, and declares skipped() (Decision 170)."""
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            registry.outcome_scope("validate_executor_boundary"),
        ):
            failed: list[str] = []
            validate_executor_boundary(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "skipped"
        assert "Decision 215" in declaration.reason
        assert "rec-4236" in declaration.reason

    def test_executor_boundary_matches_new_prompt_path(self, tmp_path: Path) -> None:
        """config/agent/executor/prompts/*.prompt.md is recognised as a boundary file.

        Regression guard for T-1.7 config split: the YAML boundary_patterns must list
        the new path so open recs targeting executor prompts are still flagged.
        """
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        rec["file"] = "config/agent/executor/prompts/planning.prompt.md"
        rec["acceptance"] = "`grep -q planning config/agent/executor/prompts/planning.prompt.md`"
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert "Executor boundary validation" in failed

    def test_boundary_pattern_in_acceptance_only_not_flagged(self, tmp_path: Path) -> None:
        """A boundary pattern in the acceptance command but NOT the file is not a violation.

        Regression for the rec-2048 false positive: validate_executor_boundary previously
        substring-matched boundary patterns against the acceptance command text, so a rec
        targeting a benign file (docs/ROADMAP-PLATFORM.yaml) whose acceptance greps for a
        string containing a boundary filename ('DECISIONS.md') was wrongly flagged. The
        check now matches the `file` field only.
        """
        import copy

        rec = copy.deepcopy(self._VALID_REC)
        rec["file"] = "docs/ROADMAP-PLATFORM.yaml"
        rec["acceptance"] = "`grep -c 'does NOT touch DECISIONS.md' docs/ROADMAP-PLATFORM.yaml`"
        self._write_jsonl(tmp_path, [rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == []


class TestUnreadableRecommendationsLog:
    """The OSError arm of validate_executor_boundary (validate_executor_boundary.py:64-67):
    a path that exists but cannot be read must be reported, never silently treated as clean."""

    def test_unreadable_jsonl_appends_a_failure(self, tmp_path: Path, capsys) -> None:
        # A directory at the JSONL path: exists() is True, read_text() raises IsADirectoryError
        # (an OSError) -- no mock needed, so the arm is driven by the real filesystem.
        (tmp_path / "logs" / ".recommendations-log.jsonl").mkdir(parents=True)
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == ["Executor boundary validation"]
        assert "ERROR: Could not read JSONL file:" in capsys.readouterr().out


class TestLineLevelTolerance:
    """The per-line tolerance branches (a blank line, a `#` comment line, and a line that is not
    JSON) are skipped rather than raised on -- and skipping them is not a silent pass: the one
    well-formed boundary rec in the same file is still reported."""

    def test_blank_comment_and_malformed_lines_are_skipped(self, tmp_path: Path) -> None:
        import json

        rec = {
            "id": "rec-002",
            "status": "open",
            "automatable": True,
            "file": "scripts/executor/plan.py",
        }
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / ".recommendations-log.jsonl").write_text(
            "\n# a comment line\n{ not json at all\n" + json.dumps(rec) + "\n",
            encoding="utf-8",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_executor_boundary(failed)
        assert failed == ["Executor boundary validation"]
