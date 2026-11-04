"""Overlay of a PR's terraform/personal onto main for the trusted plan (Decision 238 cl.3, PLAN-trusted-plan-cutover PR 2).

Runs the real script by path under `python3 -I` against temporary git repositories, never the network.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "ci" / "trusted_plan_overlay.py"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
P = "terraform/personal"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=GIT_ENV, check=True)
    return done.stdout.strip()


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(root: Path, msg: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


class Scene:
    """origin repo with a base commit, a PR merge ref, and a work clone checked out at (possibly newer) main."""

    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin"
        self.work = tmp / "work"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        for name, text in {
            "a.tf": "# a-main\n",
            "old.tf": "# old\n",
            "providers.tf": "# providers-base\n",
            ".terraform.lock.hcl": "# lock-base\n",
            "backend-sandbox.hcl": "# backend-base\n",
        }.items():
            put(self.origin, f"{P}/{name}", text)
        put(self.origin, "config/feature_flags.yaml", "a: 1\n")
        put(self.origin, "config/lambda/ducklake/version.yaml", "duckdb_version: 1\n")
        self.base = commit_all(self.origin, "base")
        self.head_sha = ""

    def pr(self, mutate) -> None:
        git(self.origin, "checkout", "-q", "-b", "pr", self.base)
        mutate(self.origin)
        self.head_sha = commit_all(self.origin, "pr")
        git(self.origin, "checkout", "-q", "main")
        git(self.origin, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        git(self.origin, "update-ref", "refs/pull/7/merge", "HEAD")
        git(self.origin, "reset", "-q", "--hard", self.base)

    def advance_main(self) -> None:
        put(self.origin, f"{P}/providers.tf", "# providers-newer-on-main\n")
        commit_all(self.origin, "main moves")

    def clone(self, ref: str = "main") -> None:
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.work)], check=True, env=GIT_ENV)
        git(self.work, "checkout", "-q", ref)

    def run(self, head_sha: str | None = None, pr: str = "7") -> subprocess.CompletedProcess[str]:
        env = {**GIT_ENV, "PR_NUMBER": pr, "HEAD_SHA": head_sha or self.head_sha}
        return subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=self.work, env=env, capture_output=True, text=True)

    def text(self, rel: str) -> str:
        return (self.work / rel).read_text(encoding="utf-8")


@pytest.fixture()
def scene(tmp_path: Path) -> Scene:
    return Scene(tmp_path)


def test_clean_pr_overlays_merge_tree_and_keeps_main_only_files_from_main(scene: Scene) -> None:
    def mutate(root: Path) -> None:
        put(root, f"{P}/a.tf", "# a-pr\n")
        put(root, f"{P}/new.tf", "# new\n")
        (root / P / "old.tf").unlink()

    scene.pr(mutate)
    scene.advance_main()
    scene.clone()
    out = scene.run()
    assert (out.returncode, out.stdout.strip()) == (0, "OVERLAY_OK"), out.stderr
    assert scene.text(f"{P}/a.tf") == "# a-pr\n"
    assert (scene.work / P / "new.tf").exists()
    assert not (scene.work / P / "old.tf").exists()
    assert scene.text(f"{P}/providers.tf") == "# providers-newer-on-main\n"
    assert scene.text(f"{P}/.terraform.lock.hcl") == "# lock-base\n"


def test_untracked_leftovers_in_the_directory_are_removed(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# a-pr\n"))
    scene.clone()
    put(scene.work, f"{P}/leftover.tf", "# stray\n")
    assert scene.run().stdout.strip() == "OVERLAY_OK"
    assert not (scene.work / P / "leftover.tf").exists()


def test_unicode_digit_pr_number_refused(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# x\n"))
    scene.clone()
    assert scene.run(pr="\u00b2").stdout.strip() == "OVERLAY_REFUSED bad_inputs"
    assert scene.run(head_sha=scene.head_sha + "\n").stdout.strip() == "OVERLAY_REFUSED bad_inputs"


def test_missing_merge_ref(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# x\n"))
    scene.clone()
    out = scene.run(pr="8")
    assert out.returncode == 1
    assert out.stdout.strip() == "OVERLAY_REFUSED merge_ref_absent"


def test_wrong_second_parent(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# x\n"))
    scene.clone()
    assert scene.run(head_sha="0" * 40).stdout.strip() == "OVERLAY_REFUSED merge_second_parent"


def test_bad_inputs(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# x\n"))
    scene.clone()
    assert scene.run(head_sha="nothex").stdout.strip() == "OVERLAY_REFUSED bad_inputs"
    assert scene.run(pr="7; rm -rf /").stdout.strip() == "OVERLAY_REFUSED bad_inputs"


def test_first_parent_not_ancestor_of_checked_out_main(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/a.tf", "# x\n"))
    git(scene.origin, "checkout", "-q", "--orphan", "other")
    put(scene.origin, "unrelated.txt", "x\n")
    commit_all(scene.origin, "unrelated")
    scene.clone(ref="other")
    assert scene.run().stdout.strip() == "OVERLAY_REFUSED base_not_ancestor_of_main"


def _symlink(root: Path) -> None:
    (root / P / "link.tf").symlink_to("a.tf")


def _gitlink(root: Path) -> None:
    sub = root / P / "sub"
    sub.mkdir()
    git(sub, "init", "-q")
    put(sub, "f", "x\n")
    git(sub, "add", "-A")
    git(sub, "commit", "-q", "-m", "inner")


def _executable(root: Path) -> None:
    put(root, f"{P}/run.tf", "# x\n")
    (root / P / "run.tf").chmod(0o755)


def _subdir(root: Path) -> None:
    put(root, f"{P}/nested/x.tf", "# x\n")


@pytest.mark.parametrize(
    ("mutate", "rule", "name"),
    [
        (_symlink, "file_mode", "link.tf"),
        (_gitlink, "file_mode", "sub"),
        (_executable, "file_mode", "run.tf"),
        (_subdir, "subdirectory", "nested"),
    ],
)
def test_unsafe_entry_shapes(scene: Scene, mutate, rule: str, name: str) -> None:
    scene.pr(mutate)
    scene.clone()
    out = scene.run()
    assert out.returncode == 1
    assert out.stdout.strip() == f"OVERLAY_REFUSED {rule} {name}"
    assert scene.text(f"{P}/old.tf") == "# old\n"


def test_name_outside_rule_is_refused_before_anything_is_printed(scene: Scene) -> None:
    scene.pr(lambda root: put(root, f"{P}/bad name$(x).tf", "# x\n"))
    scene.clone()
    out = scene.run()
    assert out.stdout.strip() == "OVERLAY_REFUSED file_name"
    assert "bad" not in out.stdout + out.stderr


@pytest.mark.parametrize(
    "rel",
    [
        f"{P}/providers.tf",
        f"{P}/.terraform.lock.hcl",
        f"{P}/backend-sandbox.hcl",
        "config/feature_flags.yaml",
        "config/lambda/ducklake/version.yaml",
    ],
)
def test_pr_change_to_main_only_or_config_file_refused(scene: Scene, rel: str) -> None:
    scene.pr(lambda root: put(root, rel, "# changed by the PR\n"))
    scene.clone()
    out = scene.run()
    assert out.returncode == 1
    assert out.stdout.startswith("OVERLAY_REFUSED main_only_file_changed")
    assert scene.text(f"{P}/a.tf") == "# a-main\n"


def test_nothing_from_the_pr_is_executed(scene: Scene, tmp_path: Path) -> None:
    marker = tmp_path / "ran"

    def mutate(root: Path) -> None:
        put(root, f"{P}/hook.sh", f"#!/bin/sh\ntouch {marker}\n")
        put(root, f"{P}/a.tf", "# a-pr\n")

    scene.pr(mutate)
    scene.clone()
    assert scene.run().stdout.strip() == "OVERLAY_OK"
    assert not marker.exists()
    assert (scene.work / P / "hook.sh").exists()


@pytest.mark.parametrize("name", [".gitattributes", ".gitmodules", ".gitignore"])
def test_git_metadata_files_refused_so_no_filter_can_fire(scene: Scene, tmp_path: Path, name: str) -> None:
    marker = tmp_path / "ran"

    def mutate(root: Path) -> None:
        put(root, f"{P}/{name}", "*.tf filter=evil\n")
        put(root, f"{P}/a.tf", "# a-pr\n")

    scene.pr(mutate)
    scene.clone()
    git(scene.work, "config", "filter.evil.smudge", f"touch {marker}")
    out = scene.run()
    assert out.stdout.strip() == f"OVERLAY_REFUSED git_metadata_file {name}"
    assert not marker.exists()
    assert scene.text(f"{P}/a.tf") == "# a-main\n"
