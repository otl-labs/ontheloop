"""Unit tests for scripts/ci/trailer_range.py (PLAN-trailer-range-closure) over a real temp git chain and fake GitHub reads."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import trailer_range as tr
from scripts.ci.deploy_gate import FULL_TIER_JOB, FULL_TIER_STEP, GateError
from scripts.rec_trailer import parse_resolves_trailer

_JUNIT = {"route": "junit"}


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


def _chain(tmp_path: Path, states: list[str], trailers: list[str]) -> tuple[list[str], FakeApi]:
    shas = make_chain(tmp_path, [f"c{i}\n\n{t}" if t else f"c{i}" for i, t in enumerate(trailers)])
    return shas, FakeApi(dict(zip(shas, states)))


def _census_fn(ids: list[str], rows: list[dict[str, Any]], closing_sha: str) -> list[dict[str, Any]]:
    return [
        {"rec_id": r["id"], "route": "static" if r["id"] == "rec-14" else "junit", "closing_sha": closing_sha} for r in rows
    ]


def _extend(
    shas: list[str], api: FakeApi, tmp_path: Path, rows: dict[str, dict[str, Any]], ids: list[str] | None = None
) -> dict:
    return tr.extend_census(
        shas[-1],
        ids or [],
        [],
        census_fn=lambda i, rows, closing_sha: _census_fn(i, rows, closing_sha),
        parse_trailer=parse_resolves_trailer,
        reader=FakeReader(rows),
        api=api,
        git=tr.Git(str(tmp_path)),
    )


def test_run_covers_true_false_none(tmp_path: Path) -> None:
    shas, api = _chain(tmp_path, ["started", "skipped", "error"], ["", "", ""])
    assert [tr.run_covers(api, s) for s in shas] == [True, False, None]


def test_range_merges_newest_first_and_drops_trailerless(tmp_path: Path) -> None:
    shas, api = _chain(
        tmp_path, ["started", "skipped", "skipped", "skipped", "started"], ["", "Resolves: rec-11", "", "Resolves: rec-12", ""]
    )
    found = tr.range_merges(api, tr.Git(str(tmp_path)), shas[-1], parse_trailer=parse_resolves_trailer)
    assert found["range_status"] == "resolved" and found["prev"] == shas[0]
    assert found["merges"] == [{"sha": shas[3], "ids": ["rec-12"]}, {"sha": shas[1], "ids": ["rec-11"]}]


def test_range_merges_widened_skips_merges_that_ran_their_own_full_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shas, api = _chain(
        tmp_path, ["started", "started", "skipped", "skipped"], ["", "Resolves: rec-11", "Resolves: rec-12", ""]
    )
    widened = {"head": shas[-1], "prev": shas[0], "range_status": "widened", "commits": shas[1:], "prs": []}
    monkeypatch.setattr(tr.range_context, "compute_range", lambda *a, **k: widened)
    found = tr.range_merges(api, tr.Git(str(tmp_path)), shas[-1], parse_trailer=parse_resolves_trailer)
    assert found["range_status"] == "widened" and [m["sha"] for m in found["merges"]] == [shas[2]]


def test_range_merges_widened_state_read_error_is_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shas, api = _chain(tmp_path, ["started", "error", "skipped"], ["", "Resolves: rec-11", ""])
    widened = {"head": shas[-1], "prev": shas[0], "range_status": "widened", "commits": shas[1:], "prs": []}
    monkeypatch.setattr(tr.range_context, "compute_range", lambda *a, **k: widened)
    found = tr.range_merges(api, tr.Git(str(tmp_path)), shas[-1], parse_trailer=parse_resolves_trailer)
    assert found == {"prev": shas[0], "range_status": "unresolved", "merges": []}


def test_range_merges_unresolved_and_gate_error(tmp_path: Path) -> None:
    shas, api = _chain(tmp_path, ["skipped", "skipped", "skipped"], ["", "Resolves: rec-11", ""])
    found = tr.range_merges(api, tr.Git(str(tmp_path)), shas[-1], parse_trailer=parse_resolves_trailer)
    assert found["range_status"] == "unresolved" and found["merges"] == []
    shas, api = _chain(tmp_path / "e", ["started", "error", "started"], ["", "Resolves: rec-11", ""])
    found = tr.range_merges(api, tr.Git(str(tmp_path / "e")), shas[-1], parse_trailer=parse_resolves_trailer)
    assert found["range_status"] == "unresolved" and found["merges"] == []


def test_extend_census_takes_each_rec_once_drops_non_junit_and_closed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    shas, api = _chain(
        tmp_path,
        ["started", "skipped", "skipped", "skipped", "started"],
        ["", "Resolves: rec-11, rec-14", "Resolves: rec-11, rec-13", "Resolves: rec-12", ""],
    )
    rows = {i: {"id": i, "status": "open"} for i in ("rec-11", "rec-14", "rec-12")} | {
        "rec-13": {"id": "rec-13", "status": "closed"}
    }
    doc = _extend(shas, api, tmp_path, rows)
    by = {e["rec_id"]: e["closing_sha"] for e in doc["entries"]}
    assert by == {"rec-12": shas[3], "rec-11": shas[2]}
    assert doc["ids"] == ["rec-12", "rec-11"] and doc["covers"] is True and doc["range_status"] == "resolved"
    assert "[trailer_range] covers=True" in capsys.readouterr().out


def test_extend_census_head_wins_and_no_range_when_not_covering(tmp_path: Path) -> None:
    shas, api = _chain(tmp_path, ["started", "skipped", "skipped"], ["", "Resolves: rec-11", "Resolves: rec-11"])
    head_entry = {"rec_id": "rec-11", "route": "junit", "closing_sha": shas[-1]}
    kwargs = {"census_fn": _census_fn, "parse_trailer": parse_resolves_trailer, "git": tr.Git(str(tmp_path))}
    doc = tr.extend_census(shas[-1], ["rec-11"], [head_entry], reader=FakeReader({}), api=api, **kwargs)
    assert doc["covers"] is False and doc["prev"] is None and doc["range_status"] == "not_walked"
    assert doc["entries"] == [head_entry] and not any(c.startswith("commits/") for c in api.calls)
    api.states[shas[-1]] = "started"
    reader = FakeReader({"rec-11": {"id": "rec-11", "status": "open"}})
    doc = tr.extend_census(shas[-1], ["rec-11"], [head_entry], reader=reader, api=api, **kwargs)
    assert doc["ids"] == ["rec-11"] and doc["entries"] == [head_entry] and reader.calls == []


def test_extend_census_unresolved_emits_error_annotation(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    shas, api = _chain(tmp_path, ["skipped", "skipped", "started"], ["", "Resolves: rec-11", ""])
    doc = _extend(shas, api, tmp_path, {"rec-11": {"id": "rec-11", "status": "open"}})
    assert doc["range_status"] == "unresolved" and doc["ids"] == []
    assert "::error title=trailer-range::" in capsys.readouterr().out


def test_extend_census_skips_merge_whose_recs_are_all_closed(tmp_path: Path) -> None:
    shas, api = _chain(tmp_path, ["started", "skipped", "started"], ["", "Resolves: rec-13", ""])
    assert _extend(shas, api, tmp_path, {"rec-13": {"id": "rec-13", "status": "closed"}})["ids"] == []


def test_close_groups_head_group_first_then_census_order(tmp_path: Path) -> None:
    doc = {
        "ids": ["rec-11", "rec-12", "rec-H", "rec-N"],
        "entries": [
            {"rec_id": "rec-11", "route": "junit", "closing_sha": "m1"},
            {"rec_id": "rec-12", "route": "junit", "closing_sha": "m2"},
            {"rec_id": "rec-H", "route": "static", "closing_sha": "head"},
        ],
        "covers": True,
    }
    assert tr.close_groups(doc, "head") == [("head", ["rec-H", "rec-N"]), ("m1", ["rec-11"]), ("m2", ["rec-12"])]


def test_close_groups_document_without_covers_reads_as_true() -> None:
    doc = {"ids": ["rec-11"], "entries": [{"rec_id": "rec-11", "route": "junit", "closing_sha": None}]}
    assert tr.close_groups(doc, "head") == [("head", ["rec-11"])]


@pytest.mark.parametrize(("covers", "marker"), [(False, tr.DEFERRED_MARKER), (None, tr.UNREADABLE_MARKER)])
def test_close_groups_holds_junit_recs_and_mirrors_to_summary(
    covers: bool | None, marker: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    doc = {
        "ids": ["rec-15", "rec-14"],
        "entries": [{"rec_id": "rec-15", "route": "junit"}, {"rec_id": "rec-14", "route": "static"}],
        "covers": covers,
    }
    assert tr.close_groups(doc, "head") == [("head", ["rec-14"])]
    out = capsys.readouterr().out
    assert f"{marker}: rec-15 left OPEN" in out and "rec-14 left OPEN" not in out
    assert marker in summary.read_text(encoding="utf-8")


def test_covering_names_first_started_commit_and_moves_past_aborted(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    shas = make_chain(tmp_path, ["c0", "c1", "c2"])
    api = FakeApi({shas[0]: "started", shas[1]: "skipped", shas[2]: "started"})
    git = tr.Git(str(tmp_path))
    assert tr.cmd_covering(argparse.Namespace(sha=shas[1]), api=api, git=git) == 0
    assert capsys.readouterr().out.strip() == f"COVERING {shas[2]}"
    api.states[shas[2]] = "skipped"
    assert tr.cmd_covering(argparse.Namespace(sha=shas[1]), api=api, git=git) == 0
    assert capsys.readouterr().out.strip() == "COVERING pending"
    assert tr.cmd_covering(argparse.Namespace(sha=shas[0]), api=FakeApi({shas[0]: "started"}), git=git) == 0
    assert capsys.readouterr().out.strip() == f"COVERING {shas[0]}"


def test_covering_not_on_main_and_unreadable_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    shas = make_chain(tmp_path, ["c0", "c1"])
    git = tr.Git(str(tmp_path))
    git.out("checkout", "-q", "-b", "side", shas[0])
    git.out("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "side")
    side = git.out("rev-parse", "HEAD")
    assert tr.cmd_covering(argparse.Namespace(sha=side), api=FakeApi({}), git=git) == 2
    assert capsys.readouterr().out.strip() == "COVERING not-on-main"
    assert tr.cmd_covering(argparse.Namespace(sha=shas[0]), api=FakeApi({shas[0]: "error"}), git=git) == 2
    assert capsys.readouterr().out.startswith("COVERING unreadable")


def test_main_dispatches_covering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    shas = make_chain(tmp_path, ["c0"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tr, "GithubApi", lambda: FakeApi({shas[0]: "started"}))
    assert tr.main(["covering", "--sha", shas[0]]) == 0
    assert capsys.readouterr().out.strip() == f"COVERING {shas[0]}"
