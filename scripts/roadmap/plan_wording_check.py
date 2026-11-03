"""Wording check: rehearse a plan's always-ask wording through the build's own fast tier before approval.

The planner places the sidecar's words in a scratch copy T of main (a git worktree) and runs this tool. It verifies
the placed words against the sidecar, settles the companions the build would commit with them (budget ratchets, the
decisions index), runs `scripts.validate --pre` in T and prints one verdict, its digest/base/selection facts and the
gate_rounds row to paste. The row is recorded for the plan-PR check and the critic to read, never re-run.

CLI: python -m scripts.roadmap.plan_wording_check --slug S --tree T [--sidecar P] [--plan P]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.roadmap import approved_text, plan_document, wording_check_baseline, wording_check_pending

ROOT = Path(__file__).resolve().parents[2]
PRE_TIMEOUT_S = 900
MEASURE_TIMEOUT_S = 120
ATTRIBUTION_TIMEOUT_S = 600
_BASELINE = wording_check_baseline.BASELINE
_COMPANIONS = (
    "config/prose_budgets.yaml",
    "config/structural_size_budgets.yaml",
    "docs/decisions-index.json",
    _BASELINE,
)
_BUDGET_FILES = _COMPANIONS[:2]
_INDEX = _COMPANIONS[2]
_DECISIONS = "docs/DECISIONS.md"
_MARKER = "# raise-approved: dec-"
_MANIFEST = Path("logs/debug/selection-manifest.json")
_VENV = ".venv"
_ECHO_LINES = 20
_VERBS = {"create": "creates", "modify": "modifies"}

Runner = Callable[[list[str], Path, int], "subprocess.CompletedProcess[str]"]


class CheckFailure(Exception):
    """A refusal before the fast tier runs; the message is the reason."""


@dataclass
class Result:
    verdict: str
    reason: str = ""
    digest: str = ""
    base: str = ""
    selection: str = ""
    echo: str = ""
    round: int = 1
    pairs: tuple[wording_check_pending.Pair, ...] = ()


def default_runner(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _git(tree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if check and result.returncode != 0:
        raise CheckFailure(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _run(runner: Runner, cmd: list[str], tree: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return runner(cmd, tree, timeout)
    except subprocess.TimeoutExpired as exc:
        raise CheckFailure(f"{' '.join(cmd)} timed out after {timeout} s") from exc
    except OSError as exc:
        raise CheckFailure(f"{' '.join(cmd)} could not start: {exc}") from exc


def _check_base(tree: Path) -> str:
    base = _git(tree, "rev-parse", "HEAD").stdout.strip()
    if _git(tree, "merge-base", "--is-ancestor", base, "origin/main", check=False).returncode != 0:
        raise CheckFailure(f"HEAD {base[:12]} of the scratch tree is not an ancestor of origin/main")
    return base


def _changed_paths(tree: Path) -> set[str]:
    out = _git(tree, "status", "--porcelain", "--untracked-files=all").stdout
    paths = {line[3:].split(" -> ")[-1].strip().strip('"') for line in out.splitlines() if line.strip()}
    return {p for p in paths if p != _VENV and not p.startswith(f"{_VENV}/")}


def _check_scope(tree: Path, sidecar_files: set[str]) -> None:
    stray = sorted(_changed_paths(tree) - sidecar_files - set(_COMPANIONS))
    if stray:
        raise CheckFailure(f"the scratch tree changes files outside the sidecar and its companions: {', '.join(stray)}")


def _base_text(tree: Path, rel: str, base: str) -> str:
    return _git(tree, "show", f"{base}:{rel}").stdout


def _keep_marked_lines(base_text: str, tree_text: str) -> str:
    """Line-level reset: a changed line survives only when it carries a raise-approved marker."""
    old, new = base_text.splitlines(keepends=True), tree_text.splitlines(keepends=True)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            out += new[j1:j2]
        elif tag == "replace" and i2 - i1 == j2 - j1:
            out += [n if _MARKER in n else o for o, n in zip(old[i1:i2], new[j1:j2])]
        elif new[j1:j2] and all(_MARKER in line for line in new[j1:j2]):
            out += new[j1:j2]
        else:
            out += old[i1:i2]
    return "".join(out)


def _reset_budget_changes(tree: Path, base: str) -> None:
    for rel in _BUDGET_FILES:
        path = tree / rel
        if path.exists():
            path.write_text(
                _keep_marked_lines(_base_text(tree, rel, base), path.read_text(encoding="utf-8")), encoding="utf-8"
            )


def _budget_key_sizes(surfaces: dict[str, dict], sidecar_files: set[str]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for surface, data in surfaces.items():
        members = {entry["path"]: entry["prose_bytes"] for entry in data.get("files", [])}
        if surface == "S1" and sidecar_files & members.keys():
            sizes["root_ambient_load_set"] = data["prose_bytes"]
        elif surface != "S1":
            sizes.update({path: size for path, size in members.items() if path in sidecar_files})
    return sizes


def _lower_prose_budgets(tree: Path, sidecar_files: set[str], runner: Runner) -> None:
    path = tree / "config/prose_budgets.yaml"
    if not path.exists():
        return
    code = "import json; from scripts.preflight.prose_context import measure_prose_context as m; print(json.dumps(m()))"
    proc = _run(runner, ["bin/venv-python", "-c", code], tree, MEASURE_TIMEOUT_S)
    try:
        surfaces = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise CheckFailure(f"prose measurement in the scratch tree did not return JSON (exit {proc.returncode})") from exc
    text = path.read_text(encoding="utf-8")
    for key, size in _budget_key_sizes(surfaces, sidecar_files).items():
        pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*)(\d+)", re.MULTILINE)
        text = pattern.sub(lambda m: f"{m.group(1)}{min(int(m.group(2)), size)}", text, count=1)  # noqa: B023
    path.write_text(text, encoding="utf-8")


def _settle_decisions_index(tree: Path, sidecar_files: set[str], runner: Runner) -> None:
    if _INDEX in _changed_paths(tree):
        _git(tree, "checkout", "HEAD", "--", _INDEX)
    if _DECISIONS in sidecar_files:
        proc = _run(runner, ["bin/venv-python", "-m", "scripts.decisions_index", "--write"], tree, PRE_TIMEOUT_S)
        if proc.returncode != 0:
            raise CheckFailure(f"scripts.decisions_index --write failed in the scratch tree: {proc.stdout[-300:]}")


def _link_venv(tree: Path) -> None:
    link = tree / _VENV
    if not link.exists() and not link.is_symlink() and (ROOT / _VENV).exists():
        link.symlink_to(ROOT / _VENV, target_is_directory=True)


def _selection_status(tree: Path, base: str) -> str:
    try:
        manifest = json.loads((tree / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "degraded"
    complete = (
        isinstance(manifest, dict)
        and manifest.get("sha") == base
        and not manifest.get("fallback")
        and manifest.get("capped") is False
        and not manifest.get("deferred")
    )
    return "complete" if complete else "degraded"


def _failing_lines(output: str) -> str:
    lines = output.splitlines()
    failing: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == "Failed checks:":
            in_block = True
            failing.append(line)
        elif in_block and line.startswith("  - "):
            failing.append(line)
        else:
            in_block = False
            if "FAIL" in line:
                failing.append(line)
    return "\n".join((failing or lines)[-_ECHO_LINES:])


def _load_plan(plan_path: Path) -> plan_document.PlanDocument | None:
    try:
        return plan_document.load(plan_path)
    except Exception as exc:  # noqa: BLE001 -- any unreadable or invalid plan means no pending evaluators and round 1
        first = (str(exc).splitlines() or [type(exc).__name__])[0]
        print(f"note: plan {plan_path} cannot be loaded ({first}); no pending evaluators, round 1", file=sys.stderr)
        return None


def _round_of(plan: plan_document.PlanDocument | None) -> int:
    return 1 + sum(1 for row in plan.gate_rounds if row.gate == "wording-check") if plan else 1


def run(
    slug: str,
    tree: Path,
    sidecar_path: Path | None = None,
    runner: Runner = default_runner,
    plan_path: Path | None = None,
) -> Result:
    """Rehearse the sidecar's words in `tree`; a Result with verdict PASS or FAIL (reason set when FAIL)."""
    digest = base = ""
    plan = _load_plan(plan_path or ROOT / "docs" / "plans" / f"PLAN-{slug}.yaml")
    round_ = _round_of(plan)
    try:
        sidecar_file = sidecar_path or ROOT / approved_text.sidecar_rel(slug)
        try:
            sidecar = approved_text.parse_sidecar(sidecar_file.read_text(encoding="utf-8"))
        except (OSError, approved_text.ApprovedTextError) as exc:
            raise CheckFailure(f"sidecar {sidecar_file} cannot be read: {exc}") from exc
        digest = approved_text.digest(sidecar)
        sidecar_files = {block.file for block in sidecar.blocks}
        base = _check_base(tree)
        _check_scope(tree, sidecar_files)
        mismatches = approved_text.verify(sidecar, tree, base)
        if mismatches:
            raise CheckFailure("approved text does not verify in the scratch tree: " + "; ".join(mismatches))
        _link_venv(tree)
        _reset_budget_changes(tree, base)
        _lower_prose_budgets(tree, sidecar_files, runner)
        _settle_decisions_index(tree, sidecar_files, runner)
        reason = wording_check_baseline.settle(tree, _changed_paths(tree), sidecar_files, runner, MEASURE_TIMEOUT_S)
        if reason:
            raise CheckFailure(reason)
        pairs = tuple(wording_check_pending.pending_pairs(tree, sidecar_files, plan))
        (tree / _MANIFEST).unlink(missing_ok=True)
        proc = _run(runner, ["bin/venv-python", "-m", "scripts.validate", "--pre"], tree, PRE_TIMEOUT_S)
    except CheckFailure as exc:
        return Result("FAIL", reason=str(exc), digest=digest, base=base, round=round_)
    output = (proc.stdout or "") + (proc.stderr or "")
    selection = _selection_status(tree, base)
    if proc.returncode == 0:
        return Result("PASS", digest=digest, base=base, selection=selection, round=round_)
    reason = "the fast tier (scripts.validate --pre) failed"
    if pairs:
        why = wording_check_pending.attribute(tree, output, list(pairs), runner, ATTRIBUTION_TIMEOUT_S)
        if why is None:
            return Result("PASS", "", digest, base, selection, "", round_, pairs)
        reason = f"{reason}; {why}"
    return Result("FAIL", reason, digest, base, selection, _failing_lines(output), round_)


def _render(slug: str, result: Result) -> str:
    if not result.selection:
        return f"WORDING-CHECK verdict=FAIL reason: {result.reason}"
    count = f" pending_evaluators={len(result.pairs)}" if result.pairs else ""
    lines = [
        f"WORDING-CHECK verdict={result.verdict} digest={result.digest} base={result.base} selection={result.selection}{count}"
    ]
    if result.pairs:
        lines.append(f"fast tier red only on {len(result.pairs)} plan-built evaluator(s); clean with them stood in")
        lines += [
            f"pending: docs/contracts/{name} evaluator {check} (the plan {_VERBS[action]} {module})"
            for name, check, module, action in result.pairs
        ]
    if result.reason:
        lines.append(f"reason: {result.reason}")
    if result.echo:
        lines.append(result.echo)
    pending = f", pending_evaluators: {len(result.pairs)}" if result.pairs else ""
    lines.append(
        f"  - {{gate: wording-check, round: {result.round}, verdict: {result.verdict}, digest: {result.digest}, "
        f"base: {result.base}, selection: {result.selection}{pending}}}  # pragma: allowlist secret"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.roadmap.plan_wording_check")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, default=None)
    parser.add_argument("--plan", type=Path, default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args.slug, args.tree, args.sidecar, runner=default_runner, plan_path=args.plan)
    print(_render(args.slug, result))
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
