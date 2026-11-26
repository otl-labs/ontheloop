"""Routing on the diff: the merge gate's class on every head plus T4.2 c12's extras, filtered by the bound set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.personas import resolve_pr, route
from scripts.session import merge_checks

ROOT = Path(__file__).resolve().parents[2]
PLAN = "docs/plans/PLAN-x.yaml"


def _f(name: str, status: str = "modified", prev: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"filename": name, "status": status}
    if prev:
        out["previous_filename"] = prev
    return out


@pytest.fixture
def bound_root(tmp_path: Path) -> Path:
    (tmp_path / "docs/contracts").mkdir(parents=True)
    scope = yaml.safe_load((ROOT / route.SCOPE_CONTRACT).read_text(encoding="utf-8"))["sanction_rows"][
        "implementing_plan_bookkeeping"
    ]
    (tmp_path / route.SCOPE_CONTRACT).write_text(
        yaml.safe_dump(
            {"sanction_rows": {"implementing_plan_bookkeeping": {"permitted_field_edits": scope["permitted_field_edits"]}}}
        )
    )
    rows = [
        {"persona": p, "kind": "verdict", "implementation": {"status": "implemented"}} for p in merge_checks.PERSONA_CLASSES
    ]
    (tmp_path / route.PERSONAS_CONTRACT).write_text(
        yaml.safe_dump({"personas": rows, "fixture_kind": {"personas": ["canary_persona"]}})
    )
    return tmp_path


def _texts(monkeypatch: pytest.MonkeyPatch, before: str | None, after: str | None) -> None:
    def fake(repo: str, path: str, ref: str, token: str | None) -> str | None:
        return before if ref == "base" else after

    monkeypatch.setattr(route, "_fetch_text", fake)


def _route(files: list[dict[str, Any]], root: Path, draft: bool = False, unknown: bool = False) -> list[str]:
    return route.route(files, unknown, draft, "o/r", "head", "base", "t", root)


def test_live_tree_binds_no_verdict_persona() -> None:
    assert route.bound_personas(ROOT) == set()
    assert _route([_f("scripts/x.py")], ROOT) == []


def test_plan_class_pr_routes_scout_on_every_head_and_critic_on_ready_heads(
    bound_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _texts(monkeypatch, "a: 1\n", "a: 2\n")
    files = [_f(PLAN)]
    assert _route(files, bound_root, draft=True) == ["decision_scout"]
    assert _route(files, bound_root, draft=False) == ["decision_scout", "plan_critic"]


def test_code_class_pr_routes_reviewer(bound_root: Path) -> None:
    assert _route([_f("scripts/x.py")], bound_root) == ["code_reviewer"]


def test_code_pr_adding_or_revising_a_plan_gets_both_sets(bound_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _route([_f("scripts/x.py"), _f(PLAN, "added")], bound_root, draft=True) == ["code_reviewer", "decision_scout"]
    _texts(monkeypatch, "scope: [a]\n", "scope: [a, b]\n")
    assert _route([_f("scripts/x.py"), _f(PLAN)], bound_root) == ["code_reviewer", "decision_scout", "plan_critic"]


def test_bookkeeping_only_plan_edit_in_a_code_pr_does_not_get_the_plan_set(
    bound_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = "scope: [a]\nclosures: 1\nclose_criteria: []\nimplementation_declared: false\n"
    after = "scope: [a]\nclosures: 1\nclose_criteria: []\nimplementation_declared: true\n"
    _texts(monkeypatch, before, after)
    assert _route([_f("scripts/x.py"), _f(PLAN)], bound_root) == ["code_reviewer"]


def test_array_path_bookkeeping_edit_is_not_a_revision(bound_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = "verification_plan:\n  - {step: 1, command: c, graduation: null}\n"
    after = "verification_plan:\n  - {step: 1, command: c, graduation: graduate, graduation_check_id: z}\n"
    _texts(monkeypatch, before, after)
    assert _route([_f("scripts/x.py"), _f(PLAN)], bound_root) == ["code_reviewer"]
    _texts(monkeypatch, before, after.replace("command: c", "command: d"))
    assert "decision_scout" in _route([_f("scripts/x.py"), _f(PLAN)], bound_root)


def test_plans_only_bookkeeping_edit_gets_the_plan_set_and_the_reviewer(
    bound_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _texts(monkeypatch, "scope: [a]\nimplementation_declared: false\n", "scope: [a]\nimplementation_declared: true\n")
    assert _route([_f(PLAN)], bound_root, draft=True) == ["code_reviewer", "decision_scout"]


def test_branch_name_is_never_consulted(bound_root: Path) -> None:
    assert _route([_f("scripts/x.py")], bound_root) == ["code_reviewer"]
    assert "branch" not in route.route.__code__.co_varnames


def test_unbound_personas_are_filtered(bound_root: Path) -> None:
    rows = [{"persona": "code_reviewer", "kind": "verdict", "implementation": {"status": "pending"}}]
    (bound_root / route.PERSONAS_CONTRACT).write_text(yaml.safe_dump({"personas": rows}))
    assert _route([_f("scripts/x.py")], bound_root) == []


def test_rename_into_plans_is_code_class_via_previous_filename(bound_root: Path) -> None:
    files = [_f("docs/plans/x.yaml", "renamed", prev="scripts/x.py")]
    assert _route(files, bound_root) == ["code_reviewer"]


def test_truncated_or_incomplete_list_routes_both_sets(bound_root: Path) -> None:
    assert _route([_f("scripts/x.py")], bound_root, unknown=True) == ["code_reviewer", "decision_scout", "plan_critic"]
    assert _route([_f("scripts/x.py")], bound_root, draft=True, unknown=True) == ["code_reviewer", "decision_scout"]


@pytest.mark.parametrize(
    "after",
    ["x" * (route.PLAN_SIZE_CAP + 1), "a: &anc 1\nb: *anc\n", "a: &anc 1\n", "a: [unclosed\n"],
)
def test_oversized_aliased_or_unparseable_plan_counts_as_a_revision(
    bound_root: Path, monkeypatch: pytest.MonkeyPatch, after: str
) -> None:
    _texts(monkeypatch, "a: 1\n", after)
    assert "decision_scout" in _route([_f("scripts/x.py"), _f(PLAN)], bound_root)


def test_scalar_resembling_an_alias_is_not_an_alias() -> None:
    assert route.parse_plan("a: PLAN-*.yaml\n") == {"a": "PLAN-*.yaml"}


def test_fixture_only_diff_routes_the_fixture_personas(bound_root: Path) -> None:
    base = route.FIXTURE_DIR
    names = ("proceed", "revise")
    files = [_f(f"{base}{s}.txt") for s in names]
    assert _route(files, ROOT) == ["canary_persona"]
    assert route.fixture_paths(files, False) == [f"{base}{s}.txt" for s in names]
    assert _route(files[:1], ROOT) == ["canary_persona"]
    assert _route([*files, _f("scripts/x.py")], ROOT) == []
    assert _route([_f(f"{base}notes.md")], ROOT) == []
    assert _route([_f(f"{base}proceed.txt", "removed")], ROOT) == []
    assert _route(files, ROOT, unknown=True) == []
    assert _route(files, bound_root) == ["canary_persona"]


def test_class_and_head_rule_come_from_merge_checks(bound_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        merge_checks, "PERSONA_CLASSES", {"code_reviewer": "code", "decision_scout": "plan", "plan_critic": "code"}
    )
    assert _route([_f("scripts/x.py")], bound_root) == ["code_reviewer", "plan_critic"]
    monkeypatch.setattr(merge_checks, "READY_HEAD_PERSONAS", frozenset({"code_reviewer"}))
    assert _route([_f("scripts/x.py")], bound_root, draft=True) == ["plan_critic"]


def test_changed_files_paginates_and_marks_a_300_file_list_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        1: {"files": [_f(f"a{i}") for i in range(100)], "merge_base_commit": {"sha": "m"}},
        2: {"files": [_f(f"b{i}") for i in range(100)]},
        3: {"files": [_f(f"c{i}") for i in range(100)]},
    }
    monkeypatch.setattr(resolve_pr, "get_json", lambda path, token: pages[int(path.rsplit("page=", 1)[1])])
    files, unknown, merge_base = route.changed_files("o/r", "main", "h", "t")
    assert len(files) == 300 and unknown is True and merge_base == "m"
    monkeypatch.setattr(resolve_pr, "get_json", lambda path, token: {"merge_base_commit": {"sha": "m"}})
    assert route.changed_files("o/r", "main", "h", "t")[1] is True


def test_main_logs_routes_and_writes_the_routes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir()
    resolution = {"resolved": True, "head_sha": "h", "base_ref": "main", "draft": False}
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps(resolution))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    files = [_f(f"{route.FIXTURE_DIR}proceed.txt")]
    monkeypatch.setattr(route, "changed_files", lambda *a: (files, False, "m"))
    assert route.main([]) == 0
    assert capsys.readouterr().out.strip() == "PERSONA-VERDICT routes=1 canary_persona carried=0"
    assert json.loads((tmp_path / "prepared" / "routes.json").read_text()) == {
        "routes": ["canary_persona"],
        "plan_paths": [],
        "fixture_paths": [f"{route.FIXTURE_DIR}proceed.txt"],
        "carried": [],
        "rescout": {},
    }
    assert not (tmp_path / "prepared" / route.PLAN_INPUTS).exists()
    assert (tmp_path / "out").read_text() == "routes=1\nfixture=true\nfallback_drill=false\n"


def test_main_writes_carried_and_rescout_and_keeps_routes_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.personas import rescout

    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir()
    resolution = {"resolved": True, "head_sha": "h", "base_ref": "main", "draft": False, "number": 7, "allowlisted": True}
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps(resolution))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(route, "changed_files", lambda *a: ([_f(PLAN)], False, "m"))
    monkeypatch.setattr(route, "route", lambda *a, **k: ["code_reviewer", "decision_scout"])
    seen: list[Any] = []
    entry = {"route": "decision_scout", "verdict": "NO_FLAGS"}
    inputs = {PLAN: {"plan": "p", "sidecar": None}}

    def fake_decide(repo: str, token: Any, res: dict, routes: list, plans: list, unknown: bool, root: Path) -> Any:
        seen.append((routes, plans, unknown, root))
        return rescout.Decision(["code_reviewer"], [entry], {}, inputs)

    monkeypatch.setattr(rescout, "decide", fake_decide)
    assert route.main([]) == 0
    assert seen == [(["code_reviewer", "decision_scout"], [PLAN], False, tmp_path / "main")]
    doc = json.loads((tmp_path / "prepared" / "routes.json").read_text())
    assert doc["routes"] == ["code_reviewer"] and doc["carried"] == [entry] and doc["rescout"] == {}
    assert json.loads((tmp_path / "prepared" / route.PLAN_INPUTS).read_text()) == inputs
    assert capsys.readouterr().out.strip() == "PERSONA-VERDICT routes=1 code_reviewer carried=1"
    assert (tmp_path / "out").read_text().startswith("routes=1\n")
    monkeypatch.setattr(
        rescout, "decide", lambda *a: rescout.Decision(["decision_scout"], [], {"decision_scout": "moved"}, None)
    )
    assert route.main([]) == 0
    doc = json.loads((tmp_path / "prepared" / "routes.json").read_text())
    assert doc["routes"] == ["decision_scout"] and doc["rescout"] == {"decision_scout": "moved"}


def test_an_override_resolution_yields_no_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(route, "changed_files", lambda *a: (_ for _ in ()).throw(AssertionError("compare")))
    res = {"resolved": True, "override": {"run_id": 1}, "head_sha": "a" * 40, "base_ref": "main", "draft": False}
    route.resolve_pr.RESOLUTION.parent.mkdir(parents=True)
    route.resolve_pr.RESOLUTION.write_text(json.dumps(res))
    assert route.main([]) == 0
    assert json.loads(route.ROUTES.read_text())["routes"] == []


def _main_fixture_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: dict, files: list) -> str:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir(exist_ok=True)
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps(resolution))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(route, "changed_files", lambda *a: (files, False, "m"))
    assert route.main([]) == 0
    return out.read_text()


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([f"{route.FIXTURE_DIR}proceed.txt"], "true"),
        ([f"{route.FIXTURE_DIR}proceed.txt", "README.md"], "false"),
        (["README.md"], "false"),
        ([f"{route.FIXTURE_DIR}notes.md"], "false"),
    ],
)
def test_fixture_output_is_true_only_for_a_probe_only_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], expected: str
) -> None:
    resolution = {"resolved": True, "head_sha": "h", "base_ref": "main", "draft": False}
    out = _main_fixture_output(tmp_path, monkeypatch, resolution, [_f(n) for n in names])
    assert f"fixture={expected}\n" in out


def test_fixture_is_false_when_unresolved_or_an_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setattr(route, "changed_files", lambda *a: (_ for _ in ()).throw(AssertionError("compare")))
    route.resolve_pr.RESOLUTION.parent.mkdir(parents=True)
    for res in ({"resolved": False}, {"resolved": True, "override": {"run_id": 1}}):
        route.resolve_pr.RESOLUTION.write_text(json.dumps(res))
        (tmp_path / "out").write_text("")
        assert route.main([]) == 0
        assert "fixture=false\n" in (tmp_path / "out").read_text()


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ([_f(route.DRILL_PATH, "added")], "true"),
        ([_f(route.DRILL_PATH), _f(f"{route.FIXTURE_DIR}proceed.txt")], "true"),
        ([_f(route.DRILL_PATH), _f("README.md")], "false"),
        ([_f(route.DRILL_PATH, "removed")], "false"),
        ([_f(route.DRILL_PATH, "renamed", f"{route.FIXTURE_DIR}old.txt")], "false"),
        ([_f(f"{route.FIXTURE_DIR}proceed.txt")], "false"),
    ],
)
def test_fallback_drill_only_on_fixture_diffs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: list[dict[str, Any]], expected: str
) -> None:
    resolution = {"resolved": True, "head_sha": "h", "base_ref": "main", "draft": False}
    out = _main_fixture_output(tmp_path, monkeypatch, resolution, files)
    assert f"fallback_drill={expected}\n" in out
