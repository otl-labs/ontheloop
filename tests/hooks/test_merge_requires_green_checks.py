"""rec-4080: the merge gate refuses unless every required check's newest github-actions run succeeded on the head."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx

_FIRST = 10


def _run_of(
    name: str,
    conclusion: str | None,
    run_id: int,
    *,
    status: str = "completed",
    app: str = "github-actions",
    started: str | None = "2026-11-12T10:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "app": {"slug": app},
    }


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("checks")
    f = fx.Fixture(tmp)
    for n in range(_FIRST, _FIRST + 20):
        f.branch_pr(n, f"f{n}.txt", "pr\n", name=f"pr{n}")
    project = tmp / "project"
    project.mkdir()
    fx.install_project(project)
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)
    return {"f": f, "project": project, "tmp": tmp}


class _Case:
    def __init__(self, env: dict[str, object]) -> None:
        self.f: fx.Fixture = env["f"]  # type: ignore[assignment]
        self.project: Path = env["project"]  # type: ignore[assignment]
        self.tmp: Path = env["tmp"]  # type: ignore[assignment]
        self.env = env

    def pr(self, runs: list[dict[str, object]] | None = None, total: int | None = None) -> tuple[int, str]:
        number = int(self.env.get("next", _FIRST))  # type: ignore[call-overload]
        self.env["next"] = number + 1
        sha = self.f.head_of(number)
        if runs is not None:
            self.f.set_checks(sha, runs, total)
        return number, sha

    def go(
        self,
        number: int,
        sha: str | None,
        *,
        tool: str = "mcp__github__merge_pull_request",
        env: dict[str, str] | None = None,
        **extra: object,
    ):  # noqa: ANN202
        if sha is not None:
            extra["expectedHeadSha"] = sha
        return fx.run_shim(self.project, fx.merge_payload(number, tool=tool, **extra), cwd=self.tmp, extra_env=env)


@pytest.fixture
def case(env: dict[str, object]) -> _Case:
    c = _Case(env)
    c.f.set_required(["pr-validate", "terraform-validate"])
    return c


def _red() -> list[dict[str, object]]:
    return [_run_of("pr-validate", "failure", 1), _run_of("terraform-validate", "success", 2)]


def test_green_with_expected_head_allows_silently(case: _Case) -> None:
    n, sha = case.pr()
    r = case.go(n, sha)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_red_pr_validate_refuses(case: _Case) -> None:
    n, sha = case.pr(_red())
    r = case.go(n, sha)
    assert r.returncode == 2 and "pr-validate" in r.stderr and "REFUSE" in r.stderr and sha[:12] in r.stderr


@pytest.mark.parametrize("state", ["pending", "skipped", "newer-queued"])
def test_non_success_states_refuse(case: _Case, state: str) -> None:
    ok = _run_of("terraform-validate", "success", 2)
    if state == "pending":
        runs = [_run_of("pr-validate", None, 1, status="in_progress"), ok]
    elif state == "skipped":
        runs = [_run_of("pr-validate", "skipped", 1), ok]
    else:
        runs = [
            _run_of("pr-validate", "success", 1),
            _run_of("pr-validate", None, 5, status="queued", started="2026-11-12T11:00:00Z"),
            ok,
        ]
    n, sha = case.pr(runs)
    r = case.go(n, sha)
    assert r.returncode == 2 and "REFUSE" in r.stderr and "pr-validate" in r.stderr


def test_missing_required_run_and_other_app_run_refuse(case: _Case) -> None:
    n, sha = case.pr([_run_of("pr-validate", "success", 1)])
    assert case.go(n, sha).returncode == 2
    n, sha = case.pr([_run_of("pr-validate", "success", 1, app="other"), _run_of("terraform-validate", "success", 2)])
    r = case.go(n, sha)
    assert r.returncode == 2 and "pr-validate" in r.stderr


def test_unreadable_states_are_unavailable(case: _Case) -> None:
    n, sha = case.pr()
    case.f.set_required(None)
    assert "UNAVAILABLE" in case.go(n, sha).stderr
    (case.f.origin / "merge-gate-api" / "rules-branches-main.json").unlink()
    assert "UNAVAILABLE" in case.go(n, sha).stderr
    case.f.set_required(["pr-validate", "terraform-validate"])
    (case.f.origin / "merge-gate-api" / "check-runs" / f"{sha}.json").unlink()
    r = case.go(n, sha)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr
    n, sha = case.pr(case.f.green_runs(), total=150)
    r = case.go(n, sha)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr


def test_merge_without_expected_head_refuses_naming_it(case: _Case) -> None:
    n, sha = case.pr()
    r = case.go(n, None)
    assert r.returncode == 2 and "expectedHeadSha" in r.stderr and sha in r.stderr


def test_auto_merge_is_judged_on_the_fetched_head(case: _Case) -> None:
    n, _ = case.pr()
    assert case.go(n, None, tool="mcp__github__enable_pr_auto_merge").returncode == 0
    n, _ = case.pr(_red())
    r = case.go(n, None, tool="mcp__github__enable_pr_auto_merge")
    assert r.returncode == 2 and "REFUSE" in r.stderr


def test_origin_that_is_neither_http_nor_file_refuses(case: _Case) -> None:
    n, sha = case.pr()
    project = case.project
    key = f"url.{case.f.origin_url}.insteadOf"
    fx.git(project, "config", "--unset", key)
    fx.git(project, "config", f"url.{case.f.origin}.insteadOf", fx.GITHUB_URL)
    try:
        r = case.go(n, sha)
    finally:
        fx.git(project, "config", "--unset", f"url.{case.f.origin}.insteadOf")
        fx.git(project, "config", key, fx.GITHUB_URL)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr


def test_proxy_token_and_unknown_fields_cannot_turn_a_refusal_into_a_pass(case: _Case) -> None:
    n, sha = case.pr(_red())
    env = {"HTTPS_PROXY": "http://127.0.0.1:9", "GITHUB_TOKEN": "x", "GITHUB_ACTIONS": "true"}
    r = case.go(n, sha, env=env, skip_checks=True, api_url="http://127.0.0.1:9", merge_gate_override="1")
    assert r.returncode == 2 and "REFUSE" in r.stderr


def test_short_upper_case_head_reads_the_full_sha_file(case: _Case) -> None:
    n, sha = case.pr()
    r = case.go(n, sha[:8].upper())
    assert (r.returncode, r.stderr) == (0, "")


def test_contexts_of_two_required_rules_are_both_required(case: _Case) -> None:
    n, sha = case.pr([_run_of("pr-validate", "success", 1), _run_of("terraform-validate", "success", 2)])
    rule = {"type": "required_status_checks"}
    case.f.write_api(
        "rules-branches-main.json",
        [
            {**rule, "parameters": {"required_status_checks": [{"context": "pr-validate"}]}},
            {**rule, "parameters": {"required_status_checks": [{"context": "lint"}]}},
        ],
    )
    r = case.go(n, sha)
    assert r.returncode == 2 and "lint" in r.stderr


def test_overlap_refusal_still_comes_first(case: _Case) -> None:
    f = case.f
    number = 99
    f.branch_pr(number, "a.txt", "pr\n", name="pr99")
    f.set_checks(f.head_of(number), _red())
    f.advance_main("a.txt", "main\n")
    r = case.go(number, f.head_of(number))
    assert r.returncode == 2 and "OVERLAP" in r.stderr and "REFUSE" not in r.stderr
