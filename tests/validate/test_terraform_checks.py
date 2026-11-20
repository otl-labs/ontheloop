"""run_terraform_checks() tests -- orchestrator residue (scripts/checks/_terraform.py, rec-2709 Wave 1)."""

from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.validate_module import _validate


class TestRunTerraformChecks:
    """Tests for run_terraform_checks() (full presubmit) and run_terraform_creds_free()."""

    def test_warns_when_personal_plan_exit_code_2(self, capsys: pytest.CaptureFixture) -> None:
        """run_terraform_checks() warns when the terraform/personal plan returns exit code 2."""

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 2 if "-detailed-exitcode" in cmd else 0
            return result

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            _validate.run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "WARNING: Terraform changes pending" in captured.out
        assert "terraform/personal" in captured.out
        assert failed == []

    def test_no_warning_when_exit_code_0(self, capsys: pytest.CaptureFixture) -> None:
        """run_terraform_checks() does not warn when plan returns exit code 0."""

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            _validate.run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "WARNING" not in captured.out
        assert failed == []

    def test_informational_plan_skipped_when_reconfigure_init_fails(self, capsys: pytest.CaptureFixture) -> None:
        """run_terraform_checks() skips the informational plan (never blocks) when the
        credentials-needing re-init fails -- covers the init_res.returncode != 0 branch."""

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            result = MagicMock()
            if "-reconfigure" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = 'Error: Backend initialization required, please run "terraform init"'
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            _validate.run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "Terraform plan skipped: backend/init unavailable" in captured.out
        assert "WARNING" not in captured.out
        assert failed == []

    def test_informational_plan_skipped_on_non_drift_error(self, capsys: pytest.CaptureFixture) -> None:
        """run_terraform_checks() treats a plan exit code outside {0, 2} as a non-blocking,
        credentials-unavailable skip -- covers the `result.returncode not in (0, 2)` branch."""

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            result = MagicMock()
            if "-detailed-exitcode" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Error: some other plan failure"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            _validate.run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "Terraform plan skipped or failed (credentials unavailable)" in captured.out
        assert "WARNING" not in captured.out
        assert failed == []

    def test_skips_terraform_binary_steps_when_not_found(self, capsys: pytest.CaptureFixture) -> None:
        """No terraform binary -> creds-free helper prints a skip and `run` is never invoked."""
        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("validate.shutil.which", return_value=None),
            patch("scripts.checks._common.run", side_effect=AssertionError("run must not be called when terraform is absent")),
        ):
            failed: list[str] = []
            _validate.run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "skipped" in captured.out
        assert failed == []

    def test_creds_free_covers_every_root(self) -> None:
        """run_terraform_creds_free() runs init -backend=false + validate + fmt for ALL roots, no plan.

        Set equality, not membership: the legacy depth-1 `terraform/` root was retired (it was never
        applied and had no backend), and a membership-only assertion would keep passing if it -- or
        any other retired root -- were silently re-added.
        """
        calls: list[list] = []

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed)

        chdirs = {arg for cmd in calls for arg in cmd if isinstance(arg, str) and arg.startswith("-chdir=")}
        flat = [tok for cmd in calls for tok in cmd]
        assert chdirs == {
            "-chdir=terraform/personal",
            "-chdir=terraform/github",
            "-chdir=terraform/bootstrap",
            "-chdir=terraform/organization",
        }
        assert any("-backend=false" in cmd for cmd in calls)  # creds-free init
        assert all("plan" not in cmd for cmd in calls)  # no creds-needing plan here
        assert "init" in flat and "validate" in flat and "fmt" in flat
        assert failed == []

    def test_creds_free_skips_when_terraform_absent(self, capsys: pytest.CaptureFixture) -> None:
        """run_terraform_creds_free() emits a visible skip and calls nothing when terraform is absent."""
        with (
            patch("validate.shutil.which", return_value=None),
            patch("scripts.checks._common.run", side_effect=AssertionError("run must not be called")),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed)
        assert "skipped" in capsys.readouterr().out
        assert failed == []

    def test_creds_free_init_retries_on_transient_5xx(self, capsys: pytest.CaptureFixture) -> None:
        """_terraform_init_with_retry retries on transient 5xx and succeeds on the third attempt."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                if init_call_count < 3:
                    result.returncode = 1
                    result.stdout = "Error: could not query provider registry"
                    result.stderr = ""
                else:
                    result.returncode = 0
                    result.stdout = "Terraform has been successfully initialized!"
                    result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 3
        assert failed == []

    def test_creds_free_init_fails_fast_on_non_transient(self) -> None:
        """_terraform_init_with_retry does NOT retry on non-transient errors."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                result.returncode = 1
                result.stdout = "Error: Required token could not be found"
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 1
        assert len(failed) == 1
        assert "Terraform init" in failed[0]

    def test_creds_free_init_exhausts_retries_on_persistent_transient(self) -> None:
        """A transient 5xx on all 5 attempts (widened window) exhausts the retry budget and appends to failed."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Error: 502 Bad Gateway from registry.terraform.io"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 5  # all attempts consumed
        assert len(failed) == 1
        assert "Terraform init" in failed[0]

    def test_creds_free_init_retries_on_connection_reset(self) -> None:
        """_terraform_init_with_retry retries a provider-download connection reset and succeeds on attempt 3."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                if init_call_count < 3:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = "read: connection reset by peer"
                else:
                    result.returncode = 0
                    result.stdout = "Terraform has been successfully initialized!"
                    result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 3
        assert failed == []

    def test_creds_free_init_fails_fast_on_bad_pin(self) -> None:
        """A non-transient bad-pin provider error is not retried, even though it fails init."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                result.returncode = 1
                result.stdout = (
                    "Error: Failed to install provider\n"
                    "provider registry does not have a provider named registry.terraform.io/hashicorp/aws"
                )
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 1
        assert len(failed) == 1
        assert "Terraform init" in failed[0]

    def test_creds_free_init_skips_on_proxy_403(self, capsys: pytest.CaptureFixture) -> None:
        """A github.com-403 auth-checksum init failure is a visible skip, not a failure, and fmt still runs."""
        calls: list[list] = []

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            calls.append(list(cmd))
            result = MagicMock()
            if "init" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = (
                    "Error: Failed to install provider\n"
                    "Error: retrieving checksums for provider: 403 Forbidden returned from github.com"
                )
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform/personal",))

        captured = capsys.readouterr()
        assert "SKIP" in captured.out
        assert failed == []
        assert not any("validate" in cmd for cmd in calls)
        assert any("fmt" in cmd for cmd in calls)

    def test_creds_free_init_still_fails_on_non_github_403(self) -> None:
        """A 403 lacking the github.com/checksum co-occurrence (e.g. an S3 backend 403) still fails."""

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            result = MagicMock()
            if "init" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Error: error configuring S3 Backend: 403 Forbidden: AccessDenied"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform/personal",))

        assert len(failed) == 1
        assert "Terraform init" in failed[0]

    def test_transient_init_signatures_exact_set(self) -> None:
        """_TRANSIENT_INIT_SIGNATURES contains the expected tokens (parity with workflow retry loop)."""
        expected = frozenset(
            (
                "502",
                "Bad Gateway",
                "Gateway Timeout",
                "could not query provider registry",
                "failed after ",
                "connection reset by peer",
                "i/o timeout",
                "TLS handshake timeout",
                "unexpected EOF",
            )
        )
        assert frozenset(_validate._TRANSIENT_INIT_SIGNATURES) == expected

    def test_creds_free_init_retries_on_gateway_timeout(self, capsys: pytest.CaptureFixture) -> None:
        """A '504 Gateway Timeout returned from github.com' body is classified transient and retried."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                if init_call_count < 3:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = "Error: 504 Gateway Timeout returned from github.com"
                else:
                    result.returncode = 0
                    result.stdout = "Terraform has been successfully initialized!"
                    result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 3
        assert failed == []

    def test_creds_free_init_widened_window_succeeds_on_attempt_four(self) -> None:
        """A transient body that exhausts the OLD max_attempts=3 must now succeed at attempt 4/5."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                if init_call_count < 4:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = "Error: 504 Gateway Timeout returned from github.com"
                else:
                    result.returncode = 0
                    result.stdout = "Terraform has been successfully initialized!"
                    result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 4
        assert failed == []

    def test_creds_free_init_widened_window_succeeds_on_attempt_five(self) -> None:
        """A transient body persisting through 4 attempts must now succeed at attempt 5/5 (max_attempts=5)."""
        init_call_count = 0

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                if init_call_count < 5:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = "Error: 502 Bad Gateway from registry.terraform.io"
                else:
                    result.returncode = 0
                    result.stdout = "Terraform has been successfully initialized!"
                    result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep"),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        assert init_call_count == 5
        assert failed == []

    def test_creds_free_init_backoff_capped_at_16s(self) -> None:
        """Backoff delay must be capped at 16s -- uncapped 2**attempt would reach 32s at attempt 5."""
        init_call_count = 0
        sleep_delays: list[int] = []

        def mock_run(cmd: list, **kwargs: object) -> MagicMock:
            nonlocal init_call_count
            result = MagicMock()
            if "init" in cmd:
                init_call_count += 1
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Error: 502 Bad Gateway from registry.terraform.io"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        def mock_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with (
            patch("validate.shutil.which", return_value="/usr/bin/terraform"),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("validate.time.sleep", side_effect=mock_sleep),
        ):
            failed: list[str] = []
            _validate.run_terraform_creds_free(failed, roots=("terraform",))

        # attempts 1-4 sleep (attempt 5 is final, no sleep after); uncapped would be [2, 4, 8, 16]
        # at attempt 4 -> 2**4=16 (already at the cap); the cap only bites once attempt would exceed
        # it. Assert every recorded delay respects the 16s cap.
        assert init_call_count == 5
        assert sleep_delays == [2, 4, 8, 16]
        assert all(delay <= 16 for delay in sleep_delays)
