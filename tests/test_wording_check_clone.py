"""Tests for scripts/roadmap/wording_check_clone.py: the wording check's rehearsal clone and its plan rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.roadmap import _plan_rescout_barrier as barrier
from scripts.checks.verification import validate_weakening_dispositions as weakening
from scripts.roadmap import plan_wording_check as pwc
from scripts.roadmap import wording_check_clone as wcc

ROOT = Path(__file__).resolve().parents[1]
_SLUG = "demo"
_PLAN_REL = f"docs/plans/PLAN-{_SLUG}.yaml"
_SIDECAR_REL = f"docs/plans/approved/{_SLUG}.yaml"
_DOC = "docs/note.md"
_TEST = "tests/test_demo.py"
_NODE = f"{_TEST}::test_a"
_BEFORE = "    assert x == 1\n"
_AFTER = "    assert 1 == x\n"
_TEST_TEXT = f"def test_a():\n    x = 1\n{_BEFORE}"
_DECISIONS = "## Decision 217: Title (Decided)\n\nbody one\n"
_PLAN: dict[str, Any] = {
    "schema_version": 7,
    "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
    "slug": _SLUG,
    "intent": "Build the thing",
    "plan_type": "IMPLEMENTATION",
    "verification_tier": "V2",
    "phase": "T4.32",
    "scope": [{"file": "src/a.py", "action": "Create", "purpose": "p"}],
    "cites": ["dec:217"],
    "amends": [],
    "introduces": [],
    "closes_criteria": [],
    "context": ["free text"],
    "execution_steps": ["do it"],
    "gate_rounds": [],
    "plan_path": _PLAN_REL,
    "acceptance_criteria": ["a"],
    "verification_plan": [{"step": 1, "phase": "pre-deploy", "action": "a", "command": "c", "expected": "e", "fix_if": "f"}],
}
_ENTRY = {"candidate": f"[assertion-changed] {_NODE}", "disposition": "refactor", "cite": _NODE}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plan_text(*entries: dict[str, str], extra: str = "") -> str:
    text = "# plan header\n" + yaml.safe_dump(_PLAN, sort_keys=False) + extra
    if entries:
        text += "weakening_dispositions:\n" + "".join(
            f"  - candidate: {json.dumps(e['candidate'])}\n    disposition: {e['disposition']}\n"
            f"    cite: {json.dumps(e['cite'])}\n"
            for e in entries
        )
    return text


def _sidecar_text(blocks: list[dict[str, str]]) -> str:
    return yaml.safe_dump({"schema_version": 1, "slug": _SLUG, "blocks": blocks})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(main, "config", key, value)
    _write(main, ".gitignore", "logs/debug/\n.venv\n")
    _write(main, _DOC, "one two\n")
    _write(main, _TEST, _TEST_TEXT)
    _write(main, "docs/DECISIONS.md", _DECISIONS)
    _write(
        main,
        "docs/contracts/implement-scope-boundary.yaml",
        (ROOT / "docs/contracts/implement-scope-boundary.yaml").read_text(encoding="utf-8"),
    )
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    _git(main, "update-ref", "refs/remotes/origin/main", "HEAD")
    return main


@pytest.fixture
def tree(repo: Path, tmp_path: Path) -> Path:
    scratch = tmp_path / "scratch"
    _git(repo, "worktree", "add", "-q", "--detach", str(scratch), "HEAD")
    return scratch


def _state(path: Path) -> tuple[str, str, str]:
    return (
        _git(path, "rev-parse", "HEAD"),
        _git(path, "rev-parse", "refs/remotes/origin/main"),
        _git(path, "status", "--porcelain"),
    )


def test_the_clone_main_holds_the_plan_pr_and_the_diff_holds_the_rest(tree: Path) -> None:
    base = _git(tree, "rev-parse", "HEAD")
    plan = _plan_text(_ENTRY)
    _write(tree, _DOC, "one TWO\n")
    _write(tree, "docs/new.md", "new\n")
    (tree / "docs/DECISIONS.md").unlink()
    _git(tree, "add", "docs/DECISIONS.md")
    (tree / ".venv").mkdir()
    sidecar = "sidecar: text\n"
    with wcc.rehearsal_clone(tree, base, _SLUG, sidecar, plan) as clone:
        assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=clone, capture_output=True).returncode != 0
        assert _git(clone, "rev-parse", "origin/main^") == base
        assert _git(clone, "show", f"origin/main:{_SIDECAR_REL}") == sidecar.strip()
        stripped = _git(clone, "show", f"origin/main:{_PLAN_REL}")
        assert "weakening_dispositions" not in stripped and "# plan header" in stripped
        assert (clone / _PLAN_REL).read_text(encoding="utf-8") == plan
        status = dict(
            line.split("\t")[::-1] for line in _git(clone, "diff", "--name-status", "--no-renames", "origin/main").splitlines()
        )
        assert status[_PLAN_REL] == "M" and status[_DOC] == "M" and status["docs/DECISIONS.md"] == "D"
        assert "docs/new.md" in _git(clone, "status", "--porcelain", "--untracked-files=all")
        assert ".venv" not in _git(clone, "status", "--porcelain", "--untracked-files=all").replace(".venv/", ".venv")
        assert (clone / "docs/new.md").read_text(encoding="utf-8") == "new\n"


def test_the_planner_tree_and_refs_are_untouched(tree: Path, repo: Path) -> None:
    base = _git(tree, "rev-parse", "HEAD")
    _write(tree, _DOC, "one TWO\n")
    before = (_state(tree), _state(repo))
    seen: list[Path] = []
    with wcc.rehearsal_clone(tree, base, _SLUG, "s: 1\n", _plan_text()) as clone:
        seen.append(clone.parent)
    assert (_state(tree), _state(repo)) == before and not seen[0].exists()
    with pytest.raises(RuntimeError), wcc.rehearsal_clone(tree, base, _SLUG, "s: 1\n", None) as clone:
        seen.append(clone.parent)
        raise RuntimeError("boom")
    assert not seen[1].exists() and (_state(tree), _state(repo)) == before
    with pytest.raises(wcc.WordingCloneError, match="git"), wcc.rehearsal_clone(tree, "0" * 40, _SLUG, "s: 1\n", None):
        pass


def test_strip_dispositions_removes_only_that_key() -> None:
    block = "weakening_dispositions:\n  - candidate: a\n    cite: b\n"
    text = "# top\nslug: demo  # pragma: allowlist secret\n\n" + block + "# tail comment\nintent: x\n"
    out = wcc.strip_dispositions(text)
    assert out == "# top\nslug: demo  # pragma: allowlist secret\n\n# tail comment\nintent: x\n"
    assert wcc.strip_dispositions("a: 1\n") == "a: 1\n"
    with pytest.raises(wcc.WordingCloneError):
        wcc.strip_dispositions("weakening_dispositions: [1\n")
    with pytest.raises(wcc.WordingCloneError, match="cannot be parsed"):
        wcc.strip_dispositions("weakening_dispositions: []\n  nested: 1\nb: 2\n")


def test_plan_rules_refuse_and_report(tree: Path, tmp_path: Path) -> None:
    plan_file = tmp_path / f"PLAN-{_SLUG}.yaml"
    plan_file.write_text("a: 1\n", encoding="utf-8")
    assert wcc.plan_text_for(plan_file, _SLUG, True) == "a: 1\n"
    assert wcc.plan_text_for(plan_file, _SLUG, False) is None
    assert wcc.plan_text_for(None, _SLUG, True) is None
    assert wcc.plan_text_for(tmp_path / "gone.yaml", _SLUG, True) is None
    with pytest.raises(wcc.WordingCloneError, match="does not match --slug other"):
        wcc.plan_text_for(plan_file, "other", True)
    text = _plan_text(_ENTRY)
    assert wcc.admitted_plan(tree, _SLUG, text, set()) == set()
    assert wcc.admitted_plan(tree, _SLUG, None, {_PLAN_REL}) == set()
    _write(tree, _PLAN_REL, text)
    assert wcc.admitted_plan(tree, _SLUG, text, {_PLAN_REL}) == {_PLAN_REL}
    with pytest.raises(wcc.WordingCloneError, match="differs from the loaded plan"):
        wcc.admitted_plan(tree, _SLUG, text + "x: 1\n", {_PLAN_REL})
    assert "entries in the diff: 1)" in wcc.plan_line(_SLUG, text)
    assert "entries in the diff: 0)" in wcc.plan_line(_SLUG, "{broken")
    assert wcc.plan_line(_SLUG, None) == "plan: none loaded; the clone's main holds the sidecar only"
    assert "build-code candidates" in wcc.stale_hint("x matches no current candidate y")
    assert wcc.stale_hint("fine") == ""
    base = _git(tree, "rev-parse", "HEAD")
    _git(tree, "add", "-N", _PLAN_REL)
    with wcc.rehearsal_clone(tree, base, _SLUG, "s: 1\n", text) as clone:
        assert (clone / _PLAN_REL).read_text(encoding="utf-8") == text


class _Runner:
    """Stands in for bin/venv-python: the measure, decisions_index and validate legs, the last running the real gate."""

    def __init__(self) -> None:
        self.failed: list[str] = []
        self.cwds: list[Path] = []

    def __call__(self, cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        if cmd[1] == "-c":
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        if "scripts.validate" in cmd:
            self.cwds.append(cwd)
            diff = _common.get_status_aware_diff(cwd)
            base = _common.status_diff_base(cwd)
            assert base is not None
            weakening.run_check(
                self.failed,
                root=cwd,
                base=base,
                status_changed=diff,
                read_base=lambda rel: weakening._read_file_at_ref(cwd, base, rel),
                read_head=lambda rel: weakening._read_file(cwd, rel),
            )
            (cwd / "logs/debug").mkdir(parents=True, exist_ok=True)
            manifest = {"sha": _git(cwd, "rev-parse", "HEAD"), "capped": False, "deferred": []}
            (cwd / "logs/debug/selection-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = "\n".join(self.failed)
            return subprocess.CompletedProcess(cmd, 1 if self.failed else 0, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _retarget(tree: Path, tmp_path: Path, plan_text: str | None) -> tuple[Path, Path]:
    _write(tree, _TEST, _TEST_TEXT.replace(_BEFORE, _AFTER))
    blocks = [
        {"id": "b1", "file": _TEST, "before": _BEFORE, "after": _AFTER},
        {"id": "b2", "file": _DOC, "before": "one two\n", "after": "one two\nmore\n"},
    ]
    _write(tree, _DOC, "one two\nmore\n")
    sidecar = tmp_path / "sidecar.yaml"
    sidecar.write_text(_sidecar_text(blocks), encoding="utf-8")
    plan = tmp_path / f"PLAN-{_SLUG}.yaml"
    plan.write_text(plan_text if plan_text is not None else "not: a plan\n", encoding="utf-8")
    return sidecar, plan


def test_a_retargeted_test_passes_the_real_weakening_gate_in_the_clone(tree: Path, tmp_path: Path) -> None:
    sidecar, plan = _retarget(tree, tmp_path, _plan_text(_ENTRY))
    runner = _Runner()
    result = pwc.run(_SLUG, tree, sidecar, runner, plan)
    assert result.verdict == "PASS", (result.reason, runner.failed)
    assert result.selection == "complete" and result.base == _git(tree, "rev-parse", "HEAD")
    assert "entries in the diff: 1)" in result.plan
    assert runner.cwds and runner.cwds[0] != tree
    assert "plan: rehearsed" in pwc._render(_SLUG, result)


def test_without_a_plan_the_weakening_gate_still_refuses(tree: Path, tmp_path: Path) -> None:
    sidecar, plan = _retarget(tree, tmp_path, None)
    result = pwc.run(_SLUG, tree, sidecar, _Runner(), plan)
    assert result.verdict == "FAIL" and "touches no plan" in result.echo + result.reason
    assert "none loaded" in result.plan


def test_a_stale_entry_fails_with_the_hint(tree: Path, tmp_path: Path) -> None:
    stale = {"candidate": "[assertion-changed] tests/test_demo.py::test_zzz", "disposition": "refactor", "cite": _NODE}
    sidecar, plan = _retarget(tree, tmp_path, _plan_text(_ENTRY, stale))
    result = pwc.run(_SLUG, tree, sidecar, _Runner(), plan)
    assert result.verdict == "FAIL" and "build-code candidates" in result.reason


def test_a_differing_hand_placed_plan_is_a_wording_clone_failure(tree: Path, tmp_path: Path) -> None:
    sidecar, plan = _retarget(tree, tmp_path, _plan_text(_ENTRY))
    _write(tree, _PLAN_REL, "different: 1\n")
    result = pwc.run(_SLUG, tree, sidecar, _Runner(), plan)
    assert result.verdict == "FAIL" and "differs from the loaded plan" in result.reason


def _roadmap_tree(root: Path, decisions: str) -> None:
    _write(root, "docs/DECISIONS.md", decisions)
    item = {
        "id": "T4.32",
        "tier": "T4",
        "name": "verdicts",
        "status": "in_progress",
        "intent": "i",
        "files_in_scope": ["src/a.py"],
    }
    doc = {
        "document": {"id": "R", "version": 1, "status": "ratified", "filed_via": "ops_decisions:dec-108"},
        "tier_items": [item],
    }
    _write(root, "docs/ROADMAP-PLATFORM.yaml", yaml.safe_dump(doc))
    _write(root, "docs/contracts/north-star.yaml", "stars\n")


def test_the_rescout_barrier_skips_the_plan_with_a_decisions_edit(repo: Path, tmp_path: Path) -> None:
    from scripts.roadmap import plan_fingerprint as pf

    _roadmap_tree(repo, _DECISIONS)
    base_plan = yaml.safe_load(_plan_text())
    fp = pf.fingerprint(pf.projection(base_plan, pf.load_tree(repo), None))
    base_plan["gate_rounds"] = [{"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS", "scout_input_fingerprint": fp}]
    plan_text = (
        yaml.safe_dump(base_plan, sort_keys=False)
        + "weakening_dispositions:\n  - candidate: c\n    disposition: refactor\n    cite: d\n"
    )
    _write(repo, _PLAN_REL, wcc.strip_dispositions(plan_text))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plan")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    scratch = tmp_path / "scratch2"
    _git(repo, "worktree", "add", "-q", "--detach", str(scratch), "HEAD")
    base = _git(scratch, "rev-parse", "HEAD")
    _write(scratch, "docs/DECISIONS.md", _DECISIONS.replace("one", "ONE"))
    docs = {f"PLAN-{_SLUG}.yaml": _Doc(base_plan)}
    with wcc.rehearsal_clone(scratch, base, _SLUG, "s: 1\n", plan_text) as clone:
        entries = _common.get_status_aware_diff(clone)
        assert ("M", _PLAN_REL) in entries
        res = barrier.barrier(entries, docs, clone, _common.status_diff_base(clone), False, diff_count=len(entries))
        assert (res.examined, res.compared) == (1, 0), res.failures
    entries = [("??", _PLAN_REL), ("M", "docs/DECISIONS.md")]
    res = barrier.barrier(entries, docs, scratch, base, False, diff_count=len(entries))
    assert (res.examined, res.compared) == (1, 1)


class _Doc:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.schema_version = plan["schema_version"]
        self.gate_rounds = [type("R", (), {"scout_input_fingerprint": None, **row})() for row in plan["gate_rounds"]]


def test_a_dangling_untracked_symlink_is_a_clone_failure(tree: Path) -> None:
    base = _git(tree, "rev-parse", "HEAD")
    (tree / "docs/dangling").symlink_to(tree / "missing")
    with pytest.raises(wcc.WordingCloneError, match="cannot carry untracked file"):
        with wcc.rehearsal_clone(tree, base, _SLUG, "s: 1\n", None):
            pass


def test_a_git_that_cannot_start_is_a_clone_failure(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("no git")

    monkeypatch.setattr(wcc.subprocess, "run", broken)
    with pytest.raises(wcc.WordingCloneError, match="could not start"):
        wcc._git(tree, "status")


def test_a_strip_that_changes_the_mapping_is_refused() -> None:
    with pytest.raises(wcc.WordingCloneError, match="more than that key"):
        wcc.strip_dispositions("weakening_dispositions: 1\n")


def test_an_untracked_plan_in_the_tree_is_replaced_by_the_loaded_plan(tree: Path) -> None:
    base = _git(tree, "rev-parse", "HEAD")
    text = _plan_text(_ENTRY)
    _write(tree, _PLAN_REL, text)
    with wcc.rehearsal_clone(tree, base, _SLUG, "s: 1\n", text) as clone:
        assert (clone / _PLAN_REL).read_text(encoding="utf-8") == text
        assert _git(clone, "diff", "--name-status", "--no-renames", "origin/main") == f"M\t{_PLAN_REL}"
