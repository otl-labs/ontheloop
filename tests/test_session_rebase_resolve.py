"""scripts.session.rebase_resolve against real temporary git repos (and one local clone of this repo)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.session import rebase_resolve as rr
from tests.fixtures.merge_step_repos import (
    REAL_ROOT,
    git,
    install_venv_python,
    make_repo,
    plan_text,
    push_main,
    sidecar_text,
    write,
)

IDENT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "commit.gpgsign",
    "GIT_CONFIG_VALUE_0": "false",
}
GEN = (
    "import json, re, pathlib\n"
    "nums = sorted(int(n) for n in re.findall(r'^## Decision (\\d+):', pathlib.Path('docs/DECISIONS.md').read_text(), re.M))\n"
    "pathlib.Path('docs/decisions-index.json').write_text(json.dumps(nums) + '\\n')\n"
)
CONTRACT_SRC = REAL_ROOT / "docs/contracts/mechanical-conflicts.yaml"
SUBJECT = "rebase_resolve: mechanical conflicts"


def block(number: int, title: str) -> str:
    return f"## Decision {number}: {title} (Decided)\n\n**Warehouse ID:** dec-{number:03d}\n\n"


def decisions(*blocks: str) -> str:
    return "# Decisions\n\n" + "".join(blocks) + block(2, "Two") + block(1, "One")


def run(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """rebase_resolve.main() in-process (coverage sees it) with the repo as cwd and a git identity."""
    for key, value in IDENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo)
    capsys.readouterr()
    code = rr.main([])
    return code, capsys.readouterr().out


def make_fixture(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    install_venv_python(repo)
    contract = CONTRACT_SRC.read_text(encoding="utf-8").replace(
        "bin/venv-python -m scripts.decisions_index --write", "bin/venv-python gen_index.py"
    )
    write(repo, "docs/contracts/mechanical-conflicts.yaml", contract)
    write(repo, "gen_index.py", GEN)
    write(repo, "docs/DECISIONS.md", decisions())
    write(repo, "docs/decisions-index.json", "[1, 2]\n")
    write(repo, "docs/DECISIONS_ARCHIVE.md", "# Archive\n")
    write(repo, "config/ci_rca_taxonomy.yaml", "function_to_category:\n  a: x\n")
    write(repo, "docs/contracts/foo.yaml", "contract:\n  id: foo\namendment_log:\n  - old\nother: 1\n")
    write(repo, "config/sloc_budgets.yaml", "a.py: 10\n")
    write(repo, "notes.txt", "one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    push_main(repo)
    return repo


def on_main(repo: Path, files: dict[str, str]) -> None:
    """Advance origin/main from the base commit, then return to a `feature` branch off the base."""
    base = git(repo, "rev-parse", "HEAD")
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "main change")
    push_main(repo)
    git(repo, "checkout", "-q", "-b", "feature", base)


def on_branch(repo: Path, files: dict[str, str], message: str = "branch change") -> None:
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def race(repo: Path) -> None:
    on_main(repo, {"docs/DECISIONS.md": decisions(block(3, "Main")), "docs/decisions-index.json": "[1, 2, 3, 99]\n"})
    on_branch(repo, {"docs/DECISIONS.md": decisions(block(3, "Branch")), "docs/decisions-index.json": "[1, 2, 3]\n"})


def numbers(repo: Path) -> list[int]:
    text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
    return [int(n) for n in re.findall(r"^## Decision (\d+):", text, re.M)]


def test_decision_race_resolves_clean_and_renumbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    race(repo)
    before = git(repo, "rev-list", "--count", "origin/main..HEAD")
    code, out = run(repo, monkeypatch, capsys)
    assert code == 0, out
    assert "RESOLVE CLEAN" in out and "RENUMBER Decision 3 -> 4" in out and "REGENERATED docs/decisions-index.json" in out
    assert numbers(repo) == [4, 3, 2, 1]
    live = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
    assert "## Decision 4: Branch (Decided)\n\n**Warehouse ID:** dec-004" in live
    assert json.loads((repo / "docs/decisions-index.json").read_text(encoding="utf-8")) == [1, 2, 3, 4]
    assert git(repo, "log", "-1", "--format=%s") == SUBJECT
    assert int(git(repo, "rev-list", "--count", "origin/main..HEAD")) == int(before) + 1
    assert git(repo, "status", "--porcelain") == ""


def test_roster_union_main_first_and_amendment_log_branch_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    on_main(
        repo,
        {
            "config/ci_rca_taxonomy.yaml": "function_to_category:\n  main_row: x\n  a: x\n",
            "docs/contracts/foo.yaml": "contract:\n  id: foo\namendment_log:\n  - main\n  - old\nother: 1\n",
        },
    )
    on_branch(
        repo,
        {
            "config/ci_rca_taxonomy.yaml": "function_to_category:\n  branch_row: x\n  a: x\n",
            "docs/contracts/foo.yaml": "contract:\n  id: foo\namendment_log:\n  - branch\n  - old\nother: 1\n",
        },
    )
    code, out = run(repo, monkeypatch, capsys)
    assert code == 0, out
    tax = (repo / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8")
    assert tax.index("main_row") < tax.index("branch_row")
    log = (repo / "docs/contracts/foo.yaml").read_text(encoding="utf-8")
    assert log.index("- branch") < log.index("- main")


def test_derived_file_takes_main_then_is_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    on_main(repo, {"docs/decisions-index.json": "[1, 2, 7]\n"})
    on_branch(repo, {"docs/decisions-index.json": "[1, 2, 8]\n"})
    code, out = run(repo, monkeypatch, capsys)
    assert code == 0, out
    assert "took main's copy" in out and "REGENERATED docs/decisions-index.json" in out
    assert json.loads((repo / "docs/decisions-index.json").read_text(encoding="utf-8")) == [1, 2]


@pytest.mark.parametrize(
    ("rel", "main_text", "branch_text", "reason"),
    [
        ("config/ci_rca_taxonomy.yaml", "function_to_category:\n  a: main\n", "function_to_category:\n  a: branch\n", "pure"),
        ("notes.txt", "main\n", "branch\n", "no declared class"),
        ("config/sloc_budgets.yaml", "a.py: 11\n", "a.py: 12\n", "ratchet registry"),
    ],
)
def test_other_conflicts_abort_and_leave_branch_untouched(
    tmp_path: Path,
    rel: str,
    main_text: str,
    branch_text: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_fixture(tmp_path)
    on_main(repo, {rel: main_text})
    on_branch(repo, {rel: branch_text})
    head = git(repo, "rev-parse", "HEAD")
    code, out = run(repo, monkeypatch, capsys)
    assert code == 2 and f"RESOLVE STOP {rel}" in out and reason in out
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "branch", "--show-current") == "feature"
    assert not (repo / ".git/rebase-merge").exists()


def test_duplicate_key_after_union_is_not_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    on_main(repo, {"config/ci_rca_taxonomy.yaml": "function_to_category:\n  dup: x\n  a: x\n"})
    on_branch(repo, {"config/ci_rca_taxonomy.yaml": "function_to_category:\n  dup: y\n  a: x\n"})
    code, out = run(repo, monkeypatch, capsys)
    assert code == 1 and "PARSE config/ci_rca_taxonomy.yaml" in out and "RESOLVE NOT CLEAN" in out
    assert git(repo, "log", "-1", "--format=%s") != SUBJECT


def test_bare_token_is_not_clean_and_uncommitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    on_main(repo, {"docs/DECISIONS.md": decisions(block(3, "Main"))})
    on_branch(repo, {"docs/DECISIONS.md": decisions(block(3, "Branch")), "extra.md": "the 3 rules\n"})
    code, out = run(repo, monkeypatch, capsys)
    assert code == 1 and "BARE extra.md:1 3" in out and "RESOLVE NOT CLEAN" in out
    assert git(repo, "log", "-1", "--format=%s") != SUBJECT
    assert numbers(repo) == [4, 3, 2, 1]
    assert git(repo, "diff", "--stat") != ""


def test_old_number_in_a_declared_plans_sidecar_is_not_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    write(repo, "docs/plans/PLAN-x.yaml", plan_text("x", [], False))
    write(
        repo,
        "docs/plans/approved/x.yaml",
        sidecar_text("x", [{"id": "n", "file": "notes.txt", "before": "one", "after": "one see Decision 3"}]),
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "plan")
    push_main(repo)
    on_main(repo, {"docs/DECISIONS.md": decisions(block(3, "Main"))})
    on_branch(
        repo,
        {
            "docs/DECISIONS.md": decisions(block(3, "Branch")),
            "notes.txt": "one see Decision 3\n",
            "docs/plans/PLAN-x.yaml": plan_text("x", [], True),
        },
    )
    code, out = run(repo, monkeypatch, capsys)
    assert code == 1 and "APPROVED-TEXT" in out and "RESOLVE NOT CLEAN" in out


def test_dirty_tree_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_fixture(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "notes.txt", "dirty\n")
    code, out = run(repo, monkeypatch, capsys)
    assert code == 2 and "RESOLVE REFUSED" in out


def test_no_conflict_is_a_clean_noop_and_nothing_is_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fixture(tmp_path)
    on_main(repo, {"other_main.txt": "m\n"})
    on_branch(repo, {"other_branch.txt": "b\n"})
    origin_before = git(repo, "ls-remote", "origin")
    code, out = run(repo, monkeypatch, capsys)
    assert code == 0 and "RESOLVE CLEAN" in out
    assert git(repo, "log", "-1", "--format=%s") == "branch change"
    assert git(repo, "ls-remote", "origin") == origin_before


def test_module_is_not_importable_with_side_effects() -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import scripts.session.rebase_resolve"],
        cwd=REAL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0 and done.stdout == ""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env={**os.environ, **IDENT}
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_real_corpus_race_on_a_local_clone(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--local", "-q", str(REAL_ROOT), str(clone)], check=True, capture_output=True)
    watched = [REAL_ROOT / "docs/DECISIONS.md", REAL_ROOT / "docs/decisions-index.json"]
    before = [_sha(p) for p in watched]
    origin_ref = _real_git("rev-parse", "refs/remotes/origin/main", cwd=REAL_ROOT)
    for rel in (
        "scripts/session/rebase_resolve.py",
        "scripts/session/decision_renumber.py",
        "docs/contracts/mechanical-conflicts.yaml",
    ):
        target = clone / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_ROOT / rel, target)
    if _real_git("status", "--porcelain", cwd=clone):
        _real_git("add", "-A", cwd=clone)
        _real_git("commit", "-q", "-m", "test base", cwd=clone)
    base = _real_git("rev-parse", "HEAD", cwd=clone)
    live = clone / "docs/DECISIONS.md"
    text = live.read_text(encoding="utf-8")
    top = max(int(n) for n in re.findall(r"^## Decision (\d+):", text, re.M))
    anchor = f"## Decision {top}:"
    assert anchor in text

    def entry(title: str) -> str:
        return f"## Decision {top + 1}: {title} (Decided)\n\n**Status:** Decided\n\nbody\n\n"

    _real_git("checkout", "-q", "-b", "tmp-main", cwd=clone)
    live.write_text(text.replace(anchor, entry("Main side") + anchor, 1), encoding="utf-8")
    _real_git("commit", "-q", "-am", "main side", cwd=clone)
    _real_git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=clone)
    _real_git("checkout", "-q", "-b", "feature", base, cwd=clone)
    live.write_text(text.replace(anchor, entry("Branch side") + anchor, 1), encoding="utf-8")
    _real_git("commit", "-q", "-am", "branch side", cwd=clone)

    done = subprocess.run(
        [sys.executable, "-m", "scripts.session.rebase_resolve"],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **IDENT, "OTL_VENV_PYTHON": sys.executable},
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "RESOLVE CLEAN" in out, out
    rebased = live.read_text(encoding="utf-8")
    assert f"## Decision {top + 2}: Branch side" in rebased and f"## Decision {top + 1}: Main side" in rebased
    assert rebased.index(f"Decision {top + 2}:") < rebased.index(f"Decision {top + 1}:")
    check = subprocess.run(
        [sys.executable, "-m", "scripts.decisions_index", "--check"],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert [_sha(p) for p in watched] == before
    assert _real_git("rev-parse", "refs/remotes/origin/main", cwd=REAL_ROOT) == origin_ref


def _plan(**kwargs: object) -> rr.Plan:
    return rr.Plan(**kwargs)  # type: ignore[arg-type]


class TestUnits:
    def test_load_classes_reports_a_missing_or_malformed_contract(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="mechanical-conflicts"):
            rr.load_classes(tmp_path)
        write(tmp_path, rr.CONTRACT, "classes: {derived: 1}\n")
        with pytest.raises(ValueError, match="mechanical-conflicts"):
            rr.load_classes(tmp_path)

    def test_unterminated_marker_stops(self) -> None:
        with pytest.raises(rr.Stop, match="unterminated"):
            rr.parse_conflicts("<<<<<<< HEAD\nours\n")

    def test_hunk_outside_the_declared_key_stops(self) -> None:
        text = "other:\n<<<<<<< HEAD\na\n||||||| base\n=======\nb\n>>>>>>> x\n"
        with pytest.raises(rr.Stop, match="outside the amendment_log key"):
            rr.resolve_text(text, "roster_branch_first", "amendment_log")

    def test_hunk_with_no_enclosing_key_stops(self) -> None:
        text = "<<<<<<< HEAD\na\n||||||| base\n=======\nb\n>>>>>>> x\n"
        with pytest.raises(rr.Stop):
            rr.resolve_text(text, "roster", "amendment_log")

    def test_decisions_non_heading_insertion_is_a_roster_union(self) -> None:
        text = "<<<<<<< HEAD\nmain\n||||||| base\n=======\nbranch\n>>>>>>> x\n"
        assert rr.resolve_text(text, "decisions") == ("main\nbranch\n", 1)

    def test_python_roster_with_duplicate_entry_names_is_an_error(self, tmp_path: Path) -> None:
        write(tmp_path, "m.py", 'ENTRIES = (Entry(name="a"), Entry(name="a"), Entry(name="b"))\n')
        write(tmp_path, "ok.py", 'ENTRIES = (Entry(name="a"), Entry(name="b"))\n')
        assert "duplicate Entry name ['a']" in str(rr._roster_error(tmp_path, "m.py"))
        assert rr._roster_error(tmp_path, "ok.py") is None
        assert rr._roster_error(tmp_path, "missing.py") is not None

    def test_regeneration_failures_are_reported(self, tmp_path: Path) -> None:
        plan = _plan(derived={"a.json": "false"})
        out: list[str] = []
        problems = rr._regenerate(tmp_path, ["a.json", "undeclared.json"], plan, out)
        assert len(problems) == 2 and out == []
        assert "no generator declared" in problems[1]

    def test_unparseable_sidecar_is_an_approved_text_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.roadmap import approved_text as at

        def boom(*_a: object, **_k: object) -> None:
            raise at.ApprovedTextError("broken")

        monkeypatch.setattr("scripts.checks._common.resolve_declared_plans", lambda *a, **k: ["docs/plans/PLAN-x.yaml"])
        monkeypatch.setattr(at, "load_sidecar_at", boom)
        monkeypatch.setattr(rr, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "docs/plans/PLAN-x.yaml\n", ""))
        assert rr._approved_text_messages(tmp_path) == ["APPROVED-TEXT x: broken"]


class TestResolveStopUnits:
    def _fake_git(self, monkeypatch: pytest.MonkeyPatch, unmerged: str, checkout_rc: int = 0) -> None:
        def fake(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            if args[:1] == ("diff",):
                return subprocess.CompletedProcess(args, 0, unmerged, "")
            return subprocess.CompletedProcess(args, checkout_rc if args[:1] == ("checkout",) else 0, "", "boom")

        monkeypatch.setattr(rr, "_git", fake)

    def test_derived_file_main_copy_unavailable_stops(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_git(monkeypatch, "d.json\n", checkout_rc=1)
        with pytest.raises(rr.Stop, match="cannot take main's copy"):
            rr._resolve_stop(tmp_path, _plan(derived={"d.json": "x"}), [], [], [])

    def test_unreadable_roster_file_stops(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_git(monkeypatch, "r.yaml\n")
        with pytest.raises(rr.Stop, match="unreadable"):
            rr._resolve_stop(tmp_path, _plan(rosters=["r.yaml"]), [], [], [])

    def test_unmerged_file_without_hunks_stops(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write(tmp_path, "r.yaml", "a: 1\n")
        self._fake_git(monkeypatch, "r.yaml\n")
        with pytest.raises(rr.Stop, match="no conflict hunk"):
            rr._resolve_stop(tmp_path, _plan(rosters=["r.yaml"]), [], [], [])


class TestRefusals:
    def test_outside_a_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
        code, out = run(tmp_path, monkeypatch, capsys)
        assert code == 2 and "not inside a git repository" in out

    def test_rebase_already_in_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = make_fixture(tmp_path)
        (repo / ".git" / "rebase-merge").mkdir()
        code, out = run(repo, monkeypatch, capsys)
        assert code == 2 and "already in progress" in out

    def test_missing_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = make_repo(tmp_path)
        code, out = run(repo, monkeypatch, capsys)
        assert code == 2 and "RESOLVE REFUSED" in out and "mechanical-conflicts" in out

    def test_no_merge_base(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        repo = make_fixture(tmp_path)
        git(repo, "checkout", "-q", "--orphan", "lonely")
        git(repo, "commit", "-q", "--allow-empty", "-m", "orphan")
        code, out = run(repo, monkeypatch, capsys)
        assert code == 2 and "no merge base" in out

    def test_stop_cap_aborts_the_rebase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = make_fixture(tmp_path)
        on_main(repo, {"notes.txt": "main\n"})
        on_branch(repo, {"notes.txt": "branch\n"})
        monkeypatch.setattr(rr, "_MAX_STOPS", 0)
        code, out = run(repo, monkeypatch, capsys)
        assert code == 2 and "RESOLVE STOP rebase" in out
        assert not (repo / ".git" / "rebase-merge").exists()

    def test_commit_failure_is_a_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = make_fixture(tmp_path)
        race(repo)
        real = rr._git

        def failing(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            if args[:1] == ("commit",):
                return subprocess.CompletedProcess(args, 1, "", "no identity")
            return real(root, *args, env=env)

        monkeypatch.setattr(rr, "_git", failing)
        code, out = run(repo, monkeypatch, capsys)
        assert code == 2 and "RESOLVE STOP commit" in out
