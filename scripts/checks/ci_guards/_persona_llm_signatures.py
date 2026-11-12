"""The one LLM-step and compute-plane-start detector (PLAN-persona-trigger-policy; Decision 205 cl.1, 229 cl.3).

T4.26 c3 can re-point the claude -p guards onto it. A parsed workflow step is an LLM step when it (a) names, as an env
key or a secrets.<NAME> reference, the variable of a project-configuration.yaml secret row for the run_persona port,
(b) runs claude with -p or --print, scripts/ci/claude_p_retry.sh or the run_persona entrypoint, or (c) uses
anthropics/claude-code-action or a local composite action or reusable workflow (one hop) one of whose steps matches
(a) or (b). Installing or probing the CLI is not an LLM step: no prompt runs and no credential is read.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_CONFIGURATION = Path("docs/contracts/project-configuration.yaml")
PERSONA_INDEX = Path("docs/contracts/executor-personas.yaml")
CLAUDE_INVOCATION = re.compile(r"\bclaude\s+(?:-p|--print)\b")
RETRY_WRAPPER = re.compile(r"\bclaude_p_retry\.sh\b")
RUN_PERSONA = re.compile(r"\brun_persona\b")
COMPUTE_HOST = re.compile(r"\bpersona_compute_host\b")
ECS_RUN_TASK = re.compile(r"\baws\s+ecs\s+run-task\b")
CLAUDE_CODE_ACTION = "anthropics/claude-code-action"
LOCAL_PREFIXES = ("./.dispatch-tooling/", "./")


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def llm_secret_variables(root: Path) -> frozenset[str]:
    """Variables of the secret rows whose applies_when.port is run_persona."""
    doc = load_yaml(root / PROJECT_CONFIGURATION)
    rows = doc.get("settings") if isinstance(doc, dict) else None
    names = {
        r["variable"]
        for r in rows or []
        if isinstance(r, dict)
        and r.get("kind") == "secret"
        and isinstance(r.get("variable"), str)
        and isinstance(r.get("applies_when"), dict)
        and r["applies_when"].get("port") == "run_persona"
    }
    return frozenset(names)


def compute_plane_ids(root: Path) -> frozenset[str]:
    doc = load_yaml(root / PERSONA_INDEX)
    rows = doc.get("personas") if isinstance(doc, dict) else None
    return frozenset(
        r["persona"] for r in rows or [] if isinstance(r, dict) and r.get("kind") == "compute_plane" and "persona" in r
    )


def strip_comments(body: str) -> str:
    """Drop shell comments (full-line and trailing, quote-aware); a name only in a comment is not a match."""
    out: list[str] = []
    for line in body.splitlines():
        quote = ""
        cut = len(line)
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1].isspace()):
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def _logical_lines(body: str) -> list[str]:
    return strip_comments(body).replace("\\\n", " ").splitlines()


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_walk_strings(k) + _walk_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _walk_strings(v)]
    return []


def _names_secret(step: dict[str, Any], variables: frozenset[str]) -> bool:
    env = step.get("env")
    if isinstance(env, dict) and variables & {str(k) for k in env}:
        return True
    refs = _walk_strings(env) + _walk_strings(step.get("with")) + [strip_comments(str(step.get("run") or ""))]
    return any(re.search(rf"\bsecrets\.{re.escape(v)}\b", s) for s in refs for v in variables)


def _direct_llm(step: dict[str, Any], variables: frozenset[str]) -> bool:
    run = strip_comments(str(step.get("run") or ""))
    return (
        _names_secret(step, variables)
        or bool(CLAUDE_INVOCATION.search(run) or RETRY_WRAPPER.search(run) or RUN_PERSONA.search(run))
        or str(step.get("uses") or "").startswith(CLAUDE_CODE_ACTION)
    )


def local_target(uses: object, root: Path) -> list[dict[str, Any]]:
    """Steps of the local composite action or reusable workflow a `uses:` names (one hop); [] when not local."""
    if not isinstance(uses, str):
        return []
    for prefix in LOCAL_PREFIXES:
        if uses.startswith(prefix):
            rel = uses[len(prefix) :].split("@", 1)[0].rstrip("/")
            break
    else:
        return []
    base = root / rel
    candidates = [base] if base.is_file() else [base / "action.yml", base / "action.yaml"]
    for cand in candidates:
        doc = load_yaml(cand)
        if not isinstance(doc, dict):
            continue
        runs = doc.get("runs")
        if isinstance(runs, dict) and isinstance(runs.get("steps"), list):
            return [s for s in runs["steps"] if isinstance(s, dict)]
        jobs = doc.get("jobs")
        if isinstance(jobs, dict):
            return [s for j in jobs.values() if isinstance(j, dict) for s in j.get("steps") or [] if isinstance(s, dict)]
    return []


def is_llm_step(step: dict[str, Any], root: Path, variables: frozenset[str] | None = None) -> bool:
    variables = llm_secret_variables(root) if variables is None else variables
    if _direct_llm(step, variables):
        return True
    return any(_direct_llm(inner, variables) for inner in local_target(step.get("uses"), root))


def _starts_compute_plane(step: dict[str, Any], ids: frozenset[str]) -> bool:
    run = str(step.get("run") or "")
    if ECS_RUN_TASK.search(strip_comments(run)) or COMPUTE_HOST.search(strip_comments(run)):
        return True
    for line in _logical_lines(run):
        if RUN_PERSONA.search(line) and any(re.search(rf"\b{re.escape(i)}\b", line) for i in ids):
            return True
    return False


def starts_compute_plane(step: dict[str, Any], root: Path, ids: frozenset[str] | None = None) -> bool:
    ids = compute_plane_ids(root) if ids is None else ids
    if _starts_compute_plane(step, ids):
        return True
    return any(_starts_compute_plane(inner, ids) for inner in local_target(step.get("uses"), root))
