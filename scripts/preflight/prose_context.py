"""Prose-context measurement for session_preflight (Decision 110; ACG-05/ACG-06 slice 3).

Deterministic, creds-free, wc-class metric for the permanent agent-instruction prose classes
(Decision 127): per-surface prose BYTES and ~bytes/4 TOKEN estimates for S1 (root ambient
load-set: root CLAUDE.md + transitive @-imports), S2 (each non-root **/CLAUDE.md), S3 (each
.claude/commands/*.md), S4 (each .claude/skills/*/SKILL.md entry file), and S8
(docs/PROJECT_CONTEXT.md) -- plus a stable-vs-churned byte split per surface derived from git
edit-recency.

MEASUREMENT ONLY (Decision 114/43 precedent): ships no gate, no exit-code-bearing enforcement,
no budget registry -- the S1/S2/S4/S8 ceiling/gate is the separate ACG-01 slice. S3 is measured
only by design (Decision 127 strongest-layer surface).

Local-file-only, creds-free, wc-class (Decision 88 egress invariants: no network, no AWS). Never
raises -- every resolver and the top-level entry points fail open independently, mirroring this
package's other advisory siblings (context_docs.py's _check_endstate_drift /
_scan_provisional_contracts).

Usage:
    bin/venv-python -m scripts.preflight.prose_context
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.preflight import _common

BYTES_PER_TOKEN = 4
_CHURN_LOOKBACK_DAYS = 90
_IMPORT_LINE_RE = re.compile(r"^@(\S+)\s*$", re.MULTILINE)
_NON_ROOT_CLAUDE_MD_RE = re.compile(r"^.+/CLAUDE\.md$")


def _read_bytes(path: Path) -> int:
    try:
        return len(path.read_bytes())
    except OSError:
        return 0


def _relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(_common.ROOT).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _resolve_s1_root_load_set(root: Path | None = None) -> list[Path]:
    """Root ambient load-set: root CLAUDE.md + transitive @-imports (cycle-guarded).

    Today CLAUDE.md -> @AGENTS.md, so the set is {CLAUDE.md, AGENTS.md} -- resolved
    transitively (not hardcoded) so the unit stays split-proof if more @-imports are ever added.
    """
    resolved: list[Path] = []
    seen: set[Path] = set()
    queue: list[Path] = [(root or _common.ROOT) / "CLAUDE.md"]
    while queue:
        current = queue.pop(0)
        if not current.exists():
            continue
        try:
            key = current.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        resolved.append(current)
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _IMPORT_LINE_RE.finditer(text):
            import_path = (current.parent / match.group(1)).resolve()
            if import_path not in seen:
                queue.append(import_path)
    return resolved


def _git_tracked_files() -> list[str]:
    """Repo-relative POSIX paths of every git-tracked file. [] on any git failure (fail-open)."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=_common.ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _resolve_s2_directory_claude_files() -> list[Path]:
    """Non-root **/CLAUDE.md, git-tracked only (excludes .venv/untracked)."""
    tracked = _git_tracked_files()
    return sorted(_common.ROOT / p for p in tracked if _NON_ROOT_CLAUDE_MD_RE.match(p))


def _resolve_s3_commands() -> list[Path]:
    d = _common.ROOT / ".claude" / "commands"
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _resolve_s4_skill_entries() -> list[Path]:
    d = _common.ROOT / ".claude" / "skills"
    return sorted(d.glob("*/SKILL.md")) if d.is_dir() else []


def _resolve_s8_project_context() -> list[Path]:
    p = _common.ROOT / "docs" / "PROJECT_CONTEXT.md"
    return [p] if p.exists() else []


# Fixed, ordered surface-class roster (Decision 127 permanent prose classes a [S1+S2], c
# [S3+S4], and the e-member docs/PROJECT_CONTEXT.md [S8]). Fixed membership so the standalone
# printer always emits exactly one column-0 summary line per class regardless of how many
# member files any one class currently has. The resolver is named (str), not a bound function
# object, so measure_prose_context()'s globals()-lookup at call time (below) stays patchable by
# tests via unittest.mock.patch("scripts.preflight.prose_context._resolve_...", ...).
_SURFACE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("S1", "Root ambient load-set (CLAUDE.md + transitive @-imports)", "_resolve_s1_root_load_set"),
    ("S2", "Per-directory CLAUDE.md (non-root)", "_resolve_s2_directory_claude_files"),
    ("S3", "Slash commands (.claude/commands/*.md)", "_resolve_s3_commands"),
    ("S4", "Skill entry files (.claude/skills/*/SKILL.md)", "_resolve_s4_skill_entries"),
    ("S8", "Project knowledge base (docs/PROJECT_CONTEXT.md)", "_resolve_s8_project_context"),
)


def _churned_relpaths(lookback_days: int = _CHURN_LOOKBACK_DAYS) -> set[str] | None:
    """Repo-relative paths touched by a commit within the lookback window.

    Returns None (fail-open) when git history is unavailable (no git binary, shallow clone
    error, detached/bare tree, timeout) -- callers must treat None as "split unknown", never
    silently as "zero churn".
    """
    try:
        result = subprocess.run(
            ["git", "log", f"--since={lookback_days}.days", "--name-only", "--pretty=format:"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            cwd=_common.ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _empty_surface(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "file_count": 0,
        "prose_bytes": 0,
        "token_estimate": 0,
        "stable_bytes": None,
        "churned_bytes": None,
        "split_status": "unknown",
        "files": [],
    }


def _measure_surface(label: str, paths: list[Path], churned: set[str] | None) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    total_stable = 0
    total_churned = 0
    for p in paths:
        b = _read_bytes(p)
        total_bytes += b
        rel = _relpath(p)
        entry: dict[str, Any] = {"path": rel, "prose_bytes": b}
        if churned is not None:
            is_churned = rel in churned
            entry["churned"] = is_churned
            if is_churned:
                total_churned += b
            else:
                total_stable += b
        files.append(entry)
    split_known = churned is not None
    return {
        "label": label,
        "file_count": len(files),
        "prose_bytes": total_bytes,
        "token_estimate": total_bytes // BYTES_PER_TOKEN,
        "stable_bytes": total_stable if split_known else None,
        "churned_bytes": total_churned if split_known else None,
        "split_status": "ok" if split_known else "unknown",
        "files": files,
    }


def measure_prose_context() -> dict[str, Any]:
    """Return the structured per-surface prose-context dict, keyed S1/S2/S3/S4/S8.

    Never raises: the churn lookup and every surface resolver/measurement fail open
    independently, so one broken surface degrades to an empty/unknown entry rather than
    aborting the whole report (fail-open, mirrors context_docs.py's advisory siblings).
    """
    try:
        churned = _churned_relpaths()
    except Exception:  # noqa: BLE001
        churned = None

    report: dict[str, Any] = {}
    for surface, label, resolver_name in _SURFACE_SPECS:
        try:
            resolver: Callable[[], list[Path]] = globals()[resolver_name]
            paths = resolver()
        except Exception:  # noqa: BLE001
            paths = []
        try:
            report[surface] = _measure_surface(label, paths, churned)
        except Exception:  # noqa: BLE001
            report[surface] = _empty_surface(label)
    return report


def build_report_section() -> dict[str, Any]:
    """Report-section builder: measure_prose_context() plus a whole-report byte/token rollup.

    Used by the standalone `python -m` printer and by tests exercising the aggregate shape.
    scripts/session/preflight.py's wiring assigns measure_prose_context()'s own return value
    directly to report["prose_context"] -- this wrapper is additive, not a replacement.
    """
    surfaces = measure_prose_context()
    return {
        "surfaces": surfaces,
        "total_prose_bytes": sum(s["prose_bytes"] for s in surfaces.values()),
        "total_token_estimate": sum(s["token_estimate"] for s in surfaces.values()),
    }


def format_prose_context_report(surfaces: dict[str, Any] | None = None) -> str:
    """Format one column-0 summary line per surface class, with indented per-file detail."""
    if surfaces is None:
        surfaces = measure_prose_context()
    lines: list[str] = []
    for surface, label, _resolver_name in _SURFACE_SPECS:
        data = surfaces.get(surface) or _empty_surface(label)
        if data.get("split_status") == "ok":
            split = f"stable={data.get('stable_bytes')} churned={data.get('churned_bytes')}"
        else:
            split = "split=unknown"
        lines.append(
            f"{surface} {data.get('label', label)}: {data.get('file_count', 0)} files, "
            f"{data.get('prose_bytes', 0)} bytes, ~{data.get('token_estimate', 0)} tok ({split})"
        )
        for f in data.get("files", []):
            lines.append(f"  {f.get('path')}: {f.get('prose_bytes', 0)} bytes")
    return "\n".join(lines)


def print_prose_context_report(surfaces: dict[str, Any] | None = None) -> None:
    """Print the prose-context advisory (preflight's thin call-site helper)."""
    print("\n--- Prose context (Decision 110) ---")
    print(format_prose_context_report(surfaces))
    print()


def main() -> int:
    print_prose_context_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
