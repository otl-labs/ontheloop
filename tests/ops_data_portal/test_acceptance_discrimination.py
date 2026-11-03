"""Portal-boundary mirror test for the acceptance-discrimination arm (rec-3220 /
PLAN-probe-discrimination-vacuity-alarm). scripts/ops_data_portal.py IS coverage-mapped by
map_source_to_test (unlike scripts/executor/**, which Decision 124 leaves deliberately
unmapped), so this file -- not tests/test_executor_acceptance_lint.py -- is the Decision 131
mirror location that genuinely binds for the file_rec() write-boundary wiring.

Defines its own minimal valid-fields fixture locally rather than importing
tests/fixtures/ops_portal_records.VALID_FIELDS, so the discriminating/non-discriminating
acceptance value under test is never accidentally shadowed by the shared fixture's own value.

No `duckdb = pytest.importorskip("duckdb")` guard: unlike other tests/ops_data_portal/ files,
these tests never touch a real DuckLake connection (_ducklake_write/_sync_table are mocked
throughout) and scripts.ops_data_portal itself has no duckdb import at module scope -- see
tests/test_ops_data_portal_validators.py for the same unguarded-import precedent. The guard
would also break VP-step replay: pr-validate's requirements-fast.txt has no duckdb, so an
importorskip-guarded module collects as a single skip node, and selecting a specific class by
::node_id inside it then fails with pytest's "no collectors found" (exit 4, not a clean skip).

The two success-path tests below also patch src.common.ducklake_runtime.mint_write_identity
(file_rec's deferred-import identity minter, which itself deferred-imports `ulid`) rather than
relying on the real python-ulid package -- mocking it keeps this module hermetic and avoids a
real DuckLake write, under the same constraint that motivated dropping the duckdb guard.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_FAKE_WRITE_IDENTITY = SimpleNamespace(ulid="01FAKE0000000000000000000")

_BASE_FIELDS = {
    "title": "Acceptance discrimination boundary test recommendation",
    "file": "scripts/ops_data_portal.py",
    "context": "A sufficiently long context string so the write-time content validators are satisfied here.",
    "effort": "XS",
    "priority": "Low",
    "source": "planning",
    "risk": "low",
    "status": "open",
    "automatable": True,
}


class TestFileRecRefusesNonDiscriminating:
    """file_rec() wires require_discrimination=True at its lint_acceptance_command() call
    (scripts/ops_data_portal.py:358) -- so the two presumptively non-discriminating probe shapes
    raise ValueError at the write boundary, while a discriminating probe still files cleanly."""

    def test_lone_grep_acceptance_raises_value_error(self, tmp_path: Path) -> None:
        fields = {**_BASE_FIELDS, "acceptance": "grep -q ops_data_portal scripts/ops_data_portal.py"}
        with (
            patch("scripts.ops_data_portal._ducklake_write") as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
        ):
            from scripts.ops_data_portal import file_rec

            with pytest.raises(ValueError, match="does not discriminate"):
                file_rec(dict(fields))
        mock_write.assert_not_called()

    def test_bare_pytest_path_that_exists_raises_value_error(self, tmp_path: Path) -> None:
        fields = {
            **_BASE_FIELDS,
            "acceptance": "bin/venv-python -m pytest tests/test_executor_acceptance_lint.py -q",
        }
        with (
            patch("scripts.ops_data_portal._ducklake_write") as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
        ):
            from scripts.ops_data_portal import file_rec

            with pytest.raises(ValueError, match="does not discriminate"):
                file_rec(dict(fields))
        mock_write.assert_not_called()

    def test_discriminating_acceptance_still_files(self, tmp_path: Path) -> None:
        """A chained second assertion (genuinely discriminating) files cleanly -- the arm only
        rejects the two non-discriminating shapes, never a well-formed probe."""
        fields = {
            **_BASE_FIELDS,
            "acceptance": (
                "grep -q ops_data_portal scripts/ops_data_portal.py && grep -q file_rec scripts/ops_data_portal.py"
            ),
        }
        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-8001"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            patch("src.common.ducklake_runtime.mint_write_identity", return_value=_FAKE_WRITE_IDENTITY),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(dict(fields))

        assert result == "rec-8001"
        mock_write.assert_called_once()
        entry = mock_write.call_args.args[1]
        assert entry["id"] == "rec-8001"

    def test_pytest_path_that_does_not_exist_still_files(self, tmp_path: Path) -> None:
        """The carve-out: a bare pytest path against a not-yet-created test file is legitimately
        discriminating (collection error before, pass after) and must not be refused."""
        fields = {
            **_BASE_FIELDS,
            "acceptance": "bin/venv-python -m pytest tests/test_nonexistent_xyz_probe_discrimination.py -q",
        }
        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-8002"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            patch("src.common.ducklake_runtime.mint_write_identity", return_value=_FAKE_WRITE_IDENTITY),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(dict(fields))

        assert result == "rec-8002"
        mock_write.assert_called_once()
