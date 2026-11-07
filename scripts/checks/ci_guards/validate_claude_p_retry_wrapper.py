"""claude -p retry wrapper enforcement (Decision 73, Decision 92)
and its capture pairing (PLAN-telemetry-claude-p-capture-adapter)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

WRAPPER = "claude_p_retry.sh"
CAPTURE_ACTION = ".github/actions/telemetry-ci-capture"
CAPTURE_FILE = Path("config") / "telemetry" / "capture.yaml"
UNCAPTURED_OWNER = re.compile(r"rec-\d+")
ACTION_SUFFIXES = (".yml", ".yaml", ".sh")
ALWAYS = ("always()", "${{ always() }}")


def _scan_file(path: Path, label: str) -> tuple[list[str], bool]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], False
    violations = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "command -v claude" in line or "claude --version" in line:
            continue
        if "claude -p" in line and WRAPPER not in line:
            violations.append(f"{label}:{lineno}: unwrapped `claude -p` invocation")
    return violations, True


def _scan_claude_p_raw_invocations(workflows_root: Path, actions_root: Path | None = None) -> tuple[list[str], int]:
    """Return (violations, files read) for unwrapped `claude -p` lines in CI workflow files and, when given, composite actions.

    Skips blank lines, YAML/shell comments (leading #), `command -v claude` presence
    checks, and `claude --version` calls. Parity with _TRANSIENT_CLAUDE_SIGNATURES.
    An unreadable file is skipped and not counted as read.
    """
    violations: list[str] = []
    read = 0
    paths = [(p, p.name) for p in sorted(workflows_root.glob("*.yml"))]
    if actions_root is not None and actions_root.is_dir():
        paths += [
            (p, p.relative_to(actions_root).as_posix())
            for p in sorted(actions_root.rglob("*"))
            if p.suffix in ACTION_SUFFIXES and p.is_file()
        ]
    for path, label in paths:
        found, counted = _scan_file(path, label)
        violations += found
        read += counted
    return violations, read


def _check_claude_p_raw_invocations(workflows_root: Path) -> list[str]:
    """Return violation strings for unwrapped `claude -p` lines in CI workflow files."""
    return _scan_claude_p_raw_invocations(workflows_root)[0]


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _wrapper_actions(actions_root: Path) -> set[str]:
    """Repo-relative dirs (.github/actions/<name>) of the local composite actions whose files invoke the wrapper."""
    found: set[str] = set()
    if not actions_root.is_dir():
        return found
    for path in sorted(actions_root.rglob("*")):
        if path.suffix in ACTION_SUFFIXES and path.is_file():
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if WRAPPER in body and path.relative_to(actions_root).parts[0] != Path(CAPTURE_ACTION).name:
                found.add(f".github/actions/{path.relative_to(actions_root).parts[0]}")
    return found


def _action_path(uses: object) -> str | None:
    return uses.removeprefix("./").rstrip("/") if isinstance(uses, str) else None


def _job_facts(job: dict[str, Any], wrapper_actions: set[str]) -> tuple[bool, set[str], list[dict[str, Any]]]:
    """(wrapper invoked in a run body of the job, the wrapper actions it uses, its capture steps)."""
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    in_run = any(WRAPPER in str(s.get("run") or "") for s in steps)
    used = {path for s in steps if (path := _action_path(s.get("uses"))) in wrapper_actions}
    capture = [s for s in steps if (_action_path(s.get("uses")) or "").endswith(CAPTURE_ACTION)]
    return in_run, used, capture


def _capture_problem(steps: list[dict[str, Any]]) -> str | None:
    if len(steps) != 1:
        return f"holds {len(steps)} capture steps, expected exactly one"
    step = steps[0]
    if str(step.get("if")).strip() not in ALWAYS:
        return "capture step lacks `if: always()`"
    if step.get("continue-on-error") is not True:
        return "capture step lacks `continue-on-error: true`"
    return None


def _listed_problems(root: Path, listed: list[dict], sites_in: dict[str, int]) -> list[str]:
    out: list[str] = []
    for entry in listed:
        path = entry.get("workflow")
        if path == ".github/workflows/claude.yml":
            out.append("claude.yml appears in ci_workflows")
        elif not isinstance(path, str) or not (root / path).is_file():
            out.append(f"ci_workflows entry names a workflow that does not exist: {path}")
        elif not sites_in.get(path):
            out.append(f"{Path(path).name}: ci_workflows entry has no claude_p_retry.sh call-site job")
    return out


def _exempt_problems(exempt_entries: list[dict], wrapper_actions: set[str]) -> list[str]:
    out: list[str] = []
    for entry in exempt_entries:
        site, owner = _action_path(entry.get("call_site")), entry.get("owner")
        if site == ".github/workflows/claude.yml":
            out.append("claude.yml appears in ci_uncaptured")
        elif site not in wrapper_actions:
            out.append(f"stale ci_uncaptured entry: {site} is not a claude_p_retry.sh call site")
        if not isinstance(owner, str) or not UNCAPTURED_OWNER.fullmatch(owner):
            out.append(f"ci_uncaptured entry for {site} has no rec-NNNN owner")
    return out


def _check_pairing(root: Path) -> list[str]:
    """Rule 2: every claude_p_retry.sh call site job holds exactly one proper capture step unless exempt; the allow-list and
    the pending exemptions agree with the call sites that exist."""
    out: list[str] = []
    switch = _load(root / CAPTURE_FILE)
    switch = switch if isinstance(switch, dict) else {}
    listed = [e for e in switch.get("ci_workflows") or [] if isinstance(e, dict)]
    exempt_entries = [e for e in switch.get("ci_uncaptured") or [] if isinstance(e, dict)]
    listed_paths = {e.get("workflow") for e in listed}
    exempt_sites = {_action_path(e.get("call_site")) for e in exempt_entries}
    wrapper_actions = _wrapper_actions(root / ".github" / "actions")
    paired_in: set[str] = set()
    sites_in: dict[str, int] = {}
    for wf in sorted((root / ".github" / "workflows").glob("*.yml")):
        rel = f".github/workflows/{wf.name}"
        doc = _load(wf)
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        for name, job in (jobs or {}).items():
            if not isinstance(job, dict):
                continue
            in_run, used, capture = _job_facts(job, wrapper_actions)
            if not in_run and not used:
                continue
            exempt = not in_run and used <= exempt_sites
            if exempt:
                continue
            sites_in[rel] = sites_in.get(rel, 0) + 1
            problem = _capture_problem(capture)
            if problem is not None:
                out.append(f"{wf.name}: job {name} runs claude -p but {problem}")
            else:
                paired_in.add(rel)
    for rel in sorted(paired_in - listed_paths):
        out.append(f"{Path(rel).name}: a paired workflow is not a ci_workflows entry in {CAPTURE_FILE.as_posix()}")
    out.extend(_listed_problems(root, listed, sites_in))
    out.extend(_exempt_problems(exempt_entries, wrapper_actions))
    return out


@registry.register("validate_claude_p_retry_wrapper", owner="platform")
def validate_claude_p_retry_wrapper(failed: list[str]) -> None:
    """Enforce that every `claude -p` invocation in CI workflows and composite actions routes through
    scripts/ci/claude_p_retry.sh,
    and that every claude_p_retry.sh call-site job is paired with the telemetry-ci-capture action (or a pending exemption).

    Parity with _TRANSIENT_CLAUDE_SIGNATURES (this file) and scripts/ci/claude_p_retry.sh.
    Decision 73: validate.py is the single source of truth for CI checks. Decision 92.
    Declares examined(.github/workflows/*.yml and .github/actions/** files read, unit="claude_p_files") (Decision 170).
    """
    print("\n=== claude -p retry wrapper enforcement ===")
    violations, read = _scan_claude_p_raw_invocations(
        _common.ROOT / ".github" / "workflows", _common.ROOT / ".github" / "actions"
    )
    registry.examined(read, unit="claude_p_files")
    if violations:
        for v in violations:
            print(f"  FAIL: {v}")
            failed.append(f"claude_p_retry wrapper: {v}")
    else:
        print("  PASS: all claude -p invocations route through scripts/ci/claude_p_retry.sh")
    problems = _check_pairing(_common.ROOT)
    for problem in problems:
        print(f"  FAIL: {problem}")
        failed.append(f"claude_p_retry capture pairing: {problem}")
    if not problems:
        print("  PASS: every claude_p_retry.sh call site is paired with telemetry-ci-capture or a pending exemption")
