"""Baseline settle for the wording check: let the pinned detect-secrets hook rewrite .secrets.baseline in the scratch tree.

Words that shift a line the baseline tracks make the hook rewrite the file, which the build commits as its own
regeneration. This module restores the baseline from HEAD, runs the hook serially, admits only the shifts the hook
legitimately makes and stages the result; every other difference is a refusal with its reason.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

BASELINE = ".secrets.baseline"
_VENV = ".venv"
_TAIL_LINES = 12
_REMEDY = "put `# pragma: allowlist secret` on that line inside the approved words (the operator re-approves them) or reword"
_COLD_ENV = "run `bin/venv-python -m pre_commit install-hooks`, then re-run"

Runner = Callable[[list[str], Path, int], "subprocess.CompletedProcess[str]"]


def _entry_key(entry: object) -> str:
    if not isinstance(entry, dict):
        return json.dumps(entry, sort_keys=True)
    return json.dumps({k: v for k, v in entry.items() if k != "line_number"}, sort_keys=True)


def _load(text: str, label: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"{label} baseline is not valid JSON: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("results"), dict):
        return None, f"{label} baseline has no results mapping"
    return data, None


def _file_drift(name: str, old: list, new: list, sidecar_files: set[str]) -> list[str]:
    if name not in sidecar_files:
        return [] if old == new else [f"the entry for {name}, which is not a sidecar file, changed"]
    if not isinstance(old, list) or not isinstance(new, list):
        return [f"the entry for {name} is not a list"]
    pool = [_entry_key(entry) for entry in old]
    out: list[str] = []
    for entry in new:
        key = _entry_key(entry)
        if key in pool:
            pool.remove(key)
        else:
            out.append(f"{name} gains a finding that matches no HEAD finding apart from its line number")
    return out


def drift(old_text: str, new_text: str, sidecar_files: set[str]) -> list[str]:
    """Refused differences between two baseline JSON texts; empty when only admitted shifts remain."""
    old, err_old = _load(old_text, "HEAD")
    new, err_new = _load(new_text, "scratch-tree")
    if old is None or new is None:
        return [err for err in (err_old, err_new) if err]
    out: list[str] = []
    for key in sorted((old.keys() | new.keys()) - {"generated_at", "results"}):
        if old.get(key) != new.get(key):
            out.append(f"the top-level key {key!r} differs")
    old_results, new_results = old["results"], new["results"]
    for name in sorted(old_results.keys() | new_results.keys()):
        if name not in old_results:
            out.append(f"{name} is a new file entry")
        elif name not in new_results:
            if name not in sidecar_files:
                out.append(f"the entry for {name}, which is not a sidecar file, was dropped")
        else:
            out += _file_drift(name, old_results[name], new_results[name], sidecar_files)
    return out


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


def _git(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def _hook(runner: Runner, tree: Path, files: list[str], timeout: int) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    cmd = [
        "env",
        "PRE_COMMIT_NO_CONCURRENCY=1",
        "bin/venv-python",
        "-m",
        "pre_commit",
        "run",
        "detect-secrets",
        "--files",
        *files,
    ]
    try:
        return runner(cmd, tree, timeout), ""
    except subprocess.TimeoutExpired:
        return None, f"the detect-secrets hook timed out after {timeout} s; on a cold pre-commit environment {_COLD_ENV}"
    except OSError as exc:
        return None, f"the detect-secrets hook could not start: {exc}"


def _hook_files(tree: Path, changed: set[str]) -> list[str]:
    keep = (p for p in changed if p != BASELINE and p != _VENV and not p.startswith(f"{_VENV}/"))
    return sorted(p for p in keep if (tree / p).is_file())


def settle(tree: Path, changed: set[str], sidecar_files: set[str], runner: Runner, timeout: int) -> str | None:
    """Settle .secrets.baseline in `tree`; None when settled (and staged), else the reason it is not."""
    head = _git(tree, "show", f"HEAD:{BASELINE}")
    if head.returncode != 0:
        return None
    restored = _git(tree, "checkout", "HEAD", "--", BASELINE)
    if restored.returncode != 0:
        return f"could not restore {BASELINE} from HEAD: {restored.stderr.strip()}"
    files = _hook_files(tree, changed)
    proc, reason = _hook(runner, tree, files, timeout)
    if proc is None:
        return reason
    path = tree / BASELINE
    rewritten = path.read_text(encoding="utf-8") != head.stdout
    if not rewritten:
        if proc.returncode == 0:
            return None
        return f"the detect-secrets hook rejects the placed words:\n{_tail(proc)}\nremedy: {_REMEDY}"
    refused = drift(head.stdout, path.read_text(encoding="utf-8"), sidecar_files)
    if refused:
        return "the hook's rewrite of .secrets.baseline is refused: " + "; ".join(refused)
    staged = _git(tree, "add", BASELINE)
    if staged.returncode != 0:
        return f"could not stage {BASELINE}: {staged.stderr.strip()}"
    again, reason = _hook(runner, tree, files, timeout)
    if again is None:
        return reason
    if again.returncode != 0:
        return f"the baseline did not settle:\n{_tail(again)}\nremedy: {_REMEDY}"
    return None
