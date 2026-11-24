"""The wording check's rehearsal clone: a throwaway clone whose origin/main already holds the plan PR.

The build's diff-keyed fast-tier gates meet a tree whose main holds the plan and the sidecar and whose diff holds the
build. The planner's scratch tree has the sidecar's blocks on a main that holds neither, so those gates misjudge it.
`rehearsal_clone` rebuilds the build's shape in a temp clone and never writes to the planner's tree or refs.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_VENV = ".venv"
_KEY = "weakening_dispositions"
_KEY_LINE = re.compile(rf"^{_KEY}:")
_TOP_LEVEL = re.compile(r"^[A-Za-z_#]")
_SIDECAR = "docs/plans/approved/{slug}.yaml"
_PLAN = "docs/plans/PLAN-{slug}.yaml"
_STALE = "matches no current candidate"
_HINT = (
    "; plan-time weakening_dispositions may name only candidates the sidecar's blocks create; "
    "record build-code candidates at build"
)
_COMMIT_FLAGS = [
    "-c",
    "user.name=plan-wording-check",
    "-c",
    "user.email=plan-wording-check@localhost",
    "-c",
    "commit.gpgsign=false",
]


class WordingCloneError(Exception):
    """The rehearsal clone could not be built, or a plan rule refused; the message is the reason."""


def _git(cwd: Path, *args: str, flags: list[str] | None = None, data: bytes | None = None) -> bytes:
    cmd = ["git", *(flags or []), *args]
    try:
        done = subprocess.run(cmd, cwd=cwd, input=data, capture_output=True, check=False)
    except OSError as exc:
        raise WordingCloneError(f"{' '.join(cmd)} could not start: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).decode("utf-8", "replace").strip()
        raise WordingCloneError(f"{' '.join(cmd)} failed: {detail}")
    return done.stdout


def strip_dispositions(plan_text: str) -> str:
    """plan_text without its top-level weakening_dispositions block; fails closed on any other change."""
    lines = plan_text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _KEY_LINE.match(line)), None)
    if start is None:
        return plan_text
    end = next((i for i in range(start + 1, len(lines)) if _TOP_LEVEL.match(lines[i])), len(lines))
    stripped = "".join(lines[:start] + lines[end:])
    try:
        before, after = yaml.safe_load(plan_text), yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise WordingCloneError(f"the plan cannot be parsed to strip {_KEY}: {exc}") from exc
    if not isinstance(before, dict) or {k: v for k, v in before.items() if k != _KEY} != after:
        raise WordingCloneError(f"stripping {_KEY} would change more than that key; refusing")
    return stripped


def plan_text_for(source: Path | None, slug: str, loaded: bool) -> str | None:
    """The loaded plan's text; None when no plan loaded or the file is unreadable; a file not named for slug is refused."""
    if source is None or not loaded:
        return None
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return None
    if source.name != f"PLAN-{slug}.yaml":
        raise WordingCloneError(f"plan file {source.name} does not match --slug {slug}")
    return text


def admitted_plan(tree: Path, slug: str, plan_text: str | None, changed: set[str]) -> set[str]:
    """The plan path as an admitted change when the scratch tree holds the loaded plan byte for byte."""
    rel = _PLAN.format(slug=slug)
    if plan_text is None or rel not in changed:
        return set()
    path = tree / rel
    if not path.exists() or path.read_bytes() != plan_text.encode("utf-8"):
        raise WordingCloneError(f"the scratch tree's {rel} differs from the loaded plan; remove it or pass --plan")
    return {rel}


def plan_line(slug: str, plan_text: str | None) -> str:
    if plan_text is None:
        return "plan: none loaded; the clone's main holds the sidecar only"
    entries = _entries(plan_text)
    return (
        f"plan: rehearsed on a clone whose main holds the plan PR ({_PLAN.format(slug=slug)}; "
        f"weakening_dispositions entries in the diff: {entries})"
    )


def _entries(plan_text: str) -> int:
    try:
        data = yaml.safe_load(plan_text)
    except yaml.YAMLError:
        return 0
    value = data.get(_KEY) if isinstance(data, dict) else None
    return len(value) if isinstance(value, list) else 0


def stale_hint(output: str) -> str:
    return _HINT if _STALE in output else ""


def _carry(tree: Path, clone: Path, slug: str) -> None:
    plan_rel = _PLAN.format(slug=slug)
    diff = _git(tree, "diff", "--binary", "--no-renames", "HEAD", "--", ".", f":(exclude){plan_rel}")
    if diff.strip():
        _git(clone, "apply", "--binary", "--whitespace=nowarn", data=diff)
    others = _git(tree, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    for rel in filter(None, others):
        if rel == plan_rel or rel == _VENV or rel.startswith(f"{_VENV}/"):
            continue
        dest = clone / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tree / rel, dest)
        except OSError as exc:
            raise WordingCloneError(f"cannot carry untracked file {rel} into the clone: {exc}") from exc


@contextlib.contextmanager
def rehearsal_clone(tree: Path, base: str, slug: str, sidecar_text: str, plan_text: str | None) -> Iterator[Path]:
    """A clone path whose origin/main is base plus the plan PR, with the tree's changes and the plan uncommitted."""
    temp = Path(tempfile.mkdtemp(prefix="wording-check-"))
    try:
        clone, hooks = temp / "clone", temp / "hooks"
        hooks.mkdir()
        _git(temp, "clone", "-q", "--shared", "--no-checkout", str(tree), str(clone))
        _git(clone, "checkout", "-q", "--detach", base)
        sidecar = clone / _SIDECAR.format(slug=slug)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(sidecar_text, encoding="utf-8")
        if plan_text is not None:
            (clone / _PLAN.format(slug=slug)).write_text(strip_dispositions(plan_text), encoding="utf-8")
        _git(clone, "add", "-A")
        flags = [*_COMMIT_FLAGS, "-c", f"core.hooksPath={hooks}"]
        _git(clone, "commit", "-q", "--allow-empty", "-m", "plan PR", flags=flags)
        _git(clone, "update-ref", "refs/remotes/origin/main", "HEAD")
        _carry(tree, clone, slug)
        if plan_text is not None:
            (clone / _PLAN.format(slug=slug)).write_text(plan_text, encoding="utf-8")
        with (clone / ".git" / "info" / "exclude").open("a", encoding="utf-8") as handle:
            handle.write(f"{_VENV}\n")
        link = clone / _VENV
        if (ROOT / _VENV).exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(ROOT / _VENV, target_is_directory=True)
        yield clone
    finally:
        shutil.rmtree(temp, ignore_errors=True)
