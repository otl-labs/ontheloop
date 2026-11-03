"""Tests for the scripts/ops_portal/decisions.py surface: file_decision / update_decision /
fetch_decision, and the DECISIONS.md -> ops_decisions backfill ETL.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

duckdb = pytest.importorskip("duckdb")

from tests.fixtures.ops_portal_records import VALID_DECISION_FIELDS as _VALID_DECISION_FIELDS  # noqa: E402


class TestFileDecision:
    """Tests for file_decision() -- DECISIONS.md is the numbering authority (Decision 84 I-2 exception)."""

    def test_file_decision_success_with_decision_id(self, tmp_path: Path) -> None:
        """file_decision() forms dec-NNN from fields['decision_id'] and writes via write_ops."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table") as mock_sync,
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            result = file_decision(dict(_VALID_DECISION_FIELDS))

        assert result == "dec-056"
        call_table, call_rec = mock_dl_write.call_args[0]
        assert call_table == "ops_decisions"
        assert mock_dl_write.call_args.kwargs["action"] == "write_ops"
        assert call_rec["decision_id"] == 56
        assert call_rec["id"] == "dec-056"
        assert call_rec["created_timestamp"] and call_rec["last_updated_timestamp"]
        # D4: the cache refresh is an incremental upsert, not a full-table _sync_table pull.
        mock_sync.assert_not_called()
        cached = [json.loads(line) for line in decisions_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(d["id"] == "dec-056" for d in cached)

    def test_file_decision_requires_decision_id(self, tmp_path: Path) -> None:
        """file_decision() raises ValueError when no DECISIONS.md-assigned integer is supplied."""
        with patch("scripts.ops_portal.decisions._ducklake_write") as mock_dl_write:
            from scripts.ops_data_portal import file_decision

            with pytest.raises(ValueError, match="DECISIONS.md-assigned integer"):
                file_decision({"title": "No number", "status": "open"})

        mock_dl_write.assert_not_called()

    def test_file_decision_rejects_non_positive_and_non_int_decision_id(self) -> None:
        """decision_id must be a positive int -- 0 and string forms are rejected."""
        from scripts.ops_data_portal import file_decision

        with pytest.raises(ValueError, match="DECISIONS.md-assigned integer"):
            file_decision({"title": "Zero", "status": "open", "decision_id": 0})
        with pytest.raises(ValueError, match="DECISIONS.md-assigned integer"):
            file_decision({"title": "String", "status": "open", "decision_id": "84"})

    def test_file_decision_migration_int_id_takes_precedence(self, tmp_path: Path) -> None:
        """_migration_int_id supplies the number on the backfill path (no decision_id field needed)."""
        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", tmp_path / "dec.jsonl"),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            result = file_decision({"title": "Backfill", "status": "open"}, _migration_int_id=84)

        assert result == "dec-084"
        _, call_rec = mock_dl_write.call_args[0]
        assert call_rec["id"] == "dec-084"
        assert call_rec["decision_id"] == 84


class TestUpdateDecision:
    """Tests for update_decision() and the reader-backed decision fetch."""

    _EXISTING = {
        "id": "dec-042",
        "title": "D",
        "status": "open",
        "created_timestamp": "2026-05-01T00:00:00+00:00",
        "last_updated_timestamp": "2026-05-01T00:00:00+00:00",
    }

    def test_update_decision_routes_update_ops(self, tmp_path: Path) -> None:
        """update_decision() merges and writes via _ducklake_write(action='update_ops')."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        with (
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=dict(self._EXISTING)),
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table") as mock_sync,
        ):
            from scripts.ops_data_portal import update_decision

            assert update_decision("dec-042", {"status": "closed"}) is True

        call_table, call_rec = mock_dl_write.call_args[0]
        assert call_table == "ops_decisions"
        assert call_rec["status"] == "closed"
        assert mock_dl_write.call_args.kwargs["action"] == "update_ops"
        # D4: the cache refresh is an incremental upsert, not a full-table _sync_table pull.
        mock_sync.assert_not_called()
        cached = [json.loads(line) for line in decisions_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(d["id"] == "dec-042" and d["status"] == "closed" for d in cached)

    def test_update_decision_absent_loud_fails(self) -> None:
        """update_decision() raises RuntimeError when the decision is absent from the projection."""
        with (
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._ducklake_write") as mock_dl_write,
        ):
            from scripts.ops_data_portal import update_decision

            with pytest.raises(RuntimeError, match="does not exist"):
                update_decision("dec-042", {"status": "closed"})

        mock_dl_write.assert_not_called()

    def test_fetch_decision_from_reader_uses_named_verb(self) -> None:
        """_fetch_decision_from_reader uses named('decision_by_id', id=...) on the DuckLake reader."""
        reader = MagicMock()
        reader.named.return_value = [dict(self._EXISTING)]

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_decision_from_reader

            result = _fetch_decision_from_reader("dec-042")

        assert result is not None
        assert result["id"] == "dec-042"
        reader.named.assert_called_once_with("decision_by_id", id="dec-042")

    def test_fetch_decision_from_reader_invalid_id(self) -> None:
        """Malformed decision_id raises ValueError before any reader call."""
        from scripts.ops_data_portal import _fetch_decision_from_reader

        with pytest.raises(ValueError, match="invalid decision_id"):
            _fetch_decision_from_reader("rec-042")


class TestBackfillDecisionsFromMd:
    """Tests for backfill_decisions_from_md() -- DECISIONS.md -> ops_decisions ETL."""

    def test_backfill_writes_coerced_entries(self) -> None:
        """Each parsed entry is filed via file_decision(_migration_int_id=n, _skip_sync=True)."""
        entries = [
            {
                "decision_id": 84,
                "title": "Decision 84",
                "status": "Decided",
                "problem": "p",
                "decision_text": "d",
                "context": "c",
                "decided_date": "2026-06-10",
                "related_decisions": "[81, 79]",
                "not_a_backfill_col": "dropped",
            },
            {"decision_id": "", "title": "no number"},  # skipped
        ]
        with (
            patch("scripts.decisions_md.parse_decisions_md", return_value=entries),
            patch("scripts.ops_portal.decisions.file_decision", return_value="dec-084") as mock_fd,
            patch("scripts.ops_portal.decisions._sync_table") as mock_sync,
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows") as mock_orphan_guard,
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        mock_orphan_guard.assert_called_once()
        assert result == {"written": 1, "failed": 0, "skipped": 1}
        mock_fd.assert_called_once()
        fields = mock_fd.call_args.args[0]
        assert fields["related_decisions"] == [81, 79]
        assert "not_a_backfill_col" not in fields and "decision_id" not in fields
        assert mock_fd.call_args.kwargs["_migration_int_id"] == 84
        assert mock_fd.call_args.kwargs["_skip_sync"] is True
        mock_sync.assert_called_once_with("ops_decisions")

    def test_backfill_isolates_per_row_failures(self) -> None:
        """A failing row increments failed without aborting the run; no sync when nothing written."""
        entries = [
            {"decision_id": 1, "title": "boom", "status": "Decided"},
            {"decision_id": "not-an-int", "title": "skip me"},
        ]
        with (
            patch("scripts.decisions_md.parse_decisions_md", return_value=entries),
            patch("scripts.ops_portal.decisions.file_decision", side_effect=RuntimeError("writer down")),
            patch("scripts.ops_portal.decisions._sync_table") as mock_sync,
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 0, "failed": 1, "skipped": 1}
        mock_sync.assert_not_called()


def test_file_decision_and_update_decision_refuse_through_the_gate() -> None:
    """Unpatched gate (AC1): a null or blank required input is refused with the gate's message on both call sites and the
    writer is never called."""
    from scripts.ops_data_portal import file_decision, update_decision

    existing = {"id": "dec-042", "title": "D", "status": "open"}
    with (
        patch("scripts.ops_portal.decisions._ducklake_write") as mock_dl_write,
        patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=dict(existing)),
    ):
        with pytest.raises(ValueError, match="required input field 'title' is empty"):
            file_decision({"title": "", "status": "open", "decision_id": 56})
        with pytest.raises(ValueError, match="required input field 'status' is missing or null"):
            update_decision("dec-042", {"status": None})
    mock_dl_write.assert_not_called()
