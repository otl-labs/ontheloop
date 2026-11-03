"""Bounded, fail-closed recs inputs for preflight (rec-4232, Decision 215)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import recs_signals_source as src
from src.common.ducklake_reader_client import ReaderInvokeError, ResponseTooLargeError

pytestmark = pytest.mark.unit

_TALLY = [{"open_count": 4, "aging_count": 1, "non_automatable_count": 2}]


def _reader(tally=None, rows=None):
    reader = MagicMock()
    reader.named.side_effect = lambda verb, **kw: {"open_recs_tally": tally or _TALLY, "preflight_signal_rows": rows or []}[
        verb
    ]
    return reader


class TestFailClosed:
    def test_ok_path_makes_exactly_two_calls_and_records_bytes(self):
        rows = [{"id": "rec-1", "source": "ci_rca", "status": "open"}]
        reader = _reader(rows=rows)
        out = src.fetch_recs_signal_inputs(reader, followon_ids=["rec-9"])
        assert out.status == "ok" and out.error is None
        assert out.tally == _TALLY[0] and out.signal_rows == rows
        assert [c.args[0] for c in reader.named.call_args_list] == ["open_recs_tally", "preflight_signal_rows"]
        assert out.recs_read["calls"] == 2
        assert out.recs_read["bytes"]["preflight_signal_rows"] == len(json.dumps(rows).encode("utf-8"))
        assert set(out.recs_read["bytes"]) == {"open_recs_tally", "preflight_signal_rows"}

    def test_217_followon_ids_ride_one_call(self):
        ids = [f"rec-{i}" for i in range(217)]
        reader = _reader()
        src.fetch_recs_signal_inputs(reader, followon_ids=ids)
        calls = [c for c in reader.named.call_args_list if c.args[0] == "preflight_signal_rows"]
        assert len(calls) == 1 and len(reader.named.call_args_list) == 2
        assert set(calls[0].kwargs["followon_ids"].split(",")) == set(ids)

    def test_no_followon_ids_binds_empty_string(self):
        reader = _reader()
        src.fetch_recs_signal_inputs(reader)
        assert reader.named.call_args_list[1].kwargs == {"followon_ids": ""}

    @pytest.mark.parametrize(
        "exc",
        [
            ReaderInvokeError("named_read", 502, "bad gateway", None),
            ResponseTooLargeError("named_read", "{}", {"bytes": 7, "cap": 6}),
            ConnectionError("down"),
            RuntimeError("DUCKLAKE_READER_URL not set"),
        ],
    )
    def test_any_failure_is_unavailable_with_none_rows(self, exc):
        reader = MagicMock()
        reader.named.side_effect = exc
        out = src.fetch_recs_signal_inputs(reader)
        assert out.status == "unavailable" and out.tally is None and out.signal_rows is None
        assert out.error and type(exc).__name__ in out.error

    def test_second_call_failure_discards_the_tally_too(self):
        reader = MagicMock()
        reader.named.side_effect = [_TALLY, ConnectionError("down")]
        out = src.fetch_recs_signal_inputs(reader)
        assert (out.status, out.tally, out.signal_rows) == ("unavailable", None, None)

    def test_malformed_tally_is_unavailable(self):
        out = src.fetch_recs_signal_inputs(_reader(tally=_TALLY * 2))
        assert out.status == "unavailable" and out.signal_rows is None

    def test_url_resolution_failure_when_no_reader_given(self):
        with patch("scripts.preflight._common._make_reader", side_effect=RuntimeError("no url")):
            out = src.fetch_recs_signal_inputs()
        assert out.status == "unavailable" and out.signal_rows is None and out.recs_read["calls"] == 0


class TestCiRcaOpenTotal:
    def test_uncapped_and_scoped_to_ci_rca_open_states(self):
        rows = [{"source": "ci_rca", "status": "open"}] * 7 + [
            {"source": "ci_rca", "status": "in_progress"},
            {"source": "ci_rca", "status": "closed"},
            {"source": "tf_drift", "status": "open"},
        ]
        assert src.ci_rca_open_total(rows) == 8

    def test_none_stays_none_and_empty_is_zero(self):
        assert src.ci_rca_open_total(None) is None
        assert src.ci_rca_open_total([]) == 0
