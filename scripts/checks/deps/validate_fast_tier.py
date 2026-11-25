"""Class D evaluator for docs/contracts/fast-tier.yaml (Decision 168; the fast-tier consolidation, rec-5445).

Derive-and-assert: the contract's selection cap must equal the selector's live CAP, and its forced-scope ceiling must
equal both selection_budget.CEILING_SECONDS and the derivation the contract states from ci.yml's pr-validate job
timeout less every other bounded step's timeout and a five-minute margin. The two constants are read from their
modules' source with ast, not imported, so the check's import closure stays inside its pre_globs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_PARTS = ("docs", "contracts", "fast-tier.yaml")
_CI_PARTS = (".github", "workflows", "ci.yml")
_CAP_SOURCE = ("scripts", "checks", "deps", "affected_tests.py")
_CEILING_SOURCE = ("scripts", "checks", "deps", "selection_budget.py")
_CHECK_LABEL = "Fast-tier contract"
_JOB = "pr-validate"
_FAST_TIER_RUN = "validate --pre"
_MARGIN_MINUTES = 5
_BINDINGS = 3


def load_mapping(root: Path, parts: tuple[str, ...]) -> dict:
    data = yaml.safe_load(root.joinpath(*parts).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{parts[-1]}: top-level is not a mapping")
    return data


def module_constant(root: Path, parts: tuple[str, ...], name: str) -> object:
    """The literal a module assigns to `name` at top level, read with ast (never imported or evaluated)."""
    for node in ast.parse(root.joinpath(*parts).read_text(encoding="utf-8")).body:
        value: ast.expr
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(value)
    raise ValueError(f"{parts[-1]}: no top-level literal {name}")


def derived_ceiling_seconds(workflow: dict) -> int | None:
    """pr-validate's timeout-minutes less every bounded step's but the fast tier's own, less the margin, in seconds.

    None (underived) when the job, its timeout or the fast-tier step is missing, or when another step's timeout is not
    an int literal, so an expression timeout fails the check instead of reading as unbounded.
    """
    job = (workflow.get("jobs") or {}).get(_JOB)
    if not isinstance(job, dict) or not isinstance(job.get("timeout-minutes"), int):
        return None
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    if not any(_FAST_TIER_RUN in str(s.get("run", "")) for s in steps):
        return None
    bounded: list[int] = []
    for step in steps:
        if _FAST_TIER_RUN in str(step.get("run", "")) or "timeout-minutes" not in step:
            continue
        if not isinstance(step["timeout-minutes"], int):
            return None
        bounded.append(step["timeout-minutes"])
    return (job["timeout-minutes"] - sum(bounded) - _MARGIN_MINUTES) * 60


def problems(contract: dict, workflow: dict, cap: object, ceiling_constant: object) -> list[str]:
    errors: list[str] = []
    raw_selection = contract.get("selection")
    selection: dict = raw_selection if isinstance(raw_selection, dict) else {}
    if selection.get("cap_modules") != cap:
        errors.append(f"selection.cap_modules {selection.get('cap_modules')!r} != affected_tests.CAP {cap!r}")
    raw_forced = contract.get("forced_scope")
    forced: dict = raw_forced if isinstance(raw_forced, dict) else {}
    ceiling = forced.get("ceiling_seconds")
    if ceiling != ceiling_constant:
        errors.append(f"forced_scope.ceiling_seconds {ceiling!r} != selection_budget.CEILING_SECONDS {ceiling_constant!r}")
    derived = derived_ceiling_seconds(workflow)
    if derived is None or ceiling != derived:
        errors.append(f"forced_scope.ceiling_seconds {ceiling!r} != the ci.yml derivation {derived!r}")
    return errors


def tree_problems(root: Path) -> list[str]:
    """The contract's disagreements with the code and ci.yml under `root`; raises when an input cannot be read."""
    return problems(
        load_mapping(root, _CONTRACT_PARTS),
        load_mapping(root, _CI_PARTS),
        module_constant(root, _CAP_SOURCE, "CAP"),
        module_constant(root, _CEILING_SOURCE, "CEILING_SECONDS"),
    )


@registry.register("validate_fast_tier", owner="platform")
def validate_fast_tier(failed: list[str], root: Path | None = None) -> None:
    """Assert the fast-tier contract's cap and ceiling agree with the selector, the budget and ci.yml."""
    print("\n=== Fast-tier contract (fast-tier.yaml) ===")
    try:
        errors = tree_problems(root if root is not None else _common.ROOT)
    except (OSError, ValueError, SyntaxError, yaml.YAMLError) as exc:
        print(f"FAIL: could not load inputs: {type(exc).__name__}: {exc}")
        failed.append(_CHECK_LABEL)
        registry.skipped(f"inputs unreadable: {type(exc).__name__}")
        return
    registry.examined(_BINDINGS, unit="fast_tier_bindings")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        failed.append(_CHECK_LABEL)
    else:
        print("PASS: cap and ceiling agree with affected_tests, selection_budget and ci.yml")
