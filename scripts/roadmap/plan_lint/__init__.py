"""Plan lint: deterministic findings derived from a plan's own fields plus committed repo state.

derive(plan, root, report=...) reads the rows of docs/contracts/plan-obligations.yaml `derived_findings` and runs
each by its own `gating` flag: gating rows always, advisory rows only when report=True, so the CI check path never
runs git log, the acceptance subprocess or anything network-backed. Never raises.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scripts.roadmap.plan_lint import companions, headroom
from scripts.roadmap.plan_lint._rows import load_rows

ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_REL = "docs/contracts/plan-obligations.yaml"
_ACCEPTANCE_MODULE = "scripts.roadmap.plan_lint.acceptance"
_ACCEPTANCE_TIMEOUT_S = 60


@dataclass
class LintResult:
    gating: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    plan: dict[str, Any]
    scope: list[dict[str, Any]]
    root: Path
    frozen: list[dict[str, Any]]
    report: bool
    extra_advisory: list[str] = field(default_factory=list)


def _acceptance_lines(ctx: Ctx, module: str = _ACCEPTANCE_MODULE) -> list[str]:
    recs = [r for r in ctx.plan.get("bundled_recommendations") or [] if isinstance(r, str)]
    if not recs:
        return []
    try:
        done = subprocess.run(
            [sys.executable, "-m", module, *recs],
            cwd=ctx.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_ACCEPTANCE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"UNREACHABLE: acceptance runner did not complete ({type(exc).__name__})"]
    if done.returncode != 0:
        return [f"UNREACHABLE: acceptance runner exited {done.returncode}"]
    return [line for line in done.stdout.splitlines() if line.strip()]


_DISPATCH: dict[str, Callable[[Ctx], list[str]]] = {
    "pre_glob_closure": companions.pre_glob_closure,
    "accounting_baseline_strike": companions.accounting_baseline_strike,
    "class_d_amendment_entry": companions.class_d_amendment_entry,
    "registry_shard_disposition": companions.registry_shard_disposition,
    "size_headroom": headroom.size_headroom,
    "unbuilt_same_file": headroom.unbuilt_same_file,
    "acceptance_decidable": _acceptance_lines,
    "frozen_companions": companions.frozen_companions,
}


def _scope_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    scope = plan.get("scope")
    if not isinstance(scope, list):
        return []
    return [r for r in scope if isinstance(r, dict) and isinstance(r.get("file"), str) and isinstance(r.get("action"), str)]


def derive(plan: dict[str, Any], root: Path = ROOT, *, report: bool = False, contract_path: Path | None = None) -> LintResult:
    """Gating and advisory lint lines for one plan dict. Never raises."""
    result = LintResult()
    try:
        rows, frozen, error = load_rows(contract_path or root / _CONTRACT_REL)
        if error:
            result.gating.append(error)
            return result
        ctx = Ctx(plan, _scope_rows(plan), root, frozen, report)
        for missing in sorted(set(rows) - set(_DISPATCH)):
            result.gating.append(f"derived_findings row {missing!r} has no derivation in scripts/roadmap/plan_lint")
        for orphan in sorted(set(_DISPATCH) - set(rows)):
            result.gating.append(f"plan_lint derivation {orphan!r} has no row in {_CONTRACT_REL} derived_findings")
        for row_id in sorted(set(rows) & set(_DISPATCH)):
            gating = rows[row_id]["gating"]
            if not gating and not report:
                continue
            try:
                lines = _DISPATCH[row_id](ctx)
            except Exception as exc:  # noqa: BLE001 -- a derivation fault is a finding on its own row's side
                lines = [f"derived_findings row {row_id!r} raised {type(exc).__name__}: {exc}"]
            (result.gating if gating else result.advisory).extend(lines)
        result.advisory.extend(ctx.extra_advisory)
    except Exception as exc:  # noqa: BLE001 -- derive never raises
        result.gating.append(f"plan_lint raised {type(exc).__name__}: {exc}")
    return result
