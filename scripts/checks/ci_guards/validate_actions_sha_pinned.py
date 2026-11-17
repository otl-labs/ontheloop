"""Every remote `uses:` reference is pinned to a commit SHA (rec-5020, NS.9).

Third-party code in secret-bearing and OIDC-holding jobs must change only through a pull request the
repository's gates check, so a floating tag or branch is refused. Scope: every *.yml/*.yaml directly under
.github/workflows and every action.yml/action.yaml under .github/actions. A reference is a job-level
`uses:` (reusable workflow), a step `uses:`, or a composite action step `uses:`.

Rules per reference value (a value starting ./ is exempt):
  - docker://...  must end @sha256:<64 lowercase hex>.
  - anything else must end @<40 lowercase hex> and its line must carry a trailing version comment
    (`# v7`, `# v7.0.1`, `# 7.1.0`), which Dependabot rewrites when it bumps the pin.

Fail closed: the YAML parse and a raw line scan of `uses:` lines must agree as multisets per file, else the
file fails ("uses: form the line scan cannot read"), so a flow-style step cannot hide a reference.

Ratchet: today's violations are grandfathered in config/actions_sha_pin_baseline.yaml, whose entries may only
shrink. At HEAD the live count of every "<path>::<value>" key must equal its baseline count (an unlisted
violation, a count above the baseline and a stale entry all fail). The no-raise leg compares HEAD with the
baseline at the merge base of HEAD and origin/main (fallback: origin/main directly), so a key added or a count
raised relative to the base fails; it is skipped with a printed SKIP line when no base baseline resolves (the
introducing change), because the HEAD equality ratchet still enforces. A Dependabot bump of a baselined
floating reference changes its key, so that PR pins the reference instead.

Form only: a SHA that exists only in a fork network (an imposter commit) needs an online audit and is out of
scope. No network; at most two git subprocess calls. .github/CODEOWNERS is read only by render_baseline.

Decision 170: every reachable exit declares registry.examined(n, unit="uses_references") last.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

_PREFIX = "actions-sha-pinned"
BASELINE_REL = "config/actions_sha_pin_baseline.yaml"
_CODEOWNERS_REL = ".github/CODEOWNERS"
_WORKFLOWS = ".github/workflows"
_ACTIONS = ".github/actions"
_COVERED_REC = "rec-5289"
_OPEN_REC = "rec-5288"

_USES_LINE = re.compile(r"^\s*(-\s+)?uses:\s*(.*?)\s*$")
_SHA40 = re.compile(r"@[0-9a-f]{40}$")
_DOCKER_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_VERSION_COMMENT = re.compile(r"#\s*v?\d+(\.\d+){0,2}\S*\s*$")
_REC_KEY = re.compile(r"^rec-\d+$")
_FIX = "pin the reference to a 40-hex commit SHA with a version comment, or lower or delete the baseline line"
_DEPENDABOT = (
    "a Dependabot bump of a baselined floating ref changes its key, so that PR pins the reference instead "
    "(the coordinator routes it to the planner of rec-5288 or rec-5289)"
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8")


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


def _is_violation(value: str, comment: str) -> bool:
    if value.startswith("./"):
        return False
    if value.startswith("docker://"):
        return _DOCKER_DIGEST.search(value) is None
    if _SHA40.search(value) is None:
        return True
    return _VERSION_COMMENT.search(comment) is None


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
    """(violation key counts, fail-closed file messages, references examined) for the tree at root."""
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
            if _is_violation(value, comment):
                counts[f"{rel}::{value}"] += 1
    return counts, unreadable, examined


def parse_baseline(text: str) -> tuple[dict[str, int], dict[str, str], list[str]]:
    """(key -> count, key -> draining rec, errors) from a baseline document; empty end states are empty."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {}, {}, [f"not valid YAML ({exc.__class__.__name__})"]
    if not isinstance(data, dict):
        return {}, {}, ["baseline is not a mapping"]
    drains = data.get("drains")
    if drains is None:
        return {}, {}, []
    if not isinstance(drains, dict):
        return {}, {}, ["drains is not a mapping"]
    counts: dict[str, int] = {}
    owner: dict[str, str] = {}
    errors: list[str] = []
    for rec, bucket in drains.items():
        if not isinstance(rec, str) or not _REC_KEY.match(rec):
            errors.append(f"bucket key {rec!r} does not match rec-<digits>")
            continue
        if bucket is None:
            continue
        if not isinstance(bucket, dict):
            errors.append(f"bucket {rec} is not a mapping")
            continue
        for key, count in bucket.items():
            if not isinstance(key, str) or "::" not in key or key.startswith("::") or key.endswith("::"):
                errors.append(f"key {key!r} in {rec} is not <path>::<value>")
            elif not isinstance(count, int) or isinstance(count, bool) or count < 1:
                errors.append(f"count for {key!r} in {rec} is not a positive int")
            elif key in owner:
                errors.append(f"key {key!r} is listed under both {owner[key]} and {rec}; each key has one owner")
            else:
                counts[key] = count
                owner[key] = rec
    return counts, owner, errors


def _head_baseline(root: Path) -> tuple[dict[str, int], list[str]]:
    path = root / BASELINE_REL
    if not path.is_file():
        return {}, []
    counts, _, errors = parse_baseline(path.read_text(encoding="utf-8"))
    return counts, [f"baseline {e}" for e in errors]


def _base_baseline_text(root: Path) -> str | None:
    base = "origin/main"
    merged = _git(root, "merge-base", "HEAD", "origin/main")
    if merged.returncode == 0 and merged.stdout.strip():
        base = merged.stdout.strip()
    shown = _git(root, "show", f"{base}:{BASELINE_REL}")
    return shown.stdout if shown.returncode == 0 else None


def _ratchet(failed: list[str], live: Counter[str], baseline: dict[str, int]) -> None:
    for key in sorted(set(live) | set(baseline)):
        have, allowed = live.get(key, 0), baseline.get(key, 0)
        if have > allowed:
            reason = "unlisted floating reference" if allowed == 0 else f"count {have} above baseline {allowed}"
            failed.append(f"{_PREFIX}: {key}: {reason}; {_FIX}; {_DEPENDABOT}")
        elif have < allowed:
            failed.append(f"{_PREFIX}: {key}: stale baseline entry (live {have}, baseline {allowed}); {_FIX}; {_DEPENDABOT}")


def _no_raise(failed: list[str], root: Path, head: dict[str, int]) -> str:
    text = _base_baseline_text(root)
    if text is None:
        print("  SKIP: no-raise leg (no base ref resolves or the baseline is absent at the base)")
        return "no-raise leg skipped: no base baseline"
    base, _, errors = parse_baseline(text)
    for error in errors:
        failed.append(f"{_PREFIX}: base baseline {error}")
    if errors:
        return ""
    for key, count in sorted(head.items()):
        if key not in base:
            failed.append(f"{_PREFIX}: {key}: key added relative to the base baseline; the baseline only shrinks; {_FIX}")
        elif count > base[key]:
            failed.append(f"{_PREFIX}: {key}: count {count} raised above base {base[key]}; the baseline only shrinks; {_FIX}")
    return ""


@registry.register("validate_actions_sha_pinned", owner="platform")
def validate_actions_sha_pinned(failed: list[str]) -> None:
    """Remote uses: references are SHA-pinned; grandfathered ones only shrink. See module docstring."""
    print("\n=== actions SHA-pinning guard ===")
    root = _common.ROOT
    live, unreadable, examined = scan(root)
    before = len(failed)
    for message in unreadable:
        print(f"  FAIL: {message}")
        failed.append(f"{_PREFIX}: {message}")
    head, errors = _head_baseline(root)
    for error in errors:
        print(f"  FAIL: {error}")
        failed.append(f"{_PREFIX}: {error}")
    if errors:
        registry.examined(examined, unit="uses_references")
        return
    _ratchet(failed, live, head)
    note = _no_raise(failed, root, head)
    for message in failed[before:]:
        print(f"  FAIL: {message}")
    if len(failed) == before:
        print(f"  PASS: {examined} uses references, {sum(live.values())} grandfathered" + (f" ({note})" if note else ""))
    registry.examined(examined, unit="uses_references")


def _covered(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


def render_baseline(root: Path) -> str:
    """The baseline document for the tree at root, partitioned by .github/CODEOWNERS."""
    live, _, _ = scan(root)
    owners = root / _CODEOWNERS_REL
    patterns: list[str] = []
    if owners.is_file():
        for line in owners.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line.split()[0].lstrip("/"))
    buckets: dict[str, list[str]] = {_OPEN_REC: [], _COVERED_REC: []}
    for key in sorted(live):
        rec = _COVERED_REC if _covered(key.split("::", 1)[0], patterns) else _OPEN_REC
        buckets[rec].append(f"    {json.dumps(key)}: {live[key]}")
    lines = [
        "# Shrink-only baseline for validate_actions_sha_pinned (rec-5020).",
        "# Entries only shrink: pin the reference and lower or delete the line. Never add a key or raise a count.",
        "# rec-5288 drains files no .github/CODEOWNERS pattern covers; rec-5289 drains files one does.",
        "drains:",
    ]
    for rec in (_OPEN_REC, _COVERED_REC):
        lines.append(f"  {rec}:" if buckets[rec] else f"  {rec}: {{}}")
        lines.extend(buckets[rec])
    return "\n".join(lines) + "\n"
