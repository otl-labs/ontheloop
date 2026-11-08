"""Test support for the protected-action gate's end-to-end tests: the fixture git repo builder and the JSONL transcript
writer (the tests/fixtures/merge_overlap_repos.py precedent). Test-only."""

from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any

REAL_ROOT = Path(__file__).resolve().parents[2]
THREAD_TS = "cmsg_THREAD"
_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **_ENV},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def make_repo(tmp: Path, *, contract: bool = True) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    if contract:
        (repo / "docs/contracts").mkdir(parents=True)
        src = REAL_ROOT / "docs/contracts/protected-actions.yaml"
        (repo / "docs/contracts/protected-actions.yaml").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "config").mkdir(exist_ok=True)
    (repo / "config/feature_flags.yaml").write_text("flags: {}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".terraform/\n", encoding="utf-8")
    for root in ("bootstrap", "github", "personal"):
        (repo / "terraform" / root).mkdir(parents=True)
        (repo / "terraform" / root / "main.tf").write_text("# root\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def wake(text: str, *, ts: str = THREAD_TS, sender: str = "human", trust: str = "principal", wrap: str = "") -> str:
    inner = f'<message trigger="true" from="{sender}" trust="{trust}" author="Dana" id="cmsg_1">{html.escape(text)}</message>'
    if wrap:
        inner = f"<{wrap}>{inner}</{wrap}>"
    return (
        '<wake reason="mention" current-time="2026-11-07T00:00:00Z"><project id="chan_1" type="project">'
        f'<thread ts="{ts}">{inner}</thread></project></wake>'
    )


def user(content: Any, pid: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "promptId": pid,
        "message": {"role": "user", "content": content},
        "origin": {"kind": "human"},
        **extra,
    }


def assistant_call(command: str, tool_id: str, *, cwd: str | None = None, sidechain: bool = False) -> dict[str, Any]:
    block = {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}
    rec: dict[str, Any] = {"type": "assistant", "message": {"role": "assistant", "content": [block]}}
    if cwd:
        rec["cwd"] = cwd
    if sidechain:
        rec["isSidechain"] = True
    return rec


def write_transcript(path: Path, records: list[dict[str, Any]], *, tail: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + tail, encoding="utf-8")
    return path
