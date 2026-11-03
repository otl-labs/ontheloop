"""merge_step premerge / readback / recover on the late-closure route, with a stubbed rec reader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.session import merge_step as ms
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
PLAN = fx.plan_rel(SLUG)
BUNDLED = ["rec-1"]
SHARD_ID = "chk-a"
SHARD = f"config/agent/verification_registry/entries/{SHARD_ID}.yaml"
SRC = "src/impl.py"


def plan_doc(declared: bool | None, bundled: list[str] | None = None, extra: dict | None = None) -> dict:
    doc = {
        "slug": SLUG,
        "bundled_recommendations": BUNDLED if bundled is None else bundled,
        "scope": [{"file": SRC}, {"file": PLAN}],
        "verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": SHARD_ID}],
    }
    if declared is not None:
        doc["implementation_declared"] = declared
    return doc | (extra or {})


def write_plan_doc(repo: Path, doc: dict) -> None:
    fx.write(repo, PLAN, yaml.safe_dump(doc, sort_keys=False))


def shard_row(check_id: str = SHARD_ID, path: str = SRC, slug: str = SLUG) -> dict:
    return {
        "check_id": check_id,
        "primitive_slot": "file_presence",
        "guard_target": path,
        "plan_slug": slug,
        "graduated_at": "2026-11-03",
        "check_spec": {"path": path, "mode": "exists"},
    }


def shard_text(check_id: str = SHARD_ID, path: str = SRC) -> str:
    return yaml.safe_dump(shard_row(check_id, path), sort_keys=False)


def commit_all(repo: Path, message: str) -> str:
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", message)
    return fx.git(repo, "rev-parse", "HEAD")


def build_main(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
    *,
    bundled: list[str] | None = None,
    impl_message: str | None = "feat\n\nResolves: rec-1\n",
    impl_file: str = SRC,
    impl_declared: bool | None = None,
    sidecar: bool = False,
) -> tuple[Path, str | None]:
    """Main = seed, plan commit (undeclared), implementation commit X. Returns (repo, X sha or None)."""
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    write_plan_doc(repo, plan_doc(False, bundled))
    fx.write(repo, "config/agent/verification_registry/entries/chk-seed.yaml", shard_text("chk-seed", "README.md"))
    if sidecar:
        fx.write_sidecar(repo, SLUG, [{"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}])
    commit_all(repo, "plan")
    sha = None
    if impl_message is not None:
        fx.write(repo, impl_file, "x = 1\n")
        if impl_declared is not None:
            write_plan_doc(repo, plan_doc(impl_declared, bundled))
        sha = commit_all(repo, impl_message)
    fx.push_main(repo)
    return repo, sha


def closure_branch(
    repo: Path,
    *,
    shard: str = SHARD_ID,
    extra_files: dict[str, str] | None = None,
    plan_extra: dict | None = None,
    message: str = "closure\n",
    bundled: list[str] | None = None,
    commit: bool = True,
) -> str | None:
    fx.branch(repo, "closure")
    write_plan_doc(repo, plan_doc(True, bundled, plan_extra))
    fx.write(repo, f"config/agent/verification_registry/entries/{shard}.yaml", shard_text(shard))
    for rel, text in (extra_files or {}).items():
        fx.write(repo, rel, text)
    return commit_all(repo, message) if commit else None


def _premerge(repo: Path, tmp_path: Path, message: str = "closure\n") -> int:
    msg = fx.message_file(tmp_path, message)
    return ms.main(["premerge", "--slug", SLUG, "--root", str(repo), "--message-file", str(msg)])


def _rows(x: str, status: str = "closed", resolution: str | None = None) -> list[dict]:
    return [{"id": "rec-1", "status": status, "resolution": resolution or f"Auto-closed: merge commit {x} (run: 1)"}]


def _closure(tmp_path, monkeypatch, **kwargs) -> tuple[Path, str]:
    repo, x = build_main(tmp_path, monkeypatch, **kwargs)
    closure_branch(repo)
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
    return ms.main(["readback", "--slug", SLUG, "--root", str(repo), "--sha", sha])


def test_readback_ok_for_the_closure_merge_and_for_x(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert _readback(repo, closure) == 0
    assert f"late-closure satisfied-by={x}" in capsys.readouterr().out
    assert _readback(repo, x) == 0
    assert "late-closed" in capsys.readouterr().out


def test_readback_of_x_before_the_plan_is_declared_keeps_the_unexpected_line(tmp_path, monkeypatch, capsys):
    repo, x = build_main(tmp_path, monkeypatch)
    assert _readback(repo, x) == 1
    assert "READBACK UNEXPECTED" in capsys.readouterr().out


def test_readback_of_a_sidecar_plans_closure_is_not_ok(tmp_path, monkeypatch, capsys):
    repo, _ = build_main(tmp_path, monkeypatch, sidecar=True)
    closure_branch(repo)
    closure = fx.git(repo, "rev-parse", "HEAD")
    assert _readback(repo, closure) == 1
    assert "READBACK MISSING" in capsys.readouterr().out


def test_recover_refuses_a_closure_merge(tmp_path, monkeypatch, capsys):
    repo, x, closure = _merged_closure(tmp_path, monkeypatch)
    assert ms.main(["recover", "--slug", SLUG, "--root", str(repo), "--sha", closure]) == 2
    assert f"RECOVER refused: late closure, recs closed by {x}" in capsys.readouterr().out


def _ambiguous(tmp_path, monkeypatch) -> tuple[Path, str]:
    repo, x = build_main(tmp_path, monkeypatch)
    fx.write(repo, SRC, "x = 2\n")
    commit_all(repo, "again\n\nResolves: rec-1\n")
    fx.push_main(repo)
    closure_branch(repo)
    return repo, x


def test_premerge_fails_on_competing_anchors(tmp_path, monkeypatch, capsys):
    repo, _ = _ambiguous(tmp_path, monkeypatch)
    assert _premerge(repo, tmp_path) == 1
    assert "PREMERGE LATE-CLOSURE ERROR" in capsys.readouterr().out


def test_readback_of_x_fails_on_competing_anchors(tmp_path, monkeypatch, capsys):
    repo, x = _ambiguous(tmp_path, monkeypatch)
    fx.git(repo, "checkout", "main")
    fx.git(repo, "merge", "--ff-only", "closure")
    fx.push_main(repo)
    assert _readback(repo, x) == 1
    assert "READBACK LATE-CLOSURE ERROR" in capsys.readouterr().out


def test_recover_does_not_refuse_as_late_on_competing_anchors(tmp_path, monkeypatch, capsys):
    repo, _ = _ambiguous(tmp_path, monkeypatch)
    closure = fx.git(repo, "rev-parse", "HEAD")
    with patch.object(ms, "_fetch_rows", side_effect=RuntimeError("no creds")):
        assert ms.main(["recover", "--slug", SLUG, "--root", str(repo), "--sha", closure]) == 2
    out = capsys.readouterr().out
    assert "RECOVER refused: late closure" not in out and "rec reader unavailable" in out
