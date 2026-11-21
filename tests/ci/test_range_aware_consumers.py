"""rec-5106 acceptance and end-to-end cases for the range-aware trailer gate (PLAN-trailer-range-closure).

Drives the real cmd_census and cmd_close over a temp git repo (a first-parent chain of single-parent, squash-shaped commits
with Resolves trailers) and a fake GitHub API and reader; scripts.ops_data_portal.update_rec is patched to record calls.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

import scripts.ops_portal.ci_rca_lifecycle as lifecycle
import scripts.rec_trailer_acceptance as rta
from scripts.ci import trailer_range as tr
from scripts.ci.deploy_gate import FULL_TIER_JOB, FULL_TIER_STEP, GateError
from scripts.rec_trailer_acceptance_junit import junit_verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
_NODE_FILES = {
    0: {
        "tests/test_a.py": "def test_a():\n    pass\n",
        "tests/test_b.py": "def test_b():\n    pass\n",
    }
}
_ROWS = {
    "rec-11": {"id": "rec-11", "status": "open", "acceptance": "pytest tests/test_a.py::test_a"},
    "rec-12": {"id": "rec-12", "status": "open", "acceptance": "pytest tests/test_b.py::test_b"},
    "rec-13": {"id": "rec-13", "status": "closed", "acceptance": "pytest tests/test_a.py::test_a"},
}


_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True, env={**_ENV, "PATH": "/usr/bin:/bin"}
    )
    return result.stdout.strip()


def make_chain(root: Path, messages: list[str], files: dict[int, dict[str, str]] | None = None) -> list[str]:
    """Single-parent (squash-shaped) commits, oldest first; commit i also writes files[i]; origin/main points at the tip."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    shas = []
    for i, message in enumerate(messages):
        for rel, text in {f"src/f{i}.py": f"x = {i}\n", **(files or {}).get(i, {})}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", message)
        shas.append(git(root, "rev-parse", "HEAD"))
    git(root, "update-ref", "refs/remotes/origin/main", shas[-1])
    return shas


class FakeApi:
    """states: sha -> started | skipped | absent | error (a GateError on read)."""

    def __init__(self, states: dict[str, str]) -> None:
        self.states = states
        self.calls: list[str] = []

    def get(self, path: str) -> Any:
        self.calls.append(path)
        if path.startswith("commits/"):
            return []
        if path.startswith("actions/workflows/ci.yml/runs"):
            sha = path.split("head_sha=")[1].split("&")[0]
            state = self.states.get(sha, "absent")
            if state == "error":
                raise GateError("fake api down")
            if "event=push" in path and state != "absent":
                return {"workflow_runs": [{"id": int(sha[:6], 16), "head_sha": sha, "run_attempt": 1}]}
            return {"workflow_runs": []}
        run_id = int(path.split("/")[2])
        sha = next(s for s in self.states if int(s[:6], 16) == run_id)
        step = {
            "name": FULL_TIER_STEP,
            "status": "completed",
            "conclusion": "skipped" if self.states[sha] == "skipped" else "success",
        }
        return {"jobs": [{"name": FULL_TIER_JOB, "steps": [step]}]}


class FakeReader:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def named(self, verb: str, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append((verb, kw))
        ids = kw["ids"].split(",") if "ids" in kw else [kw["id"]]
        return [self.rows[i] for i in ids if i in self.rows]


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, states: list[str], trailers: list[str], rows: dict[str, Any] | None = None
) -> tuple[list[str], FakeApi]:
    repo = tmp_path / "repo"
    messages = [f"c{i}\n\n{t}" if t else f"c{i}" for i, t in enumerate(trailers)]
    shas = make_chain(repo, messages, _NODE_FILES)
    api = FakeApi(dict(zip(shas, states, strict=True)))
    reader = FakeReader(_ROWS if rows is None else rows)
    monkeypatch.setattr(rta, "ROOT", repo)
    monkeypatch.setattr(lifecycle, "ROOT", repo)
    monkeypatch.setattr(rta, "GithubApi", lambda: api)
    monkeypatch.setattr(rta, "_make_reader", lambda profile=None: reader)
    monkeypatch.setenv("GITHUB_SHA", shas[-1])
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "9")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return shas, api


def _census(tmp_path: Path) -> dict[str, Any]:
    art = tmp_path / "art"
    assert rta.cmd_census(argparse.Namespace(artifact_dir=art)) == 0
    return rta._read_json(art / rta._CENSUS_ARTIFACT)


def _close(tmp_path: Path, census_doc: dict[str, Any], head: str, passed: tuple[str, ...]) -> list[tuple[str, str, dict]]:
    art = tmp_path / "art"
    report = tmp_path / "junit.xml"
    cases = "".join(f'<testcase classname="tests.test_{n}" name="test_{n}"/>' for n in passed)
    report.write_text(f"<?xml version='1.0'?><testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    rta._write_json(art / rta._VERDICT_ARTIFACT, {"records": []})
    rta._write_json(art / "junit_verdict.json", junit_verdict(census_doc, report, head))
    calls: list[tuple[str, str, dict]] = []

    def fake_update(rec_id: str, updates: dict, profile: Any = None, **kw: Any) -> bool:
        calls.append((rec_id, kw.get("closing_sha"), kw.get("acceptance_verdict")))
        return True

    with patch("scripts.ops_data_portal.update_rec", fake_update):
        assert rta.cmd_close(argparse.Namespace(artifact_dir=art, junit_verdict=art / "junit_verdict.json")) == 0
    return calls


def test_three_merge_batch_files_one_rec_and_closes_two_junit_recs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One range means one ci-rca diagnosis and one rec (inferred: compute_range is ci-rca's only range source)."""
    from scripts.ci_rca import range_context

    shas, api = _setup(
        tmp_path,
        monkeypatch,
        ["started", "skipped", "skipped", "started"],
        ["", "Resolves: rec-11", "Resolves: rec-12", ""],
    )
    rng = range_context.compute_range(api, tr.Git(str(tmp_path / "repo")), shas[3], "ci")
    assert rng["commits"] == shas[1:] and len(rng["prs"]) == 3
    doc = _census(tmp_path)
    assert {e["rec_id"]: e["closing_sha"] for e in doc["entries"]} == {"rec-11": shas[1], "rec-12": shas[2]}
    calls = _close(tmp_path, doc, shas[3], ("a", "b"))
    assert sorted((r, sha) for r, sha, _ in calls) == [("rec-11", shas[1]), ("rec-12", shas[2])]
    assert all(v["sha"] == sha and v["verdict"] == "holds" for _, sha, v in calls)


def test_parent_ran_full_tier_is_todays_census_and_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shas, _ = _setup(tmp_path, monkeypatch, ["started", "started"], ["", "Resolves: rec-11"])
    doc = _census(tmp_path)
    assert [(e["rec_id"], e["closing_sha"]) for e in doc["entries"]] == [("rec-11", shas[1])]
    assert doc["ids"] == ["rec-11"] and doc["covers"] is True and doc["prev"] == shas[0]
    calls = _close(tmp_path, doc, shas[1], ("a",))
    assert [(r, sha) for r, sha, _ in calls] == [("rec-11", shas[1])]


def test_run_without_full_tier_defers_junit_and_walks_no_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    rows = {**_ROWS, "rec-14": {"id": "rec-14", "status": "open", "acceptance": "grep -c 'x = 1' src/f1.py"}}
    shas, api = _setup(tmp_path, monkeypatch, ["started", "skipped"], ["", "Resolves: rec-11, rec-14"], rows)
    doc = _census(tmp_path)
    assert doc["covers"] is False and doc["range_status"] == "not_walked"
    assert not any(c.startswith("commits/") for c in api.calls)
    calls = _close(tmp_path, doc, shas[1], ("a",))
    assert "rec-11" not in {r for r, _, _ in calls}
    assert f"{tr.DEFERRED_MARKER}: rec-11 left OPEN" in capsys.readouterr().out


def test_unreadable_state_marks_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    shas, _ = _setup(tmp_path, monkeypatch, ["started", "error"], ["", "Resolves: rec-11"])
    doc = _census(tmp_path)
    assert doc["covers"] is None
    assert _close(tmp_path, doc, shas[1], ("a",)) == []
    assert f"{tr.UNREADABLE_MARKER}: rec-11 left OPEN" in capsys.readouterr().out


def test_closed_rec_in_range_is_not_censused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _ = _setup(tmp_path, monkeypatch, ["started", "skipped", "started"], ["", "Resolves: rec-13", ""])
    assert _census(tmp_path)["ids"] == []


def test_widened_range_skips_merges_that_ran_their_own_full_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shas, _ = _setup(
        tmp_path, monkeypatch, ["started", "started", "skipped", "started"], ["", "Resolves: rec-11", "Resolves: rec-12", ""]
    )
    widened = {"head": shas[3], "prev": shas[0], "range_status": "widened", "commits": shas[1:], "prs": []}
    monkeypatch.setattr(tr.range_context, "compute_range", lambda *a, **k: widened)
    doc = _census(tmp_path)
    assert doc["range_status"] == "widened" and [e["rec_id"] for e in doc["entries"]] == ["rec-12"]


def test_unresolved_range_censuses_head_alone_with_error_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    shas, _ = _setup(tmp_path, monkeypatch, ["skipped", "skipped", "started"], ["", "Resolves: rec-11", "Resolves: rec-12"])
    doc = _census(tmp_path)
    assert doc["range_status"] == "unresolved" and doc["ids"] == ["rec-12"]
    assert [e["closing_sha"] for e in doc["entries"]] == [shas[2]]
    assert "::error title=trailer-range::" in capsys.readouterr().out


def _jobs() -> dict[str, Any]:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]


def test_ci_yml_trailer_jobs_shape() -> None:
    jobs = _jobs()
    census = jobs["trailer-census"]
    assert census["needs"] == ["main-validate"]
    assert census["if"] == "always() && github.event_name == 'push'"
    assert census["permissions"]["actions"] == "read"
    assert next(s for s in census["steps"] if "checkout" in str(s.get("uses")))["with"]["fetch-depth"] == 0
    step = next(s for s in census["steps"] if s.get("name", "").startswith("Run trailer census"))
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    evaluate = jobs["trailer-acceptance-evaluate"]
    assert evaluate["if"] == "always() && github.event_name == 'push' && needs.trailer-census.result == 'success'"
    assert evaluate["permissions"] == {"contents": "read"}
    closure_checkout = next(s for s in jobs["trailer-closure"]["steps"] if "checkout" in str(s.get("uses")))
    assert closure_checkout["with"]["fetch-depth"] == 0
    assert jobs["trailer-junit-verdict"]["permissions"] == {"contents": "read"}


def test_static_recs_close_when_main_validate_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure before the full-tier step reads covers False (a red full tier reads started, so covers True)."""
    census_doc = {
        "ids": ["rec-11", "rec-14"],
        "sha": "head",
        "covers": False,
        "entries": [
            {"rec_id": "rec-11", "route": "junit", "closing_sha": "head", "acceptance_sha256": "a"},
            {"rec_id": "rec-14", "route": "static", "closing_sha": "head", "acceptance_sha256": "b"},
        ],
    }
    art = tmp_path / "art"
    rta._write_json(art / rta._CENSUS_ARTIFACT, census_doc)
    rta._write_json(
        art / rta._VERDICT_ARTIFACT,
        {"records": [{"rec_id": "rec-14", "acceptance_sha256": "b", "verdict": "holds", "source": "static"}]},
    )
    monkeypatch.setenv("GITHUB_SHA", "head")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    reader = FakeReader({"rec-11": {"id": "rec-11"}, "rec-14": {"id": "rec-14"}})
    monkeypatch.setattr(rta, "_make_reader", lambda profile=None: reader)
    seen: list[tuple[list[str], str]] = []
    with patch.object(lifecycle, "close_recs_from_trailer", lambda ids, sha, *a, **k: seen.append((ids, sha)) or 0):
        assert rta.cmd_close(argparse.Namespace(artifact_dir=art, junit_verdict=None)) == 0
    assert seen == [(["rec-14"], "head")]
