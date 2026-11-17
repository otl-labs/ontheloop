"""scripts/ci_rca/range_context.py: resolve, probe and observe (Decision 254 cl.4)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import deploy_gate as dg
from scripts.ci_rca import range_context as rc


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git = dg.Git(str(path))
        self.git.out("init", "-q", "-b", "main")
        self.git.out("config", "user.email", "t@example.com")
        self.git.out("config", "user.name", "t")
        self.git.out("config", "commit.gpgsign", "false")
        self.n = 0

    def commit(self, *files: str) -> str:
        self.n += 1
        for rel in files or (f"misc/{self.n}.txt",):
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{self.n}\n", encoding="utf-8")
        self.git.out("add", "-A")
        self.git.out("commit", "-q", "-m", f"c{self.n}")
        return self.git.out("rev-parse", "HEAD")


class Api:
    """Fake ci.yml runs/jobs API keyed by sha; supports workflow_dispatch and per-attempt jobs."""

    def __init__(self) -> None:
        self.runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.latest: dict[int, list[dict[str, Any]]] = {}
        self.attempts: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.fail = False
        self._id = 500

    @staticmethod
    def _job(step_conclusion: str, status: str, job_conclusion: str, step: str) -> list[dict[str, Any]]:
        steps = [{"name": step, "conclusion": step_conclusion, "status": status}] if step else []
        return [{"name": dg.FULL_TIER_JOB, "conclusion": job_conclusion, "steps": steps}]

    def add(
        self,
        sha: str,
        *,
        event: str = "push",
        conclusion: str = "success",
        step_conclusion: str = "success",
        status: str = "completed",
        job_conclusion: str = "success",
        step: str = rc.lg.FULL_TIER_STEP,
        attempt: int = 1,
        earlier: tuple[str, ...] = (),
    ) -> int:
        self._id += 1
        run = {"id": self._id, "head_sha": sha, "conclusion": conclusion, "run_attempt": attempt}
        self.runs.setdefault((sha, event), []).append(run)
        self.latest[self._id] = self._job(step_conclusion, status, job_conclusion, step)
        for n, earlier_step in enumerate(earlier, start=1):
            self.attempts[(self._id, n)] = self._job(earlier_step, "completed", "failure", rc.lg.FULL_TIER_STEP)
        return self._id

    def get(self, path: str) -> dict[str, Any]:
        if self.fail:
            raise dg.GateError("boom")
        if "/attempts/" in path:
            parts = path.split("?")[0].split("/")
            return {"jobs": self.attempts[(int(parts[2]), int(parts[4]))]}
        if "/jobs" in path:
            return {"jobs": self.latest[int(path.split("/")[2])]}
        query = path.split("?")[1]
        sha = re.search(r"head_sha=(\w+)", query).group(1)  # type: ignore[union-attr]
        event = re.search(r"event=(\w+)", query).group(1)  # type: ignore[union-attr]
        return {"workflow_runs": self.runs.get((sha, event), [])}


class RangeApi(Api):
    def __init__(self) -> None:
        super().__init__()
        self.pulls: dict[str, list[dict[str, Any]]] = {}
        self.rca_runs: list[dict[str, Any]] = []
        self.rca_jobs: dict[int, list[dict[str, Any]]] = {}
        self.annotations: dict[int, list[dict[str, Any]]] = {}
        self.pages: list[str] = []

    def get(self, path: str) -> Any:  # type: ignore[override]
        if path.startswith("commits/"):
            if self.fail:
                raise dg.GateError("boom")
            return self.pulls.get(path.split("/")[1], [])
        if path.startswith("actions/workflows/ci-rca.yml"):
            self.pages.append(path)
            return {"workflow_runs": self.rca_runs}
        if path.startswith("check-runs/"):
            return self.annotations.get(int(path.split("/")[1]), [])
        m = re.match(r"actions/runs/(\d+)/jobs", path)
        if m and int(m.group(1)) in self.rca_jobs:
            return {"jobs": self.rca_jobs[int(m.group(1))]}
        return super().get(path)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "r")


def chain(repo: Repo, n: int = 5) -> list[str]:
    return [repo.commit() for _ in range(n)]


def compute(api: RangeApi, repo: Repo, head: str, slug: str = "ci", recorded: str | None = None) -> dict[str, Any]:
    return rc.compute_range(api, repo.git, head, slug, recorded)


def test_full_tier_parent_gives_head_only_range(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3])
    out = compute(api, repo, c[4])
    assert out["prev"] == c[3] and out["commits"] == [c[4]] and out["range_status"] == "resolved"


def test_plans_only_parent_widens_range(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3], step_conclusion="skipped")
    api.add(c[2])
    out = compute(api, repo, c[4])
    assert out["prev"] == c[2] and out["commits"] == [c[3], c[4]]


def test_timed_out_parent_passed_over(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3], step_conclusion="cancelled", job_conclusion="timed_out")
    api.add(c[2])
    assert compute(api, repo, c[4])["prev"] == c[2]


def test_in_progress_parent_counts_and_queued_is_passed_over(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3], status="in_progress", step_conclusion="", job_conclusion="")
    assert compute(api, repo, c[4])["prev"] == c[3]
    api2 = RangeApi()
    api2.add(c[3], status="pending", step_conclusion="", job_conclusion="")
    api2.add(c[2])
    assert compute(api2, repo, c[4])["prev"] == c[2]


def test_recorded_range_wins(repo: Repo, tmp_path: Path) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3])
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"prev": c[1]}), encoding="utf-8")
    out = compute(api, repo, c[4], recorded=str(rec))
    assert out["range_status"] == "recorded" and out["prev"] == c[1] and out["commits"] == c[2:]


def test_non_ancestor_prev_widens_to_last_green(repo: Repo, tmp_path: Path) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[2])
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"prev": "f" * 40}), encoding="utf-8")
    out = compute(api, repo, c[4], recorded=str(rec))
    assert out["range_status"] == "widened" and out["prev"] == c[2]


def test_nothing_resolves_is_unresolved_head_alone(repo: Repo) -> None:
    c = chain(repo)
    out = compute(RangeApi(), repo, c[4])
    assert out["range_status"] == "unresolved" and out["commits"] == [c[4]] and out["prev"] is None


def test_non_ci_slug_returns_head_only(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = chain(repo)
    out = compute(RangeApi(), repo, c[4], slug="main_canary")
    assert out["range_status"] == "head_only" and out["commits"] == [c[4]] and out["prev"] is None
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"head_sha": c[4]}), encoding="utf-8")
    args = rc.argparse.Namespace(
        run_json=str(run), workflow_slug="main_canary", repo="o/r", out=str(tmp_path / "r.json"), recorded_range=None
    )
    rc.cmd_resolve(args, api=RangeApi(), git=repo.git)
    text = outputs.read_text(encoding="utf-8")
    assert "diff_base=HEAD^" in text and "prompt_note=\n" in text


def test_api_error_returns_unresolved(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.fail = True
    out = compute(api, repo, c[4])
    assert out["range_status"] == "unresolved" and out["commits"] == [c[4]]


def test_one_pr_per_commit(repo: Repo) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[2])
    api.add(c[3], step_conclusion="skipped")
    api.pulls[c[3]] = [{"number": 7}]
    out = compute(api, repo, c[4])
    assert out["prs"] == [{"sha": c[3], "pr": 7}, {"sha": c[4], "pr": None}]


def test_resolve_writes_outputs_and_notice_to_stdout_only(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3])
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"head_sha": c[4]}), encoding="utf-8")
    args = rc.argparse.Namespace(
        run_json=str(run), workflow_slug="ci", repo="o/r", out=str(tmp_path / "r.json"), recorded_range=None
    )
    assert rc.cmd_resolve(args, api=api, git=repo.git) == 0
    text = outputs.read_text(encoding="utf-8")
    assert f"diff_base={c[3]}" in text and "range_status=resolved" in text and "prompt_note=" in text
    assert "::notice" not in text
    stdout = capsys.readouterr().out
    assert stdout.strip().splitlines()[-1] == f"::notice title=ci-rca-range::range_status=resolved diff_base={c[3]}"
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["commits"] == [c[4]]


def test_unresolved_ci_range_has_empty_diff_base(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = chain(repo)
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"head_sha": c[4]}), encoding="utf-8")
    args = rc.argparse.Namespace(
        run_json=str(run), workflow_slug="ci", repo="o/r", out=str(tmp_path / "r.json"), recorded_range=None
    )
    rc.cmd_resolve(args, api=RangeApi(), git=repo.git)
    assert "diff_base=\n" in outputs.read_text(encoding="utf-8")


def probe(api: RangeApi, repo: Repo, depth: int = 6) -> tuple[int, str]:
    import argparse
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rc.cmd_probe(argparse.Namespace(ref="HEAD", depth=depth), api=api, git=repo.git)
    return code, buf.getvalue().strip()


def probe_chain(repo: Repo) -> list[str]:
    """c0 code, c1 plans-only, c2 code, c3 code."""
    c0 = repo.commit("src/a.py")
    c1 = repo.commit("docs/plans/PLAN-x.yaml")
    c2 = repo.commit("src/b.py")
    c3 = repo.commit("src/c.py")
    return [c0, c1, c2, c3]


def fast_add(api: RangeApi, sha: str) -> None:
    api.add(sha, step_conclusion="skipped")
    jobs = api.latest[api._id]
    jobs[0]["steps"].append({"name": rc.lg.FAST_TIER_STEP, "conclusion": "success", "status": "completed"})


def test_probe_ok_over_chain_with_skipped_plans_only(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    api.add(c[2])
    fast_add(api, c[1])
    api.add(c[0])
    assert probe(api, repo) == (0, "probe=ok skipped=1 started=2")


def test_probe_fails_when_oracle_disagrees_on_skipped(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    fast_add(api, c[2])
    api.add(c[0])
    code, out = probe(api, repo)
    assert code == 1 and out.startswith("probe=fail")


def test_probe_fails_when_oracle_disagrees_on_started(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    api.add(c[1])
    fast_add(api, c[2])
    assert probe(api, repo)[0] == 1


def test_probe_fails_when_k_or_s_zero(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    api.add(c[2])
    assert probe(api, repo)[0] == 1
    api2 = RangeApi()
    fast_add(api2, c[1])
    assert probe(api2, repo)[0] == 1


def test_probe_fails_when_resolve_prev_stops_on_skipped(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    fast_add(api, c[2])
    api.add(c[1], step_conclusion="skipped")
    api.add(c[0])
    code, out = probe(api, repo)
    assert code == 1


def test_probe_excludes_skipped_commit_whose_job_failed_before_fast_tier(repo: Repo) -> None:
    c = probe_chain(repo)
    api = RangeApi()
    api.add(c[3])
    api.add(c[2])
    api.add(c[1], step_conclusion="skipped")
    api.add(c[0])
    assert probe(api, repo) == (1, "probe=fail window held skipped=0 started=2; widen --depth")


def observe(api: RangeApi, repo: Repo) -> tuple[int, str]:
    import argparse
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rc.cmd_observe(argparse.Namespace(merge_sha="HEAD"), api=api, git=repo.git)
    return code, buf.getvalue().strip()


def add_rca(api: RangeApi, run_id: int, created: str, step_conclusion: str, note: bool, conclusion: str = "success") -> None:
    api.rca_runs.append({"id": run_id, "created_at": created, "conclusion": conclusion})
    api.rca_jobs[run_id] = [
        {"id": run_id * 10, "name": "rca", "steps": [{"name": rc.RANGE_STEP, "conclusion": step_conclusion}]}
    ]
    if note:
        api.annotations[run_id * 10] = [{"title": "ci-rca-range", "message": "range_status=resolved diff_base=abc"}]


def test_observe_takes_oldest_with_range_step_and_reads_notice(repo: Repo) -> None:
    chain(repo, 1)
    api = RangeApi()
    add_rca(api, 3, "2026-01-03T00:00:00Z", "success", True)
    add_rca(api, 1, "2026-01-01T00:00:00Z", "skipped", False)
    add_rca(api, 2, "2026-01-02T00:00:00Z", "success", True, conclusion="skipped")
    assert observe(api, repo) == (0, "observe=3 slug=ci resolve=completed range_status=resolved")


def test_observe_incomplete_when_step_success_but_notice_absent(repo: Repo) -> None:
    chain(repo, 1)
    api = RangeApi()
    add_rca(api, 5, "2026-01-01T00:00:00Z", "success", False)
    assert observe(api, repo) == (1, "observe=5 slug=ci resolve=incomplete")


def test_observe_none_yet_on_empty_page(repo: Repo) -> None:
    chain(repo, 1)
    assert observe(RangeApi(), repo) == (0, "observe=none-yet")


def test_recorded_prev_unreadable_or_malformed_is_ignored(repo: Repo, tmp_path: Path) -> None:
    c = chain(repo)
    api = RangeApi()
    api.add(c[3])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert compute(api, repo, c[4], recorded=str(bad))["range_status"] == "resolved"
    bad.write_text("[1]", encoding="utf-8")
    assert compute(api, repo, c[4], recorded=str(bad))["range_status"] == "resolved"


def test_prev_equal_to_head_is_not_a_strict_ancestor(repo: Repo, tmp_path: Path) -> None:
    c = chain(repo)
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"prev": c[4]}), encoding="utf-8")
    assert compute(RangeApi(), repo, c[4], recorded=str(rec))["range_status"] == "unresolved"


def test_main_dispatches_probe_and_observe(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chain(repo, 1)
    monkeypatch.setattr(rc, "GithubApi", lambda *a, **k: RangeApi())
    monkeypatch.setattr(rc, "Git", lambda *a, **k: repo.git)
    assert rc.main(["observe", "--merge-sha", "HEAD"]) == 0
    assert rc.main(["probe", "--ref", "HEAD", "--depth", "3"]) == 1
    out = capsys.readouterr().out
    assert "observe=none-yet" in out and "probe=fail" in out


def test_probe_and_observe_report_api_errors(repo: Repo) -> None:
    import argparse

    chain(repo, 2)
    api = RangeApi()
    api.fail = True
    assert rc.cmd_probe(argparse.Namespace(ref="HEAD", depth=3), api=api, git=repo.git) == 1

    class Boom(RangeApi):
        def get(self, path: str):  # type: ignore[no-untyped-def,override]
            raise dg.GateError("boom")

    assert rc.cmd_observe(argparse.Namespace(merge_sha="HEAD"), api=Boom(), git=repo.git) == 1


def test_observe_pages_until_a_short_page(repo: Repo) -> None:
    import argparse

    chain(repo, 1)

    class Paged(RangeApi):
        def get(self, path: str):  # type: ignore[no-untyped-def,override]
            if path.startswith("actions/workflows/ci-rca.yml"):
                self.pages.append(path)
                return {
                    "workflow_runs": [{"id": 1, "created_at": "x", "conclusion": "skipped"}]
                    * (100 if len(self.pages) == 1 else 0)
                }
            return super().get(path)

    api = Paged()
    assert rc.cmd_observe(argparse.Namespace(merge_sha="HEAD"), api=api, git=repo.git) == 0
    assert len(api.pages) == 2
