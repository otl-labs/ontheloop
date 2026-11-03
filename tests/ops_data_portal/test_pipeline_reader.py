"""Tests for the ops pipeline consolidation / reader-cache-sync surface (Decision 69 / 84):
_fetch_rec_from_reader and _sync_table / sync.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS


class TestPipelineConsolidation:
    """Tests for the ops pipeline consolidation changes (Decision 69 / Decision 84)."""

    def test_update_rec_reads_from_reader_not_jsonl(self, tmp_path: Path) -> None:
        """update_rec() calls _fetch_rec_from_reader (warehouse reader) not a local JSONL read."""
        recs_file = tmp_path / ".recommendations-log.jsonl"
        existing = dict(_VALID_FIELDS, id="rec-042", status="open")

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing) as mock_fetch,
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            update_rec("rec-042", {"status": "closed"})

        mock_fetch.assert_called_once_with("rec-042", profile=None)

    def test_sync_returns_pull_only_report(self) -> None:
        """sync() returns {'pulled': ...} only -- no drain/compact/view-refresh keys (Decision 84 I-4)."""
        with patch("scripts.sync.ops._pull_single_table", return_value=10):
            from scripts.ops_data_portal import sync

            result = sync(["ops_decisions"])

        assert result == {"pulled": {"ops_decisions": 10}}

    def test_sync_defaults_to_all_migrated_tables(self) -> None:
        """sync() with no args pulls decisions and the priority queue (recs have no cache, Decision 215)."""
        pulled: list[str] = []
        with patch("scripts.sync.ops._pull_single_table", side_effect=lambda t: pulled.append(t) or 1):
            from scripts.ops_data_portal import sync

            result = sync()

        assert pulled == ["ops_decisions", "ops_priority_queue"]
        assert result == {"pulled": dict.fromkeys(pulled, 1)}

    def test_update_rec_raises_on_reader_unreachable(self, tmp_path: Path) -> None:
        """update_rec() propagates RuntimeError when _fetch_rec_from_reader raises."""
        with (
            patch(
                "scripts.ops_data_portal._fetch_rec_from_reader",
                side_effect=RuntimeError("reader unreachable"),
            ),
        ):
            from scripts.ops_data_portal import update_rec

            with pytest.raises(RuntimeError, match="reader unreachable"):
                update_rec("rec-042", {"status": "closed"})


class TestFetchRecFromReader:
    """Tests for _fetch_rec_from_reader -- the rec_by_id named verb (Decision 84 I-3)."""

    _REC_ROW = {
        "id": "rec-042",
        "title": "Test rec",
        "file": "scripts/ops_data_portal.py",
        "context": "ctx",
        "acceptance": "grep -q x y",
        "effort": "XS",
        "priority": "Low",
        "source": "planning",
        "risk": "low",
        "status": "open",
        "automatable": True,
        "last_updated_timestamp": "2026-05-01T00:00:00Z",
        "created_timestamp": "2026-05-01T00:00:00Z",
    }

    def test_named_verb_returns_record(self) -> None:
        """Reader named('rec_by_id', id=...) success -> returns sanitised record."""
        reader = MagicMock()
        reader.named.return_value = [dict(self._REC_ROW)]

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_rec_from_reader

            result = _fetch_rec_from_reader("rec-042")

        assert result is not None
        assert result["id"] == "rec-042"
        assert result["status"] == "open"
        reader.named.assert_called_once_with("rec_by_id", id="rec-042")

    def test_reader_failure_loud_fails(self) -> None:
        """Reader failure propagates -- no local-cache fallback (Decision 69)."""
        reader = MagicMock()
        reader.named.side_effect = RuntimeError("ducklake_reader 'named_read' failed (HTTP 500)")

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_rec_from_reader

            with pytest.raises(RuntimeError, match="ducklake_reader"):
                _fetch_rec_from_reader("rec-042")

    def test_reader_returns_none_when_row_not_found(self) -> None:
        """Reader returns empty list -> function returns None."""
        reader = MagicMock()
        reader.named.return_value = []

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_rec_from_reader

            result = _fetch_rec_from_reader("rec-999")

        assert result is None

    def test_invalid_rec_id_raises_value_error(self) -> None:
        """Malformed rec_id raises ValueError before any reader call (security guard)."""
        from scripts.ops_data_portal import _fetch_rec_from_reader

        with pytest.raises(ValueError, match="invalid rec_id"):
            _fetch_rec_from_reader("'; DROP TABLE ops_recommendations; --")


class TestSyncTable:
    """_sync_table / sync are a pure reader cache-pull (no drain, no compaction)."""

    def test_sync_table_pulls_single_table(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        calls: list[str] = []
        monkeypatch.setattr("scripts.sync.ops._pull_single_table", lambda t: calls.append(t) or 0)
        p._sync_table("ops_recommendations")
        p._sync_table("ops_decisions")
        assert calls == ["ops_recommendations", "ops_decisions"]


class TestRecsCacheNotWritten:
    """Decision 215: portal writes never create or modify the recs cache; sync() refuses the table."""

    def test_update_rec_does_not_touch_recs_cache(self, tmp_path: Path) -> None:
        recs_file = tmp_path / ".recommendations-log.jsonl"
        recs_file.write_text('{"id": "rec-042", "status": "open"}\n', encoding="utf-8")
        before = recs_file.read_text(encoding="utf-8")
        existing = dict(_VALID_FIELDS, id="rec-042", status="open")

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch(
                "scripts.ops_data_portal._ducklake_write", return_value={"ok": True, "ulid": "ulid-test-0001"}
            ) as mock_write,
            patch("scripts.sync.ops._pull_single_table") as mock_pull,
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            update_rec("rec-042", {"status": "closed"})

        assert mock_write.call_args.args[1]["status"] == "closed"
        mock_pull.assert_not_called()
        assert recs_file.read_text(encoding="utf-8") == before

    def test_file_rec_does_not_create_recs_cache(self, tmp_path: Path) -> None:
        recs_file = tmp_path / ".recommendations-log.jsonl"

        with (
            patch(
                "scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-700", "ulid": "ulid-test-0001"}
            ) as mock_write,
            patch("scripts.sync.ops._pull_single_table") as mock_pull,
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            assert file_rec(dict(_VALID_FIELDS)) == "rec-700"

        assert mock_write.call_args.args[1]["id"] == "rec-700"
        mock_pull.assert_not_called()
        assert not recs_file.exists()

    def test_sync_refuses_ops_recommendations(self) -> None:
        with patch("scripts.sync.ops._pull_single_table") as mock_pull:
            from scripts.ops_data_portal import sync

            with pytest.raises(ValueError, match="Decision 215"):
                sync(["ops_decisions", "ops_recommendations"])

        mock_pull.assert_not_called()

    def test_sync_default_omits_ops_recommendations(self) -> None:
        with patch("scripts.sync.ops._pull_single_table", return_value=0) as mock_pull:
            from scripts.ops_data_portal import sync

            result = sync()

        assert "ops_recommendations" not in result["pulled"]
        assert all(c.args[0] != "ops_recommendations" for c in mock_pull.call_args_list)
