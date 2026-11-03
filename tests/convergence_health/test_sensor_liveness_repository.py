"""Both sensor_liveness entry points query the repository resolved from GITHUB_REPOSITORY (rec-3903).

A sibling of test_sensor_liveness.py, which sits at its SLOC budget."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from scripts.convergence_health import sensor_liveness as sl

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
LOOP = "loop.yml"
PEERS = {LOOP: ["0 6 * * 1"]}


def _capturing_caller(captured: list[str]) -> Callable[[str], Any]:
    def caller(url: str) -> Any:
        captured.append(url)
        if url.endswith("/workflows?per_page=100"):
            return {"workflows": [{"path": f".github/workflows/{LOOP}", "state": "active"}]}
        return {"workflow_runs": [{"id": 1, "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "conclusion": "success"}]}

    return caller


def _git_runner(cmd: list[str]) -> str:
    return "false" if cmd[:2] == ["git", "rev-parse"] else "cafef00d 0\n"


def _detect(caller: Callable[[str], Any]) -> None:
    sl.detect_stale_loops(
        peers=PEERS, now=NOW, gh_caller=caller, git_runner=_git_runner, open_recs=[], resolved_recs=[], dry_run=True
    )


def _probe(caller: Callable[[str], Any]) -> None:
    sl.stale_peers_for_probe(peers=PEERS, now=NOW, gh_caller=caller, git_runner=_git_runner)


@pytest.mark.parametrize("entry_point", [_detect, _probe])
def test_every_liveness_query_follows_github_repository(
    monkeypatch: pytest.MonkeyPatch, entry_point: Callable[[Callable[[str], Any]], None]
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    captured: list[str] = []
    entry_point(_capturing_caller(captured))
    assert captured
    assert all("repos/example-org/example-repo/" in url for url in captured)
