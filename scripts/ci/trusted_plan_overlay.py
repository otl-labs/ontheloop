#!/usr/bin/env python3
"""Overlay a PR's terraform/personal onto main's checkout for the trusted plan (Decision 238 cl.3).

Run by path with `python3 -I` from the repo root, before any credential exists. Inputs PR_NUMBER and HEAD_SHA come from
env. Fetches +refs/pull/<n>/merge (a public read), then replaces terraform/personal in the working tree with the merge
tree's copy and restores the main-only files from the checked-out main commit. It never runs anything from the PR: git
is invoked with fixed argv, no shell. Prints one line, OVERLAY_OK or "OVERLAY_REFUSED <rule> [<path>]", where a path is
printed only after it passed the file-name rule.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(_ROOT))

from scripts.ci import terraform_hcl_screen as screen  # noqa: E402

PERSONAL = "terraform/personal"
MERGE_REF = "refs/trusted/pr-merge"
NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
HEX40 = re.compile(r"[0-9a-f]{40}")
PR_RE = re.compile(r"[0-9]{1,9}")
CONFIG_FILES = ("config/feature_flags.yaml", "config/lambda/ducklake/version.yaml")


class Refusal(Exception):
    def __init__(self, rule: str, path: str | None = None) -> None:
        super().__init__(rule)
        self.rule = rule
        self.path = path


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=check, env=env, timeout=300)


def _ok(repo: Path, *args: str) -> bool:
    return _git(repo, *args, check=False).returncode == 0


def _out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.decode("utf-8", errors="replace").strip()


def _entries(repo: Path, merge: str) -> list[tuple[str, str, str]]:
    raw = _git(repo, "ls-tree", "-z", f"{merge}:{PERSONAL}").stdout
    entries = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, _, name = rec.partition(b"\t")
        mode, kind, _sha = meta.decode("ascii", errors="replace").split()
        entries.append((mode, kind, name.decode("utf-8", errors="replace")))
    return entries


def overlay(repo: Path, pr_number: str, head_sha: str, remote: str = "origin") -> None:
    if not PR_RE.fullmatch(pr_number) or not HEX40.fullmatch(head_sha):
        raise Refusal("bad_inputs")
    if not _ok(repo, "fetch", "--no-tags", "--quiet", remote, f"+refs/pull/{pr_number}/merge:{MERGE_REF}"):
        raise Refusal("merge_ref_absent")
    merge = _out(repo, "rev-parse", MERGE_REF)
    parents = _out(repo, "rev-list", "--parents", "-n", "1", merge).split()[1:]
    if len(parents) != 2 or parents[1] != head_sha:
        raise Refusal("merge_second_parent")
    main_sha = _out(repo, "rev-parse", "HEAD")
    if not _ok(repo, "merge-base", "--is-ancestor", parents[0], main_sha):
        raise Refusal("base_not_ancestor_of_main")
    entries = _entries(repo, merge)
    for mode, kind, name in entries:
        if not NAME_RE.fullmatch(name):
            raise Refusal("file_name")
    for mode, kind, name in entries:
        if kind == "tree" or mode == "040000":
            raise Refusal("subdirectory", name)
        if name.startswith(".git"):
            raise Refusal("git_metadata_file", name)
        if mode != "100644":
            raise Refusal("file_mode", name)
    protected = {f"{PERSONAL}/{n}": n for n in screen.MAIN_ONLY_FILES} | {p: p for p in CONFIG_FILES}
    changed = _git(repo, "diff", "--name-only", "--no-renames", "-z", parents[0], merge).stdout.split(b"\0")
    for path in sorted(c.decode("utf-8", errors="replace") for c in changed if c):
        if path in protected:
            raise Refusal("main_only_file_changed", protected[path])
    shutil.rmtree(repo / PERSONAL)
    _git(repo, "checkout", merge, "--", PERSONAL)
    for name in sorted(screen.MAIN_ONLY_FILES):
        target = repo / PERSONAL / name
        if _ok(repo, "cat-file", "-e", f"HEAD:{PERSONAL}/{name}"):
            _git(repo, "checkout", "HEAD", "--", f"{PERSONAL}/{name}")
        elif target.exists():
            target.unlink()


def main() -> int:
    if not Path(screen.__file__).resolve().is_relative_to(_ROOT):
        print("OVERLAY_REFUSED scripts_package_location")
        return 2
    try:
        overlay(Path.cwd(), os.environ.get("PR_NUMBER", ""), os.environ.get("HEAD_SHA", ""))
    except Refusal as exc:
        print(f"OVERLAY_REFUSED {exc.rule}" + (f" {exc.path}" if exc.path else ""))
        return 1
    except (subprocess.SubprocessError, OSError):
        print("OVERLAY_REFUSED git_error")
        return 1
    print("OVERLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
