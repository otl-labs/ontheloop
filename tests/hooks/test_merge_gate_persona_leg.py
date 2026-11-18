"""T4.32 c5 / rec-5121: the merge gate's persona leg (Decision 264 cl.4-5), through the shim as the harness runs it."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx

_CODE = "persona-verdict/code_reviewer"
_CRITIC = "persona-verdict/plan_critic"
_TOOLS = ("mcp__github__merge_pull_request", "mcp__github__enable_pr_auto_merge")


def _run(name: str, conclusion: str | None, run_id: int, *, status: str = "completed", app: str = "verdict-app") -> dict:
    app_object = {"slug": app, "id": fx.VERDICT_APP_ID} if app == "verdict-app" else {"slug": app}
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-11-16T10:00:00Z",
        "app": app_object,
    }


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("persona")
    f = fx.Fixture(tmp)
    project = tmp / "project"
    project.mkdir()
    fx.install_project(project)
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)
    return {"f": f, "project": project, "tmp": tmp, "n": itertools.count(100)}


class _Case:
    def __init__(self, env: dict[str, object]) -> None:
        self.f: fx.Fixture = env["f"]  # type: ignore[assignment]
        self.project: Path = env["project"]  # type: ignore[assignment]
        self.tmp: Path = env["tmp"]  # type: ignore[assignment]
        self.counter = env["n"]
        self.f.set_required(["pr-validate", "terraform-validate"])
        self.set_main()

    def set_main(self, *, rows: bool = True, **values: str) -> None:
        app_id = fx.VERDICT_APP_ID if rows and "actions" in values.values() else None
        text = fx.config_text(values, rows=rows, app_id=app_id)
        fx.git(self.f.work, "checkout", "-q", "main")
        self.f.sources = dict(values) if rows else {}
        self.f.app_id = app_id
        path = self.f.work / fx.CONFIG_REL
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            self.f.advance_main(fx.CONFIG_REL, text)

    def pr(self, rel: str = "src/code.py", persona_runs: list[dict] | None = None) -> tuple[int, str]:
        number = next(self.counter)  # type: ignore[call-overload]
        sha = self.f.branch_pr(number, rel, "x\n", name=f"pr{number}")
        self.checks(sha, persona_runs)
        return number, sha

    def checks(self, sha: str, persona_runs: list[dict] | None) -> None:
        self.f.set_checks(sha, [*self.f.green_runs(), *(persona_runs or [])])

    def switch_pr(self, persona: str, value: str, persona_runs: list[dict] | None = None) -> tuple[int, str]:
        number = next(self.counter)  # type: ignore[call-overload]
        sha = self.f.branch_pr_switch(number, persona, value, name=f"pr{number}")
        self.checks(sha, persona_runs)
        return number, sha

    def go(self, number: int, sha: str, tool: str = _TOOLS[0], env: dict[str, str] | None = None, **extra: object):  # noqa: ANN202
        if tool == _TOOLS[0]:
            extra["expectedHeadSha"] = sha
        return fx.run_shim(self.project, fx.merge_payload(number, tool=tool, **extra), cwd=self.tmp, extra_env=env)


@pytest.fixture
def case(env: dict[str, object]) -> _Case:
    return _Case(env)


@pytest.mark.parametrize("tool", _TOOLS)
def test_all_in_session_allows_a_green_head(case: _Case, tool: str) -> None:
    n, sha = case.pr()
    r = case.go(n, sha, tool)
    assert (r.returncode, r.stderr) == (0, "")


@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize(
    "runs",
    [
        [],
        [_run(_CODE, "failure", 1)],
        [_run(_CODE, None, 1, status="in_progress")],
        [_run(_CODE, "success", 1), _run(_CODE, None, 9, status="queued")],
        [_run(_CODE, "success", 1, app="other")],
    ],
    ids=["absent", "failed", "pending", "newer-queued", "other-app"],
)
def test_actions_row_requires_the_persona_check_on_a_code_pr(case: _Case, tool: str, runs: list[dict]) -> None:
    case.set_main(code_reviewer="actions")
    n, sha = case.pr(persona_runs=runs)
    r = case.go(n, sha, tool)
    assert r.returncode == 2 and _CODE in r.stderr and "REFUSE" in r.stderr
    assert "gates.code_reviewer.verdict_source reads actions on origin/main" in r.stderr


def test_actions_row_with_a_green_persona_check_allows(case: _Case) -> None:
    case.set_main(code_reviewer="actions")
    n, sha = case.pr(persona_runs=[_run(_CODE, "success", 1)])
    assert case.go(n, sha).returncode == 0


def test_plan_critic_needs_its_check_on_a_plans_only_pr_and_a_code_pr_is_unaffected(case: _Case) -> None:
    case.set_main(plan_critic="actions")
    n, sha = case.pr(rel="docs/plans/PLAN-x.yaml")
    r = case.go(n, sha)
    assert r.returncode == 2 and _CRITIC in r.stderr
    case.checks(sha, [_run(_CRITIC, "success", 1)])
    assert case.go(n, sha).returncode == 0
    n, sha = case.pr(rel="src/code.py")
    assert case.go(n, sha).returncode == 0


def test_a_mixed_pr_counts_as_code(case: _Case) -> None:
    case.set_main(code_reviewer="actions", plan_critic="actions")
    f = case.f
    number = next(case.counter)  # type: ignore[call-overload]
    fx.git(f.work, "checkout", "-q", "-b", f"mixed{number}", "main")
    fx.commit(f.work, "docs/plans/PLAN-y.yaml", "y\n")
    sha = fx.commit(f.work, "src/mixed.py", "m\n")
    fx.git(f.work, "push", "origin", f"HEAD:refs/pull/{number}/head")
    fx.git(f.work, "checkout", "-q", "main")
    case.checks(sha, [_run(_CODE, "success", 1)])
    assert case.go(number, sha).returncode == 0
    case.checks(sha, [_run(_CRITIC, "success", 1)])
    r = case.go(number, sha)
    assert r.returncode == 2 and _CODE in r.stderr and _CRITIC not in r.stderr


def test_unreadable_config_on_main_is_unavailable(case: _Case) -> None:
    n, sha = case.pr()
    fx.git(case.f.work, "checkout", "-q", "main")
    fx.git(case.f.work, "rm", "-q", fx.CONFIG_REL)
    fx.git(case.f.work, "commit", "-q", "-m", "drop config")
    fx.git(case.f.work, "push", "origin", "main")
    r = case.go(n, sha)
    assert r.returncode == 2 and "verdict-source: UNAVAILABLE" in r.stderr


@pytest.mark.parametrize("bad", ["missing", "value", "split"])
def test_bad_rows_on_main_are_unavailable(case: _Case, bad: str) -> None:
    n, sha = case.pr()
    lines = fx.config_text().splitlines()
    if bad == "missing":
        lines = lines[:-2]
    elif bad == "value":
        lines[2] = "    defaults: {local: both, cloud: both}"
    else:
        lines[2] = "    defaults: {local: actions, cloud: in_session}"
    case.f.advance_main(fx.CONFIG_REL, "\n".join(lines) + "\n")
    r = case.go(n, sha)
    assert r.returncode == 2 and "verdict-source: UNAVAILABLE" in r.stderr


@pytest.mark.parametrize("tool", _TOOLS)
def test_a_pr_returning_a_row_to_in_session_is_refused_for_an_agent(case: _Case, tool: str) -> None:
    case.set_main(code_reviewer="actions")
    n, sha = case.switch_pr("code_reviewer", "in_session", [_run(_CODE, "success", 1)])
    r = case.go(n, sha, tool)
    assert r.returncode == 2 and "code_reviewer" in r.stderr and "operator merges it in the GitHub UI" in r.stderr


def test_a_pr_that_does_not_touch_the_file_is_never_a_switch_back(case: _Case) -> None:
    case.set_main()
    n, sha = case.pr(persona_runs=[_run(_CODE, "success", 1)])
    case.set_main(code_reviewer="actions")
    assert case.go(n, sha).returncode == 0


def test_a_pr_whose_head_predates_the_rows_passes_once_main_reads_actions(case: _Case) -> None:
    case.set_main(rows=False)
    n, sha = case.pr(persona_runs=[_run(_CODE, "success", 1)])
    case.set_main(code_reviewer="actions")
    assert case.go(n, sha).returncode == 0
    n, sha = case.switch_pr("code_reviewer", "in_session", [_run(_CODE, "success", 1)])
    assert case.go(n, sha).returncode == 2


def test_an_actions_row_with_no_ruleset_read_is_unavailable(case: _Case) -> None:
    case.set_main(code_reviewer="actions")
    n, sha = case.pr(persona_runs=[_run(_CODE, "success", 1)])
    (case.f.origin / "merge-gate-api" / "rules-branches-main.json").unlink()
    r = case.go(n, sha)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr


def test_a_main_with_no_gates_rows_accepts_a_pr_adding_them_at_in_session(case: _Case) -> None:
    case.set_main(rows=False)
    n, sha = case.switch_pr("code_reviewer", "in_session")
    assert case.go(n, sha).returncode == 0


def test_a_removed_actions_row_is_refused(case: _Case) -> None:
    case.set_main(code_reviewer="actions")
    number = next(case.counter)  # type: ignore[call-overload]
    sha = case.f.branch_pr(number, fx.CONFIG_REL, fx.config_text(rows=False), name=f"pr{number}")
    case.checks(sha, [_run(_CODE, "success", 1)])
    r = case.go(number, sha)
    assert r.returncode == 2 and "code_reviewer" in r.stderr


def test_nothing_local_turns_a_refusal_into_an_allow(case: _Case) -> None:
    case.set_main(code_reviewer="actions")
    n, sha = case.pr()
    (case.project / fx.CONFIG_REL).parent.mkdir(parents=True, exist_ok=True)
    (case.project / fx.CONFIG_REL).write_text(fx.config_text(), encoding="utf-8")
    env = {"OTL_CODE_REVIEWER_VERDICT_SOURCE": "in_session", "OTL_VERDICT_SOURCE": "in_session"}
    r = case.go(n, sha, env=env, verdictSource="in_session", skipPersonaLeg=True)
    assert r.returncode == 2 and _CODE in r.stderr
