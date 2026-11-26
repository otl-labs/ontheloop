"""The merge gate's scout-input predicate (rec-5654, Decision 287): a plan whose scout inputs main moved is refused."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.roadmap import _plan_rescout_barrier, _plan_rescout_wiring
from scripts.roadmap import plan_fingerprint
from scripts.session import merge_scout_inputs as msi
from tests.fixtures import scout_inputs_repos as fx
from tests.fixtures.merge_overlap_repos import REAL_ROOT, git

PLAN = "docs/plans/PLAN-demo.yaml"
SIDECAR = "docs/plans/approved/demo.yaml"
_RECORDING = {"on": False, "opened": set()}


def _audit(event: str, args: tuple) -> None:
    if _RECORDING["on"] and event == "open" and args and isinstance(args[0], str):
        _RECORDING["opened"].add(args[0])


sys.addaudithook(_audit)


def _judge(repo: Path, **kwargs: object) -> msi.Verdict:
    return msi.judge(repo, "pr", "main", fx.merge_base(repo), **kwargs)  # type: ignore[arg-type]


def _decisions(repo: Path, old: str, new: str) -> None:
    path = repo / "docs/DECISIONS.md"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _main_moves(repo: Path, old: str = "body one", new: str = "body 1") -> None:
    fx.on_main(repo)
    _decisions(repo, old, new)
    fx.commit(repo, "main moves a decision")
    git(repo, "checkout", "-q", "pr")


def _plan_pr(tmp_path: Path, **overrides: object) -> Path:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, PLAN, fx.plan_text(**overrides))
    fx.commit(repo, "add plan")
    return repo


def test_plan_pr_is_refused_when_main_moved_a_cited_decision(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    verdict = _judge(repo)
    assert verdict.kind == msi.STALE and verdict.path == "computed" and verdict.plans == ("PLAN-demo.yaml",)


def test_plan_pr_is_clear_when_main_moved_an_uncited_decision(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo, "body two", "body 2")
    verdict = _judge(repo)
    assert verdict.kind == msi.CLEAR and verdict.path == "computed" and verdict.candidates == 1


def test_code_pr_is_clear_when_main_moved_a_cited_decision(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, "src/b.py", "y = 2\n")
    fx.commit(repo, "code")
    _main_moves(repo)
    verdict = _judge(repo, timeout=0.0)
    assert (verdict.kind, verdict.path) == (msi.CLEAR, "no-plan")


def test_build_pr_with_unchanged_plan_inputs_is_clear(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "plan on main")
    fx.pr_branch(repo)
    fx.write(repo, PLAN, fx.plan_text(context=["bookkeeping edit"]))
    fx.commit(repo, "build edits the plan")
    _main_moves(repo)
    verdict = _judge(repo)
    assert (verdict.kind, verdict.path, verdict.candidates) == (msi.CLEAR, "computed", 1)


def test_sidecar_only_change_is_not_a_candidate(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, SIDECAR, "schema_version: 1\n")
    fx.commit(repo, "sidecar only")
    _main_moves(repo)
    assert (_judge(repo).kind, _judge(repo).path) == (msi.CLEAR, "no-plan")


def test_plan_below_schema_six_is_not_a_candidate(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path, schema_version=5)
    _main_moves(repo)
    verdict = _judge(repo)
    assert (verdict.kind, verdict.path, verdict.candidates) == (msi.CLEAR, "no-candidate", 0)


def test_deleted_plan_is_skipped(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "plan on main")
    fx.pr_branch(repo)
    git(repo, "rm", "-q", PLAN)
    fx.commit(repo, "delete")
    _main_moves(repo)
    assert _judge(repo).path == "no-plan"


def test_type_changed_plan_is_skipped(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "plan on main")
    fx.pr_branch(repo)
    (repo / PLAN).unlink()
    (repo / PLAN).symlink_to("../../src/a.py")
    fx.commit(repo, "type change")
    _main_moves(repo)
    assert _judge(repo).path == "no-plan"


def test_quoted_plan_path_is_skipped(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, "docs/plans/PLAN-café.yaml", fx.plan_text(slug="cafe"))
    fx.commit(repo, "non-ascii plan")
    _main_moves(repo)
    assert _judge(repo).path == "no-plan"


def test_candidates_equal_the_barrier_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = fx.make_repo(tmp_path)
    for name in ("m", "d", "t"):
        fx.write(repo, f"docs/plans/PLAN-{name}.yaml", fx.plan_text(slug=name))
    fx.commit(repo, "plans on main")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    fx.pr_branch(repo)
    fx.write(repo, "docs/plans/PLAN-m.yaml", fx.plan_text(slug="m", context=["x"]))
    fx.write(repo, "docs/plans/PLAN-a.yaml", fx.plan_text(slug="a"))
    fx.write(repo, "docs/plans/PLAN-café.yaml", fx.plan_text(slug="cafe"))
    git(repo, "rm", "-q", "docs/plans/PLAN-d.yaml")
    (repo / "docs/plans/PLAN-t.yaml").unlink()
    (repo / "docs/plans/PLAN-t.yaml").symlink_to("../../src/a.py")
    fx.commit(repo, "mixed")
    for var in ("GITHUB_EVENT_NAME", "GITHUB_BASE_REF", "GITHUB_EVENT_BEFORE"):
        monkeypatch.delenv(var, raising=False)
    barrier = {
        (s, p) for s, p in _common.get_status_aware_diff(repo) if s in ("A", "M", "??") and _common.PLAN_PATH_RE.match(p)
    }
    mine = {(s, p) for s, p in msi._plan_candidates(repo, fx.merge_base(repo), "pr")}
    assert mine == barrier == {("M", "docs/plans/PLAN-m.yaml"), ("A", "docs/plans/PLAN-a.yaml")}
    assert callable(_plan_rescout_wiring.run)


def test_non_plan_content_at_a_plan_path_clears_when_main_is_unchanged(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, "docs/plans/PLAN-x.yaml", "not: [a plan\n")
    fx.commit(repo, "not a plan")
    fx.on_main(repo)
    fx.write(repo, "src/other.py", "z = 1\n")
    fx.commit(repo, "main moves code only")
    git(repo, "checkout", "-q", "pr")
    verdict = _judge(repo)
    assert (verdict.kind, verdict.path) == (msi.CLEAR, "main-unchanged")


def test_branch_updated_with_main_is_clear(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    git(repo, "merge", "-q", "--no-edit", "main")
    verdict = msi.judge(repo, "pr", "main", git(repo, "merge-base", "main", "pr"))
    assert (verdict.kind, verdict.path) == (msi.CLEAR, "main-unchanged")


def test_scout_input_changed_on_both_sides_is_judged_merged(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    _decisions(repo, "body two", "body 2")
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "pr edits an uncited decision and adds a plan")
    _main_moves(repo)
    verdict = _judge(repo)
    assert verdict.kind == msi.STALE and "decisions" in verdict.reason


def test_conflicting_scout_input_is_unavailable(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    _decisions(repo, "body one", "body PR")
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "pr")
    _main_moves(repo)
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "rebase_resolve" in verdict.reason and "docs/DECISIONS.md" in verdict.reason


def test_conflict_elsewhere_is_ignored(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, "src/a.py", "x = 'pr'\n")
    fx.write(repo, PLAN, fx.plan_text())
    fx.commit(repo, "pr")
    fx.on_main(repo)
    fx.write(repo, "src/a.py", "x = 'main'\n")
    _decisions(repo, "body one", "body 1")
    fx.commit(repo, "main")
    git(repo, "checkout", "-q", "pr")
    assert _judge(repo).kind == msi.STALE


def test_unparsable_plan_is_unavailable(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.pr_branch(repo)
    fx.write(repo, PLAN, "not: [a plan\n")
    fx.commit(repo, "bad plan")
    _main_moves(repo)
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "PLAN-demo.yaml" in verdict.reason


def test_import_error_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)

    def boom(*args: object) -> None:
        raise ImportError("no plan_fingerprint")

    monkeypatch.setattr(msi, "_compare", boom)
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "ImportError" in verdict.reason


def test_unexpected_exception_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    monkeypatch.setattr(msi, "_compare", lambda *a: (_ for _ in ()).throw(RuntimeError("odd")))
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "RuntimeError: odd" in verdict.reason


def test_timeout_is_unavailable(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    verdict = _judge(repo, timeout=0.0)
    assert verdict.kind == msi.UNAVAILABLE and "deadline" in verdict.reason


@pytest.mark.parametrize("code", [2, 129])
def test_merge_tree_failure_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    real = msi._git

    def stub(root: Path, *args: str, **kw: object) -> subprocess.CompletedProcess[bytes]:
        if args and args[0] == "merge-tree":
            return subprocess.CompletedProcess(args, code, b"", b"usage: git merge-tree")
        return real(root, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(msi, "_git", stub)
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "merge-tree" in verdict.reason


def test_refusal_names_the_plan_and_the_moved_key(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    verdict = _judge(repo)
    assert "PLAN-demo.yaml" in verdict.reason and "decisions" in verdict.reason
    assert "PLAN-demo.yaml" in msi.format_line(verdict)


def test_tree_files_suffice_for_the_fingerprint() -> None:
    _RECORDING["opened"].clear()
    _RECORDING["on"] = True
    try:
        plan_fingerprint.load_tree(REAL_ROOT)
    finally:
        _RECORDING["on"] = False
    seen = set()
    for opened in _RECORDING["opened"]:
        if opened.endswith((".py", ".pyc")):
            continue
        try:
            seen.add(Path(opened).resolve().relative_to(REAL_ROOT).as_posix())
        except ValueError:
            continue
    assert seen == set(msi.TREE_FILES)


def test_plan_path_rule_equals_the_barrier() -> None:
    assert msi.PLAN_PATH_RE.pattern == _common.PLAN_PATH_RE.pattern


def test_schema_floor_equals_the_barrier() -> None:
    assert msi.FINGERPRINT_MIN_SCHEMA_VERSION == _plan_rescout_barrier.FINGERPRINT_MIN_SCHEMA_VERSION


def _sidecar_text(after: str) -> str:
    block = {"id": "b1", "file": "docs/DECISIONS.md", "before": "body one", "after": after}
    return yaml.safe_dump({"schema_version": 1, "slug": "demo", "blocks": [block]})


def test_main_changing_only_a_candidate_sidecar_is_computed(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    fx.write(repo, PLAN, fx.plan_text())
    fx.write(repo, SIDECAR, _sidecar_text("body 1"))
    fx.commit(repo, "plan and sidecar on main")
    fx.pr_branch(repo)
    fx.write(repo, PLAN, fx.plan_text(intent="Build the other thing"))
    fx.commit(repo, "pr edits the plan")
    fx.on_main(repo)
    fx.write(repo, SIDECAR, _sidecar_text("body 2"))
    fx.commit(repo, "main edits the sidecar")
    git(repo, "checkout", "-q", "pr")
    verdict = _judge(repo)
    assert verdict.kind == msi.STALE and "approved_blocks" in verdict.reason


def test_main_changing_an_unrelated_plan_takes_the_inputs_unchanged_path(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path)
    fx.on_main(repo)
    fx.write(repo, "docs/plans/PLAN-other.yaml", fx.plan_text(slug="other"))
    fx.commit(repo, "main adds an unrelated plan")
    git(repo, "checkout", "-q", "pr")
    verdict = _judge(repo)
    assert (verdict.kind, verdict.path) == (msi.CLEAR, "inputs-unchanged")


def test_quoted_schema_version_is_a_candidate(tmp_path: Path) -> None:
    repo = _plan_pr(tmp_path, schema_version="7")
    _main_moves(repo)
    assert _judge(repo).kind == msi.STALE


@pytest.mark.parametrize("version", ["x", None, True])
def test_absent_or_non_integral_schema_version_is_unavailable(tmp_path: Path, version: object) -> None:
    repo = _plan_pr(tmp_path, schema_version=version)
    _main_moves(repo)
    verdict = _judge(repo)
    assert verdict.kind == msi.UNAVAILABLE and "PLAN-demo.yaml" in verdict.reason


def test_cli_prints_the_path_taken(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _plan_pr(tmp_path)
    _main_moves(repo)
    code = msi.main(["--head-ref", "pr", "--base-ref", "main", "--root", str(repo)])
    out = capsys.readouterr().out
    assert code == 1 and "path=computed" in out and "candidates=1" in out and "elapsed=" in out


def test_cli_unresolved_merge_base_is_unavailable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = fx.make_repo(tmp_path)
    assert msi.main(["--head-ref", "nope", "--base-ref", "main", "--root", str(repo)]) == 2
    assert "UNAVAILABLE" in capsys.readouterr().out


def _cp(code: int, out: bytes = b"", err: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], code, out, err)


def _stub_git(monkeypatch: pytest.MonkeyPatch, results: dict[str, subprocess.CompletedProcess[bytes]]) -> None:
    monkeypatch.setattr(msi, "_git", lambda root, *args, **kw: results[args[0]])


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("git", 1), OSError("no git")])
def test_git_failure_to_run_is_unavailable(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*a: object, **k: object) -> None:
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(msi._Unavailable):
        msi._git(Path("."), "status", dl=msi._Deadline(5.0))


def test_show_reads_absent_failed_and_present_files(monkeypatch: pytest.MonkeyPatch) -> None:
    dl = msi._Deadline(5.0)
    _stub_git(monkeypatch, {"ls-tree": _cp(1, err=b"bad rev")})
    with pytest.raises(msi._Unavailable, match="ls-tree"):
        msi._show(Path("."), "r", "a.txt", dl, "s")
    _stub_git(monkeypatch, {"ls-tree": _cp(0, b"other\0")})
    assert msi._show(Path("."), "r", "a.txt", dl, "s") is None
    _stub_git(monkeypatch, {"ls-tree": _cp(0, b"a.txt\0"), "show": _cp(128, err=b"gone")})
    with pytest.raises(msi._Unavailable, match="git show"):
        msi._show(Path("."), "r", "a.txt", dl, "s")
    _stub_git(monkeypatch, {"ls-tree": _cp(0, b"a.txt\0"), "show": _cp(0, b"data")})
    assert msi._show(Path("."), "r", "a.txt", dl, "s") == b"data"


def test_git_diff_failures_and_junk_lines_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_git(monkeypatch, {"diff": _cp(2, err=b"bad")})
    with pytest.raises(msi._Unavailable, match="git diff"):
        msi._quiet_diff(Path("."), "m", "main", ["x"], None)
    with pytest.raises(msi._Unavailable, match="git diff"):
        msi._plan_candidates(Path("."), "m", "pr")
    listing = b"junk\nA\tdocs/plans/PLAN-a.yaml\nD\tdocs/plans/PLAN-b.yaml\n"
    _stub_git(monkeypatch, {"diff": _cp(0, listing)})
    assert msi._plan_candidates(Path("."), "m", "pr") == [("A", "docs/plans/PLAN-a.yaml")]


def test_unusable_plan_documents_are_unavailable() -> None:
    with pytest.raises(msi._Unavailable, match="not a mapping"):
        msi._load_plan(b"- a\n", "PLAN-x.yaml")
    with pytest.raises(msi._Unavailable, match="slug"):
        msi._sidecar_rel({"slug": "../x"}, "PLAN-x.yaml")
    with pytest.raises(msi._Unavailable, match="absent from the PR head"):
        msi._must(None, "PLAN-x.yaml")


def test_unusable_merge_tree_output_and_missing_merged_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_git(monkeypatch, {"merge-tree": _cp(0, b"not-a-tree\0")})
    with pytest.raises(msi._Unavailable, match="no tree id"):
        msi._conflicts(Path("."), "m", "pr", "main", msi._Deadline(5.0))
    with pytest.raises(msi._Unavailable, match="absent from the compared tree"):
        msi._read_tree_plan(tmp_path, PLAN, "PLAN-demo.yaml")


def test_modified_plan_absent_at_the_merge_base_is_not_unchanged(tmp_path: Path) -> None:
    repo = fx.make_repo(tmp_path)
    assert (
        msi._unchanged_plan(repo, fx.merge_base_of(repo), PLAN, "PLAN-demo.yaml", None, None, "x", msi._Deadline(5.0)) is False
    )
