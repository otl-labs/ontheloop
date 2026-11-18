"""Advisory derivations: size_headroom and unbuilt_same_file."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from scripts.roadmap.plan_lint import Ctx

_PY_LIMIT = 500
_NEAR_CAP_DENOMINATOR = 20
_PLAN_PATH_PREFIX = "docs/plans/PLAN-"
_PROSE_NOTE = (
    "the prose ratchet pins every entry to its measured size: a shrinking edit lowers its entry, growth needs compaction "
    "or a `# raise-approved` marker"
)


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _prose_budgets(root: Path) -> dict[str, int]:
    data = _load(root / "config" / "prose_budgets.yaml")
    flat: dict[str, int] = {}
    for section in (data or {}).values() if isinstance(data, dict) else []:
        if isinstance(section, dict):
            flat.update({k: v for k, v in section.items() if isinstance(v, int)})
    return flat


def _py_sloc_limit(root: Path, rel: str, text: str) -> tuple[int, int, str]:
    sloc = len([ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")])
    data = _load(root / "config" / "sloc_budgets.yaml")
    budgets = data.get("budgets") if isinstance(data, dict) else None
    limit = budgets.get(rel, _PY_LIMIT) if isinstance(budgets, dict) else _PY_LIMIT
    return sloc, int(limit), "SLOC"


def _structural(rel: str, text: str) -> tuple[int, int, str] | None:
    from scripts.checks.structural import _classify  # noqa: PLC0415

    slug = _classify.classify_path(rel, text=text)
    reg = _classify.load_registry()
    row = next((r for r in reg.get("classes") or [] if r.get("slug") == slug), None)
    if row is None or not row.get("governed") or not isinstance(row.get("limit"), int):
        return None
    limit = (reg.get("budgets") or {}).get(rel, row["limit"])
    return _classify.effective_lines(text), int(limit), f"effective lines ({slug})"


def _git_out(root: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
    )
    return done.stdout.strip() if done.returncode == 0 else None


def _byte_delta(root: Path, rel: str, size: int) -> str:
    """The file's byte change since the newest origin/main commit at least 30 days old."""
    sha = _git_out(root, "rev-list", "-1", "--before=30.days", "origin/main")
    if not sha:
        return "history unavailable"
    old = _git_out(root, "cat-file", "-s", f"{sha}:{rel}")
    return "new" if old is None else f"{size - int(old):+d} B"


def _byte_note(root: Path, rel: str, text: str) -> tuple[str, bool]:
    """(headroom column text, near-cap) for a structural row carrying max_bytes; ("", False) otherwise."""
    from scripts.checks.structural import _classify  # noqa: PLC0415

    reg = _classify.load_registry()
    row = next((r for r in reg.get("classes") or [] if r.get("slug") == _classify.classify_path(rel, text=text)), None)
    if row is None or not isinstance(row.get("max_bytes"), int):
        return "", False
    size = len(text.encode("utf-8"))
    limit = int(row["max_bytes"])
    directory, _, name = rel.rpartition("/")
    roster = reg.get("byte_budgets") or {}
    if directory == "docs/contracts" and isinstance(roster.get(name), int):
        limit = int(roster[name])
    per_line = size // max(1, _classify.effective_lines(text))
    near = (limit - size) * _NEAR_CAP_DENOMINATOR < limit
    note = f"; {size} of {limit} B, {per_line} B per effective line, 30-day delta {_byte_delta(root, rel, size)}"
    return note, near


def _relief(rel: str, text: str) -> str:
    from scripts.checks.structural import _classify  # noqa: PLC0415

    row = next(
        (
            r
            for r in _classify.load_registry().get("classes") or []
            if r.get("slug") == _classify.classify_path(rel, text=text)
        ),
        {},
    )
    return f"; relief valves: {row.get('relief_valve', '')}"


def size_headroom(ctx: Ctx) -> list[str]:
    prose = _prose_budgets(ctx.root)
    lines = []
    for row in ctx.scope:
        rel = row["file"]
        path = ctx.root / rel
        if row["action"] != "Modify" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        note = ""
        if rel.endswith(".py"):
            measured: tuple[int, int, str] | None = _py_sloc_limit(ctx.root, rel, text)
        elif rel in prose:
            measured, note = (len(text.encode("utf-8")), prose[rel], "B"), f"; {_PROSE_NOTE}"
        else:
            measured = _structural(rel, text)
        if measured is None:
            continue
        size, limit, unit = measured
        room = limit - size
        near_lines = room * _NEAR_CAP_DENOMINATOR < limit
        byte_note, near_bytes = ("", False) if rel.endswith(".py") or rel in prose else _byte_note(ctx.root, rel, text)
        near = " NEAR_CAP" if near_lines or near_bytes else ""
        relief = _relief(rel, text) if byte_note and near else ""
        lines.append(f"{rel}: {size} of {limit} {unit}, headroom {room}{near}{note}{byte_note}{relief}")
    return lines


def _recent_plan_paths(root: Path) -> list[str]:
    done = subprocess.run(
        ["git", "log", "--diff-filter=A", "--since=30.days", "--name-only", "--pretty=format:", "--", "docs/plans/"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if done.returncode != 0:
        return []
    return sorted({p for p in done.stdout.splitlines() if p.startswith(_PLAN_PATH_PREFIX) and p.endswith(".yaml")})


def unbuilt_same_file(ctx: Ctx) -> list[str]:
    own = ctx.plan.get("plan_path") or f"{_PLAN_PATH_PREFIX}{ctx.plan.get('slug')}.yaml"
    mine = {r["file"] for r in ctx.scope}
    lines = []
    for rel in _recent_plan_paths(ctx.root):
        data = _load(ctx.root / rel)
        if rel == own or not isinstance(data, dict):
            continue
        version = data.get("schema_version")
        if not isinstance(version, int) or version < 4 or data.get("implementation_declared") is True:
            continue
        shared = sorted(mine & {r.get("file") for r in data.get("scope") or [] if isinstance(r, dict)})
        if shared:
            lines.append(f"{rel} (unbuilt) also scopes {', '.join(shared)}; whichever builds second rebases")
    return lines
