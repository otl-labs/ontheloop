"""scripts/ci/last_green.py: last-green ref, covered, full_tier_state, resolve_prev (Decision 254 cl.2)."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci import deploy_gate as dg
from scripts.ci import last_green as lg

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def gate_repo(repo: Repo) -> list[str]:
    """c1 (pre-gate), c2 adds the gate module, c3 watched, c4 unwatched, c5 unwatched."""
    c1 = repo.commit("src/w/a.py")
    c2 = repo.commit(dg.GATE_MODULE)
    c3 = repo.commit("src/w/a.py")
    c4 = repo.commit()
    c5 = repo.commit()
    return [c1, c2, c3, c4, c5]


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
        step: str = lg.FULL_TIER_STEP,
        attempt: int = 1,
        earlier: tuple[str, ...] = (),
    ) -> int:
        self._id += 1
        run = {"id": self._id, "head_sha": sha, "conclusion": conclusion, "run_attempt": attempt}
        self.runs.setdefault((sha, event), []).append(run)
        self.latest[self._id] = self._job(step_conclusion, status, job_conclusion, step)
        for n, earlier_step in enumerate(earlier, start=1):
            self.attempts[(self._id, n)] = self._job(earlier_step, "completed", "failure", lg.FULL_TIER_STEP)
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


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "r")


def test_last_green_counts_dispatch_and_any_attempt(repo: Repo) -> None:
    c = gate_repo(repo)
    api = Api()
    api.add(c[4], step_conclusion="failure", conclusion="failure")
    api.add(c[3], event="workflow_dispatch")
    assert lg.last_green_sha(api, repo.git, ref="HEAD") == c[3]
    api2 = Api()
    api2.add(c[4], step_conclusion="failure", conclusion="failure", attempt=2, earlier=("success",))
    assert lg.last_green_sha(api2, repo.git, ref="HEAD") == c[4]


def test_newest_green_sha_unchanged_for_dispatch_and_attempts(repo: Repo) -> None:
    c = gate_repo(repo)
    api = Api()
    assert lg.newest_green_sha is dg.newest_green_sha
    api.add(c[4], event="workflow_dispatch")

    class PushOnly:
        def get(self, path: str) -> dict[str, Any]:
            assert "event=push" in path or "/jobs" in path
            return api.get(path)

    assert dg.newest_green_sha(PushOnly(), repo.git, ref="HEAD") is None


def test_covered_is_green_and_its_ancestors(repo: Repo) -> None:
    c = gate_repo(repo)
    api = Api()
    api.add(c[2])
    assert lg.covered(api, repo.git, c[2], ref="HEAD")
    assert lg.covered(api, repo.git, c[1], ref="HEAD")
    assert not lg.covered(api, repo.git, c[3], ref="HEAD")


def test_full_tier_state_started_skipped_absent() -> None:
    api = Api()
    api.add("a1", step_conclusion="success")
    api.add("a2", step_conclusion="skipped", status="completed")
    api.add("a3", step_conclusion="", status="in_progress", job_conclusion="")
    api.add("a4", step_conclusion="", status="pending", job_conclusion="")
    api.add("a5", step_conclusion="cancelled", job_conclusion="timed_out")
    api.add("a6", step_conclusion="success", event="workflow_dispatch")
    states = {s: lg.full_tier_state(api, s) for s in ["a1", "a2", "a3", "a4", "a5", "a6", "a7"]}
    assert states == {
        "a1": "started",
        "a2": "skipped",
        "a3": "started",
        "a4": "absent",
        "a5": "absent",
        "a6": "started",
        "a7": "absent",
    }


def test_resolve_prev_passes_over_skipped_and_timed_out(repo: Repo) -> None:
    c = gate_repo(repo)
    api = Api()
    api.add(c[4], step_conclusion="skipped")
    api.add(c[3], step_conclusion="cancelled", job_conclusion="timed_out")
    api.add(c[2])
    assert lg.resolve_prev(api, repo.git, c[4]) == c[2]
    assert lg.resolve_prev(api, repo.git, c[4], limit=1) is None


def test_module_imports_only_stdlib_and_deploy_gate() -> None:
    tree = ast.parse((REPO_ROOT / "scripts/ci/last_green.py").read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module if node.module.startswith("scripts") else node.module.split(".")[0])
    stdlib = {"__future__", "urllib", "typing"}
    assert mods <= stdlib | {"scripts.ci.deploy_gate"}, mods


def test_step_names_match_ci_yml() -> None:
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = {s.get("name") for s in wf["jobs"][lg.FULL_TIER_JOB]["steps"]}
    assert lg.FULL_TIER_STEP in steps and lg.FAST_TIER_STEP in steps
