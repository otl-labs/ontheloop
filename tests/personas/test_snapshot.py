"""The head-tree snapshot: links and out-of-tree members are refused; the read helper refuses a planted symlink."""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from scripts.personas import materialise, resolve_pr, snapshot


def _tar(members: list[tuple[str, str, bytes | str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = payload if isinstance(payload, bytes) else payload.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = str(payload)
                tar.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = str(payload)
                tar.addfile(info)
    return buf.getvalue()


def test_clean_tarball_extracts_regular_files_only(tmp_path: Path) -> None:
    data = _tar([("repo-sha/a.txt", "file", "hello"), ("repo-sha/d/b.txt", "file", "x")])
    assert snapshot.extract(data, tmp_path / "snap") == 2
    assert (tmp_path / "snap" / "a.txt").read_text() == "hello"
    assert (tmp_path / "snap" / "d" / "b.txt").read_text() == "x"


@pytest.mark.parametrize(
    "members",
    [
        [("r/a", "file", "x"), ("r/link", "symlink", "a")],
        [("r/a", "file", "x"), ("r/hard", "hardlink", "r/a")],
        [("/abs", "file", "x")],
        [("r/../escape", "file", "x")],
    ],
)
def test_unsafe_member_is_refused(tmp_path: Path, members: list[tuple[str, str, bytes | str]]) -> None:
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.extract(_tar(members), tmp_path / "snap")


def test_unreadable_tarball_is_refused(tmp_path: Path) -> None:
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.extract(b"not a tarball", tmp_path / "snap")


def test_refusal_makes_every_route_input_unusable(tmp_path: Path) -> None:
    out = tmp_path / "prepared"
    out.mkdir()
    resolution = {"change_id": "o/r#1", "head_sha": "a" * 40}
    materialise.materialise(
        tmp_path / "snap", out, resolution, ["verdict_probe.proceed", "verdict_probe.revise"], [], usable=False
    )
    for route in ("verdict_probe.proceed", "verdict_probe.revise"):
        assert json.loads((out / f"failure-{route}.json").read_text())["failure"] == "input_unusable"
        assert not (out / f"input-{route}.json").exists()


def test_read_helper_refuses_a_symlink_planted_after_extraction(tmp_path: Path) -> None:
    root = tmp_path / "snap"
    root.mkdir()
    (root / "real.txt").write_text("secret")
    os.symlink(root / "real.txt", root / "link.txt")
    (root / "d").mkdir()
    os.symlink(root, root / "d" / "loop")
    assert snapshot.read_bytes(root, "real.txt") == b"secret"
    assert snapshot.read_bytes(root, "missing.txt") is None
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_bytes(root, "link.txt")
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_bytes(root, "d/loop/real.txt")
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_bytes(root, "d")
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_bytes(root, "../outside")


def test_fetch_verb_records_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir()
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps({"head_sha": "a" * 40}))
    monkeypatch.setenv("PERSONA_SNAPSHOT", str(tmp_path / "snap"))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    bad = _tar([("r/link", "symlink", "x")])
    monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (200, bad))
    assert snapshot.main([]) == 0
    assert json.loads((tmp_path / "prepared" / "snapshot.json").read_text())["ok"] is False


def test_fetch_refuses_to_run_without_the_snapshot_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONA_SNAPSHOT", raising=False)
    with pytest.raises(RuntimeError):
        snapshot.main([])
