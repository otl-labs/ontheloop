"""Gating derivations (pre_glob_closure, accounting_baseline_strike, class_d_amendment_entry,
registry_shard_disposition) plus the advisory frozen_companions row. Each takes plan_lint's Ctx and returns lines."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from scripts.roadmap.plan_lint import Ctx

_BASELINE = "config/check_accounting_baseline.yaml"
_SHARD_DIR = "config/agent/verification_registry/entries"


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _module_of(rel: str) -> str:
    stem = rel[:-3]
    return (stem[: -len("/__init__")] if stem.endswith("/__init__") else stem).replace("/", ".")


def _py_rows(ctx: Ctx) -> list[dict[str, Any]]:
    return [r for r in ctx.scope if r["file"].endswith(".py") and r["action"] in ("Create", "Modify")]


def _undeclared_reach(ctx: Ctx, rows: list[dict[str, Any]]) -> list[str]:
    from scripts.checks.deps import validate_pre_glob_closure as closure  # noqa: PLC0415

    view = closure._closure_view(ctx.root)
    reach = {e.name: closure._closure_modules(view, e.module) for e in closure._gated_entries()}
    lines = []
    for row in rows:
        names = sorted(n for n, mods in reach.items() if _module_of(row["file"]) in mods)
        if names:
            lines.append(f"{row['file']} is reached by gated --pre Entries ({', '.join(names)}); declare new_imports")
    return lines


def pre_glob_closure(ctx: Ctx) -> list[str]:
    rows = _py_rows(ctx)
    edges, declared = [], False
    for row in rows:
        for target in row.get("new_imports") or []:
            declared = True
            edges.append((_module_of(row["file"]), target))
    if ctx.report:
        ctx.extra_advisory.extend(_undeclared_reach(ctx, [r for r in rows if not r.get("new_imports")]) if rows else [])
    if not declared:
        return []
    from scripts.checks.deps import validate_pre_glob_closure as closure  # noqa: PLC0415

    creates = {_module_of(r["file"]): r["file"] for r in ctx.scope if r["action"] == "Create" and r["file"].endswith(".py")}
    uncovered = closure.uncovered_after_edges(edges, creates, ctx.root)
    files = {r["file"] for r in ctx.scope}
    lines = []
    for name, paths in sorted(uncovered.items()):
        entry = closure.registry._ALL_ENTRIES[name]
        manifest = f"{entry.module.rsplit('.', 1)[0].replace('.', '/')}/_manifest.py"
        if manifest not in files:
            lines.append(
                f"declared new_imports leave {', '.join(paths)} outside the pre_globs of Entry {name}; "
                f"{manifest} is required in scope"
            )
    return lines


def accounting_baseline_strike(ctx: Ctx) -> list[str]:
    from scripts.checks import registry  # noqa: PLC0415

    data = _yaml(ctx.root / _BASELINE)
    names = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(names, list) or any(r["file"] == _BASELINE for r in ctx.scope):
        return []
    modules = {}
    for name in names:
        entry = registry._ALL_ENTRIES.get(name)
        if entry is not None:
            modules[entry.module.replace(".", "/") + ".py"] = name
    return [
        f"{r['file']} is the module of baselined check {modules[r['file']]}; {_BASELINE} is required in scope "
        "(Decision 170 touch-it-fix-it)"
        for r in ctx.scope
        if r["action"] == "Modify" and r["file"] in modules
    ]


def class_d_amendment_entry(ctx: Ctx) -> list[str]:
    from scripts.checks.contracts import _population  # noqa: PLC0415

    lines = []
    for row in ctx.scope:
        file = row["file"]
        if row["action"] != "Modify" or not fnmatch.fnmatchcase(file, "docs/contracts/*.yaml"):
            continue
        if _population.classify_file(_yaml(ctx.root / file)) == "class_d" and "amendment_log" not in str(
            row.get("purpose", "")
        ):
            lines.append(f"{file} is a Class D contract; its row's purpose must name a new amendment_log entry")
    return lines


def registry_shard_disposition(ctx: Ctx) -> list[str]:
    tests = {r["file"] for r in ctx.scope if r["action"] in ("Modify", "Delete") and r["file"].startswith("tests/")}
    tests = {t for t in tests if t.endswith(".py")}
    if not tests:
        return []
    plan_text = json.dumps(ctx.plan, default=str)
    in_scope = {r["file"] for r in ctx.scope}
    lines = []
    for shard in sorted((ctx.root / _SHARD_DIR).glob("*.yaml")):
        data = _yaml(shard)
        spec = data.get("check_spec") if isinstance(data, dict) else None
        if not isinstance(spec, dict):
            continue
        text = f"{spec.get('node_id', '')} {spec.get('command', '')}"
        hit = sorted(t for t in tests if t in text)
        check_id = str(data.get("check_id", shard.stem))
        rel = f"{_SHARD_DIR}/{shard.name}"
        if hit and rel not in in_scope and check_id not in plan_text:
            lines.append(f"graduated shard {check_id} names {hit[0]}; put {rel} in scope or name {check_id} as kept")
    return lines


def frozen_companions(ctx: Ctx) -> list[str]:
    lines = []
    for rule in ctx.frozen:
        source = str(rule.get("source", ""))
        for row in ctx.scope:
            if fnmatch.fnmatchcase(row["file"], source):
                lines.append(f"{row['file']}: {rule.get('test')} breaks when {rule.get('breaks_when')}")
    return lines
