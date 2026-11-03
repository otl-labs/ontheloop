"""Tests for the scripts/ops_data_portal.py write-path core: file_rec / update_rec,
the row-rule gate dispatch, retired-surface guards, and the private migration params.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3). Decision 124
namespace migration: patches below target the facade (scripts.ops_data_portal) because the
driving call (file_rec/update_rec) is facade-resident and resolves its dependencies as its own
module globals (tests/CLAUDE.md namespace migration discipline).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")
from pydantic import ValidationError  # noqa: E402

from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402
from tests.fixtures.ops_rec_rows import reader_rec_row, writer_stand_in  # noqa: E402


class TestFileRec:
    """Tests for file_rec() -- writer-allocated IDs via the file_ops action."""

    def test_file_rec_success_consumes_allocated_key(self, tmp_path: Path) -> None:
        """file_rec() sends action=file_ops with an idempotency ULID and returns the writer-allocated id."""
        recs_file = tmp_path / ".recommendations-log.jsonl"

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-2171"}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(dict(_VALID_FIELDS))

        assert result == "rec-2171"
        mock_dl_write.assert_called_once()
        call_table, call_rec = mock_dl_write.call_args[0]
        assert call_table == "ops_recommendations"
        assert call_rec["status"] == "open"
        assert mock_dl_write.call_args.kwargs["action"] == "file_ops"
        ulid = mock_dl_write.call_args.kwargs["idempotency_ulid"]
        assert isinstance(ulid, str) and len(ulid) == 26  # ULID (time-ordered tiebreak for history reads)
        # the record handed to the writer carries the allocated id (no local cache write, Decision 215)
        assert call_rec["id"] == "rec-2171"

    def test_file_rec_missing_allocated_key_raises(self, tmp_path: Path) -> None:
        """file_rec() raises RuntimeError when the writer response carries no allocated key."""
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
        ):
            from scripts.ops_data_portal import file_rec

            with pytest.raises(RuntimeError, match="no allocated key"):
                file_rec(dict(_VALID_FIELDS))

    def test_file_rec_raises_loudly_on_writer_failure(self, tmp_path: Path) -> None:
        """file_rec() propagates the writer failure -- there is no offline outbox / 'pending-' path."""
        recs_file = tmp_path / ".recommendations-log.jsonl"

        with (
            patch(
                "scripts.ops_data_portal._ducklake_write",
                side_effect=RuntimeError("ducklake_writer file_ops ops_recommendations failed (HTTP 500)"),
            ),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            with pytest.raises(RuntimeError, match="ducklake_writer"):
                file_rec(dict(_VALID_FIELDS))

        assert not recs_file.exists()  # nothing was written through

    def test_file_rec_invalid_schema(self) -> None:
        """file_rec() raises when rec fields fail validation (write-time gate or Pydantic)."""
        invalid_fields = dict(_VALID_FIELDS)
        invalid_fields.pop("status")

        from scripts.ops_data_portal import file_rec

        with pytest.raises((ValidationError, ValueError)):
            file_rec(invalid_fields)

    def test_file_rec_date_added_if_missing(self, tmp_path: Path) -> None:
        """file_rec() adds today's date to the record if not supplied."""
        recs_file = tmp_path / ".recommendations-log.jsonl"

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-602"}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(dict(_VALID_FIELDS))

        assert result == "rec-602"
        entry = mock_dl_write.call_args[0][1]
        assert entry.get("date") is not None  # date was added

    def test_file_rec_rejects_unregistered_source(self, tmp_path: Path) -> None:
        """file_rec() raises ValueError when source is not in the registry; no write is attempted."""
        fields = dict(_VALID_FIELDS)
        fields["source"] = "ghost-agent"

        with (
            patch("scripts.ops_data_portal._ducklake_write") as mock_dl_write,
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
        ):
            from scripts.ops_data_portal import file_rec

            with pytest.raises(ValueError, match="Unknown source 'ghost-agent'"):
                file_rec(fields)

        mock_dl_write.assert_not_called()

    def test_file_rec_accepts_registered_source(self, tmp_path: Path) -> None:
        """file_rec() succeeds when source is a registered canonical_id."""
        recs_file = tmp_path / ".recommendations-log.jsonl"
        fields = dict(_VALID_FIELDS)
        fields["source"] = "planning"

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-998"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(fields)

        assert result == "rec-998"


class TestUpdateRec:
    """Tests for update_rec()."""

    def test_update_rec_success(self, tmp_path: Path) -> None:
        """update_rec() reads via the reader, merges updates, writes update_ops, writes through ducklake_writer."""
        existing = {**_VALID_FIELDS, "id": "rec-042", "date": "2026-01-01"}
        recs_file = tmp_path / ".recommendations-log.jsonl"
        recs_file.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            result = update_rec("rec-042", {"status": "closed", "execution_result": "success"})

        assert result is True
        call_table, call_rec = mock_dl_write.call_args[0]
        assert call_table == "ops_recommendations"
        assert call_rec["status"] == "closed"
        assert call_rec["execution_result"] == "success"
        assert mock_dl_write.call_args.kwargs["action"] == "update_ops"
        assert call_rec["id"] == "rec-042"

    def test_update_rec_invalid_status(self) -> None:
        """update_rec() raises ValueError for an invalid status: the gate's status accepted_values refuses it after the
        existing-row read."""
        from scripts.ops_data_portal import update_rec

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=reader_rec_row(id="rec-042")),
            patch("scripts.ops_data_portal._ducklake_write") as mock_dl_write,
            pytest.raises(ValueError, match="status"),
        ):
            update_rec("rec-042", {"status": "done"})
        mock_dl_write.assert_not_called()

    def test_update_rec_context_replace(self, tmp_path: Path) -> None:
        """update_rec() with a >=80-char context writes it through via action=update_ops."""
        existing = {**_VALID_FIELDS, "id": "rec-042", "date": "2026-01-01"}
        recs_file = tmp_path / ".recommendations-log.jsonl"
        recs_file.write_text(json.dumps(existing) + "\n", encoding="utf-8")
        new_context = "Replacement context for rec-042 -- long enough to clear the 80-stripped-char write-time floor."

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            result = update_rec("rec-042", {"context": new_context})

        assert result is True
        call_table, call_rec = mock_dl_write.call_args[0]
        assert call_table == "ops_recommendations"
        assert call_rec["context"] == new_context
        assert mock_dl_write.call_args.kwargs["action"] == "update_ops"

    def test_update_rec_context_too_short_rejected(self, tmp_path: Path) -> None:
        """update_rec() still raises ValueError referencing context when the new context is <80 stripped chars: the
        portal's update pre-check skips the dated min_length, so the writer (the shared fake runs its own check_write
        with the stored row as prior) refuses it, mapped to ValueError as writer_transport maps the 422."""
        existing = reader_rec_row(id="rec-042")
        fake_writer = writer_stand_in([existing])

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
            patch("scripts.ops_data_portal._ducklake_write", fake_writer),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / ".recommendations-log.jsonl"),
        ):
            from scripts.ops_data_portal import update_rec

            with pytest.raises(ValueError, match="context"):
                update_rec("rec-042", {"context": "too short"})

        assert fake_writer.writes == []

    def test_update_rec_absent_rec_loud_fails(self, tmp_path: Path) -> None:
        """update_rec() loud-fails on an absent rec (referential, CD.33 cl.8 / D-5).

        An update of a record that does not exist in the current projection raises
        RuntimeError and never silently creates a partial row.
        """
        recs_file = tmp_path / ".recommendations-log.jsonl"
        updates = {**_VALID_FIELDS, "id": "rec-042", "status": "closed"}

        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=None),
            patch("scripts.ops_data_portal._ducklake_write") as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            with pytest.raises(RuntimeError, match="does not exist"):
                update_rec("rec-042", updates)

        # No write happened -- the absent rec was rejected before any write path.
        mock_dl_write.assert_not_called()


class TestRetiredSurfaces:
    """Decision 84: the offline outbox, OpsWriter transport, and DynamoDB id allocation are gone."""

    def test_outbox_and_legacy_symbols_absent(self) -> None:
        """drain_pending / outbox dirs / _next_id / OpsWriter / backend flag no longer exist on the portal."""
        import scripts.ops_data_portal as portal

        for name in (
            "drain_pending",
            "drain_pending_decisions",
            "_PENDING_OUTBOX",
            "_DECISIONS_PENDING_OUTBOX",
            "_next_id",
            "OpsWriter",
            "_ops_backend",
            "_rewrite_jsonl_excluding_postmortems",
        ):
            assert not hasattr(portal, name), f"retired symbol still present: {name}"

    def test_portal_exposes_no_import_bypass_write_surface(self) -> None:
        """The ONLY ops write surface is file_rec/update_rec/file_decision/update_decision. No
        import/bootstrap/bypass writer is exposed on the portal (Decision 81)."""
        import scripts.ops_data_portal as portal

        forbidden = [
            n
            for n in dir(portal)
            if any(k in n.lower() for k in ("import_rec", "bootstrap", "bypass", "seed_rec", "raw_write", "import_ops"))
        ]
        assert forbidden == []


class TestWriteTimeDispatch:
    """file_rec and update_rec dispatch to the writer's own row-rule gate (gate_write); _derive_computed_fields feeds it."""

    def test_gate_dispatch(self) -> None:
        """file_rec gates a new rec with insert=True; update_rec gates the merged row with insert=False and the stored row."""
        from scripts.ops_data_portal import file_rec, update_rec

        existing = reader_rec_row(id="rec-042")
        with (
            patch("scripts.ops_data_portal.gate_write") as mock_gate,
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-960"}),
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
        ):
            file_rec(dict(_VALID_FIELDS))
            assert mock_gate.call_args.args[0] == "ops_recommendations" and mock_gate.call_args.kwargs == {"insert": True}
            update_rec("rec-042", {"priority": "High"})
            assert mock_gate.call_args.kwargs["insert"] is False
            assert mock_gate.call_args.kwargs["existing"] == existing
            assert mock_gate.call_args.args[1]["priority"] == "High"
            file_rec(dict(_VALID_FIELDS), _migration_int_id=9)
            assert mock_gate.call_args.kwargs == {"insert": False}

    def test_file_rec_and_update_rec_refuse_through_the_gate(self) -> None:
        """Unpatched gate: a blank required input is refused with the gate's message on both call sites and the writer is
        never called."""
        from scripts.ops_data_portal import file_rec, update_rec

        with (
            patch("scripts.ops_data_portal._ducklake_write") as mock_dl_write,
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=reader_rec_row(id="rec-042")),
        ):
            with pytest.raises(ValueError, match="at least 80 stripped characters"):
                file_rec({**_VALID_FIELDS, "context": "   "})
            with pytest.raises(ValueError, match="required input field 'title' is empty"):
                update_rec("rec-042", {"title": ""})
        mock_dl_write.assert_not_called()

    def test_write_time_rejects_null_required_field(self) -> None:
        """file_rec() raises ValueError through the gate when a required field is null."""
        from scripts.ops_data_portal import file_rec

        fields = dict(_VALID_FIELDS)
        fields["status"] = None  # status is nullable:false in the contract

        with pytest.raises(ValueError, match="status"):
            file_rec(fields)

    def test_file_rec_computes_automatable(self, tmp_path: Path) -> None:
        """file_rec() derives and sets automatable for records that lack it."""
        recs_file = tmp_path / "recs.jsonl"
        fields_no_automatable = {k: v for k, v in _VALID_FIELDS.items() if k != "automatable"}

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-950"}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            file_rec(fields_no_automatable)

        _, written = mock_dl_write.call_args[0]
        assert written.get("automatable") is not None, "automatable should be derived and non-null"

    def test_file_rec_created_timestamp_full_precision(self, tmp_path: Path) -> None:
        """file_rec() sets created_timestamp with a time component, not a date-only midnight fallback."""
        recs_file = tmp_path / "recs.jsonl"
        fields = dict(_VALID_FIELDS)
        fields.pop("created_timestamp", None)

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-951"}) as mock_dl_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import file_rec

            file_rec(fields)

        _, written = mock_dl_write.call_args[0]
        ts = written.get("created_timestamp", "")
        assert ts, "created_timestamp must be set"
        assert "T" in ts, f"created_timestamp must contain a time component (got {ts!r})"
        assert len(ts) > 10, f"created_timestamp must be a full ISO datetime, not date-only (got {ts!r})"


class TestMigrationParams:
    """Tests for the private migration params on file_rec / file_decision."""

    def test_file_rec_migration_int_id_pads_and_uses_write_ops(self, tmp_path: Path) -> None:
        """_migration_int_id preserves the historical id via a caller-keyed write_ops upsert."""
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.ops_data_portal._sync_table"),
        ):
            from scripts.ops_data_portal import file_rec

            result = file_rec(dict(_VALID_FIELDS), _migration_int_id=5, _skip_sync=True)

        assert result == "rec-005"
        _, call_rec = mock_dl_write.call_args[0]
        assert call_rec["id"] == "rec-005"
        assert mock_dl_write.call_args.kwargs["action"] == "write_ops"
        assert "idempotency_ulid" not in mock_dl_write.call_args.kwargs

    def test_file_rec_never_appends_or_upserts_recs_cache(self, tmp_path: Path) -> None:
        """Neither the bulk (_skip_sync) path nor the default path writes a recs cache (Decision 215)."""
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-700"}),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.ops_data_portal._sync_table") as mock_sync,
            patch("scripts.ops_portal.cache._append_to_local_jsonl") as mock_append,
            patch("scripts.sync.ops.upsert_cache_row") as mock_upsert,
        ):
            from scripts.ops_data_portal import file_rec

            file_rec(dict(_VALID_FIELDS), _skip_sync=True)
            file_rec(dict(_VALID_FIELDS))
            mock_append.assert_not_called()
            mock_upsert.assert_not_called()
            mock_sync.assert_not_called()

    def test_file_rec_migration_int_id_evaluates_undated_rules_only(self, tmp_path: Path) -> None:
        """The caller-keyed _migration_int_id upsert evaluates undated rules only (never stricter than the writer): a
        historical row failing a dated content rule imports, an undated violation is still refused."""
        thin = {**_VALID_FIELDS, "context": "too short"}

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_dl_write,
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.ops_data_portal._sync_table"),
        ):
            from scripts.ops_data_portal import file_rec

            assert file_rec(dict(thin), _migration_int_id=701, _skip_sync=True) == "rec-701"
            with pytest.raises(ValueError, match="status"):
                file_rec({**thin, "status": "bogus"}, _migration_int_id=702, _skip_sync=True)
            mock_dl_write.assert_called_once()

    def test_file_decision_skip_sync_suppresses_sync_table(self, tmp_path: Path) -> None:
        """_skip_sync=True on file_decision flows to _refresh_cache_after_write(append_only=True)."""
        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", tmp_path / "dec.jsonl"),
            patch("scripts.ops_portal.decisions._refresh_cache_after_write") as mock_refresh,
        ):
            from scripts.ops_data_portal import file_decision

            file_decision({"title": "d", "status": "open", "decision_id": 77}, _skip_sync=True)
            mock_refresh.assert_called_once()
            assert mock_refresh.call_args.kwargs.get("append_only") is True
