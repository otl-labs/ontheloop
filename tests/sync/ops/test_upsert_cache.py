"""Tests for scripts/sync/ops.py -- upsert_cache_row() + warm_sync() concern.

PLAN-coverage-paydown-ops-writer-sync-ops: covers upsert_cache_row's remaining 18 statements
(unknown-table/missing-merge-key guards, the char-device OSError path, and the existing-cache
read/parse loop) and warm_sync's 10 statements (the whole function -- not otherwise exercised).
"""

from __future__ import annotations

import json
from unittest.mock import patch


class TestUpsertCacheRowGuards:
    def test_unknown_table_no_path_returns_zero(self):
        """upsert_cache_row() returns 0 for a table with no _TABLE_TO_LOCAL mapping and no path override."""
        from scripts.sync.ops import upsert_cache_row

        result = upsert_cache_row("not_a_real_table", {"id": "x-1"})
        assert result == 0

    def test_row_missing_merge_key_returns_zero(self, tmp_path):
        """upsert_cache_row() returns 0 when the row has no value for the merge key."""
        from scripts.sync.ops import upsert_cache_row

        cache_file = tmp_path / "recs.jsonl"
        result = upsert_cache_row("ops_recommendations", {"title": "no id here"}, path=cache_file)
        assert result == 0
        assert not cache_file.exists()

    def test_row_with_falsy_merge_key_value_returns_zero(self, tmp_path):
        """A merge-key value of '' is falsy and treated as missing."""
        from scripts.sync.ops import upsert_cache_row

        cache_file = tmp_path / "recs.jsonl"
        result = upsert_cache_row("ops_recommendations", {"id": ""}, path=cache_file)
        assert result == 0


class TestUpsertCacheRowCharDeviceGuard:
    """The is_chardev try/except OSError branch and the is_chardev-True short-circuit."""

    def test_stat_ischr_raising_oserror_is_treated_as_not_chardev(self, tmp_path):
        """A stat.S_ISCHR failure is caught (OSError) and treated as is_chardev=False -- write proceeds."""
        from scripts.sync import ops as sync_ops

        cache_file = tmp_path / "recs.jsonl"
        cache_file.write_text("", encoding="utf-8")  # must exist for local_path.exists() to be True

        with patch("scripts.sync.ops.stat.S_ISCHR", side_effect=OSError("simulated stat failure")):
            result = sync_ops.upsert_cache_row("ops_recommendations", {"id": "rec-0001", "title": "t"}, path=cache_file)

        assert result == 1
        saved = json.loads(cache_file.read_text(encoding="utf-8").strip())
        assert saved["id"] == "rec-0001"

    def test_is_chardev_true_returns_zero_without_writing(self, tmp_path):
        """When the path resolves as a character device, upsert_cache_row() no-ops (returns 0)."""
        from scripts.sync import ops as sync_ops

        cache_file = tmp_path / "recs.jsonl"
        cache_file.write_text("existing content", encoding="utf-8")

        with patch("scripts.sync.ops.stat.S_ISCHR", return_value=True):
            result = sync_ops.upsert_cache_row("ops_recommendations", {"id": "rec-0002", "title": "t"}, path=cache_file)

        assert result == 0
        # File must be untouched -- no tmp-file rewrite occurred
        assert cache_file.read_text(encoding="utf-8") == "existing content"


class TestUpsertCacheRowExistingCacheParsing:
    """The existing-cache read loop: blank lines, comments, malformed JSON, and missing keys."""

    def test_parses_existing_cache_skipping_blanks_comments_and_bad_json(self, tmp_path):
        """Blank lines, '#'-prefixed comments, and malformed JSON are all skipped without error."""
        from scripts.sync import ops as sync_ops

        cache_file = tmp_path / "recs.jsonl"
        cache_file.write_text(
            "\n".join(
                [
                    "",
                    "  ",
                    "# a comment line",
                    json.dumps({"id": "rec-0001", "title": "existing one"}),
                    "not valid json {{{",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = sync_ops.upsert_cache_row("ops_recommendations", {"id": "rec-0002", "title": "new one"}, path=cache_file)

        assert result == 2  # existing rec-0001 preserved + new rec-0002 appended
        lines = [json.loads(line) for line in cache_file.read_text(encoding="utf-8").splitlines()]
        ids = {row["id"] for row in lines}
        assert ids == {"rec-0001", "rec-0002"}

    def test_existing_row_missing_merge_key_is_not_cached(self, tmp_path):
        """An existing cache row with no merge-key value is dropped from by_key (not re-emitted)."""
        from scripts.sync import ops as sync_ops

        cache_file = tmp_path / "recs.jsonl"
        cache_file.write_text(json.dumps({"title": "hollow row, no id"}) + "\n", encoding="utf-8")

        result = sync_ops.upsert_cache_row("ops_recommendations", {"id": "rec-0003", "title": "t"}, path=cache_file)

        assert result == 1
        lines = [json.loads(line) for line in cache_file.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 1
        assert lines[0]["id"] == "rec-0003"

    def test_replace_in_place_keeps_position(self, tmp_path):
        """Updating an existing merge-key row keeps its original position (last-wins in place)."""
        from scripts.sync import ops as sync_ops

        cache_file = tmp_path / "recs.jsonl"
        cache_file.write_text(
            "\n".join(
                [
                    json.dumps({"id": "rec-a", "title": "first"}),
                    json.dumps({"id": "rec-b", "title": "second"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = sync_ops.upsert_cache_row("ops_recommendations", {"id": "rec-a", "title": "updated"}, path=cache_file)

        assert result == 2
        lines = [json.loads(line) for line in cache_file.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["id"] == "rec-a"
        assert lines[0]["title"] == "updated"
        assert lines[1]["id"] == "rec-b"


class TestWarmSync:
    """warm_sync() -- the single warm-up pass of per-table reader pulls."""

    def test_warm_sync_covers_every_table(self, tmp_path):
        """warm_sync() returns pulled/rows/reader_ok keyed by every _TABLE_TO_LOCAL table."""
        from scripts.sync import ops as sync_ops

        fake_tables = {"ops_recommendations": ".recs.jsonl", "ops_decisions": ".decisions.jsonl"}

        def _fake_pull(table, profile=None):
            if table == "ops_recommendations":
                return 3, [{"id": "rec-1"}, {"id": "rec-2"}, {"id": "rec-3"}]
            return 0, None  # reader failure for ops_decisions

        with (
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", fake_tables),
            patch("scripts.sync.ops._pull_single_table_with_rows", side_effect=_fake_pull) as mock_pull,
        ):
            result = sync_ops.warm_sync(profile="test-profile")

        assert mock_pull.call_count == 2
        assert "drained" not in result
        assert result["pulled"] == {"ops_recommendations": 3, "ops_decisions": 0}
        assert result["rows"]["ops_recommendations"] == [{"id": "rec-1"}, {"id": "rec-2"}, {"id": "rec-3"}]
        assert result["rows"]["ops_decisions"] is None
        assert result["reader_ok"] == {"ops_recommendations": True, "ops_decisions": False}

    def test_warm_sync_pulls_tables_in_registration_order(self, tmp_path):
        """warm_sync() issues one reader pull per table, in _TABLE_TO_LOCAL order."""
        from scripts.sync import ops as sync_ops

        call_order: list[str] = []

        def _fake_pull(table, profile=None):
            call_order.append(f"pull:{table}")
            return 0, None

        with (
            patch("scripts.sync.ops._LOGS_DIR", tmp_path),
            patch("scripts.sync.ops._TABLE_TO_LOCAL", {"ops_recommendations": ".recs.jsonl", "ops_decisions": ".d.jsonl"}),
            patch("scripts.sync.ops._pull_single_table_with_rows", side_effect=_fake_pull),
        ):
            sync_ops.warm_sync()

        assert call_order == ["pull:ops_recommendations", "pull:ops_decisions"]
