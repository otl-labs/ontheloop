"""Portability leg of validate_verification_registry (rec-4889): drives the validator end to end over a tmp git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts import verification_graduation
from scripts.checks.verification.validate_verification_registry import validate_verification_registry

PREFIX = "verification-registry portability:"
ADMITTED = verification_graduation.DifferentialOutcome(admitted=True, reason="admitted -- test double")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _row(check_id: str, slot: str = "command_exit_zero", command: list[str] | None = None) -> dict:
    spec = {"node_id": "tests/x.py::t"} if slot == "test_selector" else {"command": command or ["bash", "-c", "true"]}
    return {
        "check_id": check_id,
        "primitive_slot": slot,
        "guard_target": "scripts/foo.py",
        "plan_slug": "p",
        "graduated_at": "2026-11-05",
        "check_spec": spec,
    }


def _write(repo: Path, row: dict) -> None:
    path = repo / verification_graduation.REGISTRY_ENTRIES_REL / f"{row['check_id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(row, sort_keys=False), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _write(root, _row("live-rg", command=["bash", "-c", "rg x"]))
    _write(root, _row("live-plain"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    return root


def _run(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[list[str], str]:
    failed: list[str] = []
    with patch("scripts.verification_graduation.run_differential", return_value=ADMITTED):
        validate_verification_registry(failed, root=root)
    return [f for f in failed if f.startswith(PREFIX)], capsys.readouterr().out


class TestPortabilityLeg:
    def test_added_rg_record_is_refused(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(repo, _row("new-rg", command=["bash", "-c", "rg x"]))
        failures, out = _run(repo, capsys)
        assert len(failures) == 1
        assert "check_id=new-rg" in failures[0]
        assert "Portability (rec-4889): 1 added-or-modified command-slot row(s) examined, 1 refused" in out

    def test_added_live_checkout_record_is_refused(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(repo, _row("new-live", command=["git", "branch", "--show-current"]))
        failures, _ = _run(repo, capsys)
        assert len(failures) == 1
        assert "check_id=new-live" in failures[0]
        assert "LIVE_CHECKOUT:" in failures[0]

    def test_modified_record_gaining_rg_is_refused(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(repo, _row("live-plain", command=["bash", "-c", "true && rg x"]))
        failures, _ = _run(repo, capsys)
        assert len(failures) == 1
        assert "check_id=live-plain" in failures[0]

    def test_clean_added_record_passes(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(repo, _row("new-clean", command=["bash", "-c", "grep -q x README.md"]))
        failures, out = _run(repo, capsys)
        assert failures == []
        assert "1 added-or-modified command-slot row(s) examined, 0 refused" in out

    def test_unmodified_live_rg_record_is_not_scanned(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        failures, out = _run(repo, capsys)
        assert failures == []
        assert "Portability (rec-4889): 0 added-or-modified command-slot row(s) examined" in out

    def test_added_test_selector_record_passes(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(repo, _row("new-selector", slot="test_selector"))
        failures, out = _run(repo, capsys)
        assert failures == []
        assert "0 added-or-modified command-slot row(s) examined" in out
