"""rec-4235's acceptance file. Fully hermetic: fake reader, tmp repo, no duckdb, no credentials, no network.

The Decision 201 closure verdict runs it in the static sandbox on the merge push, before the deploy, so nothing here may
reach the live reader. Git-touching tests build their repo under tmp_path with an explicit identity and signing off.
"""

from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.recs_related.envelope import CHANNEL_OK
from scripts.recs_related.history import HistoryIndex

# The package facade re-exports the related_recs FUNCTION under the submodule's own name, so import the module by name.
rr = importlib.import_module("scripts.recs_related.related_recs")

pytestmark = pytest.mark.unit

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

_FILES = {
    "src/__init__.py": "",
    "src/pkg/__init__.py": "",
    "src/pkg/a.py": "from src.pkg.b import VALUE\n\n\ndef alpha_function():\n    return VALUE\n",
    "src/pkg/b.py": "VALUE = 1\n",
    "src/pkg/c.py": "from src.pkg.a import VALUE\n",
    "src/pkg/widget.py": "def build_widget():\n    return 1\n",
    "tests/__init__.py": "",
    "tests/test_a.py": "def test_a():\n    pass\n",
    "docs/contracts/ports.yaml": "owner: x\n",
    "docs/contracts/other.yaml": "owner: y\n",
}


class FakeReader:
    """Records every named() call; returns canned rows or raises."""

    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def named(self, verb: str, **params: Any) -> list[dict]:
        self.calls.append((verb, params))
        if self.error is not None:
            raise self.error
        return self.rows


def _row(rid: str, file: str | None, *, direct: bool = False, total: int = 1, direct_total: int = 0, **kw: Any) -> dict:
    return {
        "id": rid,
        "title": f"title {rid}",
        "status": "open",
        "priority": kw.pop("priority", "high"),
        "effort": "S",
        "source": "manual",
        "file": file,
        "created_timestamp": None,
        "direct": direct,
        "score": kw.pop("score", 1.0),
        "matched_terms": kw.pop("matched_terms", None),
        "snippet": "snippet",
        "total": total,
        "direct_total": direct_total,
    }


def _binds(reader: FakeReader) -> dict[str, Any]:
    ((verb, params),) = reader.calls
    assert verb == "recs_related"
    return params


def _near(params: dict[str, Any]) -> dict[str, float]:
    return {k: float(v) for k, v in (e.rsplit("|", 1) for e in params["near"].split(",") if e)}


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for rel, text in _FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(rr, "tracked_files", lambda _root: frozenset(_FILES))
    monkeypatch.setattr("scripts.test_coverage_checker.ROOT", tmp_path)
    monkeypatch.setattr(rr.history, "load_history", lambda _root: HistoryIndex())
    return tmp_path


def _run(tree: Path, files: list[str], reader: FakeReader, **kw: Any):
    return rr.related_recs(files, reader=reader, repo_root=tree, **kw)


class TestRelatedRecs:
    def test_exactly_one_named_call_with_the_expected_binds_and_weights(self, tree: Path):
        reader = FakeReader([_row("rec-1", "src/pkg/a.py", direct=True, total=1, direct_total=1)])
        env = _run(tree, ["src/pkg/a.py"], reader, terms_text="retry")
        params = _binds(reader)
        assert list(params) == ["files", "near", "terms", "exclude_ids", "max_rows"]
        assert all(isinstance(v, str) for v in params.values())
        assert params["files"] == "src/pkg/a.py" and params["exclude_ids"] == "" and params["max_rows"] == "20"
        near = _near(params)
        assert near["src/pkg/b.py"] == 4 + 1 + 0.5  # upstream + sibling + dotted mention
        assert near["src/pkg/c.py"] == 1 + 1  # downstream + sibling
        assert near["tests/test_a.py"] == 1  # tests mirror
        assert near["src/pkg"] == near["src/pkg/"] == 0.5  # parent directory scope, both spellings
        assert "src/pkg/a.py" not in near
        terms = dict(e.split("|") for e in params["terms"].split(","))
        assert terms["retry"] == "4.0" and terms["a.py"] == "0.5" and terms["src.pkg.a"] == "1.0"
        assert terms["alpha_function"] == "0.5" and terms["src/pkg/a.py"] == "0.5"
        assert env.query["files"] == ["src/pkg/a.py"] and env.query_files == [{"path": "src/pkg/a.py", "status": "tracked"}]

    def test_stem_term_carries_the_dominant_weight(self, tree: Path):
        reader = FakeReader()
        _run(tree, ["src/pkg/widget.py"], reader)
        terms = dict(e.split("|") for e in _binds(reader)["terms"].split(","))
        assert terms["widget"] == "16.0" and terms["build_widget"] == "0.5"

    def test_every_hit_carries_a_why_and_a_tier(self, tree: Path):
        reader = FakeReader(
            [
                _row("rec-1", "src/pkg/a.py", direct=True, total=3, direct_total=1),
                _row("rec-2", "src/pkg/b.py", total=3),
                _row("rec-3", "elsewhere/x.py", total=3, matched_terms="alpha_function retry"),
            ]
        )
        env = _run(tree, ["src/pkg/a.py"], reader)
        assert [h.tier for h in env.hits] == ["direct", "near", "term"]
        assert env.hits[0].why == ["direct:src/pkg/a.py"]
        assert "upstream:src/pkg/a.py" in env.hits[1].why and "mention:src/pkg/a.py" in env.hits[1].why
        assert env.hits[2].why == ["term:alpha_function", "term:retry"]
        assert all(h.why for h in env.hits)

    def test_a_row_the_expansion_cannot_explain_is_marked_unattributed_not_empty(self, tree: Path):
        env = _run(tree, ["src/pkg/a.py"], FakeReader([_row("rec-9", "mystery.py", total=1)]))
        assert env.hits[0].tier == "term" and env.hits[0].why == ["unattributed"]

    def test_aliases_join_the_direct_files_bind_and_are_explained(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        index = HistoryIndex(renames=[("src/pkg/old_a.py", "src/pkg/a.py")], seen={"src/pkg/old_a.py", "src/pkg/a.py"})
        monkeypatch.setattr(rr.history, "load_history", lambda _root: index)
        reader = FakeReader([_row("rec-1", "src/pkg/old_a.py", direct=True, total=1, direct_total=1)])
        env = _run(tree, ["src/pkg/a.py"], reader)
        assert _binds(reader)["files"] == "src/pkg/a.py,src/pkg/old_a.py"
        assert env.query["aliases"] == ["src/pkg/old_a.py"]
        assert env.hits[0].why == ["alias:src/pkg/old_a.py (earlier name of src/pkg/a.py)"]

    def test_change_coupling_joins_near_with_shared_and_confidence(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        commits = [frozenset({"src/pkg/a.py", "docs/contracts/ports.yaml"})] * 3 + [frozenset({"src/pkg/a.py", "x.py"})] * 3
        index = HistoryIndex(commits=commits)
        monkeypatch.setattr(rr.history, "load_history", lambda _root: index)
        reader = FakeReader([_row("rec-1", "docs/contracts/ports.yaml", total=1)])
        env = _run(tree, ["src/pkg/a.py"], reader)
        near = _near(_binds(reader))
        assert near["docs/contracts/ports.yaml"] == 0.5 * 3 + 0.5 * 0.5
        assert env.hits[0].tier == "near" and env.hits[0].why == ["coupling:src/pkg/a.py (3 shared commits)"]

    def test_coupling_weight_saturates_at_five_shared_commits(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        index = HistoryIndex(commits=[frozenset({"src/pkg/a.py", "x.py"})] * 9)
        monkeypatch.setattr(rr.history, "load_history", lambda _root: index)
        reader = FakeReader()
        _run(tree, ["src/pkg/a.py"], reader)
        assert _near(_binds(reader))["x.py"] == 0.5 * 5 + 0.5 * 1.0

    def test_ancestor_directories_reach_near_in_both_spellings_and_are_counted(self, tree: Path):
        reader = FakeReader(
            [
                _row("rec-1", "docs/contracts", total=4),
                _row("rec-2", "docs/contracts/", total=4),
                _row("rec-3", "docs", total=4),
                _row("rec-4", "docs/contracts/other.yaml", total=4),
            ]
        )
        env = _run(tree, ["docs/contracts/ports.yaml"], reader)
        near = _near(_binds(reader))
        assert near["docs/contracts"] == near["docs/contracts/"] == 0.5
        assert "docs" not in near and "docs/" not in near  # a single-segment root is never directory scope
        assert env.hits[0].why == ["ancestor_dir:docs/contracts"] == env.hits[1].why
        assert env.hits[2].tier == "term"
        assert env.dir_anchor_hits == 2

    def test_grandparent_directory_scope_has_the_smaller_weight(self, tree: Path):
        reader = FakeReader()
        _run(tree, ["src/pkg/sub/deep.py"], reader)
        near = _near(_binds(reader))
        assert near["src/pkg/sub"] == near["src/pkg/sub/"] == 0.5
        assert near["src/pkg"] == near["src/pkg/"] == 0.25
        assert "src" not in near

    def test_query_files_sharing_a_directory_add_their_weights(self, tree: Path):
        reader = FakeReader()
        _run(tree, ["docs/contracts/ports.yaml", "docs/contracts/other.yaml"], reader)
        near = _near(_binds(reader))
        assert near["docs/contracts"] == near["docs/contracts/"] == 1.0
        assert not {"docs/contracts/ports.yaml", "docs/contracts/other.yaml"} & set(near)  # query files are direct, not near

    def test_a_path_with_comma_or_pipe_is_reported_invalid_and_never_bound(self, tree: Path):
        reader = FakeReader()
        env = _run(tree, ["src/pkg/a.py", "bad,name.py", "worse|name.py"], reader)
        assert [q["status"] for q in env.query_files] == ["tracked", "invalid", "invalid"]
        assert _binds(reader)["files"] == "src/pkg/a.py"

    def test_query_paths_are_normalised_and_given_a_status(self, tree: Path):
        raw = [
            "./src//pkg/a.py",
            str(tree / "src/pkg/b.py"),
            "src\\pkg\\c.py",
            "src/pkg/not_yet.py",
            "/etc/passwd",
            "../up.py",
            "..",
            ".",
        ]
        reader = FakeReader()
        env = _run(tree, raw, reader)
        assert env.query_files == [
            {"path": "src/pkg/a.py", "status": "tracked"},
            {"path": "src/pkg/b.py", "status": "tracked"},
            {"path": "src/pkg/c.py", "status": "tracked"},
            {"path": "src/pkg/not_yet.py", "status": "untracked"},
            {"path": "/etc/passwd", "status": "outside_repo"},
            {"path": "../up.py", "status": "outside_repo"},
            {"path": "..", "status": "outside_repo"},
            {"path": ".", "status": "invalid"},
        ]
        assert _binds(reader)["files"] == "src/pkg/a.py,src/pkg/b.py,src/pkg/c.py,src/pkg/not_yet.py"

    def test_a_not_yet_created_file_keeps_direct_siblings_and_terms_but_reports_channels_unavailable(self, tree: Path):
        reader = FakeReader()
        env = _run(tree, ["src/pkg/not_yet.py"], reader)
        params = _binds(reader)
        assert params["files"] == "src/pkg/not_yet.py"
        assert _near(params)["src/pkg/a.py"] == 1.0  # sibling only
        assert "not_yet" in params["terms"]
        for channel in ("imports", "mentions", "history"):
            assert env.channels[channel].startswith("unavailable: ") and "src/pkg/not_yet.py" in env.channels[channel]

    def test_channel_status_groups_files_by_reason(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(rr.history, "load_history", lambda _root: HistoryIndex(status="unavailable: shallow clone"))
        env = _run(tree, ["src/pkg/a.py", "src/pkg/b.py"], FakeReader())
        assert env.channels["history"] == "unavailable: shallow clone [src/pkg/a.py, src/pkg/b.py]"
        assert env.channels["imports"] == CHANNEL_OK and env.channels["mentions"] == CHANNEL_OK

    def test_terms_only_query_makes_one_call_with_empty_file_binds(self, tree: Path):
        reader = FakeReader([_row("rec-1", "z.py", total=1, matched_terms="retry")])
        env = _run(tree, [], reader, terms_text="Retry  backoff")
        params = _binds(reader)
        assert (params["files"], params["near"]) == ("", "")
        assert params["terms"] == "backoff|4.0,retry|4.0"
        assert env.query == {"files": [], "aliases": [], "terms": ["backoff", "retry"]}
        assert env.channels == {"imports": CHANNEL_OK, "mentions": CHANNEL_OK, "history": CHANNEL_OK}

    def test_the_40_term_cap_truncates_by_weight_and_is_reported(self, tree: Path):
        words = " ".join(f"word{i:02d}" for i in range(60))
        reader = FakeReader()
        env = _run(tree, ["src/pkg/widget.py"], reader, terms_text=words)
        sent = dict(e.split("|") for e in _binds(reader)["terms"].split(","))
        assert len(sent) == 40 and env.terms_truncated is True
        assert sent["widget"] == "16.0" and list(sent.values()).count("4.0") == 39  # the heaviest are kept
        assert "src.pkg.widget" not in sent and "build_widget" not in sent  # the lighter derived terms were cut
        assert len(env.query["terms"]) == 40

    def test_no_truncation_flag_for_a_small_term_set(self, tree: Path):
        env = _run(tree, ["src/pkg/a.py"], FakeReader())
        assert env.terms_truncated is False and env.near_truncated is False

    def test_near_list_is_capped_at_400_paths_by_weight_and_truncation_is_reported(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ):
        many = frozenset(f"docs/many/f{i:04d}.md" for i in range(500)) | frozenset(_FILES)
        monkeypatch.setattr(rr, "tracked_files", lambda _root: many)
        reader = FakeReader()
        env = _run(tree, ["docs/many/f0000.md"], reader)
        near = _near(_binds(reader))
        assert len(near) == rr.MAX_NEAR and env.near_truncated is True
        assert set(near.values()) == {1.0} and "docs/many" not in near  # heavier siblings outrank directory scope

    def test_unsafe_near_paths_and_aliases_are_dropped_with_a_note(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        tracked = frozenset(_FILES) | {"src/pkg/we,ird.py"}
        monkeypatch.setattr(rr, "tracked_files", lambda _root: tracked)
        index = HistoryIndex(renames=[("src/pkg/old|a.py", "src/pkg/a.py")])
        monkeypatch.setattr(rr.history, "load_history", lambda _root: index)
        reader = FakeReader()
        env = _run(tree, ["src/pkg/a.py"], reader)
        params = _binds(reader)
        assert "we,ird" not in params["near"] and "old|a" not in params["files"]
        assert any("near path(s) dropped" in n for n in env.notes)

    def test_three_page_sequence_tracks_completeness_and_next_exclude(self, tree: Path):
        def page(ids: list[str], total: int, direct_total: int, exclude: list[str]):
            rows = [_row(i, "src/pkg/a.py", direct=True, total=total, direct_total=direct_total) for i in ids]
            reader = FakeReader(rows)
            env = _run(tree, ["src/pkg/a.py"], reader, limit=2, exclude_ids=exclude)
            assert _binds(reader)["exclude_ids"] == ",".join(exclude) and _binds(reader)["max_rows"] == "2"
            return env

        p1 = page(["rec-1", "rec-2"], total=5, direct_total=5, exclude=[])
        assert (p1.complete, p1.direct_complete, p1.next_exclude) == (False, False, ["rec-1", "rec-2"])
        p2 = page(["rec-3", "rec-4"], total=3, direct_total=3, exclude=p1.next_exclude or [])
        assert (p2.complete, p2.direct_complete, p2.next_exclude) == (False, False, ["rec-1", "rec-2", "rec-3", "rec-4"])
        p3 = page(["rec-5"], total=1, direct_total=1, exclude=p2.next_exclude or [])
        assert (p3.complete, p3.direct_complete, p3.next_exclude) == (True, True, None)

    def test_paging_stops_at_the_200_id_cap_with_a_narrowing_hint(self, tree: Path):
        prior = [f"rec-{i}" for i in range(199)]
        rows = [
            _row("rec-900", "src/pkg/a.py", direct=True, total=9, direct_total=9),
            _row("rec-901", "src/pkg/a.py", direct=True, total=9, direct_total=9),
        ]
        env = _run(tree, ["src/pkg/a.py"], FakeReader(rows), exclude_ids=prior)
        assert env.complete is False and env.next_exclude is None
        assert any("paging stopped" in n and "narrow the query" in n for n in env.notes)

    def test_paging_may_continue_up_to_exactly_200_excluded_ids(self, tree: Path):
        prior = [f"rec-{i}" for i in range(199)]
        rows = [_row("rec-900", "src/pkg/a.py", direct=True, total=9, direct_total=9)]
        env = _run(tree, ["src/pkg/a.py"], FakeReader(rows), exclude_ids=prior)
        assert env.next_exclude is not None and len(env.next_exclude) == 200

    def test_a_reader_error_propagates_and_is_never_an_empty_result(self, tree: Path):
        with pytest.raises(RuntimeError, match="reader down"):
            _run(tree, ["src/pkg/a.py"], FakeReader(error=RuntimeError("reader down")))

    def test_an_empty_query_is_refused(self, tree: Path):
        reader = FakeReader()
        with pytest.raises(ValueError, match="empty query"):
            _run(tree, [], reader)
        with pytest.raises(ValueError, match="bad,name.py.*invalid"):
            _run(tree, ["bad,name.py"], reader, terms_text="  ")
        assert reader.calls == []

    def test_limit_and_exclude_bounds_are_enforced_before_any_call(self, tree: Path):
        reader = FakeReader()
        for bad in (0, 101):
            with pytest.raises(ValueError, match="limit must be 1..100"):
                _run(tree, ["src/pkg/a.py"], reader, limit=bad)
        with pytest.raises(ValueError, match="at most 200 excluded ids"):
            _run(tree, ["src/pkg/a.py"], reader, exclude_ids=[f"rec-{i}" for i in range(201)])
        assert reader.calls == []

    def test_exclude_ids_are_stripped_and_deduplicated(self, tree: Path):
        reader = FakeReader()
        _run(tree, ["src/pkg/a.py"], reader, exclude_ids=[" rec-1", "rec-1", "", "rec-2"])
        assert _binds(reader)["exclude_ids"] == "rec-1,rec-2"

    def test_an_empty_page_is_reported_with_zero_totals(self, tree: Path):
        env = _run(tree, ["src/pkg/a.py"], FakeReader([]))
        assert (env.total, env.direct_total, env.returned, env.complete) == (0, 0, 0, True)

    def test_the_default_reader_is_the_live_reader_factory(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        fake = FakeReader()
        monkeypatch.setattr(rr, "make_reader", lambda: fake)
        rr.related_recs(["src/pkg/a.py"], repo_root=tree)
        assert len(fake.calls) == 1

    def test_envelope_serialises_to_json(self, tree: Path):
        env = _run(tree, ["src/pkg/a.py"], FakeReader([_row("rec-1", "src/pkg/a.py", direct=True, total=1, direct_total=1)]))
        assert '"direct_total": 1' in env.to_json()


class TestTrackedFiles:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        for args in (["init", "-q"], ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", *args], cwd=repo, env=_GIT_ENV, check=True, capture_output=True)
        (repo / "a.txt").write_text("1", encoding="utf-8")
        (repo / "sub").mkdir()
        (repo / "sub" / "b.txt").write_text("2", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, env=_GIT_ENV, check=True, capture_output=True)
        return repo

    def test_lists_the_index(self, tmp_path: Path):
        assert rr.tracked_files(self._repo(tmp_path)) == {"a.txt", "sub/b.txt"}

    def test_outside_a_repo_it_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="cannot list tracked files"):
            rr.tracked_files(tmp_path)

    def test_when_git_cannot_run_it_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(rr.history.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
        with pytest.raises(RuntimeError, match="git could not run"):
            rr.tracked_files(tmp_path)

    def test_end_to_end_over_a_real_repo(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, env=_GIT_ENV, check=True, capture_output=True)
        reader = FakeReader([_row("rec-1", "a.txt", direct=True, total=1, direct_total=1)])
        env = rr.related_recs(["a.txt"], reader=reader, repo_root=repo)
        assert env.hits[0].why == ["direct:a.txt"]
        assert env.channels["history"] == CHANNEL_OK
        assert _near(_binds(reader))["sub/b.txt"] == 0.5 + 0.5  # coupling of the single init commit, nothing else
