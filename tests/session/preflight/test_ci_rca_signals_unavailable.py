"""Unavailable signal rows are None, never [] (Decision 55, rec-4232)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.preflight import _common, ci_rca_signals

pytestmark = pytest.mark.unit


class TestUnavailableIsNotEmpty:
    @pytest.mark.parametrize(
        "fn",
        [
            ci_rca_signals._fetch_ci_rca_recs,
            ci_rca_signals._fetch_ci_rca_dispute_recs,
            ci_rca_signals._fetch_ci_rca_undetermined_recs,
            lambda rows: ci_rca_signals._fetch_ci_rca_recs_since("2026-01-01T00:00:00+00:00", cache_rows=rows),
            lambda rows: ci_rca_signals._fetch_convergence_gap_tracking_recs_since(
                "2026-01-01T00:00:00+00:00", cache_rows=rows
            ),
        ],
    )
    def test_none_input_returns_none_not_empty_list(self, fn):
        assert fn(None) is None
        assert fn([]) == []

    def test_liveness_raises_no_alert_on_unavailable_rows(self):
        with patch("scripts.preflight.ci_rca_signals.subprocess.run") as run:
            assert ci_rca_signals._check_ci_rca_liveness("ok", None) is None
        run.assert_not_called()

    def test_liveness_still_alerts_on_available_rows_with_no_rec(self):
        import json as _json
        import subprocess

        stdout = _json.dumps([{"conclusion": "failure", "createdAt": "2020-01-01T00:00:00Z", "url": "u"}])
        done = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with patch("scripts.preflight.ci_rca_signals.subprocess.run", return_value=done):
            assert ci_rca_signals._check_ci_rca_liveness("ok", [])["run_url"] == "u"

    def test_convergence_gap_raises_no_alert_on_unavailable_rows(self):
        health = {"status": "red", "red_since": "2020-01-01T00:00:00+00:00", "red_age_hours": 9.0}
        assert ci_rca_signals._check_convergence_rca_gap(health, None) is None
        assert ci_rca_signals._check_convergence_rca_gap(health, [])["red_since"] == health["red_since"]

    def test_sentinel_default_is_not_unavailable(self):
        assert ci_rca_signals._fetch_ci_rca_dispute_recs() == []
        assert _common._READER_SENTINEL is not None

    def test_entries_carry_acceptance_and_total_is_uncapped(self):
        from scripts.preflight.recs_signals_source import ci_rca_open_total

        rows = [
            {"id": f"rec-{i}", "source": "ci_rca", "status": "open", "acceptance": f"cmd {i}", "created_timestamp": None}
            for i in range(7)
        ]
        recs = ci_rca_signals._fetch_ci_rca_recs(rows)
        assert len(recs) == 5 and all(r["acceptance"].startswith("cmd") for r in recs)
        assert ci_rca_open_total(rows) == 7
