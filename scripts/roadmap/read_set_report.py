"""Measure-only report of the bytes a planning run and an implementing run are sent to read, by band.

Reads the band table at config/agent/read_sets.yaml; fails nothing and gates nothing (rec-5256).
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

BANDS = ("always_read", "gate_subagent", "on_demand")
READINGS = ("primary", "alternative")
DEFAULT_CONFIG = "config/agent/read_sets.yaml"
_TARGET_SUFFIXES = (".md", ".yaml", ".yml", ".json")
_SKIPPED_PREFIXES = ("docs/plans/", "logs/")
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:md|yaml|yml|json)")
_SKILL_RE = re.compile(r"(?<![\w-])([a-z][a-z0-9-]*)`? skill\b")


class GitError(RuntimeError):
    """A git command failed (unreadable ref, not a repository)."""


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip() or 'exit ' + str(done.returncode)}")
    return done.stdout


def load_read_sets(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return None, f"cannot load {path}: {exc}"
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, dict) or not runs:
        return None, f"{path}: top-level 'runs' mapping missing"
    for name, run in runs.items():
        files = run.get("files") if isinstance(run, dict) else None
        if not isinstance(files, list) or not all(
            isinstance(r, dict) and r.get("band") in BANDS and r.get("path") for r in files
        ):
            return None, f"{path}: run '{name}' needs files rows with a path and a band in {BANDS}"
    return data, None


def file_sizes(root: Path, ref: str | None = None) -> dict[str, int]:
    if ref is None:
        sizes: dict[str, int] = {}
        for rel in _git(root, "ls-files", "-z").split("\0"):
            if not rel:
                continue
            try:
                sizes[rel] = (root / rel).stat().st_size
            except OSError:
                continue
        return sizes
    sizes = {}
    for line in _git(root, "ls-tree", "-r", "-l", "-z", ref).split("\0"):
        meta, _, rel = line.partition("\t")
        parts = meta.split()
        if rel and len(parts) == 4 and parts[1] == "blob" and parts[3].isdigit():
            sizes[rel] = int(parts[3])
    return sizes


def _read_text(root: Path, rel: str, ref: str | None) -> str:
    try:
        if ref is None:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        return _git(root, "show", f"{ref}:{rel}")
    except (OSError, GitError):
        return ""


def named_paths(text: str, tracked: set[str] | dict[str, int]) -> set[str]:
    found: set[str] = set()
    for tok in _PATH_RE.findall(text):
        for cand in (tok, f"docs/contracts/{tok}"):
            if cand in tracked and cand.endswith(_TARGET_SUFFIXES) and not cand.startswith(_SKIPPED_PREFIXES):
                found.add(cand)
                break
    for name in _SKILL_RE.findall(text):
        cand = f".claude/skills/{name}/SKILL.md"
        if cand in tracked:
            found.add(cand)
    return found


def band_totals(run: dict[str, Any], sizes: dict[str, int], reading: str) -> dict[str, int]:
    totals = dict.fromkeys(BANDS, 0)
    for row in run["files"]:
        band = row.get("alt_band", row["band"]) if reading == "alternative" else row["band"]
        totals[band] += sizes.get(row["path"], 0)
    return totals


def _tokens(totals: dict[str, int]) -> int:
    return (totals["always_read"] + totals["gate_subagent"]) // 4


def _band_line(name: str, label: str, reading: str, totals: dict[str, int]) -> str:
    return (
        f"{name} @{label} {reading} always_read={totals['always_read']} gate_subagent={totals['gate_subagent']} "
        f"on_demand={totals['on_demand']} total={sum(totals.values())} tokens={_tokens(totals)}"
    )


def _listed(run: dict[str, Any]) -> set[str]:
    return {
        r["path"] for key in ("files", "extra", "exclude") for r in run.get(key) or [] if isinstance(r, dict) and "path" in r
    }


def _run_lines(name: str, run: dict[str, Any], root: Path, sizes: dict[str, int], ref: str | None, files: bool) -> list[str]:
    label = ref or "worktree"
    lines: list[str] = []
    current: dict[str, dict[str, int]] = {}
    for reading in READINGS:
        current[reading] = band_totals(run, sizes, reading)
        lines.append(_band_line(name, label, reading, current[reading]))
    for base in run.get("baselines") or []:
        for reading in READINGS:
            stored = {b: int(base.get(reading, {}).get(b, 0)) for b in BANDS}
            delta = sum(current[reading].values()) - sum(stored.values())
            lines.append(
                f"{name} baseline {base.get('boundary')} {reading} always_read={stored['always_read']} "
                f"gate_subagent={stored['gate_subagent']} on_demand={stored['on_demand']} total={sum(stored.values())} "
                f"tokens={_tokens(stored)} delta_total={delta:+d}"
            )
    lines.append(f"{name} extra total={sum(sizes.get(r['path'], 0) for r in run.get('extra') or [])}")
    scanned: set[str] = set()
    for entry in run.get("entry_files") or []:
        scanned |= named_paths(_read_text(root, entry, ref), sizes)
    unlisted = sorted(scanned - _listed(run))
    if ref:
        lines.append(f"{name} @{ref} drift (curated at HEAD; old-ref paths expected)")
    lines += [f"{name} unlisted {p} {sizes.get(p, 0)}" for p in unlisted] or [f"{name} unlisted: none"]
    if files:
        for row in run["files"]:
            lines.append(f"{name} file {row['path']} {row['band']} {row.get('alt_band', '-')} {sizes.get(row['path'], 0)}")
    return lines


def build_read_set_report(root: Path, ref: str | None = None, files: bool = False, config: Path | None = None) -> str:
    data, error = load_read_sets(config or root / DEFAULT_CONFIG)
    if data is None:
        return f"read_set_report: config error: {error}"
    try:
        sizes = file_sizes(root, ref)
    except GitError as exc:
        return f"read_set_report: git error: {exc}"
    if ref and not sizes:
        return f"read_set_report: git error: ref {ref} lists no files"
    lines: list[str] = []
    for name, run in data["runs"].items():
        lines += _run_lines(name, run, root, sizes, ref, files)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report read-set bytes by band (measure-only; always exits 0).")
    parser.add_argument("--ref", default=None)
    parser.add_argument("--files", action="store_true")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    print(build_read_set_report(args.root, args.ref, args.files, args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
