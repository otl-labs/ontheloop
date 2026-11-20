"""Tests for scripts/session/merge_step_landed.py and the premerge/readback/recover paths of a sidecar plan whose
approved words landed in an earlier merge (rec-5057). Fixture repos with a local bare origin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.roadmap import approved_text as at
from scripts.session import merge_step as ms
from scripts.session import merge_step_landed as landed_mod
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx


def _premerge(repo: Path, tmp_path: Path, message: str = "closure\n") -> int:
    msg = fx.message_file(tmp_path, message)
    return ms.main(["premerge", "--slug", lc.SLUG, "--root", str(repo), "--message-file", str(msg)])


def _rows(x: str) -> list[dict]:
    return [{"id": "rec-1", "status": "closed", "resolution": f"Auto-closed: merge commit {x} (run: 1)"}]


def _landed_closure(tmp_path, monkeypatch, **kwargs) -> tuple[Path, str]:
    repo, x = lc.build_main(tmp_path, monkeypatch, sidecar_landed=True, **kwargs)
    lc.closure_branch(repo, doc_scope=True)
    return repo, x


def test_premerge_passes_a_landed_sidecar_plan_and_prints_landed(tmp_path, monkeypatch, capsys):
    repo, x = _landed_closure(tmp_path, monkeypatch, later_edit=False)
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path) == 0
    out = capsys.readouterr().out
    assert f"PREMERGE APPROVED-TEXT LANDED doc.md {x}" in out and "STALE BASE" not in out
    assert f"PREMERGE LATE-CLOSURE OK recs ['rec-1'] closed by {x}" in out


def test_premerge_fails_a_sidecar_plan_whose_words_never_landed(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path) == 1
    out = capsys.readouterr().out
    assert "PREMERGE APPROVED-TEXT MISMATCH doc.md" in out and "LANDED" not in out


def test_premerge_reports_a_stale_base_when_origin_main_resolves_no_landing(tmp_path, monkeypatch, capsys):
    repo, x = _landed_closure(tmp_path, monkeypatch)
    fx.git(repo, "checkout", "main")
    fx.commit_file(repo, lc.DOC, lc.DOC_APPROVED + "edited on main\n", "later edit on main")
    fx.push_main(repo)
    fx.git(repo, "checkout", "closure")
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path) == 1
    out = capsys.readouterr().out
    assert f"PREMERGE APPROVED-TEXT LANDED doc.md {x}" in out and "PREMERGE STALE BASE doc.md: rebase and re-run CI" in out


def _merged_closure(tmp_path, monkeypatch) -> tuple[Path, str, str]:
    repo, x = _landed_closure(tmp_path, monkeypatch)
    closure = fx.git(repo, "rev-parse", "HEAD")
    fx.git(repo, "checkout", "main")
    fx.git(repo, "merge", "--ff-only", "closure")
    fx.push_main(repo)
    return repo, x, closure


def test_readback_of_a_sidecar_plans_closure_prints_late_closure(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert ms.main(["readback", "--slug", lc.SLUG, "--root", str(repo), "--sha", closure]) == 0
    assert f"late-closure satisfied-by={x}" in capsys.readouterr().out


def test_recover_refuses_a_landed_sidecar_plans_closure_merge(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert ms.main(["recover", "--slug", lc.SLUG, "--root", str(repo), "--sha", closure]) == 2
    assert f"RECOVER refused: late closure, recs closed by {x}" in capsys.readouterr().out


def _sidecar(repo: Path):
    return at.load_sidecar(lc.SLUG, repo)


def test_print_landed_names_each_file_and_sha(capsys):
    landed_mod.print_landed({"a.md": "1" * 40, "b.md": "2" * 40})
    assert capsys.readouterr().out.splitlines() == [
        f"PREMERGE APPROVED-TEXT LANDED a.md {'1' * 40}",
        f"PREMERGE APPROVED-TEXT LANDED b.md {'2' * 40}",
    ]


def test_unlanded_drops_only_landed_files_blocks(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    sidecar = _sidecar(repo)
    assert landed_mod.unlanded(sidecar, {}).blocks == sidecar.blocks
    assert landed_mod.unlanded(sidecar, {"doc.md": "x"}).blocks == []


def test_report_stale_is_true_when_nothing_landed(tmp_path, monkeypatch, capsys):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    assert landed_mod.report_stale(_sidecar(repo), lc.SLUG, repo, {}) is True
    assert capsys.readouterr().out == ""


def test_report_stale_compares_origin_main_resolution(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch, sidecar_landed=True)
    fx.git(repo, "fetch", "origin", "main")
    assert landed_mod.report_stale(_sidecar(repo), lc.SLUG, repo, {"doc.md": x}) is True
    assert landed_mod.report_stale(_sidecar(repo), lc.SLUG, repo, {"doc.md": "0" * 40}) is False
    assert "PREMERGE STALE BASE doc.md" in capsys.readouterr().out
