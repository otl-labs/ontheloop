"""The review-context fetch verb: shape, paging, merge-base texts, API errors and the no-op."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import resolve_pr, review_context, route

SHA = "a" * 40
BASE = "b" * 40
RES = {"resolved": True, "head_sha": SHA, "base_ref": "main", "number": 7, "change_id": "o/r#7"}


def _files() -> list[dict[str, Any]]:
    return [
        {"filename": "scripts/a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"},
        {"filename": "tests/new.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+z"},
        {"filename": "scripts/new_name.py", "status": "renamed", "previous_filename": "scripts/old.py"},
        {"filename": "src/gone.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-q"},
        {"filename": "docs/x.md", "status": "modified", "patch": "@@ -1 +1 @@\n-1\n+2"},
    ]


class Api:
    def __init__(self, fail_on: str | None = None, runs: int = 150) -> None:
        self.fail_on = fail_on
        self.runs = runs
        self.calls: list[str] = []

    def get_json(self, path: str, token: Any) -> Any:
        self.calls.append(path)
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("boom")
        if path.endswith("/pulls/7"):
            return {"author_association": "MEMBER", "head": {"ref": "agent/x"}}
        raise AssertionError(path)

    def request(self, method: str, url: str, token: Any, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        self.calls.append(url)
        if self.fail_on and self.fail_on in url:
            return 500, b"{}"
        if "/contents/" in url:
            if "gone.py" in url:
                return 404, b""
            if "old.py" in url and "binary" in self.__dict__:
                return 200, b"\xff"
            return 200, f"text of {url.split('/contents/')[1].split('?')[0]}".encode()
        page = int(url.rsplit("page=", 1)[1])
        if "/check-runs" in url:
            rows = [
                {
                    "name": f"c{i}",
                    "status": "completed",
                    "conclusion": "success",
                    "output": {"summary": "s"},
                    "app": {"id": 5, "slug": "gha"},
                }
                for i in range(self.runs)
            ][(page - 1) * 100 : page * 100]
            return 200, json.dumps({"check_runs": rows}).encode()
        if "/comments" in url:
            return 200, json.dumps([{"body": "hi", "author_association": "NONE"}] if page == 1 else []).encode()
        raise AssertionError(url)


def _install(monkeypatch: pytest.MonkeyPatch, api: Api) -> None:
    monkeypatch.setattr(resolve_pr, "request", api.request)
    monkeypatch.setattr(resolve_pr, "get_json", api.get_json)
    monkeypatch.setattr(route, "changed_files", lambda *a: (_files(), False, BASE))


def _prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, routes: list[str]) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir()
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps(RES))
    (tmp_path / "prepared" / "routes.json").write_text(json.dumps({"routes": routes}))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")


def test_fetch_writes_the_review_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api()
    _install(monkeypatch, api)
    _prepared(tmp_path, monkeypatch, ["code_reviewer"])
    assert review_context.main([]) == 0
    doc = json.loads((tmp_path / "prepared" / "review-context.json").read_text())
    assert doc["merge_base_sha"] == BASE and doc["truncated"] is False
    assert doc["head_ref"] == "agent/x" and doc["body_author_association"] == "MEMBER"
    assert doc["comments"] == [{"body": "hi", "author_association": "NONE"}]
    assert len(doc["check_runs"]) == 150 and doc["check_runs"][0]["app_slug"] == "gha"
    assert set(doc["merge_base_texts"]) == {"scripts/a.py", "scripts/old.py", "src/gone.py"} - {"src/gone.py"}
    assert doc["merge_base_texts"]["scripts/old.py"] == "text of scripts/old.py"
    assert [f["filename"] for f in doc["files"]][2] == "scripts/new_name.py"
    assert doc["files"][2]["previous_filename"] == "scripts/old.py"
    assert "patch omitted: scripts/new_name.py" in doc["diff"]
    assert "diff --git a/scripts/old.py b/scripts/new_name.py" in doc["diff"]
    assert "+y" in doc["diff"]
    assert "files=5 truncated=false" in capsys.readouterr().out


def test_fetch_error_is_input_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for failing in ("/check-runs", "/comments", "/pulls/7", "scripts/a.py"):
        api = Api(fail_on=failing)
        _install(monkeypatch, api)
        _prepared(tmp_path, monkeypatch, ["code_reviewer"])
        assert review_context.main([]) == 0
        failure = json.loads((tmp_path / "prepared" / "failure-code_reviewer.json").read_text())
        assert failure["failure"] == "input_incomplete"
        assert not (tmp_path / "prepared" / "review-context.json").exists()
        assert "failed: input_incomplete" in capsys.readouterr().out
        (tmp_path / "prepared" / "failure-code_reviewer.json").unlink()
        (tmp_path / "prepared" / "routes.json").unlink()
        (tmp_path / "prepared" / "resolution.json").unlink()
        (tmp_path / "prepared").rmdir()


def test_fetch_no_merge_base_or_unpaged_list_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api()
    _install(monkeypatch, api)
    monkeypatch.setattr(route, "changed_files", lambda *a: (_files(), False, ""))
    _prepared(tmp_path, monkeypatch, ["code_reviewer"])
    review_context.main([])
    assert "no merge base" in (tmp_path / "prepared" / "failure-code_reviewer.json").read_text()
    with pytest.raises(review_context.FetchError):
        monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (200, b'{"check_runs": 1}'))
        review_context._json_pages("/x", None, "check_runs")
    with pytest.raises(review_context.FetchError):
        monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (200, json.dumps([{}] * 100).encode()))
        review_context._json_pages("/x", None)


def test_fetch_non_utf8_base_text_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    api.binary = True  # type: ignore[attr-defined]
    _install(monkeypatch, api)
    _prepared(tmp_path, monkeypatch, ["code_reviewer"])
    review_context.main([])
    doc = json.loads((tmp_path / "prepared" / "review-context.json").read_text())
    assert "scripts/old.py" not in doc["merge_base_texts"]


def test_fetch_is_a_noop_without_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api()
    _install(monkeypatch, api)
    _prepared(tmp_path, monkeypatch, ["decision_scout"])
    assert review_context.main([]) == 0
    assert "review-context skip" in capsys.readouterr().out
    assert api.calls == [] and not (tmp_path / "prepared" / "review-context.json").exists()
