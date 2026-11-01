"""Tests for scripts/session/merge_step.py (premerge, readback, recover). Fixture repos with a local bare origin."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import rec_trailer_acceptance as acceptance
from scripts.roadmap import approved_text as at
from scripts.session import merge_step as ms
from scripts.verification_graduation import GraduationError
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
BUNDLED = ["rec-1", "rec-2"]


def _merged(tmp_path, monkeypatch, bundled=BUNDLED, **kwargs) -> tuple[Path, str]:
    """Feature branch fast-forwarded into main and pushed; returns (repo, merge sha)."""
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, bundled, **kwargs)
    fx.git(repo, "checkout", "main")
    fx.git(repo, "merge", "--ff-only", "feature")
    fx.push_main(repo)
    return repo, fx.git(repo, "rev-parse", "HEAD")


def _readback(repo: Path, sha: str) -> int:
    return ms.main(["readback", "--slug", SLUG, "--root", str(repo), "--sha", sha])


def _premerge(repo: Path, tmp_path: Path, message: str | None = None, *extra: str) -> int:
    msg = fx.message_file(tmp_path, message if message is not None else fx.head_message(repo))
    return ms.main(["premerge", "--slug", SLUG, "--root", str(repo), "--message-file", str(msg), *extra])


class TestReadback:
    def test_ok_names_every_bundled_rec(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch)
        assert _readback(repo, sha) == 0
        assert f"READBACK OK {sha} recs=['rec-1', 'rec-2']" in capsys.readouterr().out

    def test_missing_trailer(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat: no trailer\n")
        assert _readback(repo, sha) == 1
        assert f"READBACK MISSING {sha} missing=['rec-1', 'rec-2']" in capsys.readouterr().out

    def test_unexpected_trailer_on_undeclared_plan_prints_remedy(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, declared=False)
        assert _readback(repo, sha) == 1
        out = capsys.readouterr().out
        assert f"READBACK UNEXPECTED {sha} extra=['rec-1', 'rec-2']" in out
        assert "implementation_declared is false" in out and "never drop the trailer" in out

    def test_extra_rec_on_declared_plan_has_no_declare_remedy(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1, rec-2, rec-9\n")
        assert _readback(repo, sha) == 1
        out = capsys.readouterr().out
        assert "extra=['rec-9']" in out and "remedy" not in out

    def test_empty_bundle_expects_no_trailer(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, bundled=[])
        assert _readback(repo, sha) == 0
        assert f"READBACK OK {sha} recs=[]" in capsys.readouterr().out

    def test_expected_set_comes_from_the_plan_at_the_sha(self, tmp_path, monkeypatch):
        repo, sha = _merged(tmp_path, monkeypatch)
        fx.write_plan(repo, SLUG, ["rec-1", "rec-2", "rec-3"], True)
        assert _readback(repo, sha) == 0

    def test_unreadable_plan_exits_two(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch)
        assert ms.main(["readback", "--slug", "ghost", "--root", str(repo), "--sha", sha]) == 2
        assert "READBACK ERROR" in capsys.readouterr().out


class TestPremergeTrailer:
    def test_exact_match_passes_and_prints_head(self, tmp_path, monkeypatch, capsys):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
        assert _premerge(repo, tmp_path) == 0
        assert f"PREMERGE HEAD {fx.git(repo, 'rev-parse', 'HEAD')}" in capsys.readouterr().out

    def test_missing_rec_names_the_add_remedy(self, tmp_path, monkeypatch, capsys):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1\n")
        assert _premerge(repo, tmp_path) == 1
        out = capsys.readouterr().out
        assert "PREMERGE TRAILER MISMATCH expected=['rec-1', 'rec-2'] parsed=['rec-1']" in out
        assert "add a commit whose message carries: Resolves: rec-1, rec-2" in out and "--force-with-lease" not in out

    def test_extra_rec_names_the_rewrite_remedy(self, tmp_path, monkeypatch, capsys):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, ["rec-1"], message="feat\n\nResolves: rec-1, rec-8\n")
        assert _premerge(repo, tmp_path) == 1
        out = capsys.readouterr().out
        assert "--force-with-lease" in out and "add a commit" not in out

    def test_undeclared_plan_without_trailer_passes(self, tmp_path, monkeypatch):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False, message="feat: intermediate\n")
        assert _premerge(repo, tmp_path) == 0

    def test_undeclared_plan_with_trailer_prints_undeclared(self, tmp_path, monkeypatch, capsys):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False)
        assert _premerge(repo, tmp_path) == 1
        out = capsys.readouterr().out
        assert f"PREMERGE UNDECLARED {SLUG}: implementation_declared is false at HEAD" in out
        assert "never drop the trailer" in out

    def test_unreadable_plan_exits_two(self, tmp_path, monkeypatch, capsys):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
        msg = fx.message_file(tmp_path, "feat\n")
        assert ms.main(["premerge", "--slug", "ghost", "--root", str(repo), "--message-file", str(msg)]) == 2
        assert "PREMERGE ERROR" in capsys.readouterr().out


class TestPremergePreviewArms:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        return fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED)

    def _stub(self, monkeypatch, rows, verdict="holds"):
        monkeypatch.setattr(ms, "_fetch_rows", lambda ids: rows)
        monkeypatch.setattr(
            acceptance,
            "census",
            lambda ids, rows=None, *, repo_root, **_: [{"rec_id": r["id"], "route": "static"} for r in rows],
        )
        monkeypatch.setattr(
            acceptance,
            "evaluate",
            lambda entries, root, sha: {"records": [{"rec_id": e["rec_id"], "verdict": verdict, "arm": "x"} for e in entries]},
        )

    def _rows(self, **status):
        return [{"id": i, "status": status.get(i.replace("-", "_"), "open")} for i in BUNDLED]

    def test_holds_for_each_rec(self, repo, tmp_path, monkeypatch, capsys):
        self._stub(monkeypatch, self._rows())
        assert _premerge(repo, tmp_path) == 0
        out = capsys.readouterr().out
        assert "PREVIEW rec-1 holds" in out and "PREVIEW rec-2 holds" in out

    def test_already_closed_is_not_fatal_and_not_previewed(self, repo, tmp_path, monkeypatch, capsys):
        self._stub(monkeypatch, self._rows(rec_1="closed", rec_2="closed"))
        assert _premerge(repo, tmp_path) == 0
        out = capsys.readouterr().out
        assert "PREVIEW rec-1 already-closed" in out and "holds" not in out

    @pytest.mark.parametrize("status", ["superseded", "declined"])
    def test_superseded_or_declined_is_fatal_with_amendment_remedy(self, repo, tmp_path, monkeypatch, capsys, status):
        self._stub(monkeypatch, self._rows(rec_1=status))
        assert _premerge(repo, tmp_path) == 1
        out = capsys.readouterr().out
        assert f"PREVIEW rec-1 already-{status}: remove it from bundled_recommendations by a plan amendment PR" in out

    def test_missing_rec_is_fatal(self, repo, tmp_path, monkeypatch, capsys):
        self._stub(monkeypatch, [{"id": "rec-2", "status": "open"}])
        assert _premerge(repo, tmp_path) == 1
        assert "PREVIEW rec-1 missing" in capsys.readouterr().out

    def test_junit_route_is_reset_to_static_before_evaluate(self, repo, tmp_path, monkeypatch):
        seen = []
        self._stub(monkeypatch, self._rows())
        monkeypatch.setattr(
            acceptance, "census", lambda ids, rows=None, *, repo_root, **_: [{"rec_id": i, "route": "junit"} for i in ids]
        )
        monkeypatch.setattr(
            acceptance,
            "evaluate",
            lambda entries, root, sha: seen.extend(e["route"] for e in entries) or {"records": []},
        )
        assert _premerge(repo, tmp_path) == 0
        assert seen == ["static", "static"]

    def test_failing_verdict_blocks(self, repo, tmp_path, monkeypatch):
        self._stub(monkeypatch, self._rows(), verdict="fails")
        assert _premerge(repo, tmp_path) == 1

    def test_reader_failure_is_not_a_holds(self, repo, tmp_path, monkeypatch, capsys):
        def boom(ids):
            raise RuntimeError("no creds")

        monkeypatch.setattr(ms, "_fetch_rows", boom)
        assert _premerge(repo, tmp_path) == 0
        out = capsys.readouterr().out
        assert "PREVIEW unavailable: no creds" in out and "holds" not in out

    def test_default_fetch_goes_through_fetch_recs_by_ids_once(self, monkeypatch):
        with (
            patch("src.common.ducklake_reader_client.make_reader", return_value="reader"),
            patch("src.common.ducklake_reader_client.fetch_recs_by_ids", return_value=([{"id": "rec-1"}], [])) as fetch,
        ):
            assert ms._fetch_rows(["rec-1"]) == [{"id": "rec-1"}]
        fetch.assert_called_once_with("reader", ["rec-1"])


BASE_DOC = "alpha beta gamma delta\nepsilon zeta\n"
BLOCKS = [{"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}]


def _sidecar_branch(tmp_path, monkeypatch) -> Path:
    """Main carries doc.md and the sidecar; the branch applies the block and declares an empty-bundle plan."""
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], False)
    fx.write(repo, "doc.md", BASE_DOC)
    fx.write_sidecar(repo, SLUG, BLOCKS)
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    fx.branch(repo)
    fx.write_plan(repo, SLUG, [], True)
    fx.commit_file(repo, "doc.md", BASE_DOC.replace("beta gamma", "BETA GAMMA"), "feat\n")
    return repo


def _advance_main(repo: Path, text: str) -> None:
    fx.git(repo, "checkout", "main")
    fx.commit_file(repo, "doc.md", text, "main moves")
    fx.push_main(repo)
    fx.git(repo, "checkout", "feature")


class TestPremergeApprovedText:
    def test_ok_with_expected_digest(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        digest = at.digest(at.load_sidecar(SLUG, repo))
        assert _premerge(repo, tmp_path, "feat\n", "--expect", digest) == 0
        assert f"PREMERGE APPROVED-TEXT OK {digest}" in capsys.readouterr().out

    def test_wrong_expected_digest_fails(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        assert _premerge(repo, tmp_path, "feat\n", "--expect", "0" * 64) == 1
        assert "digest" in capsys.readouterr().out

    def test_word_drift_fails(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        fx.commit_file(repo, "doc.md", "alpha BETA GAMMA delta\nepsilon zeta extra\n", "drift")
        assert _premerge(repo, tmp_path, "feat\n") == 1
        assert "PREMERGE APPROVED-TEXT MISMATCH doc.md" in capsys.readouterr().out

    def test_edited_sidecar_fails(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        fx.write_sidecar(repo, SLUG, [{**BLOCKS[0], "after": "BETA GAMMA!"}])
        assert _premerge(repo, tmp_path, "feat\n") == 1
        assert "PREMERGE APPROVED-TEXT SIDECAR changed" in capsys.readouterr().out

    def test_unparseable_sidecar_at_base_fails(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        fx.git(repo, "checkout", "main")
        fx.commit_file(repo, at.sidecar_rel(SLUG), "schema_version: [1\n", "break")
        fx.push_main(repo)
        fx.git(repo, "rebase", "main", "feature")
        assert _premerge(repo, tmp_path, "feat\n") == 1
        assert "PREMERGE APPROVED-TEXT ERROR" in capsys.readouterr().out

    def test_stale_base_when_a_before_changed_on_main(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        _advance_main(repo, BASE_DOC.replace("beta gamma", "beta CHANGED"))
        assert _premerge(repo, tmp_path, "feat\n") == 1
        assert "PREMERGE STALE BASE b1: rebase and re-run CI" in capsys.readouterr().out

    def test_edit_elsewhere_on_main_is_tolerated(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_branch(tmp_path, monkeypatch)
        _advance_main(repo, BASE_DOC + "appended elsewhere\n")
        assert _premerge(repo, tmp_path, "feat\n") == 0
        assert "STALE BASE" not in capsys.readouterr().out

    def test_new_file_block_is_stale_once_the_file_exists_on_main(self, tmp_path, monkeypatch):
        fx.clear_ci_env(monkeypatch)
        repo = fx.make_repo(tmp_path)
        blocks = [
            {"id": "n1", "file": "README.md", "before": "", "after": "x"},
            {"id": "n2", "file": "fresh.md", "before": "", "after": "y"},
        ]
        sidecar = at.parse_sidecar(fx.sidecar_text(SLUG, blocks))
        assert ms._sidecar_blocks_stale(sidecar, repo) == ["n1"]

    def test_malformed_plan_reads_as_absent(self, tmp_path, monkeypatch):
        repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
        fx.commit_file(repo, fx.plan_rel("broken"), "a: [1\n", "broken")
        fx.commit_file(repo, fx.plan_rel("list"), "- 1\n", "list")
        assert ms.plan_at("HEAD", "broken", repo) is None
        assert ms.plan_at("HEAD", "list", repo) is None


def _decision_text(*numbers: int) -> str:
    return "# Decisions\n\n" + "".join(f"## Decision {n}: T{n} (Decided)\n\nbody\n\n" for n in sorted(numbers, reverse=True))


def _decisions_branch(tmp_path, monkeypatch, main_adds: int | None, branch_adds: int | None) -> Path:
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
    fx.git(repo, "checkout", "main")
    fx.commit_file(repo, "docs/DECISIONS.md", _decision_text(1, 2))
    fx.push_main(repo)
    fx.git(repo, "checkout", "feature")
    fx.git(repo, "rebase", "main")
    if main_adds:
        fx.git(repo, "checkout", "main")
        fx.commit_file(repo, "docs/DECISIONS.md", _decision_text(1, 2, main_adds))
        fx.push_main(repo)
        fx.git(repo, "checkout", "feature")
    if branch_adds:
        fx.commit_file(repo, "docs/DECISIONS.md", _decision_text(1, 2, branch_adds))
    return repo


class TestPremergeDecisionCollision:
    def test_number_main_took_fails_with_the_remedy(self, tmp_path, monkeypatch, capsys):
        repo = _decisions_branch(tmp_path, monkeypatch, main_adds=3, branch_adds=3)
        assert _premerge(repo, tmp_path, "feat\n") == 1
        out = capsys.readouterr().out
        assert "PREMERGE DECISION COLLISION 3 -> 4: run git fetch origin main && " in out
        assert "bin/venv-python -m scripts.session.rebase_resolve" in out

    def test_non_colliding_decision_passes(self, tmp_path, monkeypatch, capsys):
        repo = _decisions_branch(tmp_path, monkeypatch, main_adds=3, branch_adds=4)
        assert _premerge(repo, tmp_path, "feat\n") == 0
        assert "DECISION COLLISION" not in capsys.readouterr().out

    def test_branch_without_a_decision_passes(self, tmp_path, monkeypatch):
        repo = _decisions_branch(tmp_path, monkeypatch, main_adds=3, branch_adds=None)
        assert _premerge(repo, tmp_path, "feat\n") == 0

    def test_retitled_existing_heading_passes(self, tmp_path, monkeypatch):
        repo = _decisions_branch(tmp_path, monkeypatch, main_adds=3, branch_adds=None)
        fx.commit_file(repo, "docs/DECISIONS.md", _decision_text(1, 2).replace("T2 (Decided)", "T2 retitled (Decided)"))
        assert _premerge(repo, tmp_path, "feat\n") == 0


def _stub_recover(monkeypatch, repo: Path, *, junit: bool = False):
    seen: dict = {}
    rows = [{"id": "rec-2", "status": "open", "acceptance": "x"}]
    monkeypatch.setattr(ms, "_fetch_rows", lambda ids: seen.setdefault("fetched", []).append(list(ids)) or rows)

    def census(ids, rows=None, *, repo_root, **_):
        seen["census"] = (list(ids), rows, repo_root)
        return [{"rec_id": i, "acceptance_sha256": "h", "route": "junit" if junit else "static", "bucket": "x"} for i in ids]

    def evaluate(entries, root, sha):
        seen["evaluate"] = (root, sha)
        return {
            "records": [
                {"rec_id": e["rec_id"], "acceptance_sha256": "h", "verdict": "holds", "source": "static", "arm": "a"}
                for e in entries
                if e["route"] != "junit"
            ]
        }

    @contextlib.contextmanager
    def worktree(sha, root):
        seen["worktree"] = (sha, root)
        yield repo / "wt"

    def close(ids, sha, run_url, cache, **kwargs):
        seen["close"] = (list(ids), sha, run_url, cache, kwargs)
        return 0

    monkeypatch.setattr(acceptance, "census", census)
    monkeypatch.setattr(acceptance, "evaluate", evaluate)
    monkeypatch.setattr("scripts.verification_graduation.git_worktree", worktree)
    monkeypatch.setattr("scripts.ops_portal.ci_rca_lifecycle.close_recs_from_trailer", close)
    return seen


def _recover(repo: Path, sha: str) -> int:
    return ms.main(["recover", "--slug", SLUG, "--root", str(repo), "--sha", sha])


class TestRecover:
    def test_closes_only_the_missing_recs_through_the_verdict_gate(self, tmp_path, monkeypatch):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1\n")
        seen = _stub_recover(monkeypatch, repo)
        assert _recover(repo, sha) == 0
        assert seen["fetched"] == [["rec-2"]]
        assert seen["census"][0] == ["rec-2"] and seen["census"][1][0]["id"] == "rec-2"
        assert seen["census"][2] == repo / "wt" and seen["evaluate"] == (repo / "wt", sha)
        ids, close_sha, run_url, cache, kwargs = seen["close"]
        assert ids == ["rec-2"] and close_sha == sha and "no workflow run" in run_url and list(cache) == ["rec-2"]
        assert kwargs["require_acceptance_verdict"] is True
        assert kwargs["acceptance_verdicts"]["rec-2"]["verdict"] == "holds"

    def test_junit_routed_rec_is_left_open_as_unmeasurable(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1\n")
        seen = _stub_recover(monkeypatch, repo, junit=True)
        assert _recover(repo, sha) == 0
        assert "RECOVER rec-2 junit-routed: left open for the operator" in capsys.readouterr().out
        verdict = seen["close"][4]["acceptance_verdicts"]["rec-2"]
        assert verdict["verdict"] == "unmeasurable" and verdict["arm"] == "report_unavailable"

    def test_refuses_when_nothing_is_missing(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch)
        seen = _stub_recover(monkeypatch, repo)
        assert _recover(repo, sha) == 2
        assert "no missing recs" in capsys.readouterr().out and "close" not in seen

    def test_refuses_a_sha_where_the_declaration_does_not_flip(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n")
        later = fx.commit_file(repo, "later.txt", "x\n", "later\n")
        seen = _stub_recover(monkeypatch, repo)
        assert _recover(repo, later) == 2
        assert "not the merge that flipped" in capsys.readouterr().out and "close" not in seen

    def test_refuses_an_unresolvable_sha(self, tmp_path, monkeypatch):
        repo, _ = _merged(tmp_path, monkeypatch, message="feat\n")
        assert _recover(repo, "deadbeef") == 2

    def test_never_acts_on_a_rec_the_plan_does_not_bundle(self, tmp_path, monkeypatch):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1, rec-2, rec-77\n")
        _stub_recover(monkeypatch, repo)
        assert _recover(repo, sha) == 2

    def test_reader_failure_exits_two(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n")
        _stub_recover(monkeypatch, repo)

        def boom(ids):
            raise RuntimeError("reader down")

        monkeypatch.setattr(ms, "_fetch_rows", boom)
        assert _recover(repo, sha) == 2
        assert "RECOVER rec reader unavailable: reader down" in capsys.readouterr().out

    def test_worktree_failure_exits_two(self, tmp_path, monkeypatch, capsys):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n")
        _stub_recover(monkeypatch, repo)

        @contextlib.contextmanager
        def broken(ref, root):
            raise GraduationError("git worktree add failed")
            yield  # pragma: no cover

        monkeypatch.setattr("scripts.verification_graduation.git_worktree", broken)
        assert _recover(repo, sha) == 2
        assert "RECOVER worktree failed: git worktree add failed" in capsys.readouterr().out


def _worktrees(repo: Path) -> list[str]:
    return [line for line in fx.git(repo, "worktree", "list").splitlines() if line.strip()]


class TestRecoverRealWorktree:
    """The unstubbed git_worktree: bin/venv-python must resolve inside it, and it must be gone afterwards."""

    def _setup(self, tmp_path, monkeypatch):
        repo, sha = _merged(tmp_path, monkeypatch, message="feat\n")
        fx.install_venv_python(repo)
        fx.git(repo, "add", "-A")
        fx.git(repo, "commit", "-m", "tooling")
        return repo, fx.git(repo, "rev-parse", "HEAD")

    def test_venv_python_resolves_in_the_worktree_and_it_is_removed(self, tmp_path, monkeypatch):
        repo, head = self._setup(tmp_path, monkeypatch)
        seen: dict = {}

        def census(ids, rows=None, *, repo_root, **_):
            run = subprocess.run(
                [str(repo_root / "bin" / "venv-python"), "-c", "print(42)"], cwd=repo_root, capture_output=True, text=True
            )
            seen["out"] = (run.returncode, run.stdout.strip())
            seen["venv"] = (repo_root / ".venv").resolve() == (repo / ".venv").resolve()
            seen["during"] = len(_worktrees(repo))
            return []

        monkeypatch.setattr(ms, "_fetch_rows", lambda ids: [])
        monkeypatch.setattr(acceptance, "census", census)
        monkeypatch.setattr(acceptance, "evaluate", lambda entries, root, sha: {"records": []})
        monkeypatch.setattr(ms, "_readback_state", lambda slug, s, root: ([], ["rec-2"], [], True))
        monkeypatch.setattr("scripts.checks._common._plan_declared_at_ref", lambda rel, root, ref: not ref.endswith("^"))
        with patch("scripts.ops_portal.ci_rca_lifecycle.close_recs_from_trailer", return_value=0):
            assert _recover(repo, head) == 0
        assert seen["out"] == (0, "42") and seen["venv"] is True and seen["during"] == 2
        assert len(_worktrees(repo)) == 1

    def test_worktree_is_removed_when_the_body_raises(self, tmp_path, monkeypatch):
        repo, head = self._setup(tmp_path, monkeypatch)

        def census(ids, rows=None, *, repo_root, **_):
            raise RuntimeError("probe blew up")

        monkeypatch.setattr(ms, "_fetch_rows", lambda ids: [])
        monkeypatch.setattr(acceptance, "census", census)
        monkeypatch.setattr(ms, "_readback_state", lambda slug, s, root: ([], ["rec-2"], [], True))
        monkeypatch.setattr("scripts.checks._common._plan_declared_at_ref", lambda rel, root, ref: not ref.endswith("^"))
        with pytest.raises(RuntimeError, match="probe blew up"):
            _recover(repo, head)
        assert len(_worktrees(repo)) == 1


def test_main_reads_sys_argv(tmp_path, monkeypatch, capsys):
    repo, sha = _merged(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["merge_step", "readback", "--slug", SLUG, "--root", str(repo), "--sha", sha])
    assert ms.main() == 0
