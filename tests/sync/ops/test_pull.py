"""pull() / _rebuild_local_cache() / reader concern: tests/sync/ops/test_pull.py (rec-2709 Wave 10).

Split from the former tests/test_sync_ops.py monolith: TestPull (verbatim; single class, far
under 500 SLOC -- no class-split).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# pull() tests
# ---------------------------------------------------------------------------


class TestPull:
    def test_pull_reader_unreachable_returns_zero_for_every_table(self):
        """When the DuckLake reader is unreachable every table reports 0 -- it is the sole backend.

        Decision 84 I-1: _rebuild_local_cache() loops _TABLE_TO_LOCAL via the reader-only
        _pull_single_table; a failure warns loudly and leaves the cache untouched.
        """
        with patch("scripts.sync.ops._pull_via_reader", return_value=None):
            from scripts.sync.ops import _rebuild_local_cache

            result = _rebuild_local_cache()
        assert result == {
            "ops_decisions": 0,
            "ops_priority_queue": 0,
            "ops_execution_plans": 0,
        }

    def test_pull_reader_path_writes_local_files(self, tmp_path):
        """Reader path: _rebuild_local_cache() uses reader rows directly when reader succeeds."""
        local_file = tmp_path / ".recommendations-log.jsonl"

        reader_data = [
            {
                "id": "rec-001",
                "status": "open",
                "title": "Reader test",
                "source": "manual",
                "effort": "S",
                "priority": "Low",
            }
        ]

        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=reader_data),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recommendations-log.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            result = sync_ops._rebuild_local_cache()

        assert result.get("ops_recommendations") == 1
        assert local_file.exists()
        saved = json.loads(local_file.read_text(encoding="utf-8").strip())
        assert saved["id"] == "rec-001"

    def test_pull_reader_failure_leaves_cache_untouched(self, tmp_path):
        """Reader failure: no fallback path runs and the existing cache file is preserved."""
        local_file = tmp_path / ".recommendations-log.jsonl"
        local_file.write_text(json.dumps({"id": "rec-stale", "status": "open"}) + "\n", encoding="utf-8")

        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=None),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recommendations-log.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            count = sync_ops._pull_single_table("ops_recommendations")

        assert count == 0
        # Cache untouched -- the stale row is still there (never truncated on failure)
        assert json.loads(local_file.read_text(encoding="utf-8").strip())["id"] == "rec-stale"

    def test_pull_one_table_failure_continues_to_next_table(self, tmp_path):
        """_rebuild_local_cache() continues to the next table when one table's reader pull fails."""

        def _per_table(table):
            if table == "ops_recommendations":
                return None  # reader failed for this table
            return [{"rec_id": "rec-001", "rank": "1"}]

        with (
            patch("scripts.sync.ops._pull_via_reader", side_effect=_per_table),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch(
                "scripts.sync.ops._TABLE_TO_LOCAL",
                {
                    "ops_recommendations": ".recommendations-log.jsonl",
                    "ops_priority_queue": "priority-queue/.priority-queue.jsonl",
                },
            ),
        ):
            from scripts.sync import ops as sync_ops

            result = sync_ops._rebuild_local_cache()

        assert result["ops_recommendations"] == 0
        assert result["ops_priority_queue"] == 1

    def test_pull_coerces_ops_recommendations_array_fields(self, tmp_path):
        """Coercion applies to string-serialised array fields in reader rows."""
        local_file = tmp_path / ".recommendations-log.jsonl"
        reader_data = [
            {
                "id": "rec-001",
                "dependencies": "[dep-001, dep-002]",
                "tags": "[]",
                "execution_steps": "3",
                "title": "Test rec",
                "source": "manual",
                "effort": "S",
                "priority": "Low",
            }
        ]
        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=reader_data),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recommendations-log.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            sync_ops._rebuild_local_cache()
        saved = json.loads(local_file.read_text(encoding="utf-8").strip())
        assert saved["dependencies"] == ["dep-001", "dep-002"]
        assert saved["tags"] == []
        assert saved["execution_steps"] == 3

    def test_pull_strips_scd2_view_columns_from_rows(self, tmp_path):
        """_rn and row_num dedup columns are stripped from pulled rows before caching."""
        local_file = tmp_path / ".recommendations-log.jsonl"
        reader_data = [
            {
                "id": "rec-001",
                "status": "open",
                "_rn": 1,
                "row_num": 1,
                "title": "Test rec",
                "source": "manual",
                "effort": "S",
                "priority": "Low",
            }
        ]
        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=reader_data),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recommendations-log.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            sync_ops._rebuild_local_cache()

        assert local_file.exists()
        saved = json.loads(local_file.read_text(encoding="utf-8").strip())
        assert "_rn" not in saved, "_rn must be stripped by _rebuild_local_cache()"
        assert "row_num" not in saved, "row_num must be stripped by _rebuild_local_cache()"
        assert saved["id"] == "rec-001"

    def test_pull_rejects_hollow_ops_recommendations_row(self, tmp_path):
        """Hollow rows (missing required fields) are rejected and logged."""
        local_file = tmp_path / ".recommendations-log.jsonl"
        reject_log = tmp_path / "debug" / "dq-sync-rejects.jsonl"
        reader_data = [{"id": "rec-hollow", "title": "", "source": ""}]

        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=reader_data),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._SYNC_REJECTS_LOG", reject_log),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recommendations-log.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            result = sync_ops._rebuild_local_cache()

        # Hollow row must be rejected -- local JSONL should be empty or not written
        assert result.get("ops_recommendations") == 0
        assert not local_file.exists() or local_file.read_text(encoding="utf-8").strip() == ""
        # Reject log must capture the hollow row
        assert reject_log.exists()
        reject_entry = json.loads(reject_log.read_text(encoding="utf-8").strip())
        assert reject_entry["row"]["id"] == "rec-hollow"
        assert "title" in reject_entry["reason"] or "source" in reject_entry["reason"]

    def test_pull_single_table_unknown_table_returns_zero(self):
        """_pull_single_table() warns and returns 0 for a table with no local mapping."""
        with patch("scripts.sync.ops._pull_via_reader") as mock_pull:
            from scripts.sync.ops import _pull_single_table

            assert _pull_single_table("telemetry_sessions") == 0
        mock_pull.assert_not_called()

    def test_coerce_rows_list_handles_reader_typed_values(self) -> None:
        """_coerce_rows_list() tolerates already-typed values from the reader."""
        from scripts.sync.ops import _coerce_rows_list

        reader_row = {
            "id": "rec-001",
            "dependencies": ["dep-001"],
            "tags": [],
            "execution_steps": 3,
            "automatable": True,
            "title": "Test",
            "source": "manual",
            "effort": "S",
            "priority": "Low",
        }
        rows = _coerce_rows_list("ops_recommendations", [reader_row])
        assert len(rows) == 1
        assert rows[0]["id"] == "rec-001"
        assert rows[0]["execution_steps"] == 3

    def test_write_rows_to_local_creates_jsonl(self, tmp_path) -> None:
        """_write_rows_to_local() writes rows as JSONL and returns count."""
        rows = [{"id": "rec-001", "status": "open"}, {"id": "rec-002", "status": "closed"}]
        with patch("scripts.sync.ops._LOGS_DIR", tmp_path):
            from scripts.sync import ops as sync_ops

            count = sync_ops._write_rows_to_local("ops_recommendations", rows, ".recs.jsonl")

        assert count == 2
        written = list((tmp_path / ".recs.jsonl").read_text(encoding="utf-8").splitlines())
        assert len(written) == 2
        assert json.loads(written[0])["id"] == "rec-001"

    def test_pull_via_reader_returns_none_on_exception(self) -> None:
        """_pull_via_reader() returns None when the DuckLake reader raises."""
        reader = MagicMock()
        reader.current_state.side_effect = RuntimeError("reader down")
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.sync.ops import _pull_via_reader

            result = _pull_via_reader("ops_recommendations")
        assert result is None

    def test_pull_via_reader_uses_ducklake_reader_current_state(self) -> None:
        """_pull_via_reader() routes through make_reader().current_state for every table."""
        reader = MagicMock()
        reader.current_state.return_value = [{"id": "rec-1"}]
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader) as mock_make:
            from scripts.sync.ops import _pull_via_reader

            result = _pull_via_reader("ops_decisions")

        assert result == [{"id": "rec-1"}]
        mock_make.assert_called_once_with(table="ops_decisions")
        reader.current_state.assert_called_once_with("ops_decisions")

    def test_pull_single_table_uses_reader_first(self, tmp_path) -> None:
        """_pull_single_table() uses reader rows when reader succeeds."""
        reader_data = [
            {
                "id": "rec-rdr",
                "status": "open",
                "title": "Reader row",
                "source": "manual",
                "effort": "S",
                "priority": "Low",
            }
        ]
        with (
            patch("scripts.sync.ops._pull_via_reader", return_value=reader_data),
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recs.jsonl"}),
        ):
            from scripts.sync import ops as sync_ops

            count = sync_ops._pull_single_table("ops_recommendations")

        assert count == 1
        saved = json.loads((tmp_path / ".recs.jsonl").read_text(encoding="utf-8").strip())
        assert saved["id"] == "rec-rdr"


class TestRecsCacheRetired:
    """Decision 215: sync and warm_sync never pull ops_recommendations and delete a stale cache copy."""

    def test_sync_never_requests_recs_and_deletes_stale_file(self, tmp_path):
        from scripts.sync import ops as sync_ops

        stale = tmp_path / ".recommendations-log.jsonl"
        stale.write_text('{"id": "rec-1"}\n', encoding="utf-8")
        requested: list[str] = []

        def _reader(table: str) -> list[dict]:
            requested.append(table)
            return []

        with patch("scripts.sync.ops._pull_via_reader", side_effect=_reader), patch("scripts.sync.ops._LOGS_DIR", tmp_path):
            result = sync_ops.sync()

        assert "ops_recommendations" not in requested
        assert "ops_recommendations" not in result["pulled"]
        assert not stale.exists()

    def test_stale_file_that_cannot_be_deleted_is_logged_not_raised(self, tmp_path):
        from scripts.sync import ops as sync_ops

        with patch("pathlib.Path.unlink", side_effect=OSError("denied")), patch("scripts.sync.ops._LOGS_DIR", tmp_path):
            sync_ops._delete_retired_recs_cache()

    def test_warm_sync_never_requests_recs_and_deletes_stale_file(self, tmp_path):
        from scripts.sync import ops as sync_ops

        stale = tmp_path / ".recommendations-log.jsonl"
        stale.write_text('{"id": "rec-1"}\n', encoding="utf-8")
        requested: list[str] = []

        def _reader(table: str) -> list[dict]:
            requested.append(table)
            return []

        with patch("scripts.sync.ops._pull_via_reader", side_effect=_reader), patch("scripts.sync.ops._LOGS_DIR", tmp_path):
            result = sync_ops.warm_sync()

        assert "ops_recommendations" not in requested
        assert "ops_recommendations" not in result["rows"]
        assert not stale.exists()

    def test_missing_stale_file_is_not_an_error(self, tmp_path):
        from scripts.sync import ops as sync_ops

        with patch("scripts.sync.ops._pull_via_reader", return_value=[]), patch("scripts.sync.ops._LOGS_DIR", tmp_path):
            assert sync_ops.sync()["failed"] == []
