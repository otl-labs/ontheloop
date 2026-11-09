"""Tests for scripts.session.coordinator_copy against a temporary git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.session import coordinator_copy as cc


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, body: str) -> None:
    path = repo / cc.CONTRACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "c")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "contract: one\n")
    monkeypatch.chdir(repo)
    return repo


def test_write_then_check_round_trips_byte_identical(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert cc.main(["write", str(out), "--ref", "main"]) == 0
    assert cc.main(["check", str(out), "--ref", "main"]) == 0
    text = (out / "coordinator.yaml").read_text(encoding="utf-8")
    header, body = text.split("\n", 1)
    assert header.startswith("# GENERATED from docs/contracts/coordinator.yaml at ")
    assert body == (repo / cc.CONTRACT).read_text(encoding="utf-8")


def test_edited_body_is_stale(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    copy = out / "coordinator.yaml"
    copy.write_text(copy.read_text(encoding="utf-8") + "edit\n", encoding="utf-8")
    assert cc.main(["check", str(out), "--ref", "main"]) == 1


def test_stale_header_sha_is_stale(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    copy = out / "coordinator.yaml"
    lines = copy.read_text(encoding="utf-8").split("\n", 1)
    copy.write_text(lines[0].replace(" at ", " at 0") + "\n" + lines[1], encoding="utf-8")
    assert cc.main(["check", str(out), "--ref", "main"]) == 1


def test_newer_commit_of_contract_is_stale(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    _commit(repo, "contract: two\n")
    assert cc.main(["check", str(out), "--ref", "main"]) == 1


def test_missing_copy_exits_2(repo: Path, tmp_path: Path) -> None:
    assert cc.main(["check", str(tmp_path / "none"), "--ref", "main"]) == 2


def test_missing_contract_at_ref_exits_2(repo: Path, tmp_path: Path) -> None:
    assert cc.main(["write", str(tmp_path / "o"), "--ref", "nope"]) == 2


def test_check_with_missing_ref_exits_2(repo: Path, tmp_path: Path) -> None:
    assert cc.main(["check", str(tmp_path / "o"), "--ref", "nope"]) == 2


def test_ref_without_the_contract_exits_2(repo: Path, tmp_path: Path) -> None:
    _git(repo, "rm", "-q", cc.CONTRACT)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "rm")
    _git(repo, "tag", "gone")
    _git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "x").write_text("x", encoding="utf-8")
    _git(repo, "add", "x")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "x")
    assert cc.main(["check", str(tmp_path / "o"), "--ref", "other"]) == 2
    assert cc.main(["write", str(tmp_path / "o"), "--ref", "other"]) == 2


def _commit_file(repo: Path, rel: str, data: bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "c")


def test_harness_profiles_are_copied_beside_the_contract(repo: Path, tmp_path: Path) -> None:
    _commit_file(repo, "docs/contracts/coordinator-harness-x.yaml", b"profile: x\n")
    _commit_file(repo, "docs/contracts/other.yaml", b"other: 1\n")
    out = tmp_path / "out"
    assert cc.main(["write", str(out), "--ref", "main"]) == 0
    copy = (out / "coordinator-harness-x.yaml").read_bytes()
    header, body = copy.split(b"\n", 1)
    assert header.startswith(b"# GENERATED from docs/contracts/coordinator-harness-x.yaml at ")
    assert body == b"profile: x\n"
    assert not (out / "other.yaml").exists()
    assert cc.main(["check", str(out), "--ref", "main"]) == 0


def test_stale_profile_copy_is_stale(repo: Path, tmp_path: Path) -> None:
    _commit_file(repo, "docs/contracts/coordinator-harness-x.yaml", b"profile: x\n")
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    copy = out / "coordinator-harness-x.yaml"
    copy.write_bytes(copy.read_bytes() + b"edit\n")
    assert cc.main(["check", str(out), "--ref", "main"]) == 1


def test_missing_profile_copy_exits_2(repo: Path, tmp_path: Path) -> None:
    _commit_file(repo, "docs/contracts/coordinator-harness-x.yaml", b"profile: x\n")
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    (out / "coordinator-harness-x.yaml").unlink()
    contract_copy = out / "coordinator.yaml"
    contract_copy.write_bytes(contract_copy.read_bytes() + b"edit\n")
    assert cc.main(["check", str(out), "--ref", "main"]) == 2


def test_orphan_profile_copy_is_stale(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    cc.main(["write", str(out), "--ref", "main"])
    (out / "coordinator-harness-gone.yaml").write_bytes(b"# old\n")
    (out / "unrelated.txt").write_bytes(b"keep\n")
    assert cc.main(["check", str(out), "--ref", "main"]) == 1
    assert cc.main(["write", str(out), "--ref", "main"]) == 0
    assert not (out / "coordinator-harness-gone.yaml").exists()
    assert (out / "unrelated.txt").read_bytes() == b"keep\n"
    assert cc.main(["check", str(out), "--ref", "main"]) == 0


def test_crlf_contract_round_trips_byte_identical(repo: Path, tmp_path: Path) -> None:
    _git(repo, "config", "core.autocrlf", "false")
    raw = b"contract: one\r\nline: two\r\n"
    _commit_file(repo, cc.CONTRACT, raw)
    out = tmp_path / "out"
    assert cc.main(["write", str(out), "--ref", "main"]) == 0
    copy = out / "coordinator.yaml"
    assert copy.read_bytes().split(b"\n", 1)[1] == raw
    assert cc.main(["check", str(out), "--ref", "main"]) == 0
    copy.write_bytes(copy.read_bytes().replace(b"\r\n", b"\n"))
    assert cc.main(["check", str(out), "--ref", "main"]) == 1
