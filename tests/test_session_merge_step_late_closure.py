"""merge_step premerge / readback / recover on the late-closure route, with a stubbed rec reader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.session import merge_step as ms
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx


def _premerge(repo: Path, tmp_path: Path, message: str = "closure\n") -> int:
    msg = fx.message_file(tmp_path, message)
    return ms.main(["premerge", "--slug", lc.SLUG, "--root", str(repo), "--message-file", str(msg)])


def _rows(x: str, status: str = "closed", resolution: str | None = None) -> list[dict]:
    return [{"id": "rec-1", "status": status, "resolution": resolution or f"Auto-closed: merge commit {x} (run: 1)"}]


def _closure(tmp_path, monkeypatch, **kwargs) -> tuple[Path, str]:
    repo, x = lc.build_main(tmp_path, monkeypatch, **kwargs)
    lc.closure_branch(repo)
    return repo, x


def test_premerge_passes_when_the_rec_is_closed_by_x(tmp_path, monkeypatch, capsys):
    repo, x = _closure(tmp_path, monkeypatch)
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path) == 0
    assert f"PREMERGE LATE-CLOSURE OK recs ['rec-1'] closed by {x}" in capsys.readouterr().out


def test_premerge_fails_closed_on_an_unavailable_reader(tmp_path, monkeypatch, capsys):
    repo, _ = _closure(tmp_path, monkeypatch)
    with patch.object(ms, "_fetch_rows", side_effect=RuntimeError("no creds")):
        assert _premerge(repo, tmp_path) == 1
    assert "reader unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["missing", "open", "other_sha"])
def test_premerge_fails_on_a_bad_rec_row(tmp_path, monkeypatch, case):
    repo, x = _closure(tmp_path, monkeypatch)
    rows = {"missing": [], "open": _rows(x, status="open"), "other_sha": _rows(x, resolution="merge commit " + "a" * 40)}[case]
    with patch.object(ms, "_fetch_rows", return_value=rows):
        assert _premerge(repo, tmp_path) == 1


def test_premerge_rejects_a_message_with_a_trailer(tmp_path, monkeypatch, capsys):
    repo, x = _closure(tmp_path, monkeypatch)
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path, "closure\n\nResolves: rec-1\n") == 1
    assert "late closure must carry no trailer" in capsys.readouterr().out


def test_sidecar_plan_gets_no_late_premerge_route(tmp_path, monkeypatch, capsys):
    repo, x = _closure(tmp_path, monkeypatch, sidecar=True)
    with patch.object(ms, "_fetch_rows", return_value=_rows(x)):
        assert _premerge(repo, tmp_path) == 1
    out = capsys.readouterr().out
    assert "PREMERGE TRAILER MISMATCH expected=['rec-1'] parsed=[]" in out and "LATE-CLOSURE" not in out


def _merged_closure(tmp_path, monkeypatch) -> tuple[Path, str, str]:
    repo, x = _closure(tmp_path, monkeypatch)
    closure = fx.git(repo, "rev-parse", "HEAD")
    fx.git(repo, "checkout", "main")
    fx.git(repo, "merge", "--ff-only", "closure")
    fx.push_main(repo)
    return repo, x, closure


def _readback(repo: Path, sha: str) -> int:
    return ms.main(["readback", "--slug", lc.SLUG, "--root", str(repo), "--sha", sha])


def test_readback_ok_for_the_closure_merge_and_for_x(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert _readback(repo, closure) == 0
    assert f"late-closure satisfied-by={x}" in capsys.readouterr().out
    assert _readback(repo, x) == 0
    assert "late-closed" in capsys.readouterr().out


def test_readback_of_x_before_the_plan_is_declared_keeps_the_unexpected_line(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    assert _readback(repo, x) == 1
    assert "READBACK UNEXPECTED" in capsys.readouterr().out


def test_readback_of_a_sidecar_plans_closure_is_not_ok(tmp_path, monkeypatch, capsys):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    closure = fx.git(repo, "rev-parse", "HEAD")
    assert _readback(repo, closure) == 1
    assert "READBACK MISSING" in capsys.readouterr().out


def test_recover_refuses_a_closure_merge(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert ms.main(["recover", "--slug", lc.SLUG, "--root", str(repo), "--sha", closure]) == 2
    assert f"RECOVER refused: late closure, recs closed by {x}" in capsys.readouterr().out
