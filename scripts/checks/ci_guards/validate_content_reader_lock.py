"""The telemetry content reader's deploy channel keeps its premises (Decision 239 cl.5, Decision 272).

An early warning, not the control: an agent PR can edit this check, which is why it and the channel's files are PROTECTED and
code-owned. Predicates take parsed workflows ({filename: parsed yaml}), raw workflow text, parsed contracts and path lists so
the red cases run on synthetic data; the registered check loads the real tree.

  (a) only deploy-telemetry-content.yml names Environment telemetry-content-deploy, never as an expression
  (b) that file declares no workflow_call, pull_request or pull_request_target trigger, and its deploy job is defined in it
  (c) no other workflow, and no build-lambda.yaml ducklake_function_zip_keys entry, names the content function or its zip
  (d) the deploy job runs only when held is 'false', passes the digest and the recorded digest through env, has no ${{ }} in a
      run body and no python or pip after the credential step; the build job proves the baseline before it writes the summary
  (e) the protective pathspecs cover the set, every .py file they match is bundled, and only manifest.yaml and
      deploy_config.json match unbundled
  (f) build-lambda.yaml, actions-capacity.yaml and github-actions-evidence.yaml carry the channel's rows
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import _env_name, _jobs, _steps
from scripts.verify_ci_workflow import _load

WORKFLOW = "deploy-telemetry-content.yml"
ENVIRONMENT = "telemetry-content-deploy"
FUNCTION = "agent-platform-ducklake-telemetry-content"
ZIP = "ducklake-telemetry-content-reader.zip"
PKG = "src/lambdas/ducklake_telemetry_content_reader"
PROTECTIVE = (PKG, "src/common/ducklake_content_read_verbs.py", "src/sensitive_values")
UNBUNDLED_OK = frozenset({f"{PKG}/manifest.yaml", f"{PKG}/deploy_config.json"})
_PY_RE = re.compile(r"\bpython3?\b|\bpip\b")
_BAD_TRIGGERS = ("workflow_call", "pull_request", "pull_request_target")


def _triggers(wf: dict[Any, Any]) -> set[str]:
    on = wf.get("on", wf.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(t) for t in on}
    return {str(t) for t in on} if isinstance(on, dict) else set()


def check_environment(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for fname, wf in sorted(workflows.items()):
        for jname, job in _jobs(wf).items():
            name = _env_name(job)
            if name is None:
                continue
            if "${{" in name and ENVIRONMENT in name:
                out.append(f"a: {fname}:{jname} names {ENVIRONMENT} as an expression")
            elif name == ENVIRONMENT and fname != WORKFLOW:
                out.append(f"a: {fname}:{jname} names Environment {ENVIRONMENT}; only {WORKFLOW} may")
    return out


def check_workflow_shape(wf: dict[str, Any] | None) -> list[str]:
    if wf is None:
        return [f"b: {WORKFLOW} is missing"]
    out = [f"b: {WORKFLOW} declares the {t} trigger" for t in _BAD_TRIGGERS if t in _triggers(wf)]
    deploy = _jobs(wf).get("deploy")
    if deploy is None or "uses" in deploy or not _steps(deploy):
        out.append(f"b: {WORKFLOW}'s deploy job must be defined in the file, not a reusable call")
    return out


def check_other_names(texts: dict[str, str], zip_keys: list[str]) -> list[str]:
    out = [
        f"c: {fname} names the content function or its zip"
        for fname, text in sorted(texts.items())
        if fname != WORKFLOW and (FUNCTION in text or ZIP in text)
    ]
    out += [f"c: build-lambda.yaml ducklake_function_zip_keys names {k}" for k in zip_keys if FUNCTION in k or ZIP in k]
    return out


def check_jobs(wf: dict[str, Any] | None) -> list[str]:
    if wf is None:
        return []
    jobs = _jobs(wf)
    deploy, build = jobs.get("deploy", {}), jobs.get("build", {})
    out: list[str] = []
    if "needs.build.outputs.held == 'false'" not in str(deploy.get("if", "")):
        out.append("d: the deploy job must run only when needs.build.outputs.held is 'false'")
    steps = _steps(deploy)
    scripted = [s for s in steps if "telemetry_content_deploy.sh deploy" in str(s.get("run", ""))]
    env = (scripted[0].get("env") or {}) if scripted else {}
    if "CONTENT_SHA256" not in env:
        out.append("d: the deploy step must pass CONTENT_SHA256 through env")
    if env.get("RECORD_CODE_SHA256") != "${{ needs.gate.outputs.record_code_sha256 }}":
        out.append("d: the deploy step must pass RECORD_CODE_SHA256 from needs.gate.outputs.record_code_sha256")
    for step in steps:
        if "${{" in str(step.get("run", "")):
            out.append(f"d: deploy step `{step.get('name', '')}` has an expression inside a run body; use env")
    names = [str(s.get("uses", "")) for s in steps]
    cred = next((i for i, u in enumerate(names) if u.startswith("aws-actions/configure-aws-credentials@")), None)
    if cred is None:
        out.append("d: the deploy job must configure credentials")
    else:
        out += [
            f"d: deploy step `{s.get('name', '')}` runs python or pip after the credential step"
            for s in steps[cred + 1 :]
            if _PY_RE.search(str(s.get("run", "")))
        ]
    build_names = [str(s.get("name", "")) for s in _steps(build)]
    base, summ = "Prove the recorded baseline", "Write the approval summary"
    if base not in build_names or summ not in build_names or build_names.index(base) > build_names.index(summ):
        out.append("d: the build job's baseline step must precede its summary step")
    return out


def check_pathspecs(pathspecs: tuple[str, ...], files: list[str], bundled: set[str]) -> list[str]:
    out = [f"e: pathspecs miss {p}" for p in PROTECTIVE if p not in pathspecs]
    for rel in files:
        if rel.endswith(".py") and rel not in bundled:
            out.append(f"e: {rel} is matched by the pathspecs but not bundled by the manifest")
        elif not rel.endswith(".py") and rel not in bundled and rel not in UNBUNDLED_OK:
            out.append(f"e: {rel} is matched by the pathspecs, unbundled and not an allowed unbundled file")
    return out


def check_contracts(build: dict[str, Any], capacity: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    out: list[str] = []
    channel = (build.get("deploy_channels") or {}).get("telemetry_content_function") or {}
    if WORKFLOW not in str(channel.get("governed_channel", "")):
        out.append("f: build-lambda.yaml deploy_channels.telemetry_content_function.governed_channel must name the workflow")
    if "--content-reader-only" not in (build.get("cli_flags") or []):
        out.append("f: build-lambda.yaml cli_flags must name --content-reader-only")
    rows = [r for r in (capacity.get("ledger") or {}).get("rows", []) if r.get("id") == "telemetry-content-deploy-per-merge"]
    live = (rows[0].get("live_signal") or {}) if rows else {}
    if not rows or live != {"workflow": f".github/workflows/{WORKFLOW}", "job": "*"}:
        out.append("f: actions-capacity.yaml needs ledger row telemetry-content-deploy-per-merge with its live_signal")
    jobs = {r.get("job") for r in evidence.get("governed_jobs") or [] if WORKFLOW in str(r.get("workflow", ""))}
    out += [f"f: github-actions-evidence.yaml governed_jobs lacks the {j} job" for j in ("deploy", "record") if j not in jobs]
    return out


def _declared_pathspecs(source: str) -> tuple[str, ...]:
    """TELEMETRY_CONTENT_SOURCE_PATHSPECS read from code_drift.py's text, so the check imports none of that module."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and getattr(node.target, "id", "") == "TELEMETRY_CONTENT_SOURCE_PATHSPECS"
        ):
            return tuple(ast.literal_eval(node.value))
    return ()


def run_all(root: Path) -> list[str]:
    wfdir = root / ".github" / "workflows"
    paths = sorted(p for p in wfdir.glob("*.y*ml") if p.suffix in (".yml", ".yaml"))
    workflows = {p.name: _load(str(p)) for p in paths}
    texts = {p.name: p.read_text(encoding="utf-8") for p in paths}
    contracts = root / "docs" / "contracts"
    load = lambda name: yaml.safe_load((contracts / name).read_text(encoding="utf-8"))  # noqa: E731
    build, capacity, evidence = load("build-lambda.yaml"), load("actions-capacity.yaml"), load("github-actions-evidence.yaml")
    manifest = yaml.safe_load((root / PKG / "manifest.yaml").read_text(encoding="utf-8"))
    bundled = set(manifest.get("includes") or []) | set(manifest.get("assets") or [])
    specs = _declared_pathspecs((root / "scripts" / "convergence_health" / "code_drift.py").read_text(encoding="utf-8"))
    files: list[str] = []
    for spec in specs:
        target = root / spec
        found = [target] if target.is_file() else sorted(target.rglob("*")) if target.is_dir() else []
        files += [p.relative_to(root).as_posix() for p in found if p.is_file() and "__pycache__" not in p.parts]
    zip_map = (build.get("deploy_targets") or {}).get("ducklake_function_zip_keys") or {}
    zip_keys = [*zip_map, *map(str, zip_map.values())]
    wf = workflows.get(WORKFLOW)
    return (
        check_environment(workflows)
        + check_workflow_shape(wf)
        + check_other_names(texts, zip_keys)
        + check_jobs(wf)
        + check_pathspecs(tuple(specs), files, bundled)
        + check_contracts(build, capacity, evidence)
    )


@registry.register("validate_content_reader_lock", owner="platform")
def validate_content_reader_lock(failed: list[str]) -> None:
    """Assert the content reader deploy channel's premises. See module docstring."""
    print("\n=== content reader lock ===")
    try:
        violations = run_all(_common.ROOT)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, TypeError, AttributeError, KeyError) as exc:
        print(f"  FAIL: could not load or parse the inputs: {exc}")
        failed.append("content-reader-lock: inputs unreadable, so no predicate ran")
        registry.examined(0, unit="predicates")
        return
    for violation in violations:
        print(f"  FAIL: {violation}")
        failed.append(f"content-reader-lock: {violation}")
    registry.examined(6, unit="predicates")
    if not violations:
        print("  PASS: predicates (a)-(f)")
