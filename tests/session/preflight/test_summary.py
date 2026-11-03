"""Stdout summary: an unavailable recs signal prints UNAVAILABLE, never the false zero ci_rca=0."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.preflight.summary import _format_preflight_summary

pytestmark = pytest.mark.unit


def _report(**kw) -> dict:
    base = {
        "venv_ok": True,
        "creds_status": "ok",
        "branch": "b",
        "main_freshness": {"commits_behind": 0, "commits_ahead": 0},
        "recs_read_status": "ok",
        "open_recommendations": 12,
        "non_automatable_recommendations": 3,
        "ci_rca_unresolved_recs": [],
        "ci_rca_likely_resolved_recs": [],
        "ci_rca_open_total": 0,
    }
    return {**base, **kw}


class TestUnavailableSummary:
    def test_unavailable_report_prints_unavailable_never_zero(self):
        summary = _format_preflight_summary(
            _report(
                recs_read_status="reader_unreachable",
                open_recommendations=None,
                non_automatable_recommendations=None,
                ci_rca_unresolved_recs=None,
                ci_rca_likely_resolved_recs=None,
                ci_rca_open_total=None,
            ),
            Path("/tmp/r.json"),
        )
        assert "open_recs=UNAVAILABLE" in summary and "ci_rca=UNAVAILABLE" in summary
        assert "ci_rca=0" not in summary and "open_recs=None" not in summary
        assert "[DEGRADED: recs_read_status=reader_unreachable]" in summary

    def test_null_unresolved_list_alone_is_unavailable(self):
        summary = _format_preflight_summary(_report(ci_rca_unresolved_recs=None), Path("/tmp/r.json"))
        assert "ci_rca=UNAVAILABLE" in summary and "ci_rca=0" not in summary

    def test_available_report_prints_open_total_separately_from_correlated_counts(self):
        summary = _format_preflight_summary(
            _report(ci_rca_unresolved_recs=[{"id": "a"}], ci_rca_likely_resolved_recs=[{"id": "b"}], ci_rca_open_total=9),
            Path("/tmp/r.json"),
        )
        assert "ci_rca_unresolved=1 ci_rca_likely_resolved=1 ci_rca_open_total=9" in summary
        assert "open_recs=12" in summary and "UNAVAILABLE" not in summary

    def test_available_clear_report_still_prints_zero(self):
        assert "ci_rca=0 ci_rca_open_total=0" in _format_preflight_summary(_report(), Path("/tmp/r.json"))
