"""The capture pin hook: one stderr line with the checkout facts, empty stdout, exit 0 for every source, no file written."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "session_start_log_turn.sh"
SHA = "[0-9a-f]{40}(?:[0-9a-f]{24})?"
LINE = re.compile(
    rf"\[session_start_log_turn\] capture pin checkout=({SHA}|unknown) base=({SHA}|unknown) dirty=(true|false|unknown)\n"
)
GIT_ENV = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *GIT_ENV, "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30)
    return done.stdout.strip()


def _run(script: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)], stdin=subprocess.DEVNULL, cwd=cwd, capture_output=True, text=True, timeout=30, check=False
    )


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_every_source_prints_one_pin_line_and_writes_nothing(tmp_path: Path, source: str) -> None:
    env_bin = shutil.which("env")
    bash = shutil.which("bash")
    assert env_bin and bash
    stdin = json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "s", "transcript_path": "/x"})
    result = subprocess.run(
        [
            env_bin,
            "-i",
            f"PATH={Path(bash).parent}:/usr/bin",
            "HOME=" + str(tmp_path),
            bash,
            str(HOOK),
            "--project-ref=ontheloop",
        ],
        input=stdin,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (result.returncode, result.stdout) == (0, "")
    assert LINE.fullmatch(result.stderr)
    assert list(tmp_path.iterdir()) == []


def test_closed_stdin_and_no_arguments_still_exit_zero(tmp_path: Path) -> None:
    result = _run(HOOK, tmp_path)
    assert (result.returncode, result.stdout) == (0, "")
    assert LINE.fullmatch(result.stderr)
    assert list(tmp_path.iterdir()) == []


def test_the_hook_reads_no_stdin_and_runs_no_pass() -> None:
    body = HOOK.read_text(encoding="utf-8")
    commands = [line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not any("read " in c or "cat" in c.split() or "python" in c or "record_turn" in c or "<<" in c for c in commands)
    assert 'git -C "$repo" rev-parse HEAD' in body
    assert 'git -C "$repo" merge-base HEAD origin/main' in body
    assert 'git -C "$repo" --no-optional-locks status --porcelain --untracked-files=no' in body
    assert commands[-1] == "exit 0"
    assert body.count(">&2") == 1


def test_pin_line_carries_checkout_facts_from_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    script = repo / ".claude" / "hooks" / HOOK.name
    shutil.copy(HOOK, script)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", ".claude")
    _git(repo, "commit", "-q", "-m", "one")
    main_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "checkout", "-q", "-b", "work")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "two")
    head_sha = _git(repo, "rev-parse", "HEAD")
    assert head_sha != main_sha
    index = repo / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)

    clean = _run(script, tmp_path)
    assert (clean.returncode, clean.stdout) == (0, "")
    assert clean.stderr == f"[session_start_log_turn] capture pin checkout={head_sha} base={main_sha} dirty=false\n"

    (repo / "tracked.txt").write_text("three\n", encoding="utf-8")
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    dirty = _run(script, tmp_path)
    assert dirty.stderr == f"[session_start_log_turn] capture pin checkout={head_sha} base={main_sha} dirty=true\n"
    assert (index.read_bytes(), index.stat().st_mtime_ns) == index_before
    assert before[0] == index_before[0]

    elsewhere = tmp_path / "plain" / ".claude" / "hooks"
    elsewhere.mkdir(parents=True)
    shutil.copy(HOOK, elsewhere / HOOK.name)
    unknown = _run(elsewhere / HOOK.name, tmp_path)
    assert unknown.stderr == "[session_start_log_turn] capture pin checkout=unknown base=unknown dirty=unknown\n"
    assert unknown.returncode == 0
