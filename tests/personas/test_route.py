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


@pytest.fixture(autouse=True)
def _no_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)


def _event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str | None = None, conclusion: str = "success") -> None:
    target = tmp_path / "event.json"
    target.write_text(json.dumps({"workflow_run": {"path": path or route.TRIGGER_PATH, "conclusion": conclusion}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(target))


def _route(files: list[dict[str, Any]], root: Path, draft: bool = False, unknown: bool = False) -> list[str]:
    return route.route(files, unknown, draft, "o/r", "head", "base", "t", root)


def test_live_tree_binds_no_verdict_persona() -> None:
    assert route.bound_personas(ROOT) == {"code_reviewer"}
    assert _route([_f("scripts/x.py")], ROOT) == ["code_reviewer"]


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
    assert _route([*files, _f("scripts/x.py")], ROOT) == ["code_reviewer"]
    assert _route([_f(f"{base}notes.md")], ROOT) == ["code_reviewer"]
    assert _route([_f(f"{base}proceed.txt", "removed")], ROOT) == ["code_reviewer"]
    assert _route(files, ROOT, unknown=True) == ["code_reviewer"]
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
    _event(tmp_path, monkeypatch)
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
    assert (tmp_path / "out").read_text() == "routes=1\nfixture=true\nfallback_drill=false\nkeyed=true\n"


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
    _event(tmp_path, monkeypatch)
    monkeypatch.setattr(route, "changed_files", lambda *a: ([_f(PLAN)], False, "m"))
    monkeypatch.setattr(route, "route", lambda *a, **k: ["code_reviewer", "decision_scout"])
    seen: list[Any] = []
    entry = {"route": "decision_scout", "verdict": "NO_FLAGS"}
    inputs = {PLAN: {"plan": "p", "sidecar": None}}

    def fake_decide(repo: str, token: Any, res: dict, routes: list, plans: list, unknown: bool, root: Path) -> Any:
        seen.append((routes, plans, unknown, root))
        return rescout.Decision(["decision_scout"], [entry], {}, inputs)

    monkeypatch.setattr(rescout, "decide", fake_decide)
    assert route.main([]) == 0
    assert seen == [(["decision_scout"], [PLAN], False, tmp_path / "main")]
    doc = json.loads((tmp_path / "prepared" / "routes.json").read_text())
    assert doc["routes"] == ["decision_scout"] and doc["carried"] == [entry] and doc["rescout"] == {}
    assert json.loads((tmp_path / "prepared" / route.PLAN_INPUTS).read_text()) == inputs
    assert capsys.readouterr().out.strip() == "PERSONA-VERDICT routes=1 decision_scout carried=1"
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
    _event(tmp_path, monkeypatch, route.OVERRIDE_PATH)
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
    _event(tmp_path, monkeypatch)
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
    _event(tmp_path, monkeypatch, route.OVERRIDE_PATH)
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


CODE_FILES = [_f("scripts/x.py")]
VERDICT_APP = 5817535


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, advisory: str | None = None) -> None:
    """A main checkout under GITHUB_WORKSPACE whose advisory_runs row defaults line is replaced by `advisory`."""
    main = tmp_path / "ws" / "main"
    (main / "docs/contracts").mkdir(parents=True)
    (main / route.PERSONAS_CONTRACT).write_text((ROOT / route.PERSONAS_CONTRACT).read_text(encoding="utf-8"))
    text = (ROOT / route.CONFIG).read_text(encoding="utf-8")
    if advisory is not None:
        old = 'defaults: {local: "enabled", cloud: "enabled"}'
        assert text.count(old) == 1
        text = text.replace(old, advisory) if advisory.startswith("defaults") else text
    if advisory == "absent":
        text = yaml.safe_dump(
            {"settings": [r for r in yaml.safe_load(text)["settings"] if r["id"] != "gates.code_reviewer.advisory_runs"]}
        )
    (main / route.CONFIG).write_text(text)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path / "ws"))


def _drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: list[dict[str, Any]],
    path: str | None = route.CI_PATH,
    conclusion: str = "success",
    runs: Any = None,
) -> tuple[dict[str, Any], str, str]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prepared").mkdir(exist_ok=True)
    resolution = {"resolved": True, "head_sha": "h", "base_ref": "main", "draft": False, "number": 7, "allowlisted": True}
    (tmp_path / "prepared" / "resolution.json").write_text(json.dumps(resolution))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    out = tmp_path / "out"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    if path is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        _event(tmp_path, monkeypatch, path, conclusion)
    monkeypatch.setattr(route, "changed_files", lambda *a: (files, False, "m"))

    def fake_get(url: str, token: Any) -> Any:
        if isinstance(runs, Exception):
            raise runs
        return {"check_runs": runs or []}

    monkeypatch.setattr(route.resolve_pr, "get_json", fake_get)
    assert route.main([]) == 0
    return json.loads(route.ROUTES.read_text()), out.read_text(), ""


def test_code_reviewer_routes_on_code_pr_once_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc, out, _ = _drive(tmp_path, monkeypatch, CODE_FILES)
    assert doc["routes"] == ["code_reviewer"]
    assert out == "routes=1\nfixture=false\nfallback_drill=false\nkeyed=true\n"
    doc, out, _ = _drive(tmp_path, monkeypatch, CODE_FILES, route.TRIGGER_PATH)
    assert doc["routes"] == []
    assert "keyed=false\n" in out


def test_cancelled_ci_routes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc, out, _ = _drive(tmp_path, monkeypatch, CODE_FILES, conclusion="cancelled")
    assert doc["routes"] == [] and "keyed=false\n" in out
    assert "the CI run was cancelled" in capsys.readouterr().out


def test_advisory_runs_off_skips_with_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _workspace(tmp_path, monkeypatch, 'defaults: {local: "disabled", cloud: "disabled"}')
    doc, out, _ = _drive(tmp_path, monkeypatch, CODE_FILES)
    assert doc["routes"] == [] and "keyed=false\n" in out
    assert "PERSONA-VERDICT skip code_reviewer: advisory_runs disabled" in capsys.readouterr().out


def _verdict_run(title: str, app: int = VERDICT_APP) -> dict[str, Any]:
    return {"name": "persona-verdict/code_reviewer", "app": {"id": app}, "output": {"title": title}}


def test_rerun_with_existing_check_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, runs=[_verdict_run("verdict PROCEED")])
    assert doc["routes"] == [] and "already carries a verdict check" in capsys.readouterr().out
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, runs=[_verdict_run("failure budget_exhausted")])
    assert doc["routes"] == ["code_reviewer"]
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, runs=[_verdict_run("verdict PROCEED", app=1)])
    assert doc["routes"] == ["code_reviewer"]
    monkeypatch.setattr(merge_checks, "verdict_app_id", lambda text: "the configuration does not parse")
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, runs=[_verdict_run("verdict PROCEED")])
    assert doc["routes"] == ["code_reviewer"] and "verdict app id unavailable" in capsys.readouterr().out
    monkeypatch.undo()
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, runs=RuntimeError("boom"))
    assert doc["routes"] == ["code_reviewer"] and "the check-runs read failed" in capsys.readouterr().out


def test_fixture_routes_only_from_the_trigger_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = [_f(f"{route.FIXTURE_DIR}proceed.txt")]
    drill = [_f(route.DRILL_PATH, "added")]
    for files in (fixture, drill):
        doc, out, _ = _drive(tmp_path, monkeypatch, files)
        assert doc["routes"] == []
        assert "fixture=false\nfallback_drill=false\nkeyed=false\n" in out
    doc, out, _ = _drive(tmp_path, monkeypatch, fixture, route.TRIGGER_PATH)
    assert doc["routes"] == ["canary_persona"] and "fixture=true\n" in out and "keyed=true\n" in out


def test_undeclared_upstream_path_routes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc, out, _ = _drive(tmp_path, monkeypatch, CODE_FILES, ".github/workflows/elsewhere/ci.yml")
    assert doc["routes"] == [] and "keyed=false\n" in out
    assert "undeclared upstream .github/workflows/elsewhere/ci.yml" in capsys.readouterr().out
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES, None)
    assert doc["routes"] == [] and "undeclared upstream none" in capsys.readouterr().out
    (tmp_path / "event.json").write_text(json.dumps({"workflow_run": {}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    assert route.main([]) == 0
    assert json.loads(route.ROUTES.read_text())["routes"] == []
    (tmp_path / "event.json").write_text("[1]")
    assert route.main([]) == 0
    (tmp_path / "event.json").write_text("{")
    assert route.main([]) == 0
    assert json.loads(route.ROUTES.read_text())["routes"] == []


@pytest.mark.parametrize(
    "line",
    [
        "defaults: {local: on, cloud: on}",
        "defaults: {local: off, cloud: off}",
        'defaults: {local: "maybe", cloud: "maybe"}',
        'defaults: {local: "enabled", cloud: "disabled"}',
        "defaults: null",
    ],
)
def test_advisory_runs_absent_or_bad_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    _workspace(tmp_path, monkeypatch, line)
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES)
    assert doc["routes"] == []
    assert "advisory_runs bad value" in capsys.readouterr().out


def test_advisory_runs_absent_reads_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmp_path, monkeypatch, "absent")
    doc, _, _ = _drive(tmp_path, monkeypatch, CODE_FILES)
    assert doc["routes"] == ["code_reviewer"]


def test_advisory_state_unreadable_or_duplicated_is_bad(tmp_path: Path) -> None:
    assert route.advisory_state("code_reviewer", tmp_path)[0] == "bad"
    target = tmp_path / route.CONFIG
    target.parent.mkdir(parents=True)
    target.write_text("settings: nope\n")
    assert route.advisory_state("code_reviewer", tmp_path)[0] == "bad"
    row = {"id": "gates.code_reviewer.advisory_runs", "defaults": {"local": "enabled", "cloud": "enabled"}}
    target.write_text(yaml.safe_dump({"settings": [row, row]}))
    assert route.advisory_state("code_reviewer", tmp_path)[0] == "bad"
    target.write_text(yaml.safe_dump({"settings": [row]}))
    assert route.advisory_state("code_reviewer", tmp_path) == ("enabled", "")
    target.write_text(":\n  - [")
    assert route.advisory_state("code_reviewer", tmp_path)[0] == "bad"


def test_advisory_runs_reads_the_committed_row_as_enabled() -> None:
    assert route.advisory_state("code_reviewer", ROOT) == ("enabled", "")


def test_keyed_is_false_outside_the_two_admitted_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound_root: Path
) -> None:
    assert route.is_keyed(["code_reviewer"], "ci", ROOT)
    assert route.is_keyed(["canary_persona"], "trigger", ROOT)
    assert not route.is_keyed([], "ci", ROOT)
    assert not route.is_keyed(["code_reviewer", "decision_scout"], "ci", ROOT)
    assert not route.is_keyed(["code_reviewer"], "trigger", ROOT)
    assert not route.is_keyed(["decision_scout"], "trigger", bound_root)
    assert not route.is_keyed(["canary_persona"], "ci", ROOT)
    assert not route.is_keyed(["code_reviewer"], None, ROOT)
    monkeypatch.setattr(route, "route", lambda *a, **k: ["code_reviewer", "decision_scout"])
    doc, out, _ = _drive(tmp_path, monkeypatch, [_f(PLAN)], route.TRIGGER_PATH)
    assert doc["routes"] == ["decision_scout"] and "keyed=false\n" in out


def test_existing_verdict_read_pages_and_refuses_a_bad_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[str] = []

    def fake(url: str, token: Any) -> Any:
        pages.append(url)
        if url.rsplit("page=", 1)[1] in ("1", "2"):
            return {"check_runs": [{"name": "other", "app": {"id": 1}}] * 100}
        return {"check_runs": [_verdict_run("verdict REVISE")]}

    monkeypatch.setattr(route.resolve_pr, "get_json", fake)
    assert route._existing_verdict("o/r", "t", "h", "code_reviewer", VERDICT_APP)
    assert len(pages) == 3
    monkeypatch.setattr(route.resolve_pr, "get_json", lambda url, token: {"check_runs": [{"name": "o"}] * 100})
    assert not route._existing_verdict("o/r", "t", "h", "code_reviewer", VERDICT_APP)
    monkeypatch.setattr(route.resolve_pr, "get_json", lambda url, token: {"check_runs": "no"})
    with pytest.raises(RuntimeError):
        route._existing_verdict("o/r", "t", "h", "code_reviewer", VERDICT_APP)


def test_unreadable_config_applies_no_rerun_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(route, "advisory_state", lambda persona, root: ("enabled", ""))
    assert route._gate_ci_routes(["code_reviewer"], tmp_path, "o/r", None, "h") == ["code_reviewer"]
    assert "verdict app id unavailable" in capsys.readouterr().out
