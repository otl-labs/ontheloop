#!/usr/bin/env python3
"""PreToolUse adapter for the merge_pull_request seam (Decision 203 hook-adapter pattern; Decision on merge overlap).

GitHub-specific plumbing only: repository identity, PR resolution, fetch. The first predicate is
scripts.session.merge_overlap (refs only); later predicates (rec-4080, rec-4755, rec-4451) extend this seam.
Fails closed: exit 2 on overlap, an unavailable verdict, a failed fetch, malformed input. No override exists.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_TOOL = re.compile(r"^mcp__github(-full)?__(merge_pull_request|enable_pr_auto_merge)$")
_SHA = re.compile(r"^[0-9a-fA-F]{4,40}$")
_SCP = re.compile(r"^[^/@:]+@[^:/]+:(.+)$")
_FETCH_TIMEOUT = 40

_OVERLAP_MSG = (
    "merge-gate: overlap: OVERLAP: main changed {files} since this PR's base {base} ({behind} commits behind). "
    "Update the branch (mcp__github__update_pull_request_branch), `git pull --ff-only` the updated head so premerge "
    "and expectedHeadSha name it, wait for the CI-green wake on the new head (subscribe_pr_activity, never poll), then "
    "merge; if the update reports a conflict, rebase locally with scripts.session.rebase_resolve per git-ops "
    "commit_flow_time."
)


def _deny(reason: str) -> int:
    sys.stderr.write(f"merge-gate: {reason}\n")
    return 2


def _git(root: Path, *args: str, timeout: int = _FETCH_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def _project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def parse_origin(url: str) -> tuple[str, str] | None:
    url = url.strip()
    if not url:
        return None
    if "://" in url:
        path = urlparse(url).path
    else:
        match = _SCP.match(url)
        if not match:
            return None
        path = match.group(1)
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    repo = repo[:-4] if repo.endswith(".git") else repo
    return (owner, repo) if owner and repo else None


def _pull_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value > 0:
        return value
    return None


def _fetch(root: Path, number: int, *extra: str) -> subprocess.CompletedProcess[str]:
    return _git(
        root,
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        *extra,
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        f"+refs/pull/{number}/head:refs/merge-overlap/pr/{number}",
    )


def _judge(root: Path, number: int, expected: str | None) -> int:
    from scripts.session import merge_overlap  # noqa: PLC0415

    ref = f"refs/merge-overlap/pr/{number}"
    fetched = _fetch(root, number)
    if fetched.returncode != 0:
        return _deny(f"fetch failed (unavailable): {fetched.stderr.strip()[:300]}")
    if expected is not None:
        head = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
        want = _git(root, "rev-parse", "--verify", f"{expected}^{{commit}}")
        if want.returncode != 0 or want.stdout.strip() != head:
            resolved = want.stdout.strip() or "nothing"
            return _deny(f"expectedHeadSha {expected} (resolves to {resolved}) differs from the fetched PR head {head}")
    verdict = merge_overlap.overlap(root, ref, "refs/remotes/origin/main")
    if (
        verdict.kind == merge_overlap.UNAVAILABLE
        and _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    ):
        if _fetch(root, number, "--deepen=500").returncode == 0:
            verdict = merge_overlap.overlap(root, ref, "refs/remotes/origin/main")
    if verdict.kind == merge_overlap.CLEAR:
        return 0
    if verdict.kind == merge_overlap.OVERLAP:
        sys.stderr.write(
            _OVERLAP_MSG.format(files=", ".join(verdict.files), base=verdict.merge_base[:12], behind=verdict.behind) + "\n"
        )
        return 2
    return _deny(f"UNAVAILABLE: {verdict.reason}")


def run(raw: str) -> int:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return _deny(f"malformed JSON payload on a merge tool (unavailable): {exc}")
    if not isinstance(payload, dict):
        return _deny("payload is not an object (unavailable)")
    name = payload.get("tool_name")
    if not isinstance(name, str) or not _TOOL.match(name):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _deny("tool_input missing (unavailable)")
    root = _project_root()
    try:
        url = _git(root, "config", "--get", "remote.origin.url")
        origin = parse_origin(url.stdout) if url.returncode == 0 else None
        if origin is None:
            return _deny("UNAVAILABLE: this checkout's origin is missing or unparseable")
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        if not (isinstance(owner, str) and isinstance(repo, str)):
            return _deny("owner/repo missing from tool_input (unavailable)")
        if (owner.casefold(), repo.casefold()) != (origin[0].casefold(), origin[1].casefold()):
            return 0
        number = _pull_number(tool_input.get("pullNumber"))
        if number is None:
            return _deny(f"pullNumber {tool_input.get('pullNumber')!r} is not a positive integer (malformed)")
        expected = tool_input.get("expectedHeadSha")
        if expected is not None and not (isinstance(expected, str) and _SHA.match(expected)):
            return _deny(f"expectedHeadSha {expected!r} is malformed (need 4-40 hex digits)")
        try:
            return _judge(root, number, expected)
        finally:
            _git(root, "update-ref", "-d", f"refs/merge-overlap/pr/{number}")
    except (OSError, subprocess.SubprocessError) as exc:
        return _deny(f"unavailable: {exc}")


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":
    sys.exit(main())
