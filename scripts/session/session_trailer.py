"""Claude-Session trailer attribution: stamp, commit check and merge-time set equality (rec-5004).

Leaf module: standard library only, never importing scripts.telemetry_verify (its package pulls the reader stack, far
too heavy for a commit hook or the merge-gate fixture project). The parse mirrors the arrival oracle's byte for byte;
tests/test_session_session_trailer.py pins the parity.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

TRAILER = re.compile(r"^Claude-Session:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_PREFIXES = ("session_", "cse_")
_ENV_VAR = "CLAUDE_CODE_REMOTE_SESSION_ID"
_URL = "https://claude.ai/code/session_"
_SEQUENCER_PATHS = ("rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD")


class TrailerReadError(Exception):
    """A git read the attribution check needs failed."""


def normalise(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value or None


def trailer_keys(message: str) -> list[str]:
    keys = (normalise(url.rstrip("/").rsplit("/", 1)[-1]) for url in TRAILER.findall(message))
    return [k for k in keys if k]


def own_key(environ: Mapping[str, str]) -> str | None:
    return normalise(environ.get(_ENV_VAR, "").strip())


def trailer_line(key: str) -> str:
    return f"Claude-Session: {_URL}{key}"


def foreign_to_session(message: str, environ: Mapping[str, str]) -> list[str] | None:
    own = own_key(environ)
    if own is None:
        return None
    foreign: list[str] = []
    for key in trailer_keys(message):
        if key != own and key not in foreign:
            foreign.append(key)
    return foreign


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
    )


def branch_keys(root: Path, base: str, head: str) -> set[str]:
    result = _git(root, "log", "--format=%B%x1e", f"{base}..{head}")
    if result.returncode != 0:
        raise TrailerReadError(result.stderr.strip()[:300] or f"git log {base}..{head} exited {result.returncode}")
    return {key for chunk in result.stdout.split("\x1e") for key in trailer_keys(chunk)}


def mismatch(message: str, branch: set[str]) -> tuple[list[str], list[str]]:
    keys = set(trailer_keys(message))
    return sorted(keys - branch), sorted(branch - keys)


def _expected_lines(branch: set[str]) -> list[str]:
    return [trailer_line(k) for k in sorted(branch)]


def premerge_attribution(*, root: Path, base: str, message: str) -> bool:
    try:
        branch = branch_keys(root, base, "HEAD")
    except (TrailerReadError, OSError, subprocess.SubprocessError) as exc:
        print(f"PREMERGE ATTRIBUTION UNAVAILABLE {exc}")
        return False
    extra, missing = mismatch(message, branch)
    if not branch and not extra:
        return True
    if not extra and not missing:
        print("PREMERGE ATTRIBUTION OK")
        return True
    print(f"PREMERGE ATTRIBUTION MISMATCH extra={extra} missing={missing}")
    print("  remedy: the squash message must carry exactly these lines:")
    for line in _expected_lines(branch) or ["(none)"]:
        print(f"  {line}")
    return False


def gate_denial(*, root: Path, base_ref: str, head_ref: str, message: str) -> str | None:
    try:
        found = _git(root, "merge-base", base_ref, head_ref)
        if found.returncode != 0 or not found.stdout.strip():
            return f"attribution UNAVAILABLE: no merge-base of {base_ref} and {head_ref}"
        branch = branch_keys(root, found.stdout.strip(), head_ref)
    except (TrailerReadError, OSError, subprocess.SubprocessError) as exc:
        return f"attribution UNAVAILABLE: {exc}"
    extra, missing = mismatch(message, branch)
    if not extra and not missing:
        return None
    lines = "; ".join(_expected_lines(branch)) or "none"
    return (
        "attribution: commit_message Claude-Session trailers must equal the PR branch commits' "
        f"(extra {extra}, missing {missing}); use exactly: {lines}"
    )


def _sequencer_active(root: Path) -> bool:
    for name in _SEQUENCER_PATHS:
        result = _git(root, "rev-parse", "--git-path", name)
        if result.returncode == 0 and (root / result.stdout.strip()).exists():
            return True
    return False


def stamp(path: Path, environ: Mapping[str, str], root: Path) -> None:
    own = own_key(environ)
    if own is None or _sequencer_active(root):
        return
    text = path.read_text(encoding="utf-8")
    kept = [ln for ln in text.splitlines(keepends=True) if not TRAILER.match(ln.rstrip("\r\n"))]
    path.write_text("".join(kept), encoding="utf-8")
    result = _git(root, "interpret-trailers", "--in-place", "--trailer", trailer_line(own), str(path))
    if result.returncode != 0:
        raise OSError(result.stderr.strip()[:300] or "git interpret-trailers failed")


def _commit_msg(path: Path, environ: Mapping[str, str], root: Path) -> int:
    if _sequencer_active(root):
        return 0
    foreign = foreign_to_session(path.read_text(encoding="utf-8"), environ)
    if not foreign:
        return 0
    own = own_key(environ) or ""
    sys.stderr.write(f"Claude-Session trailer names another session ({', '.join(foreign)}), not this one.\n")
    sys.stderr.write(f"replace the trailer with:\n{trailer_line(own)}\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("prepare-commit-msg", "commit-msg"):
        sys.stderr.write("usage: session_trailer (prepare-commit-msg|commit-msg) <message-file> [source] [sha]\n")
        return 2
    verb, path, root = args[0], Path(args[1]), Path.cwd()
    try:
        if verb == "prepare-commit-msg":
            stamp(path, os.environ, root)
            return 0
        return _commit_msg(path, os.environ, root)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"session_trailer {verb}: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
