"""scripts/ci/verify_plan_sha256.py: the verdicts and markers of the inline bodies it replaces."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.ci import verify_plan_sha256 as mod

LABEL = "SANDBOX_FRESH_PLAN_ARTIFACT"


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected: str, write: bool = True) -> int:
    plan = tmp_path / "plan.bin"
    if write:
        plan.write_bytes(b"plan")
    monkeypatch.setenv("FRESH_PLAN_SHA256", expected)
    return mod.main(["--label", LABEL, "--plan", str(plan)])


def test_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    digest = hashlib.sha256(b"plan").hexdigest()
    assert _run(tmp_path, monkeypatch, digest) == 0
    assert capsys.readouterr().out.strip() == f"{LABEL} sha256 verified: {digest}"


def test_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    assert _run(tmp_path, monkeypatch, "0" * 64) == 1
    err = capsys.readouterr().err
    assert f"::error::{LABEL} sha256 mismatch: expected {'0' * 64}, got " in err
    assert err.strip().endswith("Refusing to apply a possibly-corrupted plan.")


def test_missing_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    assert _run(tmp_path, monkeypatch, "a" * 64, write=False) == 1
    assert "sha256 mismatch" in capsys.readouterr().err


def test_empty_expected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    assert _run(tmp_path, monkeypatch, "") == 1
    assert "sha256 mismatch" in capsys.readouterr().err
