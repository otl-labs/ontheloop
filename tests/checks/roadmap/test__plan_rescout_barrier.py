"""Mirror test of the re-scout barrier: fixture plans over a fixture tree; fingerprints come from plan_fingerprint itself."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import scripts.roadmap
from scripts.checks.roadmap import _plan_rescout_barrier as module
from scripts.roadmap import plan_fingerprint as pf
from scripts.roadmap.approved_words import parse_sidecar

pytestmark = pytest.mark.unit

_PLAN = {
    "slug": "demo",
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
}
_DECISIONS = "## Decision 217: Title (Decided)\n\nbody one\n"
_PLAN_REL = "docs/plans/PLAN-demo.yaml"
_SIDECAR_REL = "docs/plans/approved/demo.yaml"
_ENTRIES = [("A", _PLAN_REL)]


def _write_tree(root: Path, decisions: str = _DECISIONS) -> None:
    (root / "docs" / "contracts").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "DECISIONS.md").write_text(decisions, encoding="utf-8")
    items = [
        {
            "id": "T4.32",
            "tier": "T4",
            "name": "verdicts",
            "status": "in_progress",
            "intent": "i",
            "files_in_scope": ["src/a.py"],
        }
    ]
    doc = {
        "document": {"id": "R", "version": 1, "status": "ratified", "filed_via": "ops_decisions:dec-108"},
        "tier_items": items,
    }
    (root / "docs" / "ROADMAP-PLATFORM.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    (root / "docs" / "contracts" / "north-star.yaml").write_text("stars\n", encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _write_tree(tmp_path)
    return tmp_path


def _plan(**changes: object) -> dict:
    return {**copy.deepcopy(_PLAN), **changes}


def _put(root: Path, plan: dict, rel: str = _PLAN_REL) -> None:
    (root / rel).write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")


def _value(root: Path, plan: dict, sidecar: str | None = None) -> str:
    parsed = parse_sidecar(sidecar) if sidecar is not None else None
    return pf.fingerprint(pf.projection(plan, pf.load_tree(root), parsed))


def _doc(version: int = 6, *rows: dict) -> SimpleNamespace:
    return SimpleNamespace(
        schema_version=version, gate_rounds=[SimpleNamespace(**{"scout_input_fingerprint": None, **r}) for r in rows]
    )


def _scout(value: str | None, verdict: str = "NO_FLAGS") -> dict:
    return {"gate": "decision-scout", "verdict": verdict, "scout_input_fingerprint": value}


def _reader(files: dict[str, str]):
    return lambda rel: files.get(rel)


def _run(root: Path, doc: SimpleNamespace, entries=_ENTRIES, files=None, **kw):
    args = {"base": "b", "push_context": False, "diff_count": max(len(entries), 1), "read_base": _reader(files or {})}
    args.update(kw)
    docs = {Path(path).name: doc for _, path in entries}
    return module.barrier(entries, docs, root, args.pop("base"), args.pop("push_context"), args.pop("read_base"), **args)


def test_added_matching_plan_passes_and_counts_one_comparison(root: Path) -> None:
    plan = _plan()
    _put(root, plan)
    result = _run(root, _doc(6, _scout(_value(root, plan))))
    assert (result.failures, result.warnings, result.examined, result.compared) == ([], [], 1, 1)


def test_mismatch_missing_and_no_row_fail(root: Path) -> None:
    plan = _plan()
    _put(root, plan)
    stale = "v1:" + "0" * 64
    mismatch = _run(root, _doc(6, _scout(stale))).failures
    assert len(mismatch) == 1 and "re-scout required" in mismatch[0] and stale in mismatch[0]
    assert _value(root, plan) in mismatch[0] and "PLAN-demo.yaml" in mismatch[0]
    assert "records no scout_input_fingerprint" in _run(root, _doc(6, _scout(None))).failures[0]
    assert "no decision-scout row" in _run(root, _doc(6)).failures[0]


def test_later_non_scout_row_does_not_change_the_row_read(root: Path) -> None:
    plan = _plan()
    _put(root, plan)
    rows = (_scout(_value(root, plan)), {"gate": "plan-critique", "verdict": "PROCEED"})
    assert _run(root, _doc(6, *rows)).failures == []


def test_decision_text_changed_after_the_row_fails(root: Path) -> None:
    plan = _plan()
    _put(root, plan)
    recorded = _value(root, plan)
    _write_tree(root, _DECISIONS.replace("body one", "body one amended"))
    assert "re-scout required" in _run(root, _doc(6, _scout(recorded))).failures[0]


def test_unreadable_decisions_and_unparsable_sidecar_never_pass(root: Path) -> None:
    _put(root, _plan())
    (root / "docs" / "DECISIONS.md").unlink()
    failures = _run(root, _doc(6, _scout("v1:" + "0" * 64))).failures
    assert len(failures) == 1 and "scout-input fingerprint cannot be computed: FINGERPRINT-ERROR" in failures[0]
    _write_tree(root)
    (root / _SIDECAR_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / _SIDECAR_REL).write_text("not: a sidecar\n", encoding="utf-8")
    failures = _run(root, _doc(6, _scout("v1:" + "0" * 64))).failures
    assert len(failures) == 1 and "cannot be computed" in failures[0]


def test_plan_without_a_mapping_or_slug_fails_closed(root: Path) -> None:
    (root / _PLAN_REL).write_text("- a\n", encoding="utf-8")
    assert "plan is not a mapping" in _run(root, _doc(6, _scout(None))).failures[0]
    _put(root, {k: v for k, v in _plan().items() if k != "slug"})
    assert "usable 'slug'" in _run(root, _doc(6, _scout(None))).failures[0]


def _modified(root: Path, base_plan: dict, head_plan: dict, doc: SimpleNamespace, **kw):
    _put(root, head_plan)
    files = {_PLAN_REL: yaml.safe_dump(base_plan, sort_keys=False)}
    files.update(kw.pop("files", {}))
    return _run(root, doc, [("M", _PLAN_REL)], files=files, **kw)


def test_modified_plan_with_unmoved_fingerprint_is_not_compared(root: Path) -> None:
    base = _plan()
    head = _plan(context=["other"], execution_steps=["x"], implementation_declared=True)
    result = _modified(root, base, head, _doc(6, _scout("v1:" + "0" * 64)))
    assert (result.failures, result.examined, result.compared) == ([], 1, 0)


def test_modified_plan_with_new_scope_path_is_compared(root: Path) -> None:
    head = _plan(scope=_PLAN["scope"] + [{"file": "src/b.py", "action": "Create", "purpose": "p"}])
    result = _modified(root, _plan(), head, _doc(6, _scout("v1:" + "0" * 64)))
    assert result.compared == 1 and "re-scout required" in result.failures[0]


def test_modified_plan_with_head_only_sidecar_block_is_compared(root: Path) -> None:
    (root / _SIDECAR_REL).parent.mkdir(parents=True, exist_ok=True)
    sidecar = yaml.safe_dump(
        {
            "schema_version": 1,
            "slug": "demo",
            "blocks": [{"id": "b1", "file": "docs/DECISIONS.md", "before": "a b", "after": "a c"}],
        }
    )
    (root / _SIDECAR_REL).write_text(sidecar, encoding="utf-8")
    result = _modified(root, _plan(), _plan(), _doc(6, _scout("v1:" + "0" * 64)))
    assert result.compared == 1 and "re-scout required" in result.failures[0]


def test_unreadable_base_plan_is_compared_as_added(root: Path) -> None:
    plan = _plan()
    _put(root, plan)
    result = _run(root, _doc(6, _scout(_value(root, plan))), [("M", _PLAN_REL)])
    assert (result.failures, result.compared) == ([], 1)


def test_lowered_schema_version_fails_and_modified_v5_is_not_compared(root: Path) -> None:
    result = _modified(root, _plan(schema_version=6), _plan(), _doc(5))
    assert result.failures == ["PLAN-demo.yaml: schema_version lowered from 6 to 5"] and result.compared == 0
    result = _modified(root, _plan(schema_version=5), _plan(), _doc(5))
    assert (result.failures, result.examined, result.compared) == ([], 1, 0)


def test_push_context_turns_failures_into_warnings(root: Path) -> None:
    _put(root, _plan())
    result = _run(root, _doc(6, _scout(None)), push_context=True)
    assert result.failures == [] and "records no scout_input_fingerprint" in result.warnings[0]


def test_base_none_fails_outside_push_context_and_warns_inside(root: Path) -> None:
    _put(root, _plan())
    assert "cannot resolve a diff base" in _run(root, _doc(5), base=None).failures[0]
    result = _run(root, _doc(5), base=None, push_context=True)
    assert result.failures == [] and "cannot resolve a diff base" in result.warnings[0]


def test_tree_is_loaded_once_for_two_plans(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    _put(root, plan)
    _put(root, _plan(slug="two"), "docs/plans/PLAN-two.yaml")
    calls: list[Path] = []
    real = pf.load_tree
    monkeypatch.setattr(pf, "load_tree", lambda r: calls.append(r) or real(r))
    entries = [("A", _PLAN_REL), ("A", "docs/plans/PLAN-two.yaml")]
    doc = _doc(6, _scout("v1:" + "0" * 64))
    module.barrier(entries, {"PLAN-demo.yaml": doc, "PLAN-two.yaml": doc}, root, "b", False, _reader({}), diff_count=2)
    assert len(calls) == 1


def test_added_v6_plan_left_uncompared_by_routing_fails(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _put(root, _plan())
    monkeypatch.setattr(module, "_plan_findings", lambda *a, **k: None)
    failures = _run(root, _doc(6, _scout(None))).failures
    assert failures == ["PLAN-demo.yaml: added at schema_version 6 but not compared"]


@pytest.mark.parametrize("version", [5, 6])
def test_block_row_fails_even_when_the_value_matches_and_when_accepted(root: Path, version: int) -> None:
    plan = _plan()
    _put(root, plan)
    row = {**_scout(_value(root, plan), "BLOCK"), "escalated": True, "escalation_choice": "accept-with-deferral"}
    failures = _run(root, _doc(version, row)).failures
    assert len(failures) == 1 and "the last decision-scout row is BLOCK" in failures[0] and "admin bypass" in failures[0]


def test_block_row_fails_on_a_modified_plan_whose_fingerprint_did_not_move(root: Path) -> None:
    result = _modified(root, _plan(), _plan(), _doc(6, _scout("v1:" + "0" * 64, "BLOCK")))
    assert result.compared == 0 and "BLOCK" in result.failures[0]
    assert "BLOCK" in _modified(root, _plan(schema_version=5), _plan(), _doc(5, _scout(None, "BLOCK"))).failures[0]


def test_pull_request_with_an_empty_diff_fails_and_one_entry_passes(root: Path) -> None:
    empty = _run(root, _doc(5), [], event="pull_request", diff_count=0)
    assert "the PR diff lists no file" in empty.failures[0]
    assert _run(root, _doc(5), [], event="pull_request", diff_count=1).failures == []
    assert _run(root, _doc(5), [], event=None, diff_count=0).failures == []


def test_seam_with_no_names_is_unaffected_by_git_state(root: Path) -> None:
    assert _run(root, _doc(5), [], base=None, seam=True, event="pull_request", diff_count=0).failures == []
    assert "cannot resolve a diff base" in _run(root, _doc(5), [], base=None, seam=False).failures[0]


def test_entries_outside_the_loaded_plans_are_ignored(root: Path) -> None:
    result = module.barrier(
        [("D", _PLAN_REL), ("A", "docs/plans/PLAN-gone.yaml")], {}, root, "b", False, _reader({}), diff_count=2
    )
    assert (result.failures, result.examined) == ([], 0)


def test_default_read_base_reads_a_committed_file_and_none_for_a_missing_path(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return done.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "c")
    sha = git("rev-parse", "HEAD")
    read = module.default_read_base(tmp_path, sha)
    assert read("f.txt") == "hello\n" and read("missing.txt") is None
    assert module.default_read_base(tmp_path, None)("f.txt") is None


def test_default_read_base_is_used_when_none_is_given(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    _put(root, plan)
    seen: list[tuple[Path, str | None]] = []
    monkeypatch.setattr(module, "default_read_base", lambda r, b: seen.append((r, b)) or (lambda rel: None))
    result = module.barrier(
        _ENTRIES, {"PLAN-demo.yaml": _doc(6, _scout(_value(root, plan)))}, root, "abc", False, diff_count=1
    )
    assert seen == [(root, "abc")] and result.failures == []


def test_import_failure_of_plan_fingerprint_is_a_failure_never_a_pass(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _put(root, _plan())
    monkeypatch.delattr(scripts.roadmap, "plan_fingerprint")
    monkeypatch.setitem(sys.modules, "scripts.roadmap.plan_fingerprint", None)
    result = _run(root, _doc(6, _scout(None)))
    assert len(result.failures) == 1 and "scout-input fingerprint module cannot be imported" in result.failures[0]


def test_barrier_value_equals_the_cli_digest(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / _SIDECAR_REL).parent.mkdir(parents=True, exist_ok=True)
    sidecar = yaml.safe_dump(
        {
            "schema_version": 1,
            "slug": "demo",
            "blocks": [{"id": "b1", "file": "docs/DECISIONS.md", "before": "a b", "after": "a c"}],
        }
    )
    (root / _SIDECAR_REL).write_text(sidecar, encoding="utf-8")
    _put(root, _plan())
    assert pf.main(["--plan", str(root / _PLAN_REL), "--root", str(root)]) == 0
    printed = capsys.readouterr().out.strip().removeprefix("scout_input_fingerprint=")
    result = _run(root, _doc(6, _scout(printed)))
    assert (result.failures, result.compared) == ([], 1)
