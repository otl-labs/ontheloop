"""Git plumbing for the verification graduation registry (split out of scripts/verification_graduation.py).

Holds GraduationError, the git runner, the throwaway-worktree provisioner, a batched in-memory blob reader and a
per-ref shared worktree cache. Everything stays in memory or in a temporary worktree: no index, no cache file
(Decision 176 cl.5).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[3]


class GraduationError(RuntimeError):
    """Raised on any worktree/materialize/revert failure (fail-loud, Decision 55)."""


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")


def _provision_venv_symlink(wt_path: Path, repo_root: Path) -> Path | None:
    """Symlink ``repo_root``'s ``.venv`` into the worktree, if ``repo_root`` has one.

    Keyed off the ``repo_root`` ARGUMENT, never a module-level root -- a synthetic fixture repo (tests build these
    under tmp_path, with neither a `.venv` nor a `bin/venv-python`) must stay unprovisioned so its own
    environment-probe tests can still exercise the unrunnable-interpreter path. Without this, `bin/venv-python`
    invoked inside the worktree resolves its own location via `$0` and lands on `<worktree>/.venv/bin/python`,
    absent on a bare `git worktree add` since `.venv` is gitignored. Returns the created symlink path, or None if
    `repo_root` carries no `.venv` to provision.
    """
    real_venv = repo_root / ".venv"
    if not real_venv.exists():
        return None
    wt_venv = wt_path / ".venv"
    wt_venv.symlink_to(real_venv, target_is_directory=True)
    return wt_venv


def _add_worktree(ref: str, root: Path) -> tuple[Path, Path, Path | None]:
    tmp_parent = Path(tempfile.mkdtemp(prefix="verif-graduation-"))
    wt_path = tmp_parent / "wt"
    add_result = run_git(["worktree", "add", "--detach", str(wt_path), ref], root)
    if add_result.returncode != 0:
        shutil.rmtree(tmp_parent, ignore_errors=True)
        raise GraduationError(f"git worktree add failed for ref {ref!r}: {add_result.stderr.strip()}")
    return tmp_parent, wt_path, _provision_venv_symlink(wt_path, root)


def _remove_worktree(tmp_parent: Path, wt_path: Path, venv_symlink: Path | None, root: Path) -> None:
    # Explicit unlink before `worktree remove`/`rmtree` even though both were verified not to follow the symlink into
    # the real .venv -- a 432MB destructive failure mode is not a property to rely on implicitly.
    if venv_symlink is not None and venv_symlink.is_symlink():
        venv_symlink.unlink()
    remove_result = run_git(["worktree", "remove", "--force", str(wt_path)], root)
    if remove_result.returncode != 0:
        run_git(["worktree", "prune"], root)
    shutil.rmtree(tmp_parent, ignore_errors=True)


@contextlib.contextmanager
def git_worktree(ref: str, repo_root: Path | None = None) -> Iterator[Path]:
    """Check out ``ref`` into a temporary git worktree; remove it on exit.

    Raises GraduationError on any git failure (fail-loud, Decision 55).
    """
    root = Path(repo_root) if repo_root is not None else _DEFAULT_ROOT
    tmp_parent, wt_path, venv_symlink = _add_worktree(ref, root)
    try:
        yield wt_path
    finally:
        _remove_worktree(tmp_parent, wt_path, venv_symlink, root)


def read_blobs_at_ref(ref: str, paths: list[str], root: Path) -> dict[str, str]:
    """Read every ``ref:path`` blob through ONE ``git cat-file --batch`` subprocess, in memory.

    A path whose header reads ``missing`` is omitted from the result. A subprocess that cannot start, exits non-zero
    or returns misframed output raises GraduationError (Decision 55: never a silent empty baseline).
    """
    if not paths:
        return {}
    request = "".join(f"{ref}:{p}\n" for p in paths).encode("utf-8")
    try:
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=str(root), input=request, capture_output=True)
    except OSError as exc:
        raise GraduationError(f"git cat-file --batch could not start: {exc}") from exc
    if proc.returncode != 0:
        raise GraduationError(
            f"git cat-file --batch exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    out = proc.stdout
    pos = 0
    result: dict[str, str] = {}
    for path in paths:
        eol = out.find(b"\n", pos)
        if eol < 0:
            raise GraduationError(f"git cat-file --batch output misframed before {path!r}")
        header = out[pos:eol].decode("utf-8", "replace").split(" ")
        pos = eol + 1
        if header[-1] == "missing":
            continue
        if len(header) != 3 or header[1] != "blob" or not header[2].isdigit():
            raise GraduationError(f"git cat-file --batch unexpected header for {path!r}: {' '.join(header)!r}")
        size = int(header[2])
        body = out[pos : pos + size]
        if len(body) != size or out[pos + size : pos + size + 1] != b"\n":
            raise GraduationError(f"git cat-file --batch output misframed in {path!r}")
        pos += size + 1
        try:
            result[path] = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GraduationError(f"git cat-file --batch blob {path!r} is not UTF-8: {exc}") from exc
    return result


class _SharedWorktrees:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._added: dict[str, tuple[Path, Path, Path | None, str]] = {}

    def get(self, ref: str) -> Path:
        if ref not in self._added:
            resolved = run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], self._root)
            sha = resolved.stdout.strip()
            if resolved.returncode != 0 or not sha:
                raise GraduationError(f"cannot resolve ref {ref!r}: {resolved.stderr.strip()}")
            tmp_parent, wt_path, venv = _add_worktree(sha, self._root)
            self._added[ref] = (tmp_parent, wt_path, venv, sha)
            return wt_path
        _, wt_path, _, sha = self._added[ref]
        self._restore(wt_path, sha)
        return wt_path

    def _restore(self, wt_path: Path, sha: str) -> None:
        checkout = run_git(["checkout", "-f", "-q", "--detach", sha], wt_path)
        if checkout.returncode != 0:
            raise GraduationError(f"shared worktree restore to {sha} failed: {checkout.stderr.strip()}")
        clean = run_git(["clean", "-ffdxq", "-e", ".venv"], wt_path)
        if clean.returncode != 0:
            raise GraduationError(f"shared worktree clean failed: {clean.stderr.strip()}")
        head = run_git(["rev-parse", "HEAD"], wt_path)
        if head.returncode != 0 or head.stdout.strip() != sha:
            raise GraduationError(f"shared worktree HEAD {head.stdout.strip()!r} is not the recorded base {sha}")
        status = run_git(["status", "--porcelain", "--ignored"], wt_path)
        leftovers = [ln for ln in status.stdout.splitlines() if ln.strip() and ln[3:].rstrip("/") != ".venv"]
        if status.returncode != 0 or leftovers:
            raise GraduationError(f"shared worktree not clean after restore: {leftovers[:5]}")

    def close(self) -> None:
        for tmp_parent, wt_path, venv, _sha in self._added.values():
            _remove_worktree(tmp_parent, wt_path, venv, self._root)
        self._added.clear()


@contextlib.contextmanager
def shared_worktrees(root: Path | None = None) -> Iterator[_SharedWorktrees]:
    """One reusable worktree per base ref: added on first ``get(ref)``, restored to the recorded sha on every later one."""
    shared = _SharedWorktrees(Path(root) if root is not None else _DEFAULT_ROOT)
    try:
        yield shared
    finally:
        shared.close()


WorktreeFor = Callable[[str], Path]
