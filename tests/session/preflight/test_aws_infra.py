"""aws_infra-surface tests: terraform-pending (convergence record), credentials, credentials
startup degraded-mode, credential-check-before-sync ordering, reader-URL priming (rec-2709 Wave 4).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

boto3 = pytest.importorskip("boto3")

from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402


class TestCheckTerraformPending:
    """check_terraform_pending() reads the sandbox convergence record (CD.35 Wave 6 / T2.35).

    The retired ``terraform -chdir=terraform plan`` invocation was replaced by a
    convergence-record read. The function now returns a tuple
    (pending: bool | None, convergence_health: dict | None).
    """

    from contextlib import contextmanager

    @staticmethod
    @contextmanager
    def _patched(verdict):
        """Patch the convergence-record read path so assess_health returns ``verdict``."""
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(patch("scripts.preflight._common.resolve_aws_profile", return_value="agent_platform"))
            stack.enter_context(patch("boto3.Session"))
            stack.enter_context(patch("scripts.convergence_health.read_convergence_record", return_value={}))
            stack.enter_context(patch("scripts.convergence_health.find_stuck_gated_approvals", return_value=[]))
            stack.enter_context(patch("scripts.convergence_health.assess_health", return_value=verdict))
            yield

    def test_returns_false_when_green(self) -> None:
        from scripts.convergence_health import HealthVerdict

        verdict = HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none")
        with self._patched(verdict):
            pending, health = _preflight.check_terraform_pending()
        assert pending is False
        assert health["status"] == "green"
        assert health["red_age_hours"] == 0.0

    def test_returns_true_when_red(self) -> None:
        from scripts.convergence_health import HealthVerdict

        verdict = HealthVerdict(status="red", red_age_hours=24.27, unapplied_backlog=0, severity="high")
        with self._patched(verdict):
            pending, health = _preflight.check_terraform_pending()
        assert pending is True
        assert health["status"] == "red"
        assert health["severity"] == "high"

    def test_returns_true_when_backlog_nonzero_even_if_green(self) -> None:
        from scripts.convergence_health import HealthVerdict

        verdict = HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=3, severity="low")
        with self._patched(verdict):
            pending, health = _preflight.check_terraform_pending()
        assert pending is True
        assert health["unapplied_backlog"] == 3

    def test_returns_none_when_status_unknown(self) -> None:
        from scripts.convergence_health import HealthVerdict

        verdict = HealthVerdict(status="unknown", red_age_hours=0.0, unapplied_backlog=0, severity="none")
        with self._patched(verdict):
            pending, health = _preflight.check_terraform_pending()
        assert pending is None
        assert health["status"] == "unknown"

    def test_stuck_approvals_count_surfaced(self) -> None:
        from scripts.convergence_health import HealthVerdict

        verdict = HealthVerdict(
            status="red",
            red_age_hours=2.0,
            unapplied_backlog=0,
            stuck_approvals=[{"run_id": 1}, {"run_id": 2}],
            severity="high",
        )
        with self._patched(verdict):
            _, health = _preflight.check_terraform_pending()
        assert health["stuck_approvals"] == 2

    def test_returns_none_none_on_exception(self) -> None:
        with patch("scripts.preflight._common.resolve_aws_profile", side_effect=RuntimeError("creds down")):
            result = _preflight.check_terraform_pending()
        assert result == (None, None)


class TestCheckCredentials:
    """Tests for check_credentials() -- the static-key credential gate."""

    def test_ok_when_returncode_zero(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with (
            patch("scripts.preflight._common.resolve_aws_profile", return_value="agent_platform"),
            patch("session_preflight.subprocess.run", return_value=mock_result) as mock_run,
        ):
            assert _preflight.check_credentials() == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["aws", "sts", "get-caller-identity"]
        assert "--profile" in cmd and "agent_platform" in cmd

    def test_unavailable_when_returncode_nonzero(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 255
        with (
            patch("scripts.preflight._common.resolve_aws_profile", return_value="agent_platform"),
            patch("session_preflight.subprocess.run", return_value=mock_result),
        ):
            assert _preflight.check_credentials() == "unavailable"

    def test_unavailable_when_cli_missing(self) -> None:
        with (
            patch("scripts.preflight._common.resolve_aws_profile", return_value="agent_platform"),
            patch("session_preflight.subprocess.run", side_effect=FileNotFoundError),
        ):
            assert _preflight.check_credentials() == "unavailable"

    def test_omits_profile_for_default_chain(self) -> None:
        """resolve_aws_profile() -> None (Lambda/CI) means no --profile is passed."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        with (
            patch("scripts.preflight._common.resolve_aws_profile", return_value=None),
            patch("session_preflight.subprocess.run", return_value=mock_result) as mock_run,
        ):
            assert _preflight.check_credentials() == "ok"
        assert "--profile" not in mock_run.call_args[0][0]


class TestHandleCredentialsStartup:
    """Tests for _handle_credentials_startup() -- non-fatal degraded mode (Decision 60)."""

    def test_ok_returns_status_without_warning(self, capsys: pytest.CaptureFixture) -> None:
        with patch("session_preflight.subprocess.run") as mock_run:
            assert _preflight._handle_credentials_startup("ok") == "ok"
        mock_run.assert_not_called()
        assert capsys.readouterr().err == ""

    def test_unavailable_is_non_fatal_and_never_logs_in(self, capsys: pytest.CaptureFixture) -> None:
        """No SystemExit; no login subprocess invoked; returns the status unchanged."""
        with patch("session_preflight.subprocess.run") as mock_run:
            result = _preflight._handle_credentials_startup("unavailable")
        assert result == "unavailable"
        mock_run.assert_not_called()
        assert "DEGRADED" in capsys.readouterr().err

    def test_guidance_names_helper_and_network_secret(self, capsys: pytest.CaptureFixture) -> None:
        _preflight._handle_credentials_startup("unavailable")
        err = capsys.readouterr().err
        assert "scripts.aws_identity caller-arn" in err
        assert "network secret" in err
        assert "~/.aws/credentials" not in err


class TestCredentialsOrderingInMain:
    """Verify that the credential check runs before ops sync in main()."""

    def test_credentials_startup_precedes_sync(self, tmp_path: Path) -> None:
        """_handle_credentials_startup is called before scripts.sync.ops.warm_sync in main()."""
        call_order: list[str] = []

        def _track_creds(status: str) -> str:
            call_order.append("creds")
            return "ok"

        def _track_sync(profile: str = "agent_platform") -> dict:
            call_order.append("sync")
            return {
                "pulled": {},
                "rows": {"ops_recommendations": [], "ops_decisions": [], "ops_priority_queue": []},
                "reader_ok": {"ops_recommendations": True, "ops_decisions": True, "ops_priority_queue": True},
            }

        preflight_report = tmp_path / ".preflight-report.json"

        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.run_log_sync", return_value={}),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch("scripts.preflight.aws_infra._handle_credentials_startup", side_effect=_track_creds),
            patch("scripts.sync.ops.warm_sync", side_effect=_track_sync),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
            patch("scripts.preflight.priority_queue.print_priority_queue"),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch(
                "scripts.preflight.context_docs.check_data_quality_coverage",
                return_value={"tables_covered": 0, "checks_defined": 0, "last_run": None},
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
        ):
            _preflight.main()

        assert "creds" in call_order
        assert "sync" in call_order
        assert call_order.index("creds") < call_order.index("sync"), (
            f"credential check must precede sync; got order: {call_order}"
        )


class TestUrlPriming:
    """_prime_reader_url() resolves the DuckLake reader Function URL once and sets DUCKLAKE_READER_URL."""

    def test_sets_env_var_when_creds_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On creds ok, DUCKLAKE_READER_URL is set from the resolved URL."""
        monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
        fake_url = "https://abc123.lambda-url.eu-west-2.on.aws"
        mock_reader = MagicMock()
        mock_reader._reader_url.return_value = fake_url
        with patch("scripts.preflight._common._make_reader", return_value=mock_reader):
            _preflight._prime_reader_url("ok")
        assert os.environ.get("DUCKLAKE_READER_URL") == fake_url

    def test_skips_when_creds_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Credentials unavailable -> reader is never called and env var is not set."""
        monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
        with patch("scripts.preflight._common._make_reader") as mock_make:
            _preflight._prime_reader_url("unavailable")
        mock_make.assert_not_called()
        assert "DUCKLAKE_READER_URL" not in os.environ

    def test_skips_if_already_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If DUCKLAKE_READER_URL is already set, the existing value is preserved."""
        monkeypatch.setenv("DUCKLAKE_READER_URL", "https://original.url")
        with patch("scripts.preflight._common._make_reader") as mock_make:
            _preflight._prime_reader_url("ok")
        mock_make.assert_not_called()
        assert os.environ["DUCKLAKE_READER_URL"] == "https://original.url"

    def test_priming_failure_is_nonfatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If URL resolution raises, _prime_reader_url does not propagate; env var is not set."""
        monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
        mock_reader = MagicMock()
        mock_reader._reader_url.side_effect = RuntimeError("SSM unavailable")
        with patch("scripts.preflight._common._make_reader", return_value=mock_reader):
            _preflight._prime_reader_url("ok")  # must not raise
        assert "DUCKLAKE_READER_URL" not in os.environ

    def test_non_string_url_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If _reader_url() returns a non-string (e.g. MagicMock), the env var is not polluted."""
        monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
        mock_reader = MagicMock()
        mock_reader._reader_url.return_value = MagicMock()  # not a string
        with patch("scripts.preflight._common._make_reader", return_value=mock_reader):
            _preflight._prime_reader_url("ok")
        assert "DUCKLAKE_READER_URL" not in os.environ
