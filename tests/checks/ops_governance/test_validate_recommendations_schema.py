"""Tests for validate_recommendations_schema() -- specifically the python -c ban."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.ops_governance.validate_recommendations_schema import validate_recommendations_schema


def test_absent_cache_is_recorded_as_skipped(tmp_path: Path) -> None:
    """rec-3309: the absent-cache path must declare skipped() -- an unavailable input, never a
    silent pass indistinguishable from 'examined 0 rows'."""
    with (
        patch("scripts.checks._common.ROOT", tmp_path),
        registry.outcome_scope("validate_recommendations_schema"),
    ):
        failed: list[str] = []
        validate_recommendations_schema(failed)
        declaration = registry.pop_declaration()
    assert failed == []
    assert declaration is not None
    assert declaration.kind == "skipped"
    assert "Decision 215" in declaration.reason
    assert "rec-4237" in declaration.reason


class TestValidateRecommendationsSchema:
    """Tests for validate_recommendations_schema() — specifically the python -c ban."""

    _VALID_REC = {
        "id": "rec-001",
        "date": "2026-01-01",
        "title": "Test recommendation",
        "source": "executor-supervision",
        "effort": "XS",
        "priority": "Low",
        "status": "open",
        "automatable": True,
        "risk": "low",
        "file": "scripts/foo.py",
        "context": "Some context.",
        "acceptance": "`grep -q 'pattern' scripts/foo.py`",
    }

    def _write_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:

        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        recs_path = log_dir / ".recommendations-log.jsonl"
        recs_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return recs_path

    def test_passes_on_valid_rec(self, tmp_path: Path) -> None:
        """A well-formed rec with a safe acceptance command passes."""
        self._write_jsonl(tmp_path, [self._VALID_REC])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert failed == []

    def test_skips_blank_and_comment_lines(self, tmp_path: Path) -> None:
        """Blank lines and '#'-prefixed comment lines are skipped, not parsed."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        recs_path = log_dir / ".recommendations-log.jsonl"
        recs_path.write_text(f"\n# a comment\n{json.dumps(self._VALID_REC)}\n", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert failed == []

    def test_fails_on_malformed_json_line(self, tmp_path: Path) -> None:
        """A line that is not valid JSON is reported as a parse error, not silently skipped."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        recs_path = log_dir / ".recommendations-log.jsonl"
        recs_path.write_text("{not valid json\n", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert "Recommendations schema validation" in failed

    def test_fails_on_schema_validation_error(self, tmp_path: Path) -> None:
        """A well-formed JSON entry missing a required field fails Pydantic validation."""
        import copy

        bad_rec = copy.deepcopy(self._VALID_REC)
        del bad_rec["status"]  # required field
        self._write_jsonl(tmp_path, [bad_rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert "Recommendations schema validation" in failed

    def test_fails_on_oserror_reading_jsonl(self, tmp_path: Path) -> None:
        """An OSError raised while reading the (existing) JSONL file is reported, not raised."""
        self._write_jsonl(tmp_path, [self._VALID_REC])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("pathlib.Path.read_text", side_effect=OSError("disk error")),
        ):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert "Recommendations schema validation" in failed

    def test_fails_when_acceptance_contains_python_c(self, tmp_path: Path) -> None:
        """An acceptance field containing 'python -c' triggers a schema error."""
        import copy

        bad_rec = copy.deepcopy(self._VALID_REC)
        bad_rec["acceptance"] = '`python -c "import foo; assert foo.bar"`'
        self._write_jsonl(tmp_path, [bad_rec])
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert "Recommendations schema validation" in failed

    def test_skips_when_file_missing(self, tmp_path: Path) -> None:
        """No error when the JSONL file does not exist."""
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_recommendations_schema(failed)
        assert failed == []

    def test_success_path_declares_examined(self, tmp_path: Path) -> None:
        """The present-cache, all-valid path declares examined(n) with n == the row count."""
        self._write_jsonl(tmp_path, [self._VALID_REC, self._VALID_REC])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            registry.outcome_scope("validate_recommendations_schema"),
        ):
            failed: list[str] = []
            validate_recommendations_schema(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 2
        assert declaration.unit == "rec rows"

    def test_removed_from_check_accounting_grandfather_roster(self) -> None:
        """rec-3309's other half: the roster shrink -- this check no longer appears in
        config/check_accounting_baseline.yaml's grandfather entries list."""
        import yaml

        from scripts.checks import _common

        baseline_path = _common.ROOT / "config" / "check_accounting_baseline.yaml"
        entries = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))["entries"]
        assert "validate_recommendations_schema" not in entries


class TestRecommendationsSchemaImportFailure:
    """The registered wrapper's lazy-import guard must append its own failure entry."""

    def test_unimportable_recommendation_model_appends_a_failure(self, tmp_path: Path, capsys) -> None:
        """An unimportable Recommendation model is reported, not silently tolerated.

        The lazy-import guard appends the SAME string as the errors branch further down, so
        the stdout marker is what tells this site apart from that one.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / ".recommendations-log.jsonl").write_text("", encoding="utf-8")

        failed: list[str] = []
        with (
            patch.dict(sys.modules, {"scripts.executor.jsonl_store": None}),
            patch("scripts.checks._common.ROOT", tmp_path),
        ):
            validate_recommendations_schema(failed)

        assert failed == ["Recommendations schema validation"]
        assert "Could not import Recommendation model" in capsys.readouterr().out
