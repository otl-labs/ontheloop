"""Tests for the _terraform.py readonly-lockfile hardening (rec-3318/3369/3919/4110).

A NEW module rather than an addition to tests/validate/test_terraform_checks.py -- that file is
at 474 SLOC and stays untouched (Decision 128 decompose-never-raise; out of scope, Decision 59).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.checks import _common
from scripts.checks._terraform import (
    _readonly_flag_for,
    run_terraform_checks,
    run_terraform_creds_free,
    tracked_lock_files,
)


def _chdir_root(cmd: list[str]) -> str:
    return next(a.split("=", 1)[1] for a in cmd if a.startswith("-chdir="))


def _ok(_cmd: list[str], **_kwargs: object) -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


class TestTerraformGateLockReadonly:
    def test_terraform_gate_leaves_lock_unmodified(self) -> None:
        """init for terraform/personal and terraform/github carries -lockfile=readonly;
        the test patches tracked_lock_files to the personal and github locks, so terraform/bootstrap
        (untracked here) gets no -lockfile=readonly."""
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            return _ok(cmd)

        with (
            patch("scripts.checks._terraform.shutil.which", return_value="/usr/bin/terraform"),
            patch(
                "scripts.checks._terraform.tracked_lock_files",
                return_value=["terraform/personal/.terraform.lock.hcl", "terraform/github/.terraform.lock.hcl"],
            ),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            run_terraform_creds_free(failed)

        init_calls = {_chdir_root(c): c for c in calls if "init" in c}
        assert "-lockfile=readonly" in init_calls["terraform/personal"]
        assert "-lockfile=readonly" in init_calls["terraform/github"]
        assert "-lockfile=readonly" not in init_calls["terraform/bootstrap"]
        assert failed == []

    def test_drift_check_init_is_readonly(self) -> None:
        """run_terraform_checks' informational backend init also carries the flag."""
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            return _ok(cmd)

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("scripts.checks._terraform.shutil.which", return_value="/usr/bin/terraform"),
            patch(
                "scripts.checks._terraform.tracked_lock_files",
                return_value=["terraform/personal/.terraform.lock.hcl"],
            ),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            run_terraform_checks(failed)

        reconfigure_calls = [c for c in calls if "-reconfigure" in c]
        assert len(reconfigure_calls) == 1
        assert "-lockfile=readonly" in reconfigure_calls[0]

    def test_readonly_drift_failure_is_named_not_skipped(self, capsys) -> None:
        """A readonly-refused drift is a named, un-retried failure -- never proxy_blocked, never
        silently swallowed as a skip."""
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            result = MagicMock()
            if "init" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = (
                    "Error: Provider dependency changes detected\n\n"
                    "Changes to the required provider dependencies were detected, but the lock\n"
                    'file is read-only. To use and record these requirements, run "terraform init"\n'
                    'without the "-lockfile=readonly" flag.\n'
                )
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("scripts.checks._terraform.shutil.which", return_value="/usr/bin/terraform"),
            patch(
                "scripts.checks._terraform.tracked_lock_files",
                return_value=["terraform/personal/.terraform.lock.hcl"],
            ),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            run_terraform_creds_free(failed, roots=("terraform/personal",))

        captured = capsys.readouterr()
        init_calls = [c for c in calls if "init" in c]
        assert len(init_calls) == 1
        assert failed == ["Terraform init [terraform/personal]"]
        assert "FAIL:" in captured.out
        assert "validate_terraform_lock_coherence" in captured.out
        assert "SKIP:" not in captured.out

    def test_drift_check_reports_lock_drift_distinctly(self, capsys) -> None:
        """The informational init's drift failure prints the lock-drift line, not the
        credentials-missing one."""

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            if "-reconfigure" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Error: Provider dependency changes detected\n"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("scripts.checks._terraform.validate_terraform_try"),
            patch("scripts.checks._terraform.shutil.which", return_value="/usr/bin/terraform"),
            patch(
                "scripts.checks._terraform.tracked_lock_files",
                return_value=["terraform/personal/.terraform.lock.hcl"],
            ),
            patch("scripts.checks._common.run", side_effect=mock_run),
        ):
            failed: list[str] = []
            run_terraform_checks(failed)

        captured = capsys.readouterr()
        assert "lock drift (readonly init refused)" in captured.out
        assert "credentials missing" not in captured.out

    def test_git_unavailable_falls_back_to_readonly_for_existing_locks(self, tmp_path: Path) -> None:
        """tracked_lock_files() returning None (git unavailable) falls back to readonly for any
        root whose lock file exists on disk -- fail closed, never trusting an unmeasurable
        tracking state. Hermetic: a tmp root, never the real tree."""
        root_dir = tmp_path / "terraform" / "personal"
        root_dir.mkdir(parents=True)
        (root_dir / ".terraform.lock.hcl").write_text("", encoding="utf-8")

        with patch("scripts.checks._common.ROOT", tmp_path):
            assert _readonly_flag_for("terraform/personal", None) == ["-lockfile=readonly"]
            assert _readonly_flag_for("terraform/absent", None) == []


class TestTrackedLockDiscovery:
    def test_tracked_lock_is_listed(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        lock_dir = tmp_path / "terraform" / "widget"
        lock_dir.mkdir(parents=True)
        (lock_dir / ".terraform.lock.hcl").write_text("", encoding="utf-8")
        subprocess.run(["git", "add", "terraform/widget/.terraform.lock.hcl"], cwd=tmp_path, check=True)

        assert tracked_lock_files(tmp_path) == ["terraform/widget/.terraform.lock.hcl"]

    def test_gitignored_lock_is_excluded(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / ".gitignore").write_text("terraform/**/.terraform.lock.hcl\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
        lock_dir = tmp_path / "terraform" / "bootstrap"
        lock_dir.mkdir(parents=True)
        (lock_dir / ".terraform.lock.hcl").write_text("", encoding="utf-8")
        subprocess.run(
            ["git", "add", "terraform/bootstrap/.terraform.lock.hcl"], cwd=tmp_path, check=False
        )  # gitignored -- git silently refuses, never staged

        assert tracked_lock_files(tmp_path) == []

    def test_non_repo_directory_returns_none(self, tmp_path: Path) -> None:
        assert tracked_lock_files(tmp_path) is None

    def test_git_absent_returns_none(self) -> None:
        with patch("scripts.checks._terraform.subprocess.run", side_effect=FileNotFoundError):
            assert tracked_lock_files(Path("/nonexistent")) is None

    def test_real_repo_tracks_exactly_bootstrap_personal_and_github(self) -> None:
        """UNPATCHED -- catches a discovery defect the patched gate cases above cannot, and stops
        TestRepoLocksCoherent (validate_terraform_lock_coherence's own test) from passing
        vacuously as examined(0) if the tracking state ever changes."""
        assert tracked_lock_files(_common.ROOT) == [
            "terraform/bootstrap/.terraform.lock.hcl",
            "terraform/github/.terraform.lock.hcl",
            "terraform/personal/.terraform.lock.hcl",
        ]
