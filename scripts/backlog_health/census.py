"""Backlog-health census (PLAN-backlog-health-detection, Decision 62 2026-06-16 amendment / CD.12).

One structural read of every open recommendation -- `current_state("ops_recommendations",
row_filter="status = 'open'")` -- followed by CLASSIFICATION ONLY: every open rec's `acceptance`
command is sorted into one of five buckets without executing anything. classify.py and probe.py
consume this module's output; neither this module nor classify.py ever runs a rec-authored
command (see probe.py's module docstring for the sole-executor invariant this package holds to).

Buckets (measured at a702e438 over 1079 open recs -- see PLAN-backlog-health-detection Context):
  probeable                    -- a safe, single-line, non-pytest-or-existing-node command; sent
                                   to probe.py under sandboxed isolation.
  expected_fail_missing_node   -- a pytest node-id (resolved from the actual pytest invocation
                                   token, never from the first `.py` path anywhere in the
                                   command -- see parse_pytest_target) whose file or named test
                                   does not exist on disk today. Deliberately never probed
                                   (Decision 55: an exit-4 "unwritten" state IS the correct signal
                                   for ~283 recs authored in the 2026-08 sweep -- see the plan's
                                   constraints).
  unprobeable_unsafe           -- a command matching a destructive/network/privileged shape
                                   (rm -rf, sudo, curl, git push, ...). Never sent to probe.py.
  unprobeable_shape            -- a command outside the shapes above that this module cannot
                                   safely reason about (multi-line, a `python -c` one-liner, or
                                   simply too long to trust a static read of; the trailer gate
                                   lifts only the length check for a junit-routed command, which
                                   it never executes -- rec-4384).
  prose_only                   -- an empty acceptance field, or narrative text rather than a
                                   command ("N/A", "Manual review", ...).

`collect_recent_commits` -- an UNBOUNDED `git log --name-only` over the full history -- is a fixed
literal argv over repo history with no rec-derived data reaching it; this is why the AST
sole-executor invariant (probe.py's module docstring / VP step 14) is stated over REC-AUTHORED
commands, not over subprocess use in general, and must never flag this call.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]

_TABLE = "ops_recommendations"

PROBEABLE = "probeable"
PROSE_ONLY = "prose_only"
UNPROBEABLE_UNSAFE = "unprobeable_unsafe"
UNPROBEABLE_SHAPE = "unprobeable_shape"
EXPECTED_FAIL_MISSING_NODE = "expected_fail_missing_node"

BUCKETS: tuple[str, ...] = (
    PROBEABLE,
    PROSE_ONLY,
    UNPROBEABLE_UNSAFE,
    UNPROBEABLE_SHAPE,
    EXPECTED_FAIL_MISSING_NODE,
)

# The keys every read this module performs filters on / depends on -- checked present on every
# returned row (mirrors scripts.rec_episode._BASE_FILTERED_KEYS's anti-silent-None guarantee).
_REQUIRED_KEYS: tuple[str, ...] = ("id", "status")

_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b",
        r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b",
        r"\bsudo\b",
        r"\bcurl\b",
        r"\bwget\b",
        r"\bgit\s+push\b",
        r"\bgit\s+commit\b",
        r"\bgit\s+reset\s+--hard\b",
        r">\s*/dev/(?!null\b)",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\baws\s+\S*\s*(delete|terminate|destroy)",
        r"--force\b",
        r"\bchmod\s+-R\b",
        r"\bkill\s+-9\b",
        r"\bshutdown\b",
        r"\breboot\b",
    )
)

_PROSE_PREFIXES: tuple[str, ...] = (
    "n/a",
    "manual",
    "not applicable",
    "human",
    "no automated",
    "verify manually",
    "see context",
)

_MAX_SHAPE_LEN = 500
_PYTEST_TARGET_RE = re.compile(r"([\w./-]+\.py)(::([\w:.\[\]-]+))?")
_PYTEST_INVOCATION_RE = re.compile(r"(?<![\w-])pytest(?![\w-])")


def is_prose(cmd: str) -> bool:
    """Public (not module-private) because classify.py's acceptance-quality classifier also
    consults it, to keep the two modules' prose predicate identical -- code-review found that a
    locally-reimplemented "empty string only" check let a prose command like "N/A" fall through
    to classify.py's non_discriminating bucket, double-counting a census.py PROSE_ONLY rec."""
    stripped = cmd.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return lowered.startswith(_PROSE_PREFIXES)


def _is_unsafe(cmd: str) -> bool:
    return any(p.search(cmd) for p in _UNSAFE_PATTERNS)


def _is_malshaped(cmd: str, *, cap_length: bool = True) -> bool:
    if "\n" in cmd:
        return True
    if cap_length and len(cmd) > _MAX_SHAPE_LEN:
        return True
    if "python -c" in cmd and "'python -c'" not in cmd:
        return True
    return False


def parse_pytest_target(cmd: str) -> Optional[tuple[str, Optional[str]]]:
    """Extract (file_path, node_or_None) from a `pytest <path>[::<node>]` invocation inside
    `cmd`, or None if `cmd` is not recognisably a single pytest node-id invocation.

    Anchored on a real pytest INVOCATION token -- a word-boundary-delimited `pytest`, found
    anywhere in `cmd` -- never on the first `.py` path in the whole string: a command like
    `grep -q X tests/f.py && pytest tests/f.py::Node` must resolve to the pytest node, not the
    grep's file, and a path that merely CONTAINS the substring "pytest" (e.g.
    scripts/checks/_pytest_diff.py) with no invocation must never be mistaken for one. Only the
    text AFTER the matched invocation token is searched for the target, so a `.py` path that
    precedes the invocation (as in the grep-guarded shape above) is never returned."""
    invocation = _PYTEST_INVOCATION_RE.search(cmd)
    if invocation is None:
        return None
    match = _PYTEST_TARGET_RE.search(cmd, invocation.end())
    if match is None:
        return None
    return match.group(1), match.group(3)


def pytest_node_exists(root: Path, rel_path: str, node: Optional[str]) -> bool:
    """Static (non-executing) check that `rel_path` exists and, if `node` names a
    Class::method/function chain, that chain resolves via an AST walk of module-level and
    nested class/function definitions. Parametrize suffixes (`[...]`) are stripped before
    matching -- this module never tries to enumerate parametrize IDs statically."""
    target = root / rel_path
    if not target.is_file():
        return False
    if not node:
        return True
    first_segment = node.split("[", 1)[0]
    parts = [p for p in first_segment.split("::") if p]
    if not parts:
        return True
    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return False
    scope: list[ast.stmt] = list(tree.body)
    for part in parts:
        found: Optional[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = None
        for stmt in scope:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and stmt.name == part:
                found = stmt
                break
        if found is None:
            return False
        scope = list(found.body)
    return True


def classify_command(acceptance: Optional[str], *, repo_root: Path = ROOT, cap_length: bool = True) -> str:
    """Classify one rec's acceptance command into one of the five BUCKETS -- pure, never
    executes the command itself. Order matters: an empty/prose command is never also flagged
    unsafe; an unsafe command is never also probed for a missing pytest node. cap_length=False
    lifts only the _MAX_SHAPE_LEN check, for a caller that will never execute the command (the
    trailer gate's junit route, rec-4384); every other shape check still applies."""
    stripped = (acceptance or "").strip()
    if is_prose(stripped):
        return PROSE_ONLY
    if _is_unsafe(stripped):
        return UNPROBEABLE_UNSAFE
    target = parse_pytest_target(stripped)
    if target is not None:
        path, node = target
        if not pytest_node_exists(repo_root, path, node):
            return EXPECTED_FAIL_MISSING_NODE
    if _is_malshaped(stripped, cap_length=cap_length):
        return UNPROBEABLE_SHAPE
    return PROBEABLE


def _make_reader(profile: Optional[str] = None) -> Any:
    from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

    return make_reader(profile=profile)


def read_open_recs(
    *,
    rows: Optional[list[dict[str, Any]]] = None,
    reader: Any = None,
    profile: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The one structural read: `current_state("ops_recommendations", row_filter="status =
    'open'")`. `rows` is the test-injection seam (treated as if it were already the read's
    result). Asserts every returned row's `status` is literally "open" -- a cheap standing
    invariant against a filter mis-bind (mirrors scripts.rec_episode's anti-silent-None
    guarantee for its own scoped reads)."""
    live_rows = rows
    if live_rows is None:
        live_reader = reader if reader is not None else _make_reader(profile)
        live_rows = live_reader.current_state(_TABLE, row_filter="status = 'open'")

    for row in live_rows:
        missing = [k for k in _REQUIRED_KEYS if k not in row]
        if missing:
            raise RuntimeError(f"backlog_health.census: row is missing required key(s) {missing}: {row!r}")
        if row.get("status") != "open":
            raise RuntimeError(
                f"backlog_health.census: row {row.get('id')!r} has status={row.get('status')!r}, "
                "expected 'open' -- the row_filter='status = ''open''' read returned a row it "
                "should never have projected."
            )
    return live_rows


def collect_recent_commits(
    *,
    repo_root: Path = ROOT,
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict[str, Any]]:
    """`git log --name-only` over the FULL, unbounded history -- a fixed literal argv over repo
    history, never rec-derived data (see module docstring for why the sole-executor invariant
    exempts this call). A window that truncated before some rec's creation would silently
    collapse a satisfied_candidate into vacuous in classify.py's split -- unbounded avoids that."""
    result = runner(
        ["git", "log", "--pretty=format:\x01%H\x02%cI", "--name-only"],
        cwd=str(repo_root),
        capture_output=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"backlog_health.census: git log failed (exit {result.returncode}): {result.stderr}")

    commits: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    for line in result.stdout.splitlines():
        if line.startswith("\x01"):
            if current is not None:
                commits.append(current)
            sha, _, date = line[1:].partition("\x02")
            current = {"sha": sha, "date": date, "files": []}
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    if current is not None:
        commits.append(current)
    return commits


def run_census(
    *,
    rows: Optional[list[dict[str, Any]]] = None,
    reader: Any = None,
    profile: Optional[str] = None,
    recent_commits: Optional[list[dict[str, Any]]] = None,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Full census: read every open rec, classify each into a bucket, and emit the probe-input
    payload (probeable set only, each entry stamped with an acceptance_sha256 escalate.py rejoins
    verdicts against) plus the recent-commit list classify.py's satisfied-vs-vacuous split needs.
    """
    open_rows = read_open_recs(rows=rows, reader=reader, profile=profile)
    bucket_rows: dict[str, list[dict[str, Any]]] = {b: [] for b in BUCKETS}
    for row in open_rows:
        bucket = classify_command(row.get("acceptance"), repo_root=repo_root)
        bucket_rows[bucket].append(row)

    probe_payload = [
        {
            "id": row["id"],
            "file": row.get("file"),
            "acceptance": row.get("acceptance"),
            "acceptance_sha256": hashlib.sha256((row.get("acceptance") or "").encode("utf-8")).hexdigest(),
        }
        for row in bucket_rows[PROBEABLE]
    ]
    commits = recent_commits if recent_commits is not None else collect_recent_commits(repo_root=repo_root)

    return {
        "open_rows": open_rows,
        "buckets": {b: [row["id"] for row in bucket_rows[b]] for b in BUCKETS},
        "counts": {b: len(bucket_rows[b]) for b in BUCKETS},
        "probe_payload": probe_payload,
        "recent_commits": commits,
    }
