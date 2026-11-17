"""Capture pin base resolution (rec-4426): the base comes from the default branch's live tip, never a stale tracking ref."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "session_start_log_turn.sh"
LINE = re.compile(r"\[session_start_log_turn\] capture pin checkout=(\S+) base=(\S+) dirty=(true|false|unknown)\n")
GIT_ENV = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
IDENT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *GIT_ENV, "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=60)
    return done.stdout.strip()


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        seed = root / "seed"
        seed.mkdir()
        _git(seed, "init", "-q", "-b", "main")
        (seed / "f.txt").write_text("fork\n", encoding="utf-8")
        _git(seed, "add", "f.txt")
        _git(seed, "commit", "-q", "-m", "fork")
        self.fork = _git(seed, "rev-parse", "HEAD")
        self.origin = root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(self.origin)], check=True, timeout=60)

    def url(self) -> str:
        return f"file://{self.origin}"

    def advance(self) -> str:
        parent = subprocess.run(
            ["git", "--git-dir", str(self.origin), "rev-parse", "refs/heads/main"], capture_output=True, text=True, check=True
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "--git-dir", str(self.origin), "rev-parse", f"{parent}^{{tree}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        new = subprocess.run(
            ["git", "--git-dir", str(self.origin), "commit-tree", tree, "-p", parent, "-m", "live"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **IDENT},
        ).stdout.strip()
        subprocess.run(["git", "--git-dir", str(self.origin), "update-ref", "refs/heads/main", new], check=True)
        return new

    def clone(self, name: str, *, shallow: bool) -> Path:
        dest = self.root / name
        args = ["git", "clone", "-q"] + (["--depth", "1"] if shallow else []) + [self.url(), str(dest)]
        subprocess.run(args, check=True, timeout=60)
        hooks = dest / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(HOOK, hooks / HOOK.name)
        return dest


def _work_commit(repo: Path) -> str:
    _git(repo, "checkout", "-q", "-b", "work")
    (repo / "w.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "w.txt")
    _git(repo, "commit", "-q", "-m", "work")
    return _git(repo, "rev-parse", "HEAD")


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    env.update(extra)
    return env


def _hook(repo: Path, tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> tuple[str, float, str]:
    started = time.monotonic()
    done = subprocess.run(
        ["bash", str(repo / ".claude" / "hooks" / HOOK.name), *args],
        stdin=subprocess.DEVNULL,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env if env is not None else _env(tmp_path),
    )
    elapsed = time.monotonic() - started
    assert done.returncode == 0 and done.stdout == ""
    match = LINE.fullmatch(done.stderr)
    assert match, done.stderr
    return match.group(2), elapsed, done.stderr


def test_base_resolved_when_origin_main_fetched_late(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    live = world.advance()
    _git(repo, "fetch", "-q", "origin", "+refs/heads/main:refs/heads/live")
    _git(repo, "checkout", "-q", "live")
    _git(repo, "update-ref", "refs/remotes/origin/main", world.fork)
    base, _, _ = _hook(repo, tmp_path)
    assert base == live


def test_base_waits_for_a_late_unshallow_of_the_live_tip(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    world.advance()
    _work_commit(repo)

    def unshallow() -> None:
        time.sleep(1)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--unshallow", "origin", "main"], check=True, timeout=60)

    worker = threading.Thread(target=unshallow)
    worker.start()
    try:
        base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=5")
    finally:
        worker.join()
    assert base == world.fork
    assert 0.5 <= elapsed < 8


def test_full_clone_accepts_a_fork_merge_base(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    _work_commit(repo)
    world.advance()
    _git(repo, "fetch", "-q", "origin")
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=5")
    assert base == world.fork
    assert elapsed < 3


def test_shallow_clone_rejects_a_fork_merge_base(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    _work_commit(repo)
    world.advance()
    _git(repo, "fetch", "-q", "origin", "main")
    assert _git(repo, "rev-parse", "--is-shallow-repository") == "true"
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=1")
    assert base == "unknown"
    assert elapsed >= 1


def test_unresolvable_live_tip_prints_unknown_not_the_tracking_ref(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    _work_commit(repo)
    world.advance()
    assert _git(repo, "rev-parse", "origin/main") == world.fork
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=1")
    assert base == "unknown"
    assert elapsed >= 1


def test_zero_wait_does_not_wait(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    _work_commit(repo)
    world.advance()
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=0")
    assert base == "unknown"
    assert elapsed < 3


def test_shallow_offline_prints_unknown_not_the_tracking_ref(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    _work_commit(repo)
    _git(repo, "remote", "set-url", "origin", f"file://{tmp_path}/missing.git")
    assert _git(repo, "rev-parse", "origin/main") == world.fork
    base, _, _ = _hook(repo, tmp_path, "--base-wait-seconds=1")
    assert base == "unknown"


def test_full_clone_does_not_wait(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    world.advance()
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=5")
    assert base == "unknown"
    assert elapsed < 3


def test_ci_does_not_wait(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=True)
    _work_commit(repo)
    world.advance()
    base, elapsed, _ = _hook(repo, tmp_path, "--base-wait-seconds=5", env=_env(tmp_path, GITHUB_ACTIONS="true"))
    assert base == "unknown"
    assert elapsed < 3


def test_no_origin_remote_keeps_the_tracking_ref(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    _work_commit(repo)
    _git(repo, "remote", "remove", "origin")
    _git(repo, "update-ref", "refs/remotes/origin/main", world.fork)
    base, _, _ = _hook(repo, tmp_path)
    assert base == world.fork


def test_no_timeout_binary_skips_the_live_read(tmp_path: Path) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    _work_commit(repo)
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("git", "dirname", "env", "sleep", "bash", "sh"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    assert not (tools / "timeout").exists()
    trace = tmp_path / "trace.txt"
    base, _, _ = _hook(repo, tmp_path, env={"PATH": str(tools), "HOME": str(tmp_path), "GIT_TRACE": str(trace)})
    assert base == _git(repo, "merge-base", "HEAD", "origin/main")
    assert "ls-remote" not in trace.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["61", "-1", "x", "08"])
def test_invalid_wait_values_keep_a_well_formed_line(tmp_path: Path, value: str) -> None:
    world = World(tmp_path)
    repo = world.clone("c", shallow=False)
    live = world.advance()
    _git(repo, "fetch", "-q", "origin", "+refs/heads/main:refs/heads/live")
    _git(repo, "checkout", "-q", "live")
    base, elapsed, stderr = _hook(repo, tmp_path, f"--base-wait-seconds={value}")
    assert stderr.count("\n") == 1
    assert base == live
    assert elapsed < 8
