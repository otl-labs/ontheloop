"""Tests for file_decision / update_decision and the decision reader fetch in ops_data_portal.

Decision 84: decision numbering authority is DECISIONS.md (caller supplies decision_id);
writes transit _ducklake_write (write_ops / update_ops); the offline decisions outbox and
the DynamoDB allocator are retired.

Decision 124 namespace migration: file_decision/update_decision/backfill_decisions_from_md
and _fetch_decision_from_reader moved to scripts/ops_portal/decisions.py, which imports
_ducklake_write, DECISIONS_JSONL, _sync_table, and gate_write into its OWN
module namespace (a plain `from ... import` creates a separate binding from the facade's
re-exported copy). Patches therefore target scripts.ops_portal.decisions.<sym> -- the
namespace decisions.py's callers actually resolve at call time -- not the facade.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_VALID_FIELDS = {
    "title": "Test Decision",
    "status": "Decided",
    "problem": "A test problem",
    "decision_text": "We decided to test this.",
    "context": "Context for this test decision",
}


class TestFileDecision:
    """Tests for file_decision() -- caller-keyed write_ops upsert."""

    def test_decision_id_field_returns_dec_id(self, tmp_path: Path) -> None:
        """file_decision() forms dec-NNN from fields['decision_id'] and writes via write_ops."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            result = file_decision({**_VALID_FIELDS, "decision_id": 73})

        assert result == "dec-073"
        mock_write.assert_called_once()
        table, record = mock_write.call_args[0]
        assert table == "ops_decisions"
        assert mock_write.call_args.kwargs["action"] == "write_ops"
        assert record["id"] == "dec-073"
        assert record["decision_id"] == 73

    def test_dual_write_in_record(self, tmp_path: Path) -> None:
        """file_decision() sets both id and decision_id on the written record."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            result = file_decision({**_VALID_FIELDS, "decision_id": 10})

        assert result == "dec-010"
        lines = decisions_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["id"] == "dec-010"
        assert entry["decision_id"] == 10

    def test_migration_int_id_supplies_number(self, tmp_path: Path) -> None:
        """_migration_int_id supplies the integer on the backfill path and preserves it."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            result = file_decision(dict(_VALID_FIELDS), _migration_int_id=42)

        assert result == "dec-042"
        lines = decisions_jsonl.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[0])
        assert entry["id"] == "dec-042"
        assert entry["decision_id"] == 42

    def test_missing_decision_id_raises(self) -> None:
        """Without decision_id or _migration_int_id, file_decision raises ValueError (Decision 84 I-2)."""
        with patch("scripts.ops_portal.decisions._ducklake_write") as mock_write:
            from scripts.ops_data_portal import file_decision

            with pytest.raises(ValueError, match="DECISIONS.md-assigned integer"):
                file_decision(dict(_VALID_FIELDS))

        mock_write.assert_not_called()

    def test_writer_failure_raises_loudly_no_outbox(self, tmp_path: Path) -> None:
        """A writer failure propagates -- there is no decisions outbox and no 'pending-' return."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"

        with (
            patch(
                "scripts.ops_portal.decisions._ducklake_write",
                side_effect=RuntimeError("ducklake_writer write_ops ops_decisions failed (HTTP 500)"),
            ),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
        ):
            from scripts.ops_data_portal import file_decision

            with pytest.raises(RuntimeError, match="ducklake_writer"):
                file_decision({**_VALID_FIELDS, "decision_id": 99})

        assert not decisions_jsonl.exists()  # nothing written through on failure


class TestUpdateDecision:
    """Tests for update_decision() -- reader fetch + update_ops write."""

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        """update_decision returns True when the write sequence succeeds."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        existing = {
            "id": "dec-001",
            "decision_id": 1,
            "title": "Existing Decision",
            "status": "Decided",
            "created_timestamp": "2026-05-13T12:00:00Z",
            "last_updated_timestamp": "2026-05-13T12:00:00Z",
        }

        with (
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=existing),
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
        ):
            from scripts.ops_data_portal import update_decision

            result = update_decision("dec-001", {"status": "Superseded"})

        assert result is True
        assert mock_write.call_args.kwargs["action"] == "update_ops"

    def test_str_arg_accepted(self, tmp_path: Path) -> None:
        """update_decision accepts a str decision_id (not int)."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        existing = {
            "id": "dec-072",
            "decision_id": 72,
            "title": "Test",
            "status": "Decided",
            "created_timestamp": "2026-05-13T12:00:00Z",
            "last_updated_timestamp": "2026-05-13T12:00:00Z",
        }

        with (
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=existing),
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
        ):
            from scripts.ops_data_portal import update_decision

            result = update_decision("dec-072", {"context": "updated context"})

        assert result is True


class TestRetiredDecisionsOutbox:
    """Decision 84 I-4: drain_pending_decisions and the decisions outbox are gone."""

    def test_drain_pending_decisions_absent(self) -> None:
        import scripts.ops_data_portal as portal

        assert not hasattr(portal, "drain_pending_decisions")
        assert not hasattr(portal, "_DECISIONS_PENDING_OUTBOX")


class TestFetchDecisionFromReader:
    """Tests for the decision_by_id named verb fetch (Decision 84 I-3)."""

    _DEC_ROW = {
        "id": "dec-007",
        "decision_id": 7,
        "title": "Test decision",
        "status": "Decided",
        "context": "ctx",
        "created_timestamp": "2026-05-01T00:00:00Z",
        "last_updated_timestamp": "2026-05-01T00:00:00Z",
    }

    def test_reader_path_returns_decision(self) -> None:
        """named('decision_by_id') success -> returns sanitised decision record."""
        reader = MagicMock()
        reader.named.return_value = [dict(self._DEC_ROW)]

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_decision_from_reader

            result = _fetch_decision_from_reader("dec-007")

        assert result is not None
        assert result["id"] == "dec-007"
        assert result["status"] == "Decided"
        reader.named.assert_called_once_with("decision_by_id", id="dec-007")

    def test_reader_failure_fails_loud(self) -> None:
        """Reader failure propagates -- there is no fallback read path (Decision 84 I-1)."""
        reader = MagicMock()
        reader.named.side_effect = RuntimeError("ducklake_reader 'named_read' failed (HTTP 500)")

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_decision_from_reader

            with pytest.raises(RuntimeError, match="ducklake_reader"):
                _fetch_decision_from_reader("dec-007")

    def test_reader_returns_none_when_decision_not_found(self) -> None:
        """Reader returns empty list -> function returns None."""
        reader = MagicMock()
        reader.named.return_value = []

        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            from scripts.ops_data_portal import _fetch_decision_from_reader

            result = _fetch_decision_from_reader("dec-999")

        assert result is None

    def test_invalid_decision_id_raises_value_error(self) -> None:
        """Malformed decision_id raises ValueError before any reader call."""
        from scripts.ops_data_portal import _fetch_decision_from_reader

        with pytest.raises(ValueError, match="invalid decision_id"):
            _fetch_decision_from_reader("not-a-decision")


class TestFidelityHelpers:
    """Direct unit coverage for the small helpers backfill_decisions_from_md composes."""

    def test_load_fidelity_baseline_returns_empty_set_when_file_missing(self, tmp_path: Path) -> None:
        from scripts.ops_portal.decisions import _load_fidelity_baseline

        missing = tmp_path / "missing-baseline.yaml"
        with patch("scripts.ops_portal.decisions._FIDELITY_BASELINE_PATH", missing):
            assert _load_fidelity_baseline() == set()

    def test_live_decision_ids_returns_empty_set_when_live_file_missing(self, tmp_path: Path) -> None:
        from scripts.ops_portal.decisions import _live_decision_ids

        missing_paths = [tmp_path / "DECISIONS.md"]
        with patch("scripts.decisions_md._DECISIONS_MD_PATHS", missing_paths):
            assert _live_decision_ids() == set()

    def test_live_decision_ids_resolves_live_path_by_name_not_position(self, tmp_path: Path) -> None:
        """_live_decision_ids selects DECISIONS.md by basename, not _DECISIONS_MD_PATHS[0]."""
        from scripts.ops_portal.decisions import _live_decision_ids

        live = tmp_path / "DECISIONS.md"
        live.write_text("## Decision 7: Something\n\nBody.\n", encoding="utf-8")
        # Live path is second in the list -- proves positional indexing is not being used.
        reordered_paths = [tmp_path / "DECISIONS_ARCHIVE.md", live]
        with patch("scripts.decisions_md._DECISIONS_MD_PATHS", reordered_paths):
            assert _live_decision_ids() == {7}

    def test_fidelity_issue_flags_non_iso_decided_date(self) -> None:
        from scripts.ops_portal.decisions import _fidelity_issue

        entry = {"decision_text": "a real body", "decided_date": "not a date"}
        assert _fidelity_issue(entry) == "non_iso_decided_date"

    def test_fidelity_issue_clean_entry_returns_none(self) -> None:
        from scripts.ops_portal.decisions import _fidelity_issue

        entry = {"decision_text": "a real body", "decided_date": "2026-01-01"}
        assert _fidelity_issue(entry) is None


class TestBackfillFidelityTripwire:
    """PLAN-daf-etl-parity-fidelity / Decision 134 cl.4 (DAF-01): backfill_decisions_from_md's
    per-run fidelity tripwire. Allowlist-diff against config/agent/data_quality/decisions/
    fidelity_baseline.yaml -- fails loud on a NEW live-h2 regression, never on a baselined one.
    """

    _CLEAN_ENTRY = {
        "decision_id": 1,
        "title": "A clean entry",
        "status": "Decided",
        "decision_text": "This entry has a real decision body.",
        "decided_date": "2026-01-01",
        "content_hash": "c" * 64,
        "raw_block": "## Decision 1: A clean entry (Decided)\n\n**Decision:** body.",
    }

    def test_fidelity_passes_against_the_real_checked_in_baseline(self, tmp_path: Path) -> None:
        """The real corpus + the real baseline produces zero unbaselined regressions."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result["written"] > 0
        assert result["failed"] == 0

    def test_fidelity_new_unbaselined_regression_raises_loud(self, tmp_path: Path) -> None:
        """A synthetic NEW live entry with an empty decision_text, absent from the baseline,
        must fail the backfill loudly rather than silently counting as 'written'."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        regressed_entry = {
            **self._CLEAN_ENTRY,
            "decision_id": 99999,
            "decision_text": "",  # the fidelity issue: empty on a live entry
        }

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value={99999}),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[regressed_entry]),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            with pytest.raises(RuntimeError, match="fidelity tripwire"):
                backfill_decisions_from_md()

    def test_fidelity_baselined_entry_does_not_trip_the_tripwire(self, tmp_path: Path) -> None:
        """dec-63 (a real baseline entry, pre-77-band terse style) has an empty decision_text
        by construction -- it must not trip the tripwire even though it is 'live'."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        baselined_entry = {
            **self._CLEAN_ENTRY,
            "decision_id": 63,
            "decision_text": "",
        }

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value={63}),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[baselined_entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result["written"] == 1
        assert result["failed"] == 0

    def test_fidelity_entry_with_no_valid_decision_id_is_skipped(self, tmp_path: Path) -> None:
        """A parsed entry whose decision_id is absent or non-numeric counts as skipped, not
        failed -- covers both the ValueError-on-int() branch and the n<=0 branch."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        unkeyed_entries = [
            {**self._CLEAN_ENTRY, "decision_id": "not-a-number"},
            {**self._CLEAN_ENTRY, "decision_id": 0},
        ]

        with (
            patch("scripts.ops_portal.decisions._ducklake_write") as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=unkeyed_entries),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 0, "failed": 0, "skipped": 2}
        mock_write.assert_not_called()

    def test_backfill_counts_writer_failure_as_failed_not_written(self, tmp_path: Path) -> None:
        """A per-row writer exception is isolated -- counted as failed, does not abort the run."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        entry = {**self._CLEAN_ENTRY, "content_hash": "d" * 64}

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", side_effect=RuntimeError("writer down")),
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 0, "failed": 1, "skipped": 0}


class TestBackfillContentHashSkipGate:
    """PLAN-daf-etl-parity-fidelity / Decision 134 cl.4 (DAF-01): the client-side content_hash
    skip gate. Reads the current row's content_hash via _fetch_decision_from_reader to decide
    whether a write is needed -- the write source stays DECISIONS.md (never a read cache)."""

    _ENTRY = {
        "decision_id": 1,
        "title": "An entry",
        "status": "Decided",
        "decision_text": "Some decision body.",
        "decided_date": "2026-01-01",
        "raw_block": "## Decision 1: An entry (Decided)\n\n**Decision:** body.",
    }

    def test_content_hash_skip_gate_skips_when_hash_unchanged(self, tmp_path: Path) -> None:
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        entry = {**self._ENTRY, "content_hash": "same_hash" * 8}
        existing_row = {"content_hash": "same_hash" * 8}

        with (
            patch("scripts.ops_portal.decisions._ducklake_write") as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=existing_row),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 0, "failed": 0, "skipped": 1}
        mock_write.assert_not_called()

    def test_content_hash_skip_gate_writes_when_hash_differs(self, tmp_path: Path) -> None:
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        entry = {**self._ENTRY, "content_hash": "new_hash_" * 7 + "abcdefg"}
        existing_row = {"content_hash": "old_hash_" * 7 + "abcdefg"}

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=existing_row),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 1, "failed": 0, "skipped": 0}
        mock_write.assert_called_once()

    def test_content_hash_skip_gate_writes_when_no_existing_row(self, tmp_path: Path) -> None:
        """A brand-new entry (not yet in the warehouse) always writes -- nothing to skip against."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        entry = {**self._ENTRY, "content_hash": "brand_new" * 7 + "abcdefg"}

        with (
            patch("scripts.ops_portal.decisions._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 1, "failed": 0, "skipped": 0}
        mock_write.assert_called_once()


class TestIntentBackfillColumn:
    """Decision 151 / PLAN-dcg-intent-capture (audit finding DCG-06): intent threads through
    the backfill ETL's field-selection filter, warehouse-free (no writer/reader invoked) --
    proves the ETL plumbing without invoking the live writer."""

    def test_intent_is_in_backfill_cols(self) -> None:
        from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS

        assert "intent" in _DECISION_BACKFILL_COLS

    def test_parsed_entry_with_intent_survives_the_field_selection_filter(self) -> None:
        """Mirrors the exact filter expression backfill_decisions_from_md applies before
        calling file_decision -- a non-empty intent must survive it."""
        from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS

        entry = {"title": "T", "status": "Decided", "intent": "Make the durable why quotable."}
        fields = {k: v for k, v in entry.items() if k in _DECISION_BACKFILL_COLS and v not in (None, "")}
        assert fields.get("intent") == "Make the durable why quotable."

    def test_intent_lands_in_the_fields_dict_passed_to_file_decision(self, tmp_path: Path) -> None:
        """End-to-end via the REAL backfill_decisions_from_md filter (not reconstructed inline)
        -- mocks file_decision itself, mirroring TestBackfillContentHashSkipGate's mock_write
        pattern, catching a future filter/_DECISION_BACKFILL_COLS divergence too."""
        decisions_jsonl = tmp_path / ".decisions-index.jsonl"
        entry = {
            "decision_id": 1,
            "title": "An entry",
            "status": "Decided",
            "intent": "Make the durable why quotable.",
        }

        with (
            patch("scripts.ops_portal.decisions.file_decision", return_value="dec-001") as mock_file_decision,
            patch("scripts.ops_portal.decisions.DECISIONS_JSONL", decisions_jsonl),
            patch("scripts.ops_portal.decisions._sync_table"),
            patch("scripts.ops_portal.decisions.gate_write"),
            patch("scripts.ops_portal.decisions._fetch_decision_from_reader", return_value=None),
            patch("scripts.ops_portal.decisions._live_decision_ids", return_value=set()),
            patch("scripts.decisions_md.parse_decisions_md", return_value=[entry]),
            patch("scripts.ops_portal.decisions._assert_no_orphaned_current_rows"),
        ):
            from scripts.ops_data_portal import backfill_decisions_from_md

            result = backfill_decisions_from_md()

        assert result == {"written": 1, "failed": 0, "skipped": 0}
        mock_file_decision.assert_called_once()
        fields_arg = mock_file_decision.call_args[0][0]
        assert fields_arg.get("intent") == "Make the durable why quotable."

    def test_markerless_entry_intent_empty_string_is_filtered_out(self) -> None:
        """A markerless entry parses intent="" -- the filter drops it so the column stays NULL."""
        from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS

        entry = {"title": "T", "status": "Decided", "intent": ""}
        fields = {k: v for k, v in entry.items() if k in _DECISION_BACKFILL_COLS and v not in (None, "")}
        assert "intent" not in fields


@pytest.mark.integration
@pytest.mark.aws
class TestLiveReaderParity:
    """Post-deploy anchor spot-check via the ducklake_reader boundary (VP step 11 of
    PLAN-daf-etl-parity-fidelity). Marked integration (real AWS network + credentials
    required) per the tests/common/ducklake_reader_client/ integration-test precedent. Also
    marked aws (rec-2484 blast-radius fix): the reader's internal assume-role credential
    refresh constructs a real STS client, which the tests/conftest.py L2 create_client
    tripwire blocks unless this class opts out -- @pytest.mark.integration alone bypasses
    only L1 (profile hermeticity), not L2.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_not_ready(self, _allow_network_for_integration: None) -> None:
        """Skip when creds are unavailable, or the post-deploy schema migration (VP steps
        8-10: deploy, reconcile_columns, backfill) has not landed against this warehouse yet.

        Requests _allow_network_for_integration so the probe's own reader call runs only
        after sockets are restored by that fixture.
        """
        from scripts.preflight.aws_infra import check_credentials

        if check_credentials() != "ok":
            pytest.skip("AWS credentials unavailable -- live reader parity needs the real ducklake_reader")

        from scripts.ops_portal.decisions import _fetch_decision_from_reader

        probe = _fetch_decision_from_reader("dec-084")
        if probe is None or "raw_block" not in probe:
            pytest.skip(
                "ops_decisions has no raw_block column yet -- the post-deploy reconcile_columns "
                "+ backfill sequence (PLAN-daf-etl-parity-fidelity VP steps 8-10) has not run "
                "against this environment's warehouse"
            )

    def test_live_reader_parity_anchor_decisions_are_recoverable(self) -> None:
        from scripts.ops_portal.decisions import _fetch_decision_from_reader

        anchors: tuple[tuple[str, str], ...] = (
            ("dec-084", "decision_text"),
            ("dec-114", "reversal_conditions"),
        )
        for dec_id, field in anchors:
            record = _fetch_decision_from_reader(dec_id)
            assert record is not None, f"{dec_id} not found in ops_decisions"
            assert record.get("raw_block"), f"{dec_id} raw_block empty in-table"
            assert record.get(field), f"{dec_id}.{field} empty in-table"

        dec_067 = _fetch_decision_from_reader("dec-067")
        assert dec_067 is not None, "dec-067 not found in ops_decisions"
        decided_date = dec_067.get("decided_date") or ""
        assert decided_date == "" or decided_date[:4].isdigit()

    def test_intent_live_reader_parity(self) -> None:
        """Decision 151 / PLAN-dcg-intent-capture post-deploy anchor spot-check: the governing
        Decision's own intent round-trips through the warehouse via the decision_by_id read
        verb. Writer-health-gated (rec-2821): the class-level fixture already skips when creds
        are unavailable or the raw_block schema migration has not landed; this method
        additionally skips if intent specifically is not yet a live column or dec-151 has not
        been backfilled (an older deploy that predates PLAN-dcg-intent-capture)."""
        from scripts.ops_portal.decisions import _fetch_decision_from_reader

        record = _fetch_decision_from_reader("dec-151")
        if record is None or "intent" not in record:
            pytest.skip("ops_decisions has no intent column yet, or dec-151 is not yet backfilled")
        assert record.get("intent"), "dec-151.intent empty in-table"
