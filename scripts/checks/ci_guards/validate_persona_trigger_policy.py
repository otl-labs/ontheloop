"""Persona trigger policy (Decision 205 cl.1, 229 cl.3; PLAN-persona-trigger-policy; T4.2 c10).

Over every .github/workflows/*.yml (glob; claude.yml exempt by name, Decision 184 cl.4):
  * an LLM workflow's triggers must be in ALLOWED_TRIGGERS;
  * a workflow_dispatch LLM workflow must gate every LLM step with the persona dispatch gate (an earlier step of the
    same job running the gate script, no continue-on-error, whose if: is the LLM step's if: less the refused term,
    optionally plus github.event_name == 'workflow_dispatch'), the LLM step's if: carrying the refused conjunct, no
    top-level || and no status function but success();
  * a workflow_run LLM workflow's named upstreams must qualify (see _one_upstream); a workflow_dispatch upstream, directly
    or through any workflow_run middle, qualifies only when every LLM step of the file it reaches carries the
    pull_request-event conjunct (PR_EVENT_TERM), so nothing a person or agent dispatches can reach an LLM step;
  * no pull_request-triggered workflow grants checks: write, and no workflow starts a compute-plane persona.
Gate recognition is syntactic (a fixed conjunct and the earlier gate step); no expression is ever evaluated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards import _persona_llm_signatures as sig

ALLOWED_TRIGGERS = frozenset({"pull_request", "push", "workflow_run", "workflow_dispatch"})
EXEMPT = {"claude.yml": "Decision 184 cl.4: the maintainer-triggered @claude workflow is the interactive session surface"}
GATE_ID_PREFIX = "persona_gate"
GATE_SCRIPT = "persona_dispatch_gate.py"
DISPATCH_TERM = "github.event_name == 'workflow_dispatch'"
FAILURE_TERM = "github.event.workflow_run.conclusion == 'failure'"
PR_EVENT_TERM = "github.event.workflow_run.event == 'pull_request'"
REFUSED_TERM = re.compile(r"steps\.(" + GATE_ID_PREFIX + r"[A-Za-z0-9_-]*)\.outputs\.refused != 'true'")
BAD_STATUS = re.compile(r"\b(always|failure|cancelled)\s*\(")
BAD_STATUS_PR = re.compile(r"\b(always|failure)\s*\(|(?<!!)\bcancelled\s*\(")


def split_top(expr: str, op: str) -> list[str]:
    """Split on a top-level operator, respecting parentheses and single-quoted strings."""
    parts: list[str] = []
    depth = 0
    quote = False
    cur = ""
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            quote = not quote
        elif not quote:
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and expr.startswith(op, i):
                parts.append(cur)
                cur = ""
                i += len(op)
                continue
        cur += ch
        i += 1
    parts.append(cur)
    return [" ".join(p.split()) for p in parts]


def _unwrap(expr: str) -> str:
    expr = " ".join(expr.split())
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return _strip_parens(expr)


def _strip_parens(term: str) -> str:
    term = term.strip()
    while term.startswith("(") and term.endswith(")"):
        depth = 0
        enclosed = True
        for pos, ch in enumerate(term):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and pos < len(term) - 1:
                enclosed = False
                break
        if not enclosed:
            break
        term = term[1:-1].strip()
    return term


def and_terms(expr: object) -> set[str]:
    if not isinstance(expr, str):
        return set()
    return {_strip_parens(t) for t in split_top(_unwrap(expr), "&&") if t.strip()}


def _disjuncts(expr: object) -> list[str]:
    return [_strip_parens(d) for d in split_top(_unwrap(str(expr)), "||")] if isinstance(expr, str) else []


def triggers(doc: dict[Any, Any]) -> set[str]:
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(t) for t in on}
    return {str(k) for k in on} if isinstance(on, dict) else set()


def _upstream_names(doc: dict[Any, Any]) -> list[str]:
    on = doc.get("on", doc.get(True))
    wr = on.get("workflow_run") if isinstance(on, dict) else None
    names = wr.get("workflows") if isinstance(wr, dict) else None
    return [str(n) for n in names] if isinstance(names, list) else []


def _steps(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    jobs = doc.get("jobs")
    for job_id, job in jobs.items() if isinstance(jobs, dict) else []:
        for step in (job.get("steps") if isinstance(job, dict) else None) or []:
            if isinstance(step, dict):
                out.append((str(job_id), job, step))
    return out


class Policy:
    def __init__(self, root: Path, workflows: dict[str, dict[str, Any]], config_root: Path) -> None:
        self.root = root
        self.workflows = workflows
        self.variables = sig.llm_secret_variables(config_root)
        self.compute_ids = sig.compute_plane_ids(config_root)
        self.by_name = {str(d.get("name")): f for f, d in workflows.items() if d.get("name")}

    def llm_steps(self, doc: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        return [t for t in _steps(doc) if sig.is_llm_step(t[2], self.root, self.variables)]

    def is_llm(self, file: str) -> bool:
        return bool(self.llm_steps(self.workflows[file]))

    def gate_problems(self, file: str, doc: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for job_id, job, step in self.llm_steps(doc):
            where = f"{file}: job {job_id}, step {step.get('name', step.get('id', '?'))!r}"
            cond = step.get("if")
            text = str(cond) if cond is not None else ""
            if len(split_top(_unwrap(text), "||")) > 1 or BAD_STATUS.search(text):
                out.append(f"{where}: LLM step if: has a || or a status function other than success()")
                continue
            terms = and_terms(cond)
            gate_ids = [m.group(1) for t in terms if (m := REFUSED_TERM.fullmatch(t))]
            if not gate_ids:
                out.append(f"{where}: dispatchable LLM step lacks a top-level persona_gate refused != 'true' conjunct")
                continue
            out.extend(self._gate_step_problems(where, job, step, terms, gate_ids[0]))
        return out

    def _gate_step_problems(
        self, where: str, job: dict[str, Any], step: dict[str, Any], terms: set[str], gate_id: str
    ) -> list[str]:
        steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
        ids = [s.get("id") for s in steps]
        if gate_id not in ids or step not in steps or ids.index(gate_id) >= steps.index(step):
            return [f"{where}: no earlier step {gate_id} in the same job"]
        gate = steps[ids.index(gate_id)]
        if GATE_SCRIPT not in sig.strip_comments(str(gate.get("run") or "")):
            return [f"{where}: step {gate_id} does not run {GATE_SCRIPT}"]
        if "continue-on-error" in gate:
            return [f"{where}: step {gate_id} sets continue-on-error"]
        want = terms - {f"steps.{gate_id}.outputs.refused != 'true'"}
        got = and_terms(gate.get("if"))
        if got != want and got - {DISPATCH_TERM} != want:
            return [f"{where}: step {gate_id} if: differs from the LLM step's if: less the refused term"]
        return []

    @staticmethod
    def _disjuncts_gated(expr: object) -> bool:
        parts = _disjuncts(expr)
        return bool(parts) and all(p == DISPATCH_TERM or FAILURE_TERM in and_terms(p) for p in parts)

    def failure_gated(self, doc: dict[str, Any], job: dict[str, Any], step: dict[str, Any]) -> bool:
        """Read syntactically: the if: or one of its own top-level && conjuncts (an && only narrows) is failure-gated."""
        for expr in (job.get("if"), step.get("if")):
            if self._disjuncts_gated(expr) or any(self._disjuncts_gated(term) for term in and_terms(expr)):
                return True
        return False

    def pr_event_gated(self, origin: dict[str, Any]) -> bool:
        """Every LLM step of origin carries PR_EVENT_TERM as a top-level && conjunct of its job's or its own if:."""
        for _, job, step in self.llm_steps(origin):
            if not any(
                PR_EVENT_TERM in and_terms(expr)
                and len(split_top(_unwrap(str(expr)), "||")) == 1
                and not BAD_STATUS_PR.search(str(expr))
                for expr in (job.get("if"), step.get("if"))
            ):
                return False
        return True

    def upstream_problems(
        self,
        file: str,
        doc: dict[str, Any],
        stack: tuple[str, ...],
        gated: bool | None = None,
        origin: dict[str, Any] | None = None,
    ) -> list[str]:
        out: list[str] = []
        origin = doc if origin is None else origin
        if gated is None:
            gated = all(self.failure_gated(doc, j, s) for _, j, s in self.llm_steps(doc))
        for name in _upstream_names(doc):
            up = self.by_name.get(name)
            if up is None:
                out.append(f"{file}: workflow_run upstream {name!r} does not resolve to a workflow")
            elif up in stack or up == file:
                out.append(f"{file}: workflow_run cycle through {up}")
            else:
                out.extend(self._one_upstream(file, up, gated, (*stack, file), origin))
        return list(dict.fromkeys(out))

    def _one_upstream(
        self, file: str, up: str, gated: bool, stack: tuple[str, ...], origin: dict[str, Any] | None = None
    ) -> list[str]:
        udoc = self.workflows[up]
        origin = self.workflows[file] if origin is None else origin
        if gated and not self.is_llm(up):
            return []
        problems = self.rule_problems(up, udoc, stack) if self.is_llm(up) else []
        if not gated:
            trig = triggers(udoc)
            plain = {"push", "pull_request", "workflow_run"}
            if not trig <= plain | {"workflow_dispatch"}:
                problems.append(f"{up}: upstream of the non-failure-gated {file} but triggered by {sorted(trig)}")
            elif "workflow_dispatch" in trig and not self.pr_event_gated(origin):
                problems.append(
                    f"{up}: upstream of the non-failure-gated {file} but triggered by {sorted(trig)}, "
                    f"and not every LLM step carries {PR_EVENT_TERM}"
                )
            if "workflow_run" in trig and trig <= plain | {"workflow_dispatch"}:
                problems.extend(self.upstream_problems(up, udoc, stack, gated, origin))
        return [f"{file}: upstream {up} does not qualify: {p}" for p in problems] if problems else []

    def rule_problems(self, file: str, doc: dict[str, Any], stack: tuple[str, ...] = ()) -> list[str]:
        if file in EXEMPT or not self.llm_steps(doc):
            return []
        trig = triggers(doc)
        out = [f"{file}: LLM workflow trigger {t!r} is not allowed" for t in sorted(trig - ALLOWED_TRIGGERS)]
        if "workflow_dispatch" in trig:
            out.extend(self.gate_problems(file, doc))
        if "workflow_run" in trig:
            out.extend(self.upstream_problems(file, doc, stack))
        return list(dict.fromkeys(out))

    def checks_write_problems(self, file: str, doc: dict[str, Any]) -> list[str]:
        if "pull_request" not in triggers(doc):
            return []
        perms = [("top level", doc.get("permissions"))]
        perms += [(f"job {j}", v.get("permissions")) for j, v in (doc.get("jobs") or {}).items() if isinstance(v, dict)]
        return [
            f"{file}: grants checks: write ({where}) on a pull_request-triggered workflow"
            for where, p in perms
            if p == "write-all" or (isinstance(p, dict) and p.get("checks") == "write")
        ]

    def compute_problems(self, file: str, doc: dict[str, Any]) -> list[str]:
        return [
            f"{file}: job {job_id} step {step.get('name', '?')!r} starts a compute-plane persona"
            for job_id, _job, step in _steps(doc)
            if sig.starts_compute_plane(step, self.root, self.compute_ids)
        ]

    def problems(self) -> list[str]:
        out: list[str] = []
        for file, doc in sorted(self.workflows.items()):
            out.extend(self.rule_problems(file, doc))
            out.extend(self.checks_write_problems(file, doc))
            out.extend(self.compute_problems(file, doc))
        return out


def load_workflows(workflows_dir: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(workflows_dir.glob("*.yml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            doc = None
        found[path.name] = doc if isinstance(doc, dict) else {}
    return found


def check_tree(root: Path, config_root: Path | None = None) -> tuple[list[str], int]:
    """(problems, workflows examined) for the .github/workflows of `root`; contracts are read from config_root."""
    workflows = load_workflows(root / ".github" / "workflows")
    return Policy(root, workflows, config_root or root).problems(), len(workflows)


@registry.register("validate_persona_trigger_policy", owner="platform")
def validate_persona_trigger_policy(failed: list[str]) -> None:
    """Refuse a workflow that runs an LLM persona on an inadmissible trigger, ungated, or starts a compute plane.

    Declares examined(workflow files read, unit="workflows") (Decision 170).
    """
    print("\n=== persona trigger policy (Decision 205 cl.1) ===")
    problems, examined = check_tree(_common.ROOT)
    registry.examined(examined, unit="workflows")
    for problem in problems:
        print(f"  FAIL: {problem}")
        failed.append(f"persona trigger policy: {problem}")
    if not problems:
        print(f"  PASS: {examined} workflow(s) satisfy the persona trigger policy")
