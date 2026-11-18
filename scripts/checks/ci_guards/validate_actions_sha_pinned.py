"""Every remote `uses:` reference is pinned to a commit SHA with an exact version comment (rec-5020, NS.9).

Third-party code in secret-bearing and OIDC-holding jobs must change only through a pull request the
repository's gates check, so a floating tag or branch is refused. Scope: every *.yml/*.yaml directly under
.github/workflows and every action.yml/action.yaml under .github/actions. A reference is a job-level
`uses:` (reusable workflow), a step `uses:`, or a composite action step `uses:`.

Rules per reference value (a value starting ./ is exempt):
  - docker://...  must end @sha256:<64 lowercase hex>.
  - anything else must be <owner>/<repo>[/<path>]@<40 lowercase hex> (_shared.is_sha_pinned) and its line must
    carry a trailing exact version comment (`# v7.0.1`, `# 7.1.0`), which Dependabot rewrites when it bumps the pin.
  - a value holding `#` is a comment written with no space before it, which YAML folds into the value; it fails
    with a message naming that form (rec-5342).

Fail closed: the YAML parse and a raw line scan of `uses:` lines must agree as multisets per file, else the
file fails ("uses: form the line scan cannot read"), so a flow-style step cannot hide a reference.

No grandfathering: rec-5288 and rec-5289 pinned every reference, and the shrink-only baseline that listed them
was deleted with its ratchet (rec-5289); a baseline file, if one reappears, is not read.

Form only: a SHA that exists only in a fork network (an imposter commit) needs an online audit and is out of
scope. No network, no subprocess.

Decision 170: every reachable exit declares registry.examined(n, unit="uses_references") last.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards._shared import is_sha_pinned

_PREFIX = "actions-sha-pinned"
_WORKFLOWS = ".github/workflows"
_ACTIONS = ".github/actions"

_USES_LINE = re.compile(r"^\s*(-\s+)?uses:\s*(.*?)\s*$")
_DOCKER_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_VERSION_COMMENT = re.compile(r"#\s*v?\d+\.\d+\.\d+(?:[-+][\w.]+)?\s*$")
_FIX = "pin the reference to a 40-hex commit SHA with an exact version comment (# vX.Y.Z)"
_NO_SPACE = "comment with no space before # (YAML reads it as part of the value); write `@<sha> # vX.Y.Z`"


def _split_value(rest: str) -> tuple[str, str]:
    """The `uses:` value (quotes stripped) and its trailing comment from the text after `uses:`."""
    if rest[:1] in ("'", '"'):
        end = rest.find(rest[0], 1)
        if end != -1:
            return rest[1:end], rest[end + 1 :].strip()
    match = re.search(r"\s#", rest)
    if match is None:
        return rest, ""
    return rest[: match.start()].strip(), rest[match.start() :].strip()


def _scan_lines(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _USES_LINE.match(line)
        if match:
            found.append(_split_value(match.group(2)))
    return found


def _parsed_uses(doc: Any) -> list[str]:
    values: list[str] = []
    if not isinstance(doc, dict):
        return values
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            if isinstance(job.get("uses"), str):
                values.append(job["uses"])
            values.extend(_step_uses(job.get("steps")))
    runs = doc.get("runs")
    if isinstance(runs, dict):
        values.extend(_step_uses(runs.get("steps")))
    return values


def _step_uses(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    return [s["uses"] for s in steps if isinstance(s, dict) and isinstance(s.get("uses"), str)]


def _violation(value: str, comment: str) -> str | None:
    """The reason a reference fails, or None when it passes."""
    if value.startswith("./"):
        return None
    if value.startswith("docker://"):
        return None if _DOCKER_DIGEST.search(value) else "docker reference without an @sha256 digest"
    if "#" in value:
        return _NO_SPACE
    if not is_sha_pinned(value):
        return "not pinned to a 40-hex commit SHA"
    if _VERSION_COMMENT.search(comment) is None:
        return "no exact version comment (# vX.Y.Z)"
    return None


def _files(root: Path) -> list[Path]:
    files: list[Path] = []
    wf = root / _WORKFLOWS
    if wf.is_dir():
        files.extend(sorted(p for p in wf.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")))
    acts = root / _ACTIONS
    if acts.is_dir():
        files.extend(sorted(p for p in acts.rglob("*") if p.is_file() and p.name in ("action.yml", "action.yaml")))
    return files


def scan(root: Path) -> tuple[Counter[str], list[str], int]:
    """("<path>::<value>: <reason>" counts, fail-closed file messages, references examined) for the tree at root."""
    counts: Counter[str] = Counter()
    unreadable: list[str] = []
    examined = 0
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            doc = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as exc:
            unreadable.append(f"{rel}: unreadable ({exc.__class__.__name__})")
            continue
        scanned = _scan_lines(text)
        if Counter(v for v, _ in scanned) != Counter(_parsed_uses(doc)):
            unreadable.append(f"{rel}: uses: form the line scan cannot read")
            continue
        examined += len(scanned)
        for value, comment in scanned:
            reason = _violation(value, comment)
            if reason is not None:
                counts[f"{rel}::{value}: {reason}"] += 1
    return counts, unreadable, examined


@registry.register("validate_actions_sha_pinned", owner="platform")
def validate_actions_sha_pinned(failed: list[str]) -> None:
    """Remote uses: references are SHA-pinned with an exact version comment. See module docstring."""
    print("\n=== actions SHA-pinning guard ===")
    live, unreadable, examined = scan(_common.ROOT)
    before = len(failed)
    failed.extend(f"{_PREFIX}: {m}" for m in unreadable)
    failed.extend(f"{_PREFIX}: {key} (x{n}); {_FIX}" for key, n in sorted(live.items()))
    for message in failed[before:]:
        print(f"  FAIL: {message}")
    if len(failed) == before:
        print(f"  PASS: {examined} uses references")
    registry.examined(examined, unit="uses_references")
