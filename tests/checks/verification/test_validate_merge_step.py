"""Tests for validate_merge_step (the (T) trailer leg and the (A) approved-text leg). Fixture repos only; every
case clears GITHUB_EVENT_NAME / GITHUB_EVENT_BEFORE so a push-event CI job sees the same results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.checks import _common, registry
from scripts.checks.verification import _implementation_merge as im
from scripts.checks.verification import validate_merge_step as module
from scripts.checks.verification.validate_merge_step import validate_merge_step
from scripts.roadmap import approved_text as at
from scripts.roadmap import approved_text_github, approved_text_provenance
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
PLAN = fx.plan_rel(SLUG)
BUNDLED = ["rec-1", "rec-2"]
BASE_DOC = "alpha beta gamma delta\n"
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}


APPROVED = approved_text_provenance.Verdict(approved_text_provenance.APPROVED, "run 1 approver operator-fixture")


@pytest.fixture(autouse=True)
def _approved_provenance(monkeypatch):
    """pr-validate exports the event path and the token to the whole step; every node but TestProvenanceLeg
    reads an approved sidecar and never reaches the network."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("APPROVED_TEXT_GH_TOKEN", raising=False)
    monkeypatch.setattr(
        module, "_sidecar_provenance", lambda slug, sidecar, root, in_push, deadline: (APPROVED, "enforce", None)
    )


def _run(repo: Path, **kwargs) -> tuple[list[str], str]:
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base=None, **kwargs)
    return failed, ""


def test_trailer_in_a_branch_commit_passes(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED)
    failed, _ = _run(repo)
    assert failed == []
    assert "PASS: 1 declared plan(s) examined" in capsys.readouterr().out


def test_trailer_only_in_pr_body_fails(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat: no trailer in any commit\n")
    failed, _ = _run(repo)
    assert (
        len(failed) == 1 and "[] != bundled_recommendations" in failed[0] and "add a commit whose message carries" in failed[0]
    )


def test_subset_fails_with_the_add_remedy(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "add a commit" in failed[0] and "force-with-lease" not in failed[0]


def test_superset_fails_with_the_rewrite_remedy(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1, rec-2, rec-3\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "force-with-lease" in failed[0] and "add a commit" not in failed[0]


def test_trailer_union_over_several_commits_passes(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1\n")
    fx.commit_file(repo, "more.txt", "x\n", "fix-up\n\nResolves: rec-2\n")
    assert _run(repo)[0] == []


def test_uncommitted_declaration_prints_pending_and_passes(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False, message="feat\n")
    fx.write_plan(repo, SLUG, BUNDLED, True)
    failed, _ = _run(repo)
    assert failed == []
    assert "PENDING trailer: declaration not committed" in capsys.readouterr().out


def test_push_context_skips_the_trailer_leg(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat: squashed\n")
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base="abc123")
    assert failed == []


def test_undeclared_plan_is_not_examined(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False, message="feat\n")
    assert _run(repo)[0] == []
    assert "PASS: 0 declared plan(s) examined" in capsys.readouterr().out


def test_empty_bundle_needs_no_trailer(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
    assert _run(repo)[0] == []


def test_default_seams_derive_root_base_and_changed_files(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n")
    failed: list[str] = []
    validate_merge_step(failed, root=repo)
    assert len(failed) == 1 and "bundled_recommendations" in failed[0]


def _sidecar_repo(tmp_path, monkeypatch, *, block=BLOCK, head_doc="alpha BETA GAMMA delta\n") -> Path:
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], False)
    fx.write(repo, "doc.md", BASE_DOC)
    fx.write_sidecar(repo, SLUG, [block])
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    fx.branch(repo)
    fx.write_plan(repo, SLUG, [], True)
    fx.commit_file(repo, "doc.md", head_doc, "feat\n")
    return repo


def test_exact_sidecar_passes(tmp_path, monkeypatch):
    assert _run(_sidecar_repo(tmp_path, monkeypatch))[0] == []


def test_word_mismatch_fails(tmp_path, monkeypatch):
    repo = _sidecar_repo(tmp_path, monkeypatch, head_doc="alpha BETA GAMMA delta extra\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "approved text" in failed[0] and "doc.md" in failed[0]


@pytest.mark.parametrize("edit", ["edit", "delete"])
def test_build_may_not_edit_or_delete_the_sidecar(tmp_path, monkeypatch, edit):
    repo = _sidecar_repo(tmp_path, monkeypatch)
    if edit == "edit":
        fx.write_sidecar(repo, SLUG, [{**BLOCK, "after": "BETA GAMMA!"}])
    else:
        (repo / at.sidecar_rel(SLUG)).unlink()
    failed, _ = _run(repo)
    assert len(failed) == 1 and f"sidecar {'changed' if edit == 'edit' else 'deleted'} on the branch" in failed[0]


def test_build_may_not_add_its_own_sidecar(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
    fx.write_sidecar(repo, SLUG, [BLOCK])
    failed, _ = _run(repo)
    assert len(failed) == 1 and "sidecar added on the branch" in failed[0]


def test_unparseable_sidecar_at_base_fails_loud(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], False)
    fx.write(repo, at.sidecar_rel(SLUG), "schema_version: [1\n")
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    fx.branch(repo)
    fx.write_plan(repo, SLUG, [], True)
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "feat")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "sidecar does not parse" in failed[0]


def test_real_tree_run_passes():
    if not _common.origin_main_reachable(_common.ROOT):
        pytest.skip("env: origin/main not reachable")
    failed: list[str] = []
    validate_merge_step(failed)
    assert failed == []


def _late_merge(repo) -> list[str]:
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[lc.PLAN], push_base=None)
    return failed


def test_merge_step_passes_a_closure_only_branch_and_names_x(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo)
    assert _late_merge(repo) == []
    assert "LATE-CLOSURE" in capsys.readouterr().out
    assert x in im.late_closure_route(lc.PLAN, repo, "main", "HEAD").sha


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message": "closure\n\nResolves: rec-1\n"},
        {"extra_files": {"src/more.py": "y = 1\n"}},
        {"plan_extra": {"intent": "edited"}},
    ],
)
def test_merge_step_fails_as_today_without_a_route(tmp_path, monkeypatch, kwargs, capsys):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, **kwargs)
    failed = _late_merge(repo)
    if kwargs.get("message"):
        assert failed == []
    else:
        assert len(failed) == 1 and "!= bundled_recommendations" in failed[0] and "add a commit whose message" in failed[0]
    assert "LATE-CLOSURE" not in capsys.readouterr().out


def test_merge_step_sidecar_plan_fails_leg_t_as_today(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    assert any("!= bundled_recommendations" in f for f in _late_merge(repo))


def test_merge_step_fails_on_competing_anchors(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    fx.write(repo, lc.SRC, "x = 2\n")
    second = lc.commit_all(repo, "again\n\nResolves: rec-1\n")
    lc.closure_branch(repo)
    failed = _late_merge(repo)
    assert len(failed) == 1 and "late closure" in failed[0] and second in failed[0]


class TestProvenanceLeg:
    """rec-4769: the base sidecar needs the operator's GitHub approval (or a grandfather) before the build passes."""

    @staticmethod
    def _stub(monkeypatch, verdict, mode, info=None):
        monkeypatch.setattr(
            module, "_sidecar_provenance", lambda slug, sidecar, root, in_push, deadline: (verdict, mode, info)
        )

    @pytest.mark.parametrize("kind", [approved_text_provenance.MISSING, approved_text_provenance.UNVERIFIED])
    def test_enforce_fails_a_missing_or_unreadable_approval(self, tmp_path, monkeypatch, kind):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        self._stub(monkeypatch, approved_text_provenance.Verdict(kind, "why"), "enforce")
        failed, _ = _run(repo)
        assert len(failed) == 1 and f"approval {kind} (why)" in failed[0] and "approved-text-approval.yml" in failed[0]

    def test_remedy_names_the_pr_head_sha_from_the_event_payload(self, tmp_path, monkeypatch):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        payload = tmp_path / "event.json"
        payload.write_text(json.dumps({"pull_request": {"head": {"sha": "f" * 40}}}), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
        self._stub(monkeypatch, approved_text_provenance.Verdict(approved_text_provenance.MISSING), "enforce")
        failed, _ = _run(repo)
        assert f"sha={'f' * 40}" in failed[0]

    def test_remedy_falls_back_to_head_without_an_event_payload(self, tmp_path, monkeypatch):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        self._stub(monkeypatch, approved_text_provenance.Verdict(approved_text_provenance.MISSING), "enforce")
        failed, _ = _run(repo)
        assert f"sha={fx.git(repo, 'rev-parse', 'HEAD')}" in failed[0]

    @pytest.mark.parametrize("kind", [approved_text_provenance.MISSING, approved_text_provenance.UNVERIFIED])
    def test_advisory_prints_and_never_reads_as_a_pass(self, tmp_path, monkeypatch, capsys, kind):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        self._stub(monkeypatch, approved_text_provenance.Verdict(kind, "why"), "advisory")
        failed, _ = _run(repo)
        out = capsys.readouterr().out
        assert failed == [] and f"{kind} (advisory)" in out and "PASS" not in out

    def test_a_grandfathered_sidecar_prints_the_informational_lookup_line(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        info = "lookup (informational): MISSING auth=token"
        self._stub(monkeypatch, approved_text_provenance.Verdict(approved_text_provenance.GRANDFATHERED), "enforce", info)
        failed, _ = _run(repo)
        out = capsys.readouterr().out
        assert failed == [] and info in out and "PASS: 1 declared plan(s) examined" in out

    def test_a_push_skips_the_lookup_and_still_declares_examined(self, tmp_path, monkeypatch, capsys):
        repo = _sidecar_repo(tmp_path, monkeypatch)
        monkeypatch.undo()
        fx.clear_ci_env(monkeypatch)

        def refuse(*args, **kwargs):
            raise AssertionError("the lookup must not run on a push")

        monkeypatch.setattr(approved_text_github, "http_get", refuse)
        declared: list[tuple] = []
        monkeypatch.setattr(registry, "examined", lambda n, unit="": declared.append((n, unit)))
        failed: list[str] = []
        validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base="abc123")
        assert failed == [] and declared == [(1, "plans")]
        assert "SKIP: approval lookup (on main)" in capsys.readouterr().out
