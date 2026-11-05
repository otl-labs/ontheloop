"""Recorded-API double for the approved-text adapter tests (Decision 212 cl.2: no credential, no socket).

The JSON files beside this module were recorded from real GitHub reads with ids and logins made synthetic.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from scripts.roadmap import approved_text_github as adapter

HERE = Path(__file__).parent


def load(name: str) -> Any:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def approval_run(slug: str, approved: str, head_sha: str, run_id: int = 9100, **overrides: Any) -> dict[str, Any]:
    run = copy.deepcopy(load("runs.json")["workflow_runs"][0])
    run.update(
        id=run_id,
        path=adapter.WORKFLOW_PATH,
        name="approved-text-approval",
        display_title=adapter.run_title(slug, approved),
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
