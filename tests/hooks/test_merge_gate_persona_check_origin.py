"""rec-5241 acceptance (Decision 270): the merge gate counts a persona check only from the verdict GitHub App that
gates.verdict_app.app_id names on origin/main, through the shim as the harness runs it."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx

_CODE = "persona-verdict/code_reviewer"
_TOOLS = ("mcp__github__merge_pull_request", "mcp__github__enable_pr_auto_merge")
_OTHER_APP = 900002


def _run(
    conclusion: str, run_id: int, *, app_id: int | None = fx.VERDICT_APP_ID, started: str = "2026-11-17T10:00:00Z"
) -> dict:
    app = {"slug": "github-actions", "id": 15368} if app_id is None else {"slug": "verdict-app", "id": app_id}
    return {
        "id": run_id,
        "name": _CODE,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": started,
        "app": app,
    }


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("origin")
    f = fx.Fixture(tmp)
    project = tmp / "project"
    project.mkdir()
    fx.install_project(project)
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)
    return {"f": f, "project": project, "tmp": tmp, "n": itertools.count(300)}


class _Case:
    def __init__(self, env: dict[str, object]) -> None:
        self.f: fx.Fixture = env["f"]  # type: ignore[assignment]
        self.project: Path = env["project"]  # type: ignore[assignment]
        self.tmp: Path = env["tmp"]  # type: ignore[assignment]
        self.counter = env["n"]
        self.f.set_required(["pr-validate", "terraform-validate"])

    def main(self, *, app_id: int | None = fx.VERDICT_APP_ID, code: str = "actions", text: str | None = None) -> None:
        fx.git(self.f.work, "checkout", "-q", "main")
        self.f.sources = {"code_reviewer": code} if code != "in_session" else {}
        self.f.app_id = app_id
        text = text if text is not None else fx.config_text(self.f.sources, app_id=app_id)
        path = self.f.work / fx.CONFIG_REL
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            self.f.advance_main(fx.CONFIG_REL, text)

    def pr(self, runs: list[dict] | None = None, *, rel: str = "src/code.py", text: str = "x\n") -> tuple[int, str]:
        number = next(self.counter)  # type: ignore[call-overload]
        sha = self.f.branch_pr(number, rel, text, name=f"pr{number}")
        self.f.set_checks(sha, [*self.f.green_runs(), *(runs or [])])
        return number, sha

    def config_pr(self, text: str, runs: list[dict] | None = None) -> tuple[int, str]:
        return self.pr(runs, rel=fx.CONFIG_REL, text=text)

    def go(self, number: int, sha: str, tool: str = _TOOLS[0]):  # noqa: ANN202
        extra: dict[str, object] = {"expectedHeadSha": sha} if tool == _TOOLS[0] else {}
        return fx.run_shim(self.project, fx.merge_payload(number, tool=tool, **extra), cwd=self.tmp)


@pytest.fixture
def case(env: dict[str, object]) -> _Case:
    return _Case(env)


def test_refuses_persona_check_from_another_workflow(case: _Case) -> None:
    case.main()
    n, sha = case.pr([_run("success", 1, app_id=None)])
    r = case.go(n, sha)
    assert r.returncode == 2 and _CODE in r.stderr and "no run by the verdict App" in r.stderr


@pytest.mark.parametrize("tool", _TOOLS)
def test_the_pinned_app_run_passes_beside_a_forged_one(case: _Case, tool: str) -> None:
    case.main()
    n, sha = case.pr([_run("success", 1, app_id=None), _run("success", 2)])
    r = case.go(n, sha, tool)
    assert (r.returncode, r.stderr) == (0, "")


def test_a_failed_app_run_under_a_forged_success_refuses(case: _Case) -> None:
    case.main()
    n, sha = case.pr([_run("success", 1, app_id=None, started="2026-11-17T12:00:00Z"), _run("failure", 2)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "concluded failure" in r.stderr


def test_an_empty_row_with_an_actions_row_refuses(case: _Case) -> None:
    case.main(app_id=None)
    n, sha = case.pr([_run("success", 1)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "has no verdict App" in r.stderr


def test_an_unreadable_row_refuses_only_when_a_persona_check_is_judged(case: _Case) -> None:
    bad = fx.config_text({"code_reviewer": "actions"}, app_id=fx.VERDICT_APP_ID).replace(
        f"local: {fx.VERDICT_APP_ID}, cloud: {fx.VERDICT_APP_ID}", "local: abc, cloud: abc"
    )
    case.main(text=bad)
    n, sha = case.pr([_run("success", 1)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr and "gates.verdict_app.app_id" in r.stderr
    case.main(code="in_session", text=bad.replace("actions", "in_session"))
    n, sha = case.pr()
    assert case.go(n, sha).returncode == 0


@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize(
    ("before", "after"), [(None, fx.VERDICT_APP_ID), (fx.VERDICT_APP_ID, _OTHER_APP), (fx.VERDICT_APP_ID, None)]
)
def test_a_pr_changing_the_row_is_refused_for_an_agent(case: _Case, tool: str, before: int | None, after: int | None) -> None:
    case.main(app_id=before)
    n, sha = case.config_pr(
        fx.config_text({"code_reviewer": "actions"}, app_id=after), [_run("success", 1, app_id=before or 1)]
    )
    r = case.go(n, sha, tool)
    assert r.returncode == 2 and "operator merges it in the GitHub UI" in r.stderr and "verdict_app" in r.stderr


def test_a_pr_leaving_the_row_unchanged_is_not_refused_for_it(case: _Case) -> None:
    case.main()
    n, sha = case.config_pr(
        fx.config_text({"code_reviewer": "actions"}, app_id=fx.VERDICT_APP_ID) + "# note\n", [_run("success", 1)]
    )
    assert case.go(n, sha).returncode == 0


def test_a_pr_adding_the_rows_with_the_app_null_to_a_main_without_rows_passes(case: _Case) -> None:
    fx.git(case.f.work, "checkout", "-q", "main")
    case.f.clear_gate_rows()
    n, sha = case.config_pr(fx.config_text(app_id=None))
    assert case.go(n, sha).returncode == 0


def test_a_working_tree_edit_cannot_turn_a_refusal_into_an_allow(case: _Case) -> None:
    case.main()
    n, sha = case.pr([_run("success", 1, app_id=None)])
    path = case.project / fx.CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fx.config_text(app_id=None), encoding="utf-8")
    assert case.go(n, sha).returncode == 2


def test_a_ruleset_required_persona_check_is_pinned_while_every_row_reads_in_session(case: _Case) -> None:
    case.main(code="in_session")
    case.f.set_required(["pr-validate", "terraform-validate", _CODE])
    n, sha = case.pr([_run("success", 1, app_id=None)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "no run by the verdict App" in r.stderr
    case.f.set_checks(sha, [*case.f.green_runs(), _run("success", 1, app_id=None), _run("success", 2)])
    assert case.go(n, sha).returncode == 0


def test_a_switch_back_that_also_changes_the_app_row_is_refused_for_the_persona_first(case: _Case) -> None:
    case.main()
    n, sha = case.config_pr(fx.config_text(app_id=_OTHER_APP), [_run("success", 1)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "code_reviewer" in r.stderr and "actions to in_session" in r.stderr


def test_a_head_whose_app_row_does_not_read_is_refused(case: _Case) -> None:
    case.main()
    bad = fx.config_text({"code_reviewer": "actions"}, app_id=fx.VERDICT_APP_ID).replace(
        f"local: {fx.VERDICT_APP_ID}, cloud: {fx.VERDICT_APP_ID}", "local: abc, cloud: abc"
    )
    n, sha = case.config_pr(bad, [_run("success", 1)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "the PR head's gates.verdict_app.app_id does not read" in r.stderr


def test_a_merge_base_whose_app_row_does_not_read_is_unavailable(case: _Case) -> None:
    bad = fx.config_text({"code_reviewer": "actions"}, app_id=fx.VERDICT_APP_ID).replace(
        f"local: {fx.VERDICT_APP_ID}, cloud: {fx.VERDICT_APP_ID}", "local: true, cloud: true"
    )
    case.main(text=bad)
    n, sha = case.config_pr(fx.config_text({"code_reviewer": "actions"}, app_id=fx.VERDICT_APP_ID), [_run("success", 1)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr and "on the merge base does not read" in r.stderr
