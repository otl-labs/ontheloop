"""scripts/ci/originating_pr_sha.py: same output and fail-closed marker as the inline artifact_sha bodies."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.ci import originating_pr_sha as mod

MARKER = "No originating PR resolved for abc; cannot resolve the content-addressed artifact sha (Decision 77 no-TOCTOU)."


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    monkeypatch.setenv("REPO", "o/r")
    monkeypatch.setenv("RED_SHA", "abc")
    return path


def _gh(monkeypatch: pytest.MonkeyPatch, stdout: str = "", rc: int = 0, exc: Exception | None = None) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, stdout, "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    return calls


def test_resolved(out: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _gh(monkeypatch, "deadbeef\n")
    assert mod.main() == 0
    assert out.read_text() == "value=deadbeef\n"
    assert calls[0][:3] == ["gh", "api", "repos/o/r/commits/abc/pulls"]


@pytest.mark.parametrize("stdout", ["", "null\n"])
def test_empty_or_null_fails_closed(
    out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, stdout: str
) -> None:
    _gh(monkeypatch, stdout)
    assert mod.main() == 1
    assert MARKER in capsys.readouterr().err
    assert not out.exists()


def test_gh_failure_fails_closed(out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _gh(monkeypatch, "deadbeef", rc=1)
    assert mod.main() == 1
    assert MARKER in capsys.readouterr().err


def test_gh_missing_fails_closed(out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _gh(monkeypatch, exc=FileNotFoundError())
    assert mod.main() == 1
    assert MARKER in capsys.readouterr().err
