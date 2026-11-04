"""tf-trusted-plan Environment and pull_request_target fence (PLAN-trusted-plan-identity, rec-4636).

The trusted role trusts one subject form: the tf-trusted-plan Environment plus a job_workflow_ref naming a
trusted workflow file. This guard keeps that premise true on the repo side. Predicates (a)-(h) each take parsed
data (workflows as {filename: parsed yaml}, terraform as {filename: text}, CODEOWNERS text) so the red cases run
on synthetic input; the registered check loads the real tree.

  (a) only TRUSTED_WORKFLOWS reference ENV, and no environment name is an expression
  (b) only TRUSTED_WORKFLOWS combine pull_request_target with a token-minting job
  (c) no TRUSTED_WORKFLOWS file declares workflow_call; every one exists
  (d) token-minting jobs that can run on pull_request_target declare ENV and a same-repo or dispatch guard
  (e) no run body in TRUSTED_WORKFLOWS interpolates PR-controlled expressions
  (f) terraform/github declares ENV with a main-only deployment policy, and import blocks for both
  (g) governed checkouts take no ref or repository input and set persist-credentials false
  (h) governed jobs execute only code-owned local paths (including the run bodies and local actions of every composite
      they use, and the static import closure of every executed .py file), every python call is isolated (-I), and no
      executed .py file inserts the repo root at sys.path position 0 (each appends it, so the stdlib always wins)
  (i) every non-local uses: in a governed job (and in the composites it uses) is pinned to a 40-hex commit SHA

Predicates (g) also refuse git fetch and git checkout in a governed run body: only scripts/ci/trusted_plan_overlay.py
touches the PR's ref.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Callable, Iterator
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.verify_ci_workflow import _load

ENV = "tf-trusted-plan"
TRUSTED_WORKFLOWS = frozenset({"terraform-plan-trusted.yml", "terraform-github-plan.yml"})
TEMP_NO_ENV_JOBS: frozenset[tuple[str, str]] = frozenset()

_PRT = "pull_request_target"
_DISPATCH = "github.event_name == 'workflow_dispatch'"
_SAME_REPO = "github.event.pull_request.head.repo.full_name == github.repository"
_PR_EXPR_RE = re.compile(r"\$\{\{[^}]*(?:github\.event\.pull_request\.|github\.head_ref)[^}]*\}\}")
_SCRIPT_PATH_RE = re.compile(
    r"(?<![\w./-])(?:\./|\"?\$\{?GITHUB_WORKSPACE\}?\"?/|\$\{\{\s*github\.workspace\s*\}\}/)?(scripts/[\w./-]+)"
)
_ACTION_PATH_RE = re.compile(r"\$\{\{\s*github\.action_path\s*\}\}/([\w./-]+)")
_GIT_REF_RE = re.compile(r"(?<![\w-])git\s+(?:fetch|checkout)\b")
_SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}(?:\s|$)")
_MODULE_RE = re.compile(r"(?<!\S)-m\s+(scripts(?:\.\w+)+)")
_PYTHON_RE = re.compile(r"(?<![\w.-])(?:[\w./$-]*/)?(?:[\w-]*-)?python[\d.]*(?=\s|$|[;|&)\"'])(\s+\S+)?")
_ISOLATED_FLAG_RE = re.compile(r"-[A-VYZa-z]*I[A-Za-z]*")


def _jobs(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs = wf.get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _triggers(wf: dict[str, Any]) -> set[str]:
    on = wf.get("on")
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    return {str(k) for k in on} if isinstance(on, dict) else set()


def _env_name(job: dict[str, Any]) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return str(env) if env is not None else None


def _mints(wf: dict[str, Any], job: dict[str, Any]) -> bool:
    perms = job["permissions"] if "permissions" in job else wf.get("permissions")
    return perms == "write-all" or (isinstance(perms, dict) and str(perms.get("id-token")) == "write")


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _run_bodies(job: dict[str, Any]) -> list[str]:
    return [s["run"] for s in _steps(job) if isinstance(s.get("run"), str)]


Reader = Callable[[str], "str | None"]


def _no_read(_path: str) -> str | None:
    return None


def _walk_steps(
    steps: list[dict[str, Any]], read: Reader, seen: set[str], action_dir: str | None
) -> Iterator[tuple[str, str, str | None]]:
    """Yield (kind, text, action_dir) for every run body and uses: of the steps and of the local composites they use."""
    for step in steps:
        if isinstance(step.get("run"), str):
            yield "run", step["run"], action_dir
        uses = str(step.get("uses", ""))
        if not uses:
            continue
        yield "uses", uses, action_dir
        if uses.startswith("./") and uses[2:].rstrip("/") not in seen:
            directory = uses[2:].rstrip("/")
            seen.add(directory)
            text = read(f"{directory}/action.yml")
            parsed = _safe_yaml(text)
            runs = parsed.get("runs") if isinstance(parsed, dict) else None
            inner = runs.get("steps") if isinstance(runs, dict) else None
            if isinstance(inner, list):
                yield from _walk_steps([x for x in inner if isinstance(x, dict)], read, seen, directory)


def _safe_yaml(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _expanded(job: dict[str, Any], read: Reader) -> list[tuple[str, str, str | None]]:
    return list(_walk_steps(_steps(job), read, set(), None))


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _balanced_inside(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced_inside(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def _split_outside_parens(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = start = i = 0
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            start = i + len(sep)
            i += len(sep) - 1
        i += 1
    parts.append(text[start:])
    return parts


def _disjuncts(condition: object) -> list[list[str]]:
    """Normalised top-level conjuncts of each top-level || disjunct; no condition is one empty disjunct."""
    if condition is None or str(condition).strip() == "":
        return [[]]
    text = str(condition).replace('"', "'")
    text = re.sub(r"\$\{\{|\}\}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return [
        [_strip_parens(c) for c in _split_outside_parens(_strip_parens(d), "&&")]
        for d in _split_outside_parens(_strip_parens(text), "||")
    ]


def _can_run_on_prt(wf: dict[str, Any], job: dict[str, Any]) -> bool:
    return _PRT in _triggers(wf) and any(_DISPATCH not in d for d in _disjuncts(job.get("if")))


def _guarded(job: dict[str, Any]) -> bool:
    return all(_DISPATCH in d or _SAME_REPO in d for d in _disjuncts(job.get("if")))


def _governed(name: str, wf: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Jobs (g) and (h) police: token-minting jobs that can run on pull_request_target, and ENV jobs."""
    return [
        (jid, job)
        for jid, job in _jobs(wf).items()
        if name in TRUSTED_WORKFLOWS and ((_mints(wf, job) and _can_run_on_prt(wf, job)) or _env_name(job) == ENV)
    ]


def check_environment_references(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out = []
    for name, wf in workflows.items():
        for jid, job in _jobs(wf).items():
            env = _env_name(job)
            if env is not None and "${{" in env:
                out.append(f"a: {name}:{jid} names an environment by expression")
            if env == ENV and name not in TRUSTED_WORKFLOWS:
                out.append(f"a: {name}:{jid} references {ENV}")
    return out


def check_prt_minting(workflows: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"b: {name}:{jid} combines {_PRT} with a token-minting job"
        for name, wf in workflows.items()
        if name not in TRUSTED_WORKFLOWS and _PRT in _triggers(wf)
        for jid, job in _jobs(wf).items()
        if _mints(wf, job)
    ]


def check_trusted_files(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out = [f"c: trusted workflow {n} is missing" for n in sorted(TRUSTED_WORKFLOWS - workflows.keys())]
    out += [
        f"c: {n} declares workflow_call"
        for n in sorted(TRUSTED_WORKFLOWS & workflows.keys())
        if "workflow_call" in _triggers(workflows[n])
    ]
    return out


def check_minting_jobs(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out = []
    for name in sorted(TRUSTED_WORKFLOWS & workflows.keys()):
        wf = workflows[name]
        for jid, job in _jobs(wf).items():
            if not (_mints(wf, job) and _can_run_on_prt(wf, job)):
                continue
            if _env_name(job) != ENV and (name, jid) not in TEMP_NO_ENV_JOBS:
                out.append(f"d: {name}:{jid} mints a token on {_PRT} without environment {ENV}")
            if not _guarded(job):
                out.append(f"d: {name}:{jid} has a disjunct with neither a dispatch nor a same-repo conjunct")
    return out


def check_run_bodies(workflows: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"e: {name}:{jid} interpolates a PR-controlled expression in a run body"
        for name in sorted(TRUSTED_WORKFLOWS & workflows.keys())
        for jid, job in _jobs(workflows[name]).items()
        if any(_PR_EXPR_RE.search(body) for body in _run_bodies(job))
    ]


def _balanced_block(text: str, open_brace: int) -> str:
    depth = 0
    for i in range(open_brace, len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return text[open_brace : i + 1]
    return text[open_brace:]


def _blocks(text: str, rtype: str) -> dict[str, str]:
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "//")))
    return {
        m.group(1): _balanced_block(text, m.end() - 1)
        for m in re.finditer(rf'^\s*resource\s+"{rtype}"\s+"(\w+)"\s*\{{', text, re.M)
    }


def _arg(block: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", block, re.M)
    return m.group(1) if m else None


def check_terraform(files: dict[str, str]) -> list[str]:
    text = "\n".join(files.values())
    envs = {n: b for n, b in _blocks(text, "github_repository_environment").items() if _arg(b, "environment") == f'"{ENV}"'}
    if not envs:
        return [f"f: no github_repository_environment declares {ENV}"]
    out = []
    for n, b in envs.items():
        for arg, want in (
            ("can_admins_bypass", "false"),
            ("protected_branches", "false"),
            ("custom_branch_policies", "true"),
            ("prevent_destroy", "true"),
        ):
            if _arg(b, arg) != want:
                out.append(f"f: environment {n} must set {arg} = {want}")
        if re.search(r"^\s*(reviewers|wait_timer)\b", b, re.M):
            out.append(f"f: environment {n} must declare no reviewers or wait timer")
    refs = {f"github_repository_environment.{n}.environment" for n in envs}
    policies = {
        n: b
        for n, b in _blocks(text, "github_repository_environment_deployment_policy").items()
        if _arg(b, "environment") in refs | {f'"{ENV}"'}
    }
    if len(policies) != 1:
        out.append(f"f: expected exactly one deployment policy for {ENV}, found {len(policies)}")
    for n, b in policies.items():
        if _arg(b, "branch_pattern") != '"main"' or _arg(b, "tag_pattern") is not None:
            out.append(f'f: deployment policy {n} must be branch_pattern = "main" with no tag_pattern')
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "//")))
    targets = re.findall(r"^\s*to\s*=\s*(\S+)\s*$", stripped, re.M)
    for addr in [f"github_repository_environment.{n}" for n in envs] + [
        f"github_repository_environment_deployment_policy.{n}" for n in policies
    ]:
        if targets.count(addr) != 1:
            out.append(f"f: {addr} needs exactly one import block")
    return out


def check_checkouts(workflows: dict[str, dict[str, Any]], read: Reader = _no_read) -> list[str]:
    out = []
    for name in sorted(TRUSTED_WORKFLOWS & workflows.keys()):
        for jid, job in _governed(name, workflows[name]):
            if any(kind == "run" and _GIT_REF_RE.search(text) for kind, text, _ in _expanded(job, read)):
                out.append(f"g: {name}:{jid} runs git fetch or git checkout in a run body")
            for step in _steps(job):
                if not str(step.get("uses", "")).startswith("actions/checkout@"):
                    continue
                raw = step.get("with")
                with_: dict[str, Any] = raw if isinstance(raw, dict) else {}
                if "ref" in with_ or "repository" in with_:
                    out.append(f"g: {name}:{jid} checkout sets ref or repository")
                if str(with_.get("persist-credentials")).lower() != "false":
                    out.append(f"g: {name}:{jid} checkout does not set persist-credentials false")
    return out


def _owned(path: str, codeowners: str) -> bool:
    """GitHub semantics: the last matching pattern decides, and a matching line with no owner clears ownership."""
    owned = False
    for line in codeowners.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        anchored = parts[0].startswith("/")
        pattern = parts[0].lstrip("/")
        if pattern.endswith("/"):
            hit = (path + "/").startswith(pattern)
        else:
            hit = path == pattern or fnmatch.fnmatch(path, pattern)
            hit = hit or (not anchored and fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern))
        if hit:
            owned = len(parts) > 1
    return owned


def _imported_paths(text: str, read: Reader) -> set[str]:
    """Repo paths of the scripts.* and src.* modules (and their package __init__.py files) a Python file imports."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    found: set[str] = set()

    def add_module(dotted: str, submodule: bool = False) -> None:
        parts = dotted.split(".")
        if parts[0] not in ("scripts", "src"):
            return
        base = "/".join(parts)
        if submodule:
            if read(base + ".py") is not None:
                found.add(base + ".py")
            return
        found.update("/".join(parts[:i]) + "/__init__.py" for i in range(1, len(parts)))
        found.add(base + ".py" if read(base + ".py") is not None else base + "/__init__.py")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_module(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            add_module(node.module)
            for alias in node.names:
                add_module(f"{node.module}.{alias.name}", submodule=True)
    return found


def _inserts_repo_root_first(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == 0
        ):
            return True
    return False


def _closure(py_paths: set[str], read: Reader) -> set[str]:
    seen: set[str] = set()
    todo = sorted(py_paths)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        text = read(path) if path.endswith(".py") else None
        if text is not None:
            todo.extend(sorted(_imported_paths(text, read) - seen))
    return seen


def _executed_paths(expanded: list[tuple[str, str, str | None]]) -> list[str]:
    paths = [text[2:].rstrip("/") for kind, text, _ in expanded if kind == "uses" and text.startswith("./")]
    for kind, raw, directory in expanded:
        if kind != "run":
            continue
        text = re.sub(r"(?m)^\s*#.*$", "", raw)
        paths += _SCRIPT_PATH_RE.findall(text)
        paths += [m.replace(".", "/") + ".py" for m in _MODULE_RE.findall(text)]
        if directory:
            paths += [f"{directory}/{f}" for f in _ACTION_PATH_RE.findall(text)]
    return paths


def check_owned_and_isolated(workflows: dict[str, dict[str, Any]], codeowners: str, read: Reader = _no_read) -> list[str]:
    out = []
    for name in sorted(TRUSTED_WORKFLOWS & workflows.keys()):
        wf = workflows[name]
        for jid, job in _governed(name, wf):
            expanded = _expanded(job, read)
            paths = _executed_paths(expanded)
            closure = _closure({p for p in paths if p.endswith(".py")}, read)
            out += [
                f"h: {name}:{jid} executes {p}, which no CODEOWNERS pattern covers"
                for p in dict.fromkeys(paths)
                if not _owned(p, codeowners)
            ]
            out += [
                f"h: {name}:{jid} imports {p}, which no CODEOWNERS pattern covers"
                for p in sorted(closure - set(paths))
                if not _owned(p, codeowners)
            ]
            out += [
                f"h: {name}:{jid} runs {p}, which inserts the repo root at sys.path position 0 (append it instead)"
                for p in sorted(closure)
                if p.endswith(".py") and _inserts_repo_root_first(read(p) or "")
            ]
            for kind, body, _ in expanded:
                if kind != "run":
                    continue
                for m in _PYTHON_RE.finditer(body):
                    flag = (m.group(1) or "").strip()
                    if not _ISOLATED_FLAG_RE.fullmatch(flag):
                        out.append(f"h: {name}:{jid} runs python without -I")
    return out


def check_pinned_actions(workflows: dict[str, dict[str, Any]], read: Reader = _no_read) -> list[str]:
    out = []
    for name in sorted(TRUSTED_WORKFLOWS & workflows.keys()):
        for jid, job in _governed(name, workflows[name]):
            for kind, text, _ in _expanded(job, read):
                if kind == "uses" and not text.startswith("./") and not _SHA_PIN_RE.search(text + " "):
                    out.append(f"i: {name}:{jid} uses {text.split('#')[0].strip()} without a 40-hex commit SHA pin")
    return out


def run_all(
    workflows: dict[str, dict[str, Any]], terraform: dict[str, str], codeowners: str, read: Reader = _no_read
) -> list[list[str]]:
    return [
        check_environment_references(workflows),
        check_prt_minting(workflows),
        check_trusted_files(workflows),
        check_minting_jobs(workflows),
        check_run_bodies(workflows),
        check_terraform(terraform),
        check_checkouts(workflows, read),
        check_owned_and_isolated(workflows, codeowners, read),
        check_pinned_actions(workflows, read),
    ]


def _repo_reader() -> Reader:
    def read(rel: str) -> str | None:
        try:
            return (_common.ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return read


@registry.register("validate_trusted_plan_environment", owner="platform")
def validate_trusted_plan_environment(failed: list[str]) -> None:
    """Assert the tf-trusted-plan Environment and pull_request_target fence. See module docstring."""
    print("\n=== trusted-plan environment fence ===")
    try:
        workflows = {
            p.name: _load(str(p))
            for p in sorted((_common.ROOT / ".github" / "workflows").glob("*.y*ml"))
            if p.suffix in (".yml", ".yaml")
        }
        terraform = {
            p.name: p.read_text(encoding="utf-8") for p in sorted((_common.ROOT / "terraform" / "github").glob("*.tf"))
        }
        codeowners = (_common.ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    except (OSError, yaml.YAMLError, TypeError, AttributeError) as exc:
        print(f"  FAIL: could not load the inputs: {exc}")
        failed.append("trusted-plan-environment: inputs unreadable, so no predicate ran")
        registry.examined(0, unit="fence_predicates")
        return
    violations = [x for group in run_all(workflows, terraform, codeowners, _repo_reader()) for x in group]
    for violation in violations:
        print(f"  FAIL: {violation}")
        failed.append(f"trusted-plan-environment: {violation}")
    registry.examined(9, unit="fence_predicates")
    if not violations:
        print("  PASS: predicates (a)-(i)")
