"""Git plumbing for the verification graduation registry, split out of scripts/verification_graduation.py.

Holds the worktree helpers (``git_worktree``, the shared per-ref ``shared_worktrees``) and the batched
in-memory blob reader. Nothing is cached to disk (Decision 176 cl.5); every failure raises
``GraduationError`` (fail-loud, Decision 55).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[3]
_CLEAN_STATUS_LINES = frozenset({"?? .venv", "!! .venv"})


class GraduationError(RuntimeError):
    """Raised on any worktree/materialize/revert failure (fail-loud, Decision 55)."""


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")


def _provision_venv_symlink(wt_path: Path, repo_root: Path) -> Path | None:
    """Symlink ``repo_root``'s ``.venv`` into the worktree, if ``repo_root`` has one.

    Keyed off the ``repo_root`` ARGUMENT, never a module-level root -- a synthetic fixture repo (tests build
    these under tmp_path, with neither a `.venv` nor a `bin/venv-python`) must stay unprovisioned so its own
    environment-probe tests can still exercise the unrunnable-interpreter path. Without this, `bin/venv-python`
    invoked inside the worktree re-derives its venv relative to cwd, landing on `<worktree>/.venv/bin/python`,
    absent on a bare `git worktree add` since `.venv` is gitignored. Returns the created symlink path, or None
    if `repo_root` carries no `.venv` to provision.
    """
    real_venv = repo_root / ".venv"
    if not real_venv.exists():
        return None
    wt_venv = wt_path / ".venv"
    wt_venv.symlink_to(real_venv, target_is_directory=True)
    return wt_venv


def _teardown_worktree(wt_path: Path, tmp_parent: str, venv_symlink: Path | None, root: Path) -> None:
    # Explicit unlink before `worktree remove`/`rmtree` even though both were verified not to follow the
    # symlink into the real .venv -- a 432MB destructive failure mode is not a property to rely on implicitly.
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
    tmp_parent = tempfile.mkdtemp(prefix="verif-graduation-")
    wt_path = Path(tmp_parent) / "wt"
    add_result = run_git(["worktree", "add", "--detach", str(wt_path), ref], root)
    if add_result.returncode != 0:
        shutil.rmtree(tmp_parent, ignore_errors=True)
        raise GraduationError(f"git worktree add failed for ref {ref!r}: {add_result.stderr.strip()}")
    venv_symlink = _provision_venv_symlink(wt_path, root)
    try:
        yield wt_path
    finally:
        _teardown_worktree(wt_path, tmp_parent, venv_symlink, root)


def read_blobs_at_ref(ref: str, paths: list[str], root: Path) -> dict[str, str]:
    """Every ``ref:path`` blob through ONE ``git cat-file --batch`` subprocess, in memory only.

    Frames by byte count. A path whose header reads ``missing`` is omitted from the mapping. Raises
    GraduationError when the subprocess cannot start, exits non-zero or its output is misframed.
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
    blobs: dict[str, str] = {}
    pos = 0
    for path in paths:
        nl = out.find(b"\n", pos)
        if nl < 0:
            raise GraduationError(f"git cat-file --batch output misframed before {path!r}")
        header = out[pos:nl].decode("utf-8", "replace").split(" ")
        pos = nl + 1
        if header[-1] == "missing":
            continue
        if len(header) != 3 or header[1] != "blob" or not header[2].isdigit():
            raise GraduationError(f"git cat-file --batch unexpected header for {path!r}: {' '.join(header)!r}")
        size = int(header[2])
        end = pos + size
        if out[end : end + 1] != b"\n":
            raise GraduationError(f"git cat-file --batch output misframed in {path!r}")
        try:
            blobs[path] = out[pos:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GraduationError(f"git cat-file --batch blob {path!r} is not UTF-8: {exc}") from exc
        pos = end + 1
    if pos != len(out):
        raise GraduationError("git cat-file --batch produced trailing output")
    return blobs


class _SharedWorktrees:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._by_ref: dict[str, tuple[Path, str]] = {}
        self._added: list[tuple[Path, str, Path | None]] = []

    def get(self, ref: str) -> Path:
        known = self._by_ref.get(ref)
        if known is not None:
            wt_path, sha = known
            self._restore(wt_path, sha)
            return wt_path
        resolved = run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], self._root)
        if resolved.returncode != 0:
            raise GraduationError(f"ref {ref!r} does not resolve: {resolved.stderr.strip()}")
        sha = resolved.stdout.strip()
        tmp_parent = tempfile.mkdtemp(prefix="verif-graduation-")
        wt_path = Path(tmp_parent) / "wt"
        add_result = run_git(["worktree", "add", "--detach", str(wt_path), sha], self._root)
        if add_result.returncode != 0:
            shutil.rmtree(tmp_parent, ignore_errors=True)
            raise GraduationError(f"git worktree add failed for ref {ref!r}: {add_result.stderr.strip()}")
        venv_symlink = _provision_venv_symlink(wt_path, self._root)
        self._added.append((wt_path, tmp_parent, venv_symlink))
        self._by_ref[ref] = (wt_path, sha)
        return wt_path

    def _restore(self, wt_path: Path, sha: str) -> None:
        for args in (["checkout", "-f", "-q", "--detach", sha], ["clean", "-ffdxq", "-e", ".venv"]):
            result = run_git(args, wt_path)
            if result.returncode != 0:
                raise GraduationError(f"shared worktree restore `git {' '.join(args)}` failed: {result.stderr.strip()}")
        head = run_git(["rev-parse", "HEAD"], wt_path)
        if head.returncode != 0 or head.stdout.strip() != sha:
            raise GraduationError(f"shared worktree restore left HEAD at {head.stdout.strip()!r}, expected {sha!r}")
        status = run_git(["status", "--porcelain", "--ignored"], wt_path)
        leftovers = [ln for ln in status.stdout.splitlines() if ln.strip() and ln.strip() not in _CLEAN_STATUS_LINES]
        if status.returncode != 0 or leftovers:
            raise GraduationError(f"shared worktree restore left residue: {leftovers[:5]!r}")

    def close(self) -> None:
        for wt_path, tmp_parent, venv_symlink in self._added:
            _teardown_worktree(wt_path, tmp_parent, venv_symlink, self._root)


@contextlib.contextmanager
def shared_worktrees(root: Path | None = None) -> Iterator[_SharedWorktrees]:
    """One real worktree per ref, added on first ``get(ref)``, restored on every later call, removed on exit."""
    shared = _SharedWorktrees(Path(root) if root is not None else _DEFAULT_ROOT)
    try:
        yield shared
    finally:
        shared.close()
