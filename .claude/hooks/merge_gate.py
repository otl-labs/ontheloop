#!/usr/bin/env python3
"""PreToolUse adapter for the merge_pull_request seam (Decision 203 hook-adapter pattern; Decision on merge overlap).

GitHub-specific plumbing only: repository identity, PR resolution, fetch, anonymous public REST reads. The first
predicate is scripts.session.merge_overlap (refs only). The second, scripts.session.session_trailer.gate_denial,
requires a merge_pull_request commit_message's Claude-Session trailer keys to equal the PR branch commits' (rec-5004).
The third, scripts.session.merge_checks (rec-4080, Decision 255), requires every required check's newest GitHub
Actions run to have succeeded on the exact head, under one 40 s deadline (rec-4891). Later predicates (rec-4755,
rec-4451) extend this seam.
Fails closed: exit 2 on overlap, an attribution mismatch, a red or unknown check, an unavailable verdict, a failed
fetch, a spent deadline, malformed input. No override exists.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.session import merge_checks  # noqa: E402

_TOOL = re.compile(r"^mcp__github(-full)?__(merge_pull_request|enable_pr_auto_merge)$")
_SHA = re.compile(r"^[0-9a-fA-F]{4,40}$")
_SCP = re.compile(r"^[^/@:]+@[^:/]+:(.+)$")
_BUDGET = 40.0
_CLEANUP_RESERVE = 2.0
_GIT_CAP = 25.0
_GET_CAP = 4.0
_BODY_CAP = 1 << 20
_FULL_PAGE = 100
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_clock = time.monotonic
_urlopen = urllib.request.urlopen

_OVERLAP_MSG = (
    "merge-gate: overlap: OVERLAP: main changed {files} since this PR's base {base} ({behind} commits behind). "
    "Update the branch (mcp__github__update_pull_request_branch), `git pull --ff-only` the updated head so premerge "
    "and expectedHeadSha name it, wait for the CI-green wake on the new head (subscribe_pr_activity, never poll), then "
    "merge; if the update reports a conflict, rebase locally with scripts.session.rebase_resolve per git-ops "
    "commit_flow_time."
)


class _Unavailable(Exception):
    pass


class _Deadline:
    def __init__(self, seconds: float = _BUDGET) -> None:
        self.end = _clock() + seconds

    def left(self, step: str) -> float:
        remaining = self.end - _clock()
        if remaining <= 0:
            raise _Unavailable(f"deadline spent ({step})")
        return remaining


def _deny(reason: str) -> int:
    sys.stderr.write(f"merge-gate: {reason}\n")
    return 2


def _exec(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _git(root: Path, *args: str, dl: _Deadline, step: str = "git") -> subprocess.CompletedProcess[str]:
    return _exec(["git", *args], root, min(_GIT_CAP, dl.left(step)))


def _cleanup(root: Path, number: int) -> None:
    try:
        _exec(["git", "update-ref", "-d", f"refs/merge-overlap/pr/{number}"], root, _CLEANUP_RESERVE)
    except (OSError, subprocess.SubprocessError):
        pass


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


def _fetch(root: Path, number: int, dl: _Deadline, *extra: str) -> subprocess.CompletedProcess[str]:
    return _git(
        root,
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        *extra,
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        f"+refs/pull/{number}/head:refs/merge-overlap/pr/{number}",
        dl=dl,
        step="fetch",
    )


def _required(rules: object) -> set[str] | None:
    if not isinstance(rules, list):
        raise _Unavailable("rules/branches/main did not return a list")
    if len(rules) >= _FULL_PAGE:
        raise _Unavailable("rules/branches/main read is partial (a full page of rules)")
    found: set[str] | None = None
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            found = found if found is not None else set()
            params = rule.get("parameters")
            for check in (params.get("required_status_checks") or []) if isinstance(params, dict) else []:
                if isinstance(check, dict) and isinstance(check.get("context"), str):
                    found.add(check["context"])
    return found


def _origin_api(root: Path, owner: str, repo: str, dl: _Deadline) -> tuple[str, str]:
    out = _git(root, "ls-remote", "--get-url", "origin", dl=dl, step="origin url")
    url = out.stdout.strip()
    if out.returncode != 0 or not url:
        raise _Unavailable("origin's resolved fetch URL could not be read")
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return "https", f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    if parsed.scheme == "file":
        return "file", unquote(parsed.path)
    raise _Unavailable(f"origin scheme {parsed.scheme or 'none'!r} is neither http(s) nor file")


def _get_json(api: tuple[str, str], rel: str, label: str, dl: _Deadline) -> object:
    mode, base = api
    if mode == "file":
        seam = {"rules": "rules-branches-main.json"}.get(label, rel)
        try:
            return json.loads((Path(base) / "merge-gate-api" / seam).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _Unavailable(f"{label} read failed: {exc}") from exc
    url = f"{base}/{rel}"
    get_end = min(_clock() + _GET_CAP, dl.end)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "merge-gate"})
    try:
        with _urlopen(req, timeout=min(_GET_CAP, dl.left(label))) as resp:
            if getattr(resp, "status", 200) != 200:
                raise _Unavailable(f"{label} ({rel}) answered {resp.status}")
            body = b""
            while True:
                if _clock() >= get_end:
                    raise _Unavailable(f"deadline spent ({label} body)")
                chunk = resp.read(65536)
                if not chunk:
                    break
                body += chunk
                if len(body) > _BODY_CAP:
                    raise _Unavailable(f"{label} ({rel}) body exceeds 1 MiB")
    except urllib.error.HTTPError as exc:
        raise _Unavailable(f"{label} ({rel}) answered {exc.code}") from exc
    except (urllib.error.URLError, http.client.HTTPException, socket.timeout, OSError) as exc:
        raise _Unavailable(f"{label} ({rel}) read failed: {exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise _Unavailable(f"{label} ({rel}) returned unparseable JSON") from exc


def _checks_verdict(root: Path, owner: str, repo: str, sha: str, dl: _Deadline) -> merge_checks.Verdict:
    try:
        api = _origin_api(root, owner, repo, dl)
        required = _required(_get_json(api, "rules/branches/main?per_page=100", "rules", dl))
        rel = f"commits/{sha}/check-runs?per_page=100&filter=all"
        body = _get_json(api, rel if api[0] == "https" else f"check-runs/{sha}.json", "check-runs", dl)
        if not isinstance(body, dict) or not isinstance(body.get("check_runs"), list):
            raise _Unavailable("check-runs answer has no check_runs list")
        runs = [r for r in body["check_runs"] if isinstance(r, dict)]
        total = body.get("total_count")
        return merge_checks.verdict(required, runs, total if isinstance(total, int) else None)
    except _Unavailable as exc:
        return merge_checks.Verdict(merge_checks.UNAVAILABLE, (str(exc),))
    except (OSError, subprocess.SubprocessError) as exc:
        return merge_checks.Verdict(merge_checks.UNAVAILABLE, (f"read failed: {exc}",))


def read_checks_verdict(root: Path, owner: str, repo: str, sha: str, seconds: float = 10.0) -> merge_checks.Verdict:
    return _checks_verdict(root, owner, repo, sha, _Deadline(seconds))


def _checks_denial(root: Path, owner: str, repo: str, head: str, dl: _Deadline) -> int:
    verdict = _checks_verdict(root, owner, repo, head, dl)
    if verdict.kind == merge_checks.CLEAR:
        return 0
    why = "; ".join(verdict.reasons)
    if verdict.kind == merge_checks.REFUSE:
        return _deny(
            f"checks: REFUSE: {why} on {head[:12]}. Fix the check with a new push and wait for its CI-green wake; "
            "never merge past a red required check (Decision 255)"
        )
    return _deny(f"checks: UNAVAILABLE: {why}; retry the merge later, or the operator merges in the GitHub UI")


@dataclass(frozen=True)
class _Call:
    owner: str
    repo: str
    number: int
    expected: str | None
    message: str | None
    is_merge: bool


def _judge(root: Path, call: _Call, dl: _Deadline) -> int:
    from scripts.session import merge_overlap  # noqa: PLC0415

    ref = f"refs/merge-overlap/pr/{call.number}"
    fetched = _fetch(root, call.number, dl)
    if fetched.returncode != 0:
        return _deny(f"fetch failed (unavailable): {fetched.stderr.strip()[:300]}")
    head = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}", dl=dl, step="head").stdout.strip()
    if call.expected is not None:
        want = _git(root, "rev-parse", "--verify", f"{call.expected}^{{commit}}", dl=dl, step="expectedHeadSha")
        if want.returncode != 0 or want.stdout.strip() != head:
            resolved = want.stdout.strip() or "nothing"
            return _deny(f"expectedHeadSha {call.expected} (resolves to {resolved}) differs from the fetched PR head {head}")
    dl.left("overlap")
    verdict = merge_overlap.overlap(root, ref, "refs/remotes/origin/main")
    if (
        verdict.kind == merge_overlap.UNAVAILABLE
        and _git(root, "rev-parse", "--is-shallow-repository", dl=dl, step="shallow").stdout.strip() == "true"
    ):
        if _fetch(root, call.number, dl, "--deepen=500").returncode == 0:
            dl.left("overlap")
            verdict = merge_overlap.overlap(root, ref, "refs/remotes/origin/main")
    if verdict.kind == merge_overlap.CLEAR:
        if call.message is not None:
            from scripts.session import session_trailer  # noqa: PLC0415

            dl.left("trailer")
            denial = session_trailer.gate_denial(
                root=root, base_ref="refs/remotes/origin/main", head_ref=ref, message=call.message
            )
            if denial is not None:
                return _deny(denial)
        if call.is_merge and call.expected is None:
            return _deny(
                "checks: expectedHeadSha is required: merge naming the head whose CI-green wake you received "
                f"(expectedHeadSha={head})"
            )
        if not _HEX40.match(head):
            return _deny(f"checks: UNAVAILABLE: fetched head {head!r} is not a 40-hex sha")
        return _checks_denial(root, call.owner, call.repo, head, dl)
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
    dl = _Deadline()
    number: int | None = None
    try:
        url = _git(root, "config", "--get", "remote.origin.url", dl=dl, step="origin")
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
        message = tool_input.get("commit_message")
        if message is not None and not isinstance(message, str):
            return _deny(f"commit_message {type(message).__name__} is not a string (malformed)")
        call = _Call(origin[0], origin[1], number, expected, message, name.endswith("merge_pull_request"))
        return _judge(root, call, dl)
    except _Unavailable as exc:
        return _deny(f"UNAVAILABLE: {exc}")
    except (OSError, subprocess.SubprocessError) as exc:
        return _deny(f"unavailable: {exc}")
    finally:
        if number is not None:
            _cleanup(root, number)


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":
    sys.exit(main())
