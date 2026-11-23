"""Derived CI RCA coverage table: what each ci-rca.yml step runs or reads, and how it is proven (rec-5537).

A step's direct inputs are read from the workflow itself (run bodies, with/env/if values, local actions
and the shell scripts they name); its closure is the static import closure of every module it runs. The
table (config/agent/ci_rca/coverage.yaml) holds one row per step: how the step is proven, plus only the
declared inputs no derivation can see. An unresolved reference is an error, never a skip.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts import dependency_graph
from scripts.checks.ci_guards._workflow_shell_bodies import _step_identity
from scripts.checks.ci_guards.validate_workflow_dependency_install import (
    _MODULE_AFTER_RE,
    _blank_shell_comments,
    _module_to_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_REL = ".github/workflows/ci-rca.yml"
TABLE_REL = "config/agent/ci_rca/coverage.yaml"
_SMOKE_JOB_IF = "needs.select.outputs.run == 'true'"
_UNMASKED_STEP_IF = {"", "success()", "!cancelled()"}
_VERIFY_PREFIX = "bin/venv-python -m scripts.ci_rca.smoke verify "
_VERIFY_FORBIDDEN = ("|", "&", ";", "`", "$(", "<", ">", "\n")

_ACTION_PREFIX_RE = re.compile(
    r"(?:\$\(dirname \"\$0\"\)|\$\(dirname \"\$\{BASH_SOURCE\[0\]\}\"\)|\$\{GITHUB_ACTION_PATH\}"
    r"|\$GITHUB_ACTION_PATH|\$\{\{ github\.action_path \}\})/([\w./-]+)"
)
_OTHER_PREFIX_RE = re.compile(r"(?:\$\{\{ [\w.]+ \}\}|\$\{\w+\}|\$\w+)/")
_PATH_RE = re.compile(r"(?<![\w./$}-])((?:scripts|src|config|bin|tests|\.claude|\.github)/[\w./-]+)")
_JSON_SUFFIX = ".json"


@dataclass
class _Scan:
    files: set[str] = field(default_factory=set)
    seeds: list[tuple[str, bool]] = field(default_factory=list)
    externals: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)


@dataclass
class Coverage:
    root: Path
    steps: dict[str, _Scan]
    table: dict[str, Any]
    errors: list[str]
    smoke: dict[str, _Scan] = field(default_factory=dict)
    smoke_wf: dict[str, Any] = field(default_factory=dict)
    _graph: Any = None

    def _scan(self, key: str) -> _Scan:
        return self.steps[key] if key in self.steps else self.smoke[key]

    def direct(self, key: str) -> set[str]:
        return set(self._scan(key).files)

    def _seeds(self, key: str) -> list[tuple[str, bool]]:
        seeds = list(self._scan(key).seeds)
        for rel in self._declared_list(key):
            seeds.extend(_py_seed(self.root, rel))
        return seeds

    def _declared_list(self, key: str) -> list[str]:
        if key in self.steps:
            return [str(p) for p in self._row(key).get("declared_inputs") or []]
        return [
            str(p) for k, row in self._smoke_rows() if row.get("smoke_step") == key for p in row.get("declared_inputs") or []
        ]

    def _smoke_rows(self) -> list[tuple[str, dict[str, Any]]]:
        rows = self.table.get("steps")
        rows = rows if isinstance(rows, dict) else {}
        return [(k, r) for k, r in rows.items() if isinstance(r, dict) and r.get("proven_by") == "main_smoke"]

    def closure(self, key: str) -> set[str]:
        sub = self._import_graph()
        reached: set[str] = set()
        queue = list(self._seeds(key))
        while queue:
            module, with_ancestors = queue.pop()
            if module in reached:
                continue
            reached.add(module)
            if module in sub:
                queue.extend((m, True) for m in dependency_graph.forward_closure(sub, module))
            if with_ancestors:
                parts = module.split(".")
                for n in range(1, len(parts)):
                    queue.append((".".join(parts[:n]), False))
        files: set[str] = set()
        for module in reached:
            path = _module_to_path(module, self.root)
            if path is not None:
                files.add(path.relative_to(self.root).as_posix())
        return files - self.direct(key)

    def _import_graph(self) -> Any:
        if self._graph is None:
            self._graph = dependency_graph.import_subgraph(dependency_graph.build_graph(self.root))
        return self._graph

    def _row(self, key: str) -> dict[str, Any]:
        rows = self.table.get("steps") or {}
        row = rows.get(key) if isinstance(rows, dict) else None
        return row if isinstance(row, dict) else {}

    def declared(self, key: str) -> set[str]:
        return set(self._declared_list(key))

    def workflow_inputs(self, workflow: str) -> set[str]:
        """Every smoke step's inputs plus the workflow file itself, for scripts.ci_rca.smoke select."""
        if workflow != self.table.get("smoke_workflow"):
            return set()
        out = {workflow}
        for key in self.smoke:
            out |= self.inputs(key)
        return out

    def inputs(self, key: str) -> set[str]:
        return self.direct(key) | self.closure(key) | self.declared(key)

    def proven_by(self, key: str) -> str | None:
        value = self._row(key).get("proven_by")
        return value if isinstance(value, str) else None

    def paths_proven_by(self, value: str) -> list[str]:
        out: set[str] = set()
        for key in self.steps:
            if self.proven_by(key) == value:
                out |= self.inputs(key)
        return sorted(out)

    def check(self) -> list[str]:
        """Direct-only: keys, rows, values, declared inputs, direct references. Builds no import graph."""
        problems = list(self.errors)
        rows = self.table.get("steps")
        rows = rows if isinstance(rows, dict) else {}
        values = self.table.get("proven_by_values")
        values = values if isinstance(values, dict) else {}
        for key, scan in self.steps.items():
            problems.extend(scan.errors)
            if key not in rows:
                problems.append(f"step {key}: no row in {TABLE_REL}")
        for key, row in rows.items():
            if key not in self.steps:
                problems.append(f"row {key}: no such step in {WORKFLOW_REL}")
                continue
            proven = row.get("proven_by") if isinstance(row, dict) else None
            if proven not in values:
                problems.append(f"row {key}: proven_by {proven!r} is not in proven_by_values")
            for rel in (row.get("declared_inputs") if isinstance(row, dict) else None) or []:
                if not (self.root / str(rel)).exists():
                    problems.append(f"row {key}: declared input {rel} does not exist")
        problems.extend(self._check_smoke(rows))
        return problems

    def _check_smoke(self, rows: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        wf_rel = self.table.get("smoke_workflow")
        smoke_rows = self._smoke_rows()
        for key, row in rows.items():
            if isinstance(row, dict) and row.get("proven_by") != "main_smoke" and "smoke_step" in row:
                problems.append(f"row {key}: smoke_step on a row that is not main_smoke")
        if not smoke_rows:
            return problems
        if not isinstance(wf_rel, str) or not (self.root / wf_rel).is_file():
            return problems + [f"smoke_workflow {wf_rel!r} is missing or names no file"]
        jobs: set[str] = set()
        for key, row in smoke_rows:
            sk = row.get("smoke_step")
            if not isinstance(sk, str) or sk not in self.smoke:
                problems.append(f"row {key}: smoke_step {sk!r} is not a step of {wf_rel}")
                continue
            jobs.add(sk.split("::", 1)[0])
            lacking = sorted(self.direct(key) - self.direct(sk))
            if lacking:
                problems.append(f"row {key}: smoke step {sk} lacks direct inputs {lacking}")
            if self.smoke[sk].externals != self.steps[key].externals:
                problems.append(f"row {key}: smoke step {sk} uses a different action pin")
            problems.extend(f"row {key}: {m}" for m in self._masked(sk))
        if len(jobs) > 1:
            problems.append(f"main_smoke steps are spread over more than one job: {sorted(jobs)}")
        problems.extend(self._check_verify(jobs))
        return problems

    def _step_obj(self, key: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """A key taken from self.smoke always names a step of the smoke workflow."""
        job_id = key.split("::", 1)[0]
        job = self.smoke_wf["jobs"][job_id]
        return next(
            (job_id, job, step)
            for index, step in enumerate(job.get("steps") or [])
            if f"{job_id}::{_step_identity(step, index)}" == key
        )

    def _masked(self, key: str) -> list[str]:
        job_id, job, step = self._step_obj(key)
        out: list[str] = []
        if step.get("continue-on-error", False) is not False:
            out.append(f"smoke step {key} carries continue-on-error")
        if _expr(step.get("if")) not in _UNMASKED_STEP_IF:
            out.append(f"smoke step {key} has a skipping if:")
        if job.get("continue-on-error", False) is not False:
            out.append(f"smoke job {job_id} carries continue-on-error")
        if "if" in job and _expr(job.get("if")) != _SMOKE_JOB_IF:
            out.append(f"smoke job {job_id} has an if: other than {_SMOKE_JOB_IF}")
        needs = job.get("needs") or []
        for need in [needs] if isinstance(needs, str) else needs:
            if ((self.smoke_wf.get("jobs") or {}).get(need) or {}).get("continue-on-error", False) is not False:
                out.append(f"smoke job {job_id} needs {need}, which carries continue-on-error")
        return out

    def _check_verify(self, jobs: set[str]) -> list[str]:
        key = self.table.get("smoke_verify_step")
        expected = self.table.get("smoke_verify_run")
        if not isinstance(key, str) or key not in self.smoke:
            return [f"smoke_verify_step {key!r} is not a step of the smoke workflow"]
        job_id, job, step = self._step_obj(key)
        out = [f"smoke_verify_step: {m}" for m in self._masked(key)]
        if jobs and job_id not in jobs:
            out.append(f"smoke_verify_step {key} is not in the smoke job")
        inherited = [(job.get("defaults") or {}).get("run"), (self.smoke_wf.get("defaults") or {}).get("run")]
        if "shell" in step or any(isinstance(d, dict) and "shell" in d for d in inherited):
            out.append("smoke_verify_step has or inherits a shell:")
        if (
            not isinstance(expected, str)
            or not expected.startswith(_VERIFY_PREFIX)
            or any(c in expected for c in _VERIFY_FORBIDDEN)
        ):
            out.append("smoke_verify_run must be one plain line starting with the smoke verify command")
        elif str(step.get("run", "")).strip() != expected.strip():
            out.append("smoke_verify_step run text differs from smoke_verify_run")
        return out


def _expr(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text


def overlaps(cov: Coverage) -> list[str]:
    """Declared inputs a derivation already sees (direct plus closure): the table must never list them."""
    found: list[str] = []
    for key in cov.steps:
        derivable = cov.direct(key) | cov.closure(key)
        found.extend(f"row {key}: declared input {p} is already derivable" for p in sorted(cov.declared(key) & derivable))
    return found


def _py_seed(root: Path, rel: str) -> list[tuple[str, bool]]:
    if not rel.endswith(".py"):
        return []
    module = dependency_graph._file_to_module(root / rel, root)
    return [(module, False)] if module else []


def _normalize(text: str, base_dir: str) -> str:
    def resolve(match: re.Match[str]) -> str:
        return posixpath.normpath(posixpath.join(base_dir, match.group(1)))

    text = _ACTION_PREFIX_RE.sub(resolve, text)
    return _OTHER_PREFIX_RE.sub(" ", text)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _scan_text(text: str, base_dir: str, root: Path, known: list[str], scan: _Scan, where: str) -> None:
    text = _normalize(_blank_shell_comments(text), base_dir)
    scan.texts.append(text)
    for module in _MODULE_AFTER_RE.findall(text):
        path = _module_to_path(module, root)
        if path is None:
            scan.errors.append(f"{where}: module {module} resolves to no file")
            continue
        rel = path.relative_to(root).as_posix()
        scan.files.add(rel)
        scan.seeds.append((module, True))
        if rel.endswith("/__init__.py"):
            main = path.parent / "__main__.py"
            if main.is_file():
                scan.files.add(main.relative_to(root).as_posix())
                scan.seeds.append((module + ".__main__", True))
            else:
                scan.errors.append(f"{where}: package {module} has no __main__.py")
    tokens = [m.group(1).rstrip(".,") for m in _PATH_RE.finditer(text)]
    for name in known:
        if re.search(rf"(?<![\w./$}}-]){re.escape(name)}(?![\w-])", text):
            tokens.append(name)
    for token in tokens:
        _add_path(token, root, known, scan, where)


def _add_path(token: str, root: Path, known: list[str], scan: _Scan, where: str) -> None:
    target = root / token
    if target.is_dir():
        scan.files.add(token.rstrip("/") + "/")
        return
    if not target.is_file():
        scan.errors.append(f"{where}: path {token} resolves to no file")
        return
    scan.files.add(token)
    if token in scan.visited:
        return
    scan.visited.add(token)
    scan.seeds.extend(_py_seed(root, token))
    body = target.read_text(encoding="utf-8")
    parent = posixpath.dirname(token)
    if token.endswith(".sh"):
        _scan_text(body, parent, root, known, scan, f"{where} -> {token}")
    elif token in known and token.endswith(_JSON_SUFFIX):
        _scan_text(" ".join(_strings(json.loads(body))), parent, root, known, scan, f"{where} -> {token}")


def _scan_step(step: dict[str, Any], base_dir: str, root: Path, known: list[str], scan: _Scan, where: str) -> None:
    run = step.get("run")
    if isinstance(run, str):
        _scan_text(run, base_dir, root, known, scan, where)
    for field_name in ("with", "env", "if"):
        for text in _strings(step.get(field_name)):
            _scan_text(text, base_dir, root, known, scan, where)
    uses = step.get("uses")
    if isinstance(uses, str) and uses.startswith("./"):
        _scan_action(uses[2:].rstrip("/"), root, known, scan, where)
    elif isinstance(uses, str):
        scan.externals.add(uses)


def _scan_action(directory: str, root: Path, known: list[str], scan: _Scan, where: str) -> None:
    manifest = next((f"{directory}/{n}" for n in ("action.yml", "action.yaml") if (root / directory / n).is_file()), None)
    if manifest is None or manifest in scan.visited:
        if manifest is None:
            scan.errors.append(f"{where}: local action {directory} has no action.yml")
        return
    scan.visited.add(manifest)
    scan.files.add(manifest)
    data = yaml.safe_load((root / manifest).read_text(encoding="utf-8")) or {}
    for index, step in enumerate((data.get("runs") or {}).get("steps") or []):
        _scan_step(step, directory, root, known, scan, f"{where} -> {manifest}#{index}")


def load(workflow: str | Path = WORKFLOW_REL, table: str | Path = TABLE_REL, root: Path | None = None) -> Coverage:
    base = Path(root) if root is not None else _REPO_ROOT
    wf_path = Path(workflow) if Path(workflow).is_absolute() else base / workflow
    tbl_path = Path(table) if Path(table).is_absolute() else base / table
    data = yaml.safe_load(tbl_path.read_text(encoding="utf-8")) if tbl_path.is_file() else None
    tbl = data if isinstance(data, dict) else {}
    known = [str(n) for n in tbl.get("known_top_level") or []]
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
    errors: list[str] = []
    steps = _scan_workflow(wf, base, known, errors)
    smoke_rel = tbl.get("smoke_workflow")
    smoke_wf: dict[str, Any] = {}
    smoke: dict[str, _Scan] = {}
    if isinstance(smoke_rel, str) and (base / smoke_rel).is_file():
        smoke_wf = yaml.safe_load((base / smoke_rel).read_text(encoding="utf-8")) or {}
        smoke = _scan_workflow(smoke_wf, base, known, errors)
    return Coverage(root=base, steps=steps, table=tbl, errors=errors, smoke=smoke, smoke_wf=smoke_wf)


def _scan_workflow(wf: dict[str, Any], base: Path, known: list[str], errors: list[str]) -> dict[str, _Scan]:
    steps: dict[str, _Scan] = {}
    for job_id, job in (wf.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            key = f"{job_id}::{_step_identity(step, index)}"
            if key in steps:
                errors.append(f"duplicate step key {key}")
                continue
            scan = _Scan()
            _scan_step(step, ".", base, known, scan, key)
            steps[key] = scan
    return steps


def _derive(cov: Coverage, only: str | None) -> dict[str, Any]:
    keys = [only] if only else sorted(cov.steps)
    return {
        key: {
            "direct": sorted(cov.direct(key)),
            "closure": sorted(cov.closure(key)),
            "declared": sorted(cov.declared(key)),
            "external": sorted(cov.steps[key].externals),
            "proven_by": cov.proven_by(key),
        }
        for key in keys
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.ci_rca.coverage")
    sub = parser.add_subparsers(dest="command", required=True)
    derive = sub.add_parser("derive")
    derive.add_argument("--step")
    paths = sub.add_parser("paths")
    paths.add_argument("--proven-by", required=True)
    sub.add_parser("check")
    args = parser.parse_args(argv)
    cov = load()
    if args.command == "check":
        problems = cov.check()
        for problem in problems:
            print(f"  - {problem}")
        return 1 if problems else 0
    if args.command == "paths":
        print("\n".join(cov.paths_proven_by(args.proven_by)))
        return 0
    if args.step and args.step not in cov.steps:
        print(f"unknown step {args.step}", file=sys.stderr)
        return 1
    print(json.dumps(_derive(cov, args.step), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
