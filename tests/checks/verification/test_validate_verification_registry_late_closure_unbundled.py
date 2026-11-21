"""Registry differential for a late closure of a plan with no bundled recs (rec-5470): the closure shard is admitted
against the parent of the commit that landed the plan's sidecar words."""

from __future__ import annotations

from scripts.checks.verification.validate_verification_registry import validate_verification_registry
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx

_IMPL = "feat: impl\n"


def _registry(repo) -> list[str]:
    failed: list[str] = []
    validate_verification_registry(failed, root=repo)
    return failed


def _build(tmp_path, monkeypatch, **kwargs):
    return lc.build_main(tmp_path, monkeypatch, bundled=[], impl_message=_IMPL, **kwargs)


def test_unbundled_sidecar_plan_shard_admitted_against_landing_parent(tmp_path, monkeypatch, capsys):
    repo, x = _build(tmp_path, monkeypatch, sidecar_landed=True)
    lc.closure_branch(repo, bundled=[], doc_scope=True)
    assert _registry(repo) == []
    out = capsys.readouterr().out
    assert f"admitted: {lc.SHARD_ID}" in out and fx.git(repo, "rev-parse", f"{x}^") in out


def test_unbundled_sidecar_plan_admitted_after_later_edit(tmp_path, monkeypatch, capsys):
    repo, x = _build(tmp_path, monkeypatch, sidecar_landed=True, later_edit=True)
    lc.closure_branch(repo, bundled=[], doc_scope=True)
    assert _registry(repo) == []
    out = capsys.readouterr().out
    assert f"admitted: {lc.SHARD_ID}" in out and fx.git(repo, "rev-parse", f"{x}^") in out


def test_unbundled_uncommitted_closure_prints_pending(tmp_path, monkeypatch, capsys):
    repo, _ = _build(tmp_path, monkeypatch, sidecar_landed=True)
    lc.closure_branch(repo, bundled=[], doc_scope=True, commit=False)
    assert _registry(repo) == []
    assert "PENDING" in capsys.readouterr().out


def test_unbundled_plan_without_sidecar_stays_tautological(tmp_path, monkeypatch):
    repo, _ = _build(tmp_path, monkeypatch)
    lc.closure_branch(repo, bundled=[])
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0]


def test_unbundled_sidecar_plan_whose_words_never_landed_stays_tautological(tmp_path, monkeypatch):
    repo, _ = _build(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo, bundled=[])
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0]
