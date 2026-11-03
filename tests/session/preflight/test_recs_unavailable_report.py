"""main() with the recs read model unavailable: every recs-derived field is null, one UNAVAILABLE section."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

boto3 = pytest.importorskip("boto3")

from scripts.preflight.recs_signals_source import RecsSignalInputs  # noqa: E402
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402

pytestmark = pytest.mark.unit

_NULL_FIELDS = (
    "ci_rca_recs",
    "ci_rca_unresolved_recs",
    "ci_rca_likely_resolved_recs",
    "ci_rca_dispute_recs",
    "ci_rca_undetermined_recs",
    "ci_rca_undetermined_total",
    "ci_rca_open_total",
    "followon_recs",
    "open_critical_recs",
    "open_recommendations",
    "aging_recommendations",
    "non_automatable_recommendations",
    "non_automatable_softcap_breached",
    "ci_rca_liveness_alert",
)


def _run(tmp_path: Path, inputs: RecsSignalInputs, capsys: pytest.CaptureFixture) -> tuple[dict, str, MagicMock]:
    report_path = tmp_path / ".preflight-report.json"
    reader = MagicMock()
    reader.named.return_value = []
    with (
        patch("scripts.preflight.env_git.check_venv", return_value=True),
        patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
        patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
        patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
        patch(
            "scripts.preflight.session_history.read_session_history",
            return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
        ),
        patch("session_preflight.fetch_recs_signal_inputs", return_value=inputs),
        patch("scripts.preflight._common._make_reader", return_value=reader),
        patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
        patch("session_preflight.PREFLIGHT_REPORT", report_path),
    ):
        _preflight.main()
    return json.loads(report_path.read_text(encoding="utf-8")), capsys.readouterr().out, reader


class TestRecsUnavailableReport:
    def test_every_recs_derived_field_is_null_and_one_unavailable_section_prints(self, tmp_path, capsys):
        inputs = RecsSignalInputs("unavailable", error="ConnectionError: down", recs_read={"calls": 1, "bytes": {}})
        report, out, reader = _run(tmp_path, inputs, capsys)
        assert report["recs_read_status"] == "reader_unreachable"
        for key in _NULL_FIELDS:
            assert report[key] is None, key
        assert report["recs_read"] == {"calls": 1, "bytes": {}}
        assert out.count("CI-RCA signals UNAVAILABLE (reader unreachable) -- hard block at plan commitment") == 1
        assert "ci_rca=0" not in out and "open_recs=UNAVAILABLE" in out

    def test_context_docs_makes_no_reader_call_for_the_count(self, tmp_path, capsys):
        inputs = RecsSignalInputs("unavailable", error="x")
        report, _, reader = _run(tmp_path, inputs, capsys)
        assert report["context"]["recommendations_count"] is None
        assert all(call.args[:1] != ("open_recs",) for call in reader.named.call_args_list)

    def test_available_inputs_populate_counts_lists_and_recs_read(self, tmp_path, capsys):
        rows = [{"id": "rec-1", "source": "ci_rca", "status": "open", "priority": "High", "file": "a.py", "acceptance": "x"}]
        inputs = RecsSignalInputs(
            "ok",
            {"open_count": 7, "aging_count": 2, "non_automatable_count": 1},
            rows,
            None,
            {"calls": 2, "bytes": {"preflight_signal_rows": 99}},
        )
        report, out, _ = _run(tmp_path, inputs, capsys)
        assert report["recs_read_status"] == "ok"
        assert (report["open_recommendations"], report["aging_recommendations"]) == (7, 2)
        assert report["non_automatable_recommendations"] == 1
        assert report["ci_rca_open_total"] == 1 and isinstance(report["ci_rca_unresolved_recs"], list)
        assert report["recs_read"]["calls"] == 2
        assert "UNAVAILABLE" not in out
