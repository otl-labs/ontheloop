"""Recorded-API oracle for the approved-text GitHub adapter (rec-4769; Decision 212 cl.2: no credential)."""

from __future__ import annotations

import copy
import json
import urllib.error
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import repo_identity
from scripts.roadmap import approved_text_github as gh
from scripts.roadmap import approved_text_provenance as prov
from scripts.roadmap import approved_words
from tests.fixtures import merge_step_repos as fx

HERE = Path(__file__).parent / "fixtures" / "approved_text_github"


def load(name: str) -> Any:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def approval_run(slug: str, approved: str, head_sha: str, run_id: int = 9100, **overrides: Any) -> dict[str, Any]:
    run = copy.deepcopy(load("runs.json")["workflow_runs"][0])
    run.update(
        id=run_id,
        path=gh.WORKFLOW_PATH,
        name="approved-text-approval",
        display_title=gh.run_title(slug, approved),
        head_branch="main",
        head_sha=head_sha,
        event="workflow_dispatch",
        status="completed",
        conclusion="success",
    )
    run.update(overrides)
    return run


def jobs_for(run_id: int, *, name: str = "approve", conclusion: str | None = "success") -> dict[str, Any]:
    body = copy.deepcopy(load("jobs.json"))
    body["jobs"][0].update(run_id=run_id, name=name, conclusion=conclusion)
    return body


def approvals_for(*, state: str = "approved", environment: str = "approved-text") -> list[dict[str, Any]]:
    body = copy.deepcopy(load("approvals.json"))
    body[0]["state"] = state
    body[0]["environments"][0]["name"] = environment
    return body


class Api:
    """Replaces approved_text_github.http_get: answers from fixtures and records every request."""

    def __init__(
        self,
        runs: list[dict[str, Any]],
        *,
        jobs: dict[int, Any] | None = None,
        approvals: dict[int, Any] | None = None,
        pulls: Any = None,
        fail: Exception | None = None,
    ) -> None:
        self.runs, self.jobs, self.approvals = runs, jobs or {}, approvals or {}
        self.pulls = load("pulls.json") if pulls is None else pulls
        self.fail = fail
        self.calls: list[tuple[str, str | None, float]] = []

    def __call__(self, url: str, token: str | None, timeout: float) -> Any:
        self.calls.append((url, token, timeout))
        if self.fail is not None:
            raise self.fail
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if parts[-1] == "runs":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            return {"total_count": len(self.runs), "workflow_runs": self.runs[(page - 1) * 100 : page * 100]}
        if parts[-1] == "jobs":
            run_id = int(parts[-2])
            return self.jobs.get(run_id, jobs_for(run_id))
        if parts[-1] == "approvals":
            run_id = int(parts[-2])
            return self.approvals.get(run_id, approvals_for())
        if parts[-1] == "pulls":
            return self.pulls
        raise AssertionError(f"unexpected request {url}")

    def run_list_calls(self) -> list[str]:
        return [u for u, _, _ in self.calls if urlparse(u).path.endswith("/actions/runs")]


SLUG = "demo"
DIGEST = "ab" * 32
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("APPROVED_TEXT_GH_TOKEN", "GITHUB_EVENT_PATH", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture-owner/fixture-repo")


@pytest.fixture
def repo(tmp_path) -> tuple[Path, str, str]:
    """A repository whose origin/main holds `main_sha`; `side_sha` is on a branch main does not contain."""
    root = fx.make_repo(tmp_path)
    main_sha = fx.git(root, "rev-parse", "HEAD")
    fx.branch(root, "side")
    side_sha = fx.commit_file(root, "side.txt", "x\n", "side")
    fx.git(root, "checkout", "main")
    return root, main_sha, side_sha


def _lookup(runs, root, *, digest=DIGEST, double=None, deadline=None, monkeypatch=None, repo_name=None):
    double = double or Api(runs)
    monkeypatch.setattr(gh, "http_get", double)
    return gh.lookup(SLUG, digest, root, deadline or prov.Deadline(30), repo_name), double


def test_an_approved_main_run_reads_approved(repo, monkeypatch):
    root, main_sha, _ = repo
    verdict, _ = _lookup([approval_run(SLUG, DIGEST, main_sha)], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.APPROVED and "approver operator-fixture" in verdict.detail and "run 9100" in verdict.detail


@pytest.mark.parametrize(
    "mutate",
    [
        {"display_title": "approved-text demo wrong-words-here-now " + "cd" * 32},
        {"path": ".github/workflows/deployment-approval-probe.yml"},
        {"head_branch": "feature"},
    ],
    ids=["wrong-title", "wrong-path", "branch-not-main"],
)
def test_a_run_that_is_not_the_approval_reads_missing(repo, monkeypatch, mutate):
    root, main_sha, _ = repo
    verdict, _ = _lookup([approval_run(SLUG, DIGEST, main_sha, **mutate)], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.MISSING


def test_a_sha_that_is_not_on_main_reads_missing(repo, monkeypatch):
    root, _, side_sha = repo
    verdict, _ = _lookup([approval_run(SLUG, DIGEST, side_sha)], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.MISSING


@pytest.mark.parametrize(
    "jobs,approvals",
    [
        (jobs_for(9100, conclusion="failure"), None),
        (jobs_for(9100, name="render"), None),
        (None, approvals_for(state="rejected")),
        (None, approvals_for(environment="tf-gated-apply")),
    ],
    ids=["approve-failed", "not-the-approve-job", "rejected", "wrong-environment"],
)
def test_a_run_without_an_approved_text_approval_reads_missing(repo, monkeypatch, jobs, approvals):
    root, main_sha, _ = repo
    double = Api(
        [approval_run(SLUG, DIGEST, main_sha)],
        jobs={9100: jobs} if jobs else None,
        approvals={9100: approvals} if approvals else None,
    )
    verdict, _ = _lookup(None, root, double=double, monkeypatch=monkeypatch)
    assert verdict.kind == prov.MISSING


def test_a_run_still_in_progress_with_a_successful_approve_job_reads_approved(repo, monkeypatch):
    root, main_sha, _ = repo
    run = approval_run(SLUG, DIGEST, main_sha, status="in_progress", conclusion=None)
    verdict, _ = _lookup([run], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.APPROVED


def test_newer_waiting_and_rejected_reruns_do_not_hide_an_older_approved_run(repo, monkeypatch):
    root, main_sha, _ = repo
    newer = [approval_run(SLUG, DIGEST, main_sha, 9200 + i, status="waiting", conclusion=None) for i in range(3)]
    newer += [approval_run(SLUG, DIGEST, main_sha, 9300 + i, conclusion="failure") for i in range(2)]
    runs = [*newer, approval_run(SLUG, DIGEST, main_sha, 9100)]
    verdict, double = _lookup(runs, root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.APPROVED and "run 9100" in verdict.detail
    assert not any("/9200/" in u or "/9300/" in u for u, _, _ in double.calls)


def test_the_run_list_is_the_repository_wide_endpoint_with_event_and_branch_filters(repo, monkeypatch):
    root, main_sha, _ = repo
    _, double = _lookup([approval_run(SLUG, DIGEST, main_sha)], root, monkeypatch=monkeypatch)
    expected = (
        f"{gh.API}/repos/{repo_identity.full_name()}/actions/runs?event=workflow_dispatch&branch=main&per_page=100&page=1"
    )
    assert double.run_list_calls() == [expected] and "/workflows/" not in expected


def test_a_run_past_the_first_page_is_found(repo, monkeypatch):
    root, main_sha, _ = repo
    filler = [approval_run("other", DIGEST, main_sha, 5000 + i) for i in range(100)]
    verdict, double = _lookup([*filler, approval_run(SLUG, DIGEST, main_sha)], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.APPROVED and len(double.run_list_calls()) == 2


def test_repo_argument_overrides_the_repository_identity(repo, monkeypatch):
    root, main_sha, _ = repo
    _, double = _lookup([], root, monkeypatch=monkeypatch, repo_name="other-owner/other-repo")
    assert "/repos/other-owner/other-repo/actions/runs" in double.run_list_calls()[0]


@pytest.mark.parametrize(
    "failure",
    [
        gh.GitHubError("HTTP 403 from x"),
        gh.GitHubError("request failed: timed out"),
        gh.GitHubError("unparsable body from x"),
    ],
    ids=["http-error", "timeout", "unparsable"],
)
def test_a_transport_failure_reads_unverified_never_missing(repo, monkeypatch, failure):
    root, _, _ = repo
    verdict, _ = _lookup(None, root, double=Api([], fail=failure), monkeypatch=monkeypatch)
    assert verdict.kind == prov.UNVERIFIED and str(failure) in verdict.detail


def test_a_body_without_the_expected_array_reads_unverified(repo, monkeypatch):
    root, _, _ = repo
    monkeypatch.setattr(gh, "http_get", lambda url, token, timeout: {"message": "Not Found"})
    assert gh.lookup(SLUG, DIGEST, root, prov.Deadline(30)).kind == prov.UNVERIFIED


def test_an_expired_deadline_reads_unverified(repo, monkeypatch):
    root, main_sha, _ = repo
    verdict, double = _lookup([approval_run(SLUG, DIGEST, main_sha)], root, deadline=prov.Deadline(0), monkeypatch=monkeypatch)
    assert verdict.kind == prov.UNVERIFIED and "deadline" in verdict.detail and double.calls == []


def test_a_sha_git_cannot_place_reads_unverified(repo, monkeypatch):
    root, _, _ = repo
    verdict, _ = _lookup([approval_run(SLUG, DIGEST, "0" * 40)], root, monkeypatch=monkeypatch)
    assert verdict.kind == prov.UNVERIFIED and "git cannot place" in verdict.detail


def test_only_the_dedicated_token_variable_is_read(repo, monkeypatch):
    root, main_sha, _ = repo
    monkeypatch.setenv("GH_TOKEN", "wrong")
    monkeypatch.setenv("GITHUB_TOKEN", "wrong")
    _, anonymous = _lookup([approval_run(SLUG, DIGEST, main_sha)], root, monkeypatch=monkeypatch)
    assert {tok for _, tok, _ in anonymous.calls} == {None} and gh.auth_label() == "anonymous"
    monkeypatch.setenv("APPROVED_TEXT_GH_TOKEN", "right")
    _, authed = _lookup([approval_run(SLUG, DIGEST, main_sha)], root, monkeypatch=monkeypatch)
    assert {tok for _, tok, _ in authed.calls} == {"right"} and gh.auth_label() == "token"


def test_a_request_timeout_is_capped_at_ten_seconds(repo, monkeypatch):
    root, _, _ = repo
    _, double = _lookup([], root, monkeypatch=monkeypatch)
    assert max(t for _, _, t in double.calls) <= gh.REQUEST_TIMEOUT


def test_the_http_transport_maps_errors_to_github_error(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(gh.urllib.request, "urlopen", boom)
    with pytest.raises(gh.GitHubError, match="request failed"):
        gh.http_get("https://api.github.com/x", None, 1)


class TestCli:
    def _sidecar(self, root: Path) -> str:
        fx.write_sidecar(root, SLUG, [BLOCK])
        return approved_words.approved_digest(approved_words.parse_sidecar(fx.sidecar_text(SLUG, [BLOCK])))

    def test_check_digest_overrides_the_working_tree_digest(self, repo, monkeypatch, capsys):
        root, main_sha, _ = repo
        working = self._sidecar(root)
        monkeypatch.setattr(prov, "GRANDFATHER_REF", main_sha)
        double = Api([approval_run(SLUG, DIGEST, main_sha)])
        monkeypatch.setattr(gh, "http_get", double)
        assert gh.main(["check", "--slug", SLUG, "--root", str(root), "--digest", DIGEST]) == 0
        out = capsys.readouterr().out
        assert "lookup: APPROVED run 9100 approver operator-fixture" in out and "grandfather: NOT GRANDFATHERED" in out
        assert working != DIGEST
        assert gh.main(["check", "--slug", SLUG, "--root", str(root)]) == 1
        assert "lookup: MISSING" in capsys.readouterr().out

    def test_approvals_prints_each_entry(self, monkeypatch, capsys):
        monkeypatch.setattr(gh, "http_get", Api([], approvals={7: approvals_for()}))
        assert gh.main(["approvals", "--run-id", "7", "--repo", "a/b"]) == 0
        assert capsys.readouterr().out.strip() == "state=approved environment=approved-text login=operator-fixture"

    def test_wake_comments_on_the_pull_requests_whose_head_is_the_sha(self, monkeypatch, capsys):
        posts: list[tuple] = []
        monkeypatch.setattr(gh, "http_get", Api([]))
        monkeypatch.setattr(gh, "http_post", lambda url, tok, body, timeout: posts.append((url, tok, body)))
        monkeypatch.setenv("APPROVED_TEXT_GH_TOKEN", "t")
        argv = ["wake", "--slug", SLUG, "--sha", "b" * 40, "--digest", DIGEST, "--run-id", "55"]
        assert gh.main(argv) == 0
        assert capsys.readouterr().out.strip() == f"wake: 1 open PRs at {'b' * 40} auth=token"
        ((url, tok, body),) = posts
        assert url.endswith("/repos/fixture-owner/fixture-repo/issues/100/comments") and tok == "t"
        text = body["body"]
        assert text.startswith(f"<!-- approved-text-wake:{DIGEST} -->")
        assert (
            SLUG in text and approved_words.fingerprint(DIGEST) in text and "run 55" in text and "re-run pr-validate" in text
        )

    def test_wake_with_no_matching_pull_request_posts_nothing(self, monkeypatch, capsys):
        posts: list[tuple] = []
        monkeypatch.setattr(gh, "http_get", Api([]))
        monkeypatch.setattr(gh, "http_post", lambda *a: posts.append(a))
        argv = ["wake", "--slug", SLUG, "--sha", "e" * 40, "--digest", DIGEST, "--run-id", "55"]
        assert gh.main(argv) == 0 and posts == []
        assert capsys.readouterr().out.strip() == f"wake: 0 open PRs at {'e' * 40} auth=anonymous"

    def test_wake_reports_a_failed_read_with_a_non_zero_exit(self, monkeypatch, capsys):
        monkeypatch.setattr(gh, "http_get", Api([], fail=gh.GitHubError("HTTP 500 from x")))
        argv = ["wake", "--slug", SLUG, "--sha", "b" * 40, "--digest", DIGEST, "--run-id", "55"]
        assert gh.main(argv) == 1
        assert capsys.readouterr().out.strip() == "wake: ERROR HTTP 500 from x"
