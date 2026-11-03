"""Tests for the scripts/ops_portal/maintenance_ops.py surface: enqueue_findings,
find_open_postmortem_for / purge_postmortems_for (SCD2 supersede flow), and the
--selftest-read / --selftest-roundtrip portal probes.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ops_portal import maintenance_ops as _maintenance_ops_mod  # noqa: E402
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402


class TestEnqueueFindings:
    """Tests for enqueue_findings()."""

    def test_enqueue_findings_bulk_success(self, tmp_path: Path) -> None:
        """enqueue_findings() files each valid entry through file_rec (writer-allocated ids)."""
        recs_file = tmp_path / ".recommendations-log.jsonl"
        jsonl_file = tmp_path / "findings.jsonl"
        entry = {**_VALID_FIELDS, "source": "cc-scheduled-agent-test"}
        jsonl_file.write_text("\n".join([json.dumps(entry)] * 3) + "\n", encoding="utf-8")

        with (
            patch(
                "scripts.ops_data_portal._ducklake_write",
                side_effect=[{"key": "rec-801"}, {"key": "rec-802"}, {"key": "rec-803"}],
            ) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import enqueue_findings

            result = enqueue_findings(jsonl_file)

        assert result == {"enqueued": 3, "invalid": 0, "skipped": 0}
        ids = [call.args[1]["id"] for call in mock_write.call_args_list]
        assert ids == ["rec-801", "rec-802", "rec-803"]

    def test_enqueue_findings_invalid_entries_counted_not_raised(self, tmp_path: Path) -> None:
        """enqueue_findings() counts schema failures as invalid and JSON parse errors as skipped."""
        recs_file = tmp_path / ".recommendations-log.jsonl"
        jsonl_file = tmp_path / "mixed.jsonl"
        valid = {**_VALID_FIELDS, "source": "cc-scheduled-agent-test"}
        lines = [
            json.dumps(valid),
            json.dumps(valid),
            json.dumps({"missing_required_fields": True}),
            "not valid json {{{",
        ]
        jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with (
            patch("scripts.ops_data_portal._ducklake_write", side_effect=[{"key": "rec-801"}, {"key": "rec-802"}]),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import enqueue_findings

            result = enqueue_findings(jsonl_file)

        assert result == {"enqueued": 2, "invalid": 1, "skipped": 1}

    def test_enqueue_findings_missing_path(self, tmp_path: Path) -> None:
        """enqueue_findings() returns zeros without raising when given a non-existent path."""
        from scripts.ops_data_portal import enqueue_findings

        result = enqueue_findings(tmp_path / "does_not_exist.jsonl")

        assert result == {"enqueued": 0, "invalid": 0, "skipped": 0}


class TestPostmortems:
    """Tests for find_open_postmortem_for and the SCD2 purge (supersede) flow."""

    def test_find_open_postmortem_for_returns_match(self, tmp_path: Path) -> None:
        recs_file = tmp_path / "recs.jsonl"
        postmortem = {
            "id": "rec-529",
            "status": "open",
            "source": "executor-postmortem",
            "title": "Investigate executor failure for rec-100",
        }
        recs_file.write_text(json.dumps(postmortem) + "\n", encoding="utf-8")

        with patch("scripts.ops_portal.maintenance_ops.RECS_JSONL", recs_file):
            from scripts.ops_data_portal import find_open_postmortem_for

            result = find_open_postmortem_for("rec-100")

        assert result is not None
        assert result["id"] == "rec-529"

    def test_find_open_postmortem_for_returns_none_when_declined(self, tmp_path: Path) -> None:
        recs_file = tmp_path / "recs.jsonl"
        postmortem = {
            "id": "rec-529",
            "status": "declined",
            "source": "executor-postmortem",
            "title": "Investigate executor failure for rec-100",
        }
        recs_file.write_text(json.dumps(postmortem) + "\n", encoding="utf-8")

        with patch("scripts.ops_portal.maintenance_ops.RECS_JSONL", recs_file):
            from scripts.ops_data_portal import find_open_postmortem_for

            result = find_open_postmortem_for("rec-100")

        assert result is None

    def test_find_open_postmortem_for_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "missing.jsonl"
        with patch("scripts.ops_portal.maintenance_ops.RECS_JSONL", missing_file):
            from scripts.ops_data_portal import find_open_postmortem_for

            result = find_open_postmortem_for("rec-100")

        assert result is None

    @staticmethod
    def _reader_with_rows(rows: list[dict]) -> MagicMock:
        reader = MagicMock()
        reader.named.return_value = rows
        return reader

    def test_purge_postmortems_dry_run_matches_without_writing(self) -> None:
        """dry_run reports the matched (non-superseded executor-postmortem) recs and writes nothing."""
        rows = [
            {
                "id": "rec-529",
                "source": "executor-postmortem",
                "status": "open",
                "title": "Investigate executor failure for rec-100 (attempt 2)",
            },
            {
                "id": "rec-530",
                "source": "executor-postmortem",
                "status": "superseded",
                "title": "Investigate executor failure for rec-100",
            },
            {"id": "rec-531", "source": "code-review", "status": "open", "title": "Investigate executor failure for rec-100"},
            {
                "id": "rec-532",
                "source": "executor-postmortem",
                "status": "open",
                "title": "Investigate executor failure for rec-1001",
            },
        ]
        reader = self._reader_with_rows(rows)

        with (
            patch("src.common.ducklake_reader_client.make_reader", return_value=reader),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            from scripts.ops_data_portal import purge_postmortems_for

            result = purge_postmortems_for("rec-100", dry_run=True)

        assert result == {"matched": ["rec-529"], "superseded": 0}
        reader.named.assert_called_once_with("recs_by_title_prefix", title_prefix="Investigate executor failure for rec-100%")
        mock_update.assert_not_called()

    def test_purge_postmortems_supersedes_and_declines(self) -> None:
        """Each matched postmortem becomes status=superseded via update_rec; the failed rec is declined."""
        rows = [
            {
                "id": "rec-529",
                "source": "executor-postmortem",
                "status": "open",
                "title": "Investigate executor failure for rec-100",
            },
            {
                "id": "rec-533",
                "source": "executor-postmortem",
                "status": "open",
                "title": "Investigate executor failure for rec-100 (attempt 2)",
            },
        ]
        reader = self._reader_with_rows(rows)

        with (
            patch("src.common.ducklake_reader_client.make_reader", return_value=reader),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            from scripts.ops_data_portal import purge_postmortems_for

            result = purge_postmortems_for("rec-100", dry_run=False)

        assert result["matched"] == ["rec-529", "rec-533"]
        assert result["superseded"] == 2
        statuses = {call.args[0]: call.args[1]["status"] for call in mock_update.call_args_list}
        assert statuses["rec-529"] == "superseded"
        assert statuses["rec-533"] == "superseded"
        assert statuses["rec-100"] == "declined"
        assert mock_update.call_count == 3

    def test_purge_postmortems_invalid_rec_id_raises(self) -> None:
        """Malformed rec id raises ValueError before any reader call."""
        from scripts.ops_data_portal import purge_postmortems_for

        with pytest.raises(ValueError, match="Invalid rec ID"):
            purge_postmortems_for("'; DROP TABLE ops_recommendations; --")


class TestSelftests:
    """--selftest-read / --selftest-roundtrip portal probes (VP14/15)."""

    def test_selftest_read_reports_ducklake(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        class _Reader:
            def current_state(self, table, **kw):
                return [{"id": "rec-1"}]

        monkeypatch.setattr("src.common.ducklake_reader_client.make_reader", lambda **kw: _Reader())
        out = p.selftest_read()
        assert out["backend"] == "ducklake" and out["row_count"] == 1 and out["sample_id"] == "rec-1"

    def test_selftest_roundtrip_ducklake(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        written: dict = {}
        calls: list = []

        def fake_write(t, r, *, action, profile=None):
            calls.append(action)
            written.update(id=r["id"])
            return {"ok": True}

        monkeypatch.setattr(_maintenance_ops_mod, "_ducklake_write", fake_write)

        class _Reader:
            def current_state(self, table, *, row_filter=None, **kw):
                return [{"id": written["id"]}]

        monkeypatch.setattr("src.common.ducklake_reader_client.make_reader", lambda **kw: _Reader())
        out = p.selftest_roundtrip()
        assert out["read_back"] is True and out["backend"] == "ducklake"
        assert calls == ["write_ops", "update_ops"]

    def test_selftest_roundtrip_purges_probe(self, monkeypatch) -> None:
        """rec-2114: on successful read-back, selftest_roundtrip supersedes its own probe via a
        direct writer update_ops call (caller-keyed test- keyspace, Decision 84 I-2 -- NOT
        update_rec, which only accepts writer-allocated rec-NNN ids) so no open
        test-roundtrip-<uuid> row lingers to fail the automatable not_null DQ check."""
        import scripts.ops_data_portal as p

        written: dict = {}
        write_calls: list = []

        def fake_write(t, r, *, action, profile=None):
            write_calls.append((action, dict(r)))
            written.update(id=r["id"])
            return {"ok": True}

        monkeypatch.setattr(_maintenance_ops_mod, "_ducklake_write", fake_write)

        class _Reader:
            def current_state(self, table, *, row_filter=None, **kw):
                return [{"id": written["id"]}]

        monkeypatch.setattr("src.common.ducklake_reader_client.make_reader", lambda **kw: _Reader())
        out = p.selftest_roundtrip()

        assert out["superseded"] is True
        assert len(write_calls) == 2
        supersede_action, supersede_record = write_calls[-1]
        assert supersede_action == "update_ops"
        assert supersede_record["id"] == written["id"]
        assert supersede_record["status"] == "superseded"

    def test_selftest_roundtrip_loud_fails_when_not_read_back(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_maintenance_ops_mod, "_ducklake_write", lambda *a, **k: {"ok": True})

        class _Reader:
            def current_state(self, table, *, row_filter=None, **kw):
                return []

        monkeypatch.setattr("src.common.ducklake_reader_client.make_reader", lambda **kw: _Reader())
        with pytest.raises(RuntimeError, match="read-back"):
            p.selftest_roundtrip()
