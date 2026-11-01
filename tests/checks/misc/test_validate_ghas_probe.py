"""Tests for validate_ghas_probe() (CHECK, skip-when-unscoped) and _run_cli() (RUNNER, loud-fail)."""

import json
import urllib.error
from unittest.mock import patch

import pytest

from scripts.checks.misc.validate_ghas_probe import (
    ProbeAuthError,
    ProbeTransportError,
    _actions_scope_label,
    _alerts_reachability,
    _disabled_controls,
    _get,
    _probe,
    _repo,
    _status_label,
    validate_ghas_probe,
)
from scripts.checks.misc.validate_ghas_probe import _run_cli as _ghas_run_cli


class TestValidateGhasProbe:
    """Tests for validate_ghas_probe() (CHECK, skip-when-unscoped) and _run_cli() (RUNNER, loud-fail)."""

    class _FakeResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "TestValidateGhasProbe._FakeResponse":
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

    @staticmethod
    def _repo_body(secret_scanning: str = "enabled", push_protection: str = "enabled") -> bytes:
        return json.dumps(
            {
                "security_and_analysis": {
                    "secret_scanning": {"status": secret_scanning},
                    "secret_scanning_push_protection": {"status": push_protection},
                }
            }
        ).encode("utf-8")

    @staticmethod
    def _actions_body(enabled: bool = True, allowed_actions: str = "all") -> bytes:
        return json.dumps({"enabled": enabled, "allowed_actions": allowed_actions}).encode("utf-8")

    def _make_urlopen(self, secret_scanning: str = "enabled", push_protection: str = "enabled", actions_enabled: bool = True):
        def _urlopen(request: object, timeout: float = 15) -> "TestValidateGhasProbe._FakeResponse":
            url = request.full_url  # type: ignore[attr-defined]
            if url.endswith("/actions/permissions"):
                return self._FakeResponse(200, self._actions_body(enabled=actions_enabled))
            if url.endswith("/secret-scanning/alerts"):
                if secret_scanning != "enabled":  # pragma: allowlist secret -- control-state enum, not a secret
                    # Real GitHub behavior: this endpoint 404s when secret scanning is disabled
                    # for the repo -- exactly the case this probe exists to catch.
                    raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
                return self._FakeResponse(200, b"[]")
            return self._FakeResponse(200, self._repo_body(secret_scanning, push_protection))

        return _urlopen

    # -- CHECK: validate_ghas_probe(failed) --

    def test_check_passes_all_controls_enabled(self) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._make_urlopen()),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed == []

    def test_check_fails_on_disabled_control(self) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch(
                "scripts.checks.misc.validate_ghas_probe.urlopen",
                side_effect=self._make_urlopen(secret_scanning="disabled"),
            ),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed != []
        assert "disabled" in failed[0]

    def test_check_reports_disabled_control_despite_alerts_endpoint_404(self) -> None:
        """Regression: a 404 from the alerts endpoint (GitHub's real behavior when secret
        scanning is disabled) must not be swallowed as a generic transport error that discards
        the already-fetched disabled-control state and reads as a clean SKIP."""
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch(
                "scripts.checks.misc.validate_ghas_probe.urlopen",
                side_effect=self._make_urlopen(secret_scanning="disabled"),
            ),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed != []
        assert "scanning_status=disabled" in failed[0]

    def test_check_skips_when_token_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHAS_PROBE_TOKEN", raising=False)
        with patch("scripts.checks.misc.validate_ghas_probe.urlopen") as mock_urlopen:
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed == []
        mock_urlopen.assert_not_called()

    def test_check_skips_on_auth_error(self) -> None:
        def _raise_401(request: object, timeout: float = 15) -> None:
            raise urllib.error.HTTPError(url="x", code=401, msg="Unauthorized", hdrs=None, fp=None)  # type: ignore[arg-type]

        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_raise_401),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed == []

    def test_check_skips_on_transport_error(self) -> None:
        def _raise_url_error(request: object, timeout: float = 15) -> None:
            raise urllib.error.URLError("network unreachable")

        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_raise_url_error),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert failed == []

    def test_check_never_prints_token(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "super-secret-token"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._make_urlopen()),
        ):
            failed: list[str] = []
            validate_ghas_probe(failed)
        assert "super-secret-token" not in capsys.readouterr().out

    # -- RUNNER: _run_cli() --

    def test_runner_returns_zero_when_all_enabled(self) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._make_urlopen()),
        ):
            assert _ghas_run_cli() == 0

    def test_runner_nonzero_on_disabled_control(self) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch(
                "scripts.checks.misc.validate_ghas_probe.urlopen",
                side_effect=self._make_urlopen(actions_enabled=False),
            ),
        ):
            assert _ghas_run_cli() != 0

    def test_runner_nonzero_on_disabled_secret_scanning_despite_alerts_404(self) -> None:
        """Regression: same alerts-endpoint-404 scenario as the CHECK, exercised via the
        loud-fail runner."""
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch(
                "scripts.checks.misc.validate_ghas_probe.urlopen",
                side_effect=self._make_urlopen(secret_scanning="disabled"),
            ),
        ):
            assert _ghas_run_cli() != 0

    def test_runner_nonzero_when_token_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHAS_PROBE_TOKEN", raising=False)
        with patch("scripts.checks.misc.validate_ghas_probe.urlopen") as mock_urlopen:
            assert _ghas_run_cli() != 0
        mock_urlopen.assert_not_called()

    def test_runner_nonzero_on_auth_error(self) -> None:
        def _raise_403(request: object, timeout: float = 15) -> None:
            raise urllib.error.HTTPError(url="x", code=403, msg="Forbidden", hdrs=None, fp=None)  # type: ignore[arg-type]

        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_raise_403),
        ):
            assert _ghas_run_cli() != 0

    def test_runner_nonzero_on_transport_error(self) -> None:
        def _raise_url_error(request: object, timeout: float = 15) -> None:
            raise urllib.error.URLError("network unreachable")

        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_raise_url_error),
        ):
            assert _ghas_run_cli() != 0

    def test_runner_never_prints_token(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "super-secret-token"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._make_urlopen()),
        ):
            _ghas_run_cli()
        assert "super-secret-token" not in capsys.readouterr().out


class TestDefaultRepoSlug:
    """The local default must name the renamed repository (PLAN-repo-rename-relicense PR-3).

    The sweep's own guard proves only that the OLD slug is absent; absence is not the same
    claim as correctness, so this asserts the positive value directly.
    """

    def test_default_repo_is_the_renamed_slug_when_env_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert _repo() == "benjamin-blake/theseus"

    def test_ci_supplied_repository_still_wins_over_the_default(self) -> None:
        """CI sets GITHUB_REPOSITORY; the default is only the local fallback."""
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "someone/elsewhere"}):
            assert _repo() == "someone/elsewhere"


class TestProbeErrorBranches:
    """Cover the transport/decode/enum branches the happy-path tests do not reach."""

    def test_get_wraps_url_error_as_transport_error(self) -> None:
        with patch(
            "scripts.checks.misc.validate_ghas_probe.urlopen",
            side_effect=urllib.error.URLError("no route to host"),
        ):
            with pytest.raises(ProbeTransportError, match="no route to host"):
                _get("/repos/x/y", "tok")

    def test_get_wraps_non_auth_http_error_as_transport_error(self) -> None:
        """A 5xx is a transport failure, not an auth failure -- only 401/403 mean the token."""

        def _raise_500(request: object, timeout: float = 15) -> None:
            raise urllib.error.HTTPError(url="x", code=500, msg="Server Error", hdrs=None, fp=None)  # type: ignore[arg-type]

        with patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_raise_500):
            with pytest.raises(ProbeTransportError, match="HTTP 500"):
                _get("/repos/x/y", "tok")

    def test_alerts_reachability_returns_minus_one_on_url_error(self) -> None:
        with patch(
            "scripts.checks.misc.validate_ghas_probe.urlopen",
            side_effect=urllib.error.URLError("dns failure"),
        ):
            assert _alerts_reachability("/repos/x/y/secret-scanning/alerts", "tok") == -1

    def test_status_label_coerces_unrecognised_values_to_unknown(self) -> None:
        assert _status_label("something-else") == "unknown"
        assert _status_label(None) == "unknown"

    def test_actions_scope_label_coerces_unrecognised_values_to_unknown(self) -> None:
        assert _actions_scope_label("not-a-scope") == "unknown"
        assert _actions_scope_label(None) == "unknown"

    def test_non_json_repo_info_body_raises_transport_error(self) -> None:
        def _urlopen(request: object, timeout: float = 15):
            return TestValidateGhasProbe._FakeResponse(200, b"<html>not json</html>")

        with patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_urlopen):
            with pytest.raises(ProbeTransportError, match="repo-info endpoint"):
                _probe("tok")

    def test_non_json_actions_permissions_body_raises_transport_error(self) -> None:
        def _urlopen(request: object, timeout: float = 15):
            url = request.full_url  # type: ignore[attr-defined]
            if url.endswith("/actions/permissions"):
                return TestValidateGhasProbe._FakeResponse(200, b"nonsense")
            return TestValidateGhasProbe._FakeResponse(200, TestValidateGhasProbe._repo_body())

        with patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=_urlopen):
            with pytest.raises(ProbeTransportError, match="actions/permissions endpoint"):
                _probe("tok")

    def test_disabled_push_protection_is_reported(self) -> None:
        state = {"scanning_status": "enabled", "push_protection": "disabled", "actions_enabled": True}
        assert _disabled_controls(state) == ["push_protection=disabled"]


class TestAuthErrorsAreDistinctFromTransportErrors:
    """_get must classify 401/403 as ProbeAuthError, never as the generic ProbeTransportError.
    Both callers catch the two classes together for the failed[]/exit-code outcome, so the
    class identity (and the runner's message) is the only channel that tells them apart."""

    @staticmethod
    def _raise_http(code: int):
        def _urlopen(request: object, timeout: float = 15) -> None:
            raise urllib.error.HTTPError(url="x", code=code, msg="denied", hdrs=None, fp=None)  # type: ignore[arg-type]

        return _urlopen

    def test_get_raises_probe_auth_error_on_401(self) -> None:
        with patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._raise_http(401)):
            with pytest.raises(ProbeAuthError, match="HTTP 401"):
                _get("/repos/x/y", "tok")

    def test_get_raises_probe_auth_error_on_403(self) -> None:
        with patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._raise_http(403)):
            with pytest.raises(ProbeAuthError, match="HTTP 403"):
                _get("/repos/x/y", "tok")

    def test_runner_reports_a_401_as_an_auth_error(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch.dict("os.environ", {"GHAS_PROBE_TOKEN": "tok"}),
            patch("scripts.checks.misc.validate_ghas_probe.urlopen", side_effect=self._raise_http(401)),
        ):
            assert _ghas_run_cli() != 0
        assert "auth error, cannot verify: HTTP 401" in capsys.readouterr().out


def test_malformed_repository_falls_back_to_the_default() -> None:
    with patch.dict("os.environ", {"GITHUB_REPOSITORY": "not-a-full-name"}):
        assert _repo() == "benjamin-blake/theseus"
