"""validate_persona_verdict_chain: green on the live pair, one red fixture per refused shape, registered in the fast tier."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards import validate_persona_verdict_chain as v

LIVE = Path(_common.ROOT)
Docs = tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]


def _load(rel: str) -> dict[str, Any]:
    return yaml.safe_load((LIVE / rel).read_text(encoding="utf-8"))


def _step(job: dict[str, Any], verb: str) -> dict[str, Any]:
    (found,) = [s for s in job["steps"] if str(s.get("run", "")).endswith(f" {verb}")]
    return found


def _named(job: dict[str, Any], name: str) -> dict[str, Any]:
    (found,) = [s for s in job["steps"] if s.get("name") == name]
    return found


def _tree(tmp_path: Path, edit: Callable[[Docs], None] | None) -> Path:
    docs: Docs = (_load(v.TRIGGER), _load(v.VERDICT), _load(v.EVIDENCE), _load(v.OVERRIDE))
    docs = copy.deepcopy(docs)
    if edit:
        edit(docs)
    for rel, doc in zip((v.TRIGGER, v.VERDICT, v.EVIDENCE, v.OVERRIDE), docs, strict=True):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return tmp_path


def _set_run(step: dict[str, Any], text: str) -> None:
    step["run"] = text


def _red(name: str, expect: str, edit: Callable[[Docs], None]) -> Any:
    return pytest.param(edit, expect, id=name)


def _ojobs(d: Docs) -> dict[str, Any]:
    return d[3]["jobs"]


def _ostep(d: Docs, step_id: str) -> dict[str, Any]:
    (found,) = [s for j in _ojobs(d).values() for s in j["steps"] if s.get("id") == step_id]
    return found


def _on(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("on", doc.get(True))


def _jobs(d: Docs) -> dict[str, Any]:
    return d[1]["jobs"]


def _token_in_materialise(value: str) -> Callable[[Docs], None]:
    def edit(d: Docs) -> None:
        _step(_jobs(d)["prepare"], "materialise")["env"]["X"] = value

    return edit


def _token_in_execute(value: str) -> Callable[[Docs], None]:
    def edit(d: Docs) -> None:
        _step(_jobs(d)["run"], "execute")["env"]["X"] = value

    return edit


RED = [
    _red("trigger-extra-event", "not exactly pull_request", lambda d: _on(d[0]).update({"push": {}})),
    _red("trigger-types", "pull_request types", lambda d: _on(d[0])["pull_request"].update({"types": ["opened"]})),
    _red("trigger-top-permissions", "top-level permissions", lambda d: d[0].update({"permissions": {"contents": "read"}})),
    _red(
        "trigger-job-permissions",
        "permissions are not {}",
        lambda d: d[0]["jobs"]["trigger"].update({"permissions": {"contents": "read"}}),
    ),
    _red(
        "trigger-checkout",
        "uses:",
        lambda d: d[0]["jobs"]["trigger"]["steps"].append({"uses": "actions/checkout@" + "a" * 40}),
    ),
    _red(
        "trigger-secret",
        "secrets. reference",
        lambda d: d[0]["jobs"]["trigger"]["steps"][0]["env"].update({"X": "${{ secrets.FOO }}"}),
    ),
    _red("trigger-environment", "names an Environment", lambda d: d[0]["jobs"]["trigger"].update({"environment": "e"})),
    _red("verdict-extra-event", "not exactly workflow_run", lambda d: _on(d[1]).update({"push": {}})),
    _red(
        "verdict-wrong-upstream", "does not name exactly", lambda d: _on(d[1])["workflow_run"].update({"workflows": ["other"]})
    ),
    _red(
        "verdict-types",
        "types are not exactly [completed]",
        lambda d: _on(d[1])["workflow_run"].update({"types": ["completed", "requested"]}),
    ),
    _red("verdict-top-permissions", "top-level permissions", lambda d: d[1].update({"permissions": {"contents": "read"}})),
    _red("unpinned-uses", "not pinned", lambda d: _jobs(d)["prepare"]["steps"][0].update({"uses": "actions/checkout@v7"})),
    _red(
        "short-sha-uses", "not pinned", lambda d: _jobs(d)["prepare"]["steps"][0].update({"uses": "actions/checkout@abc123"})
    ),
    _red("checks-write-on-run", "holds checks: write", lambda d: _jobs(d)["run"]["permissions"].update({"checks": "write"})),
    _red("id-token", "id-token: write", lambda d: _jobs(d)["prepare"]["permissions"].update({"id-token": "write"})),
    _red("environment-on-post", "names an Environment", lambda d: _jobs(d)["post"].update({"environment": "persona-verdict"})),
    _red(
        "prepare-permissions", "permissions differ", lambda d: _jobs(d)["prepare"]["permissions"].update({"contents": "write"})
    ),
    _red("post-permissions", "permissions differ", lambda d: _jobs(d)["post"]["permissions"].pop("actions")),
    _red(
        "python-not-pinned-argv",
        "not the pinned run.py argv",
        lambda d: _set_run(_step(_jobs(d)["prepare"], "resolve"), "python3 scripts/x.py"),
    ),
    _red(
        "python-without-isolation",
        "not the pinned run.py argv",
        lambda d: _set_run(
            _step(_jobs(d)["prepare"], "resolve"), 'python3 "$GITHUB_WORKSPACE/main/scripts/personas/run.py" resolve'
        ),
    ),
    _red(
        "python-versioned",
        "not the pinned run.py argv",
        lambda d: _jobs(d)["prepare"]["steps"].insert(3, {"run": "python3.12 -c pass", "working-directory": v.WORKDIR}),
    ),
    _red(
        "python-absolute",
        "not the pinned run.py argv",
        lambda d: _jobs(d)["prepare"]["steps"].insert(3, {"run": "/usr/bin/python3 -c pass"}),
    ),
    _red(
        "python-no-snapshot-env",
        "lacks PERSONA_SNAPSHOT",
        lambda d: _step(_jobs(d)["prepare"], "route")["env"].pop("PERSONA_SNAPSHOT"),
    ),
    _red(
        "python-wrong-working-directory",
        "working-directory is not",
        lambda d: _step(_jobs(d)["prepare"], "route").update({"working-directory": "${{ github.workspace }}"}),
    ),
    _red(
        "bare-runner-temp-env",
        "bare $RUNNER_TEMP",
        lambda d: _step(_jobs(d)["prepare"], "route")["env"].update({"PERSONA_SNAPSHOT": "$RUNNER_TEMP/snapshot"}),
    ),
    _red(
        "bare-workspace-with",
        "bare $RUNNER_TEMP",
        lambda d: _named(_jobs(d)["run"], "Download prepared inputs")["with"].update({"path": "$RUNNER_TEMP/work/prepared"}),
    ),
    _red(
        "no-mkdir", "no mkdir", lambda d: _jobs(d)["run"]["steps"].remove(_named(_jobs(d)["run"], "Create the work directory"))
    ),
    _red(
        "pip-other-set",
        "pip line",
        lambda d: _set_run(
            _named(_jobs(d)["prepare"], "Install dependencies"),
            'pip install pyyaml requests -c "$GITHUB_WORKSPACE/main/requirements.txt"',
        ),
    ),
    _red(
        "pip-no-constraint",
        "pip line",
        lambda d: _set_run(_named(_jobs(d)["run"], "Install dependencies"), "pip install jsonschema"),
    ),
    _red("run-condition", "run job condition", lambda d: _jobs(d)["run"].update({"if": "${{ always() }}"})),
    _red("post-condition", "post job condition", lambda d: _jobs(d)["post"].update({"if": "${{ always() }}"})),
    _red(
        "post-download-hard-fail",
        "lacks continue-on-error",
        lambda d: _named(_jobs(d)["post"], "Download verdict results").pop("continue-on-error"),
    ),
    _red(
        "download-wrong-path",
        "download persona-verdict-results path",
        lambda d: _named(_jobs(d)["post"], "Download verdict results")["with"].update({"path": "${{ runner.temp }}/work"}),
    ),
    _red(
        "upload-retention",
        "retention-days",
        lambda d: _named(_jobs(d)["prepare"], "Upload prepared inputs")["with"].update({"retention-days": 2}),
    ),
    _red(
        "upload-undeclared",
        "differ",
        lambda d: _named(_jobs(d)["prepare"], "Upload prepared inputs").update({"name": "Upload other"}),
    ),
    _red(
        "fetch-after-materialise",
        "fetch step comes after",
        lambda d: _jobs(d)["prepare"]["steps"].insert(
            _jobs(d)["prepare"]["steps"].index(_step(_jobs(d)["prepare"], "materialise")) + 1,
            _jobs(d)["prepare"]["steps"].pop(_jobs(d)["prepare"]["steps"].index(_step(_jobs(d)["prepare"], "fetch"))),
        ),
    ),
    _red("fetch-unconditional", "fetch step lacks the allowlisted", lambda d: _step(_jobs(d)["prepare"], "fetch").pop("if")),
    _red(
        "materialise-unconditional",
        "materialise step lacks the allowlisted",
        lambda d: _step(_jobs(d)["prepare"], "materialise").pop("if"),
    ),
    _red("materialise-github-token", "materialise step env holds a token", _token_in_materialise("${{ github.token }}")),
    _red(
        "materialise-secrets-github-token",
        "materialise step env holds a token",
        _token_in_materialise("${{ secrets.GITHUB_TOKEN }}"),
    ),
    _red(
        "materialise-gh-token-name",
        "materialise step env holds a token",
        lambda d: _step(_jobs(d)["prepare"], "materialise")["env"].update({"GH_TOKEN": "x"}),
    ),
    _red("materialise-indexed-token", "materialise step env holds a token", _token_in_materialise("${{ github['token'] }}")),
    _red("materialise-tojson", "materialise step env holds a token", _token_in_materialise("${{ toJSON(github) }}")),
    _red("materialise-mixed-case", "materialise step env holds a token", _token_in_materialise("${{ GitHub.Token }}")),
    _red("execute-github-token", "execute step env holds a token", _token_in_execute("${{ github.token }}")),
    _red("execute-tojson-secrets", "execute step env holds a token", _token_in_execute("${{ toJSON(secrets) }}")),
    _red("workflow-level-env", "workflow-level env", lambda d: d[1].update({"env": {"GH_TOKEN": "${{ github.token }}"}})),
    _red("job-level-env", "job-level env", lambda d: _jobs(d)["run"].update({"env": {"T": "${{ secrets.GITHUB_TOKEN }}"}})),
    _red(
        "other-secret",
        "other than secrets.GITHUB_TOKEN",
        lambda d: _step(_jobs(d)["post"], "post")["env"].update({"K": "${{ secrets.ANTHROPIC_KEY }}"}),
    ),
    _red(
        "indexed-other-secret",
        "other than secrets.GITHUB_TOKEN",
        lambda d: _step(_jobs(d)["post"], "post")["env"].update({"K": "${{ secrets['X'] }}"}),
    ),
    _red(
        "run-job-other-verb",
        "not only the execute verb",
        lambda d: _set_run(_step(_jobs(d)["run"], "execute"), v.RUN_PREFIX + "post"),
    ),
    _red(
        "bare-working-directory",
        "bare variable in working-directory",
        lambda d: _named(_jobs(d)["run"], "Create the work directory").update({"working-directory": "$RUNNER_TEMP"}),
    ),
    _red(
        "upload-wrong-path",
        "path is not its work directory",
        lambda d: _named(_jobs(d)["run"], "Upload verdict results")["with"].update({"path": "elsewhere"}),
    ),
    _red("no-permissions-mapping", "no permissions mapping", lambda d: _jobs(d)["prepare"].pop("permissions")),
    _red(
        "verdict-third-upstream",
        "does not name exactly",
        lambda d: _on(d[1])["workflow_run"].update({"workflows": [*v.UPSTREAMS, "third"]}),
    ),
    _red(
        "verdict-missing-override-upstream",
        "does not name exactly",
        lambda d: _on(d[1])["workflow_run"].update({"workflows": ["persona-verdict-trigger"]}),
    ),
    _red(
        "verdict-old-run-condition",
        "run job condition",
        lambda d: _jobs(d)["run"].update({"if": "${{ !cancelled() && needs.prepare.result == 'success' }}"}),
    ),
    _red("verdict-prepare-no-actions-read", "permissions differ", lambda d: _jobs(d)["prepare"]["permissions"].pop("actions")),
    _red("verdict-run-name", "run-name is not", lambda d: d[1].update({"run-name": "x"})),
    _red("verdict-concurrency-group", "concurrency group", lambda d: d[1]["concurrency"].update({"group": "x"})),
    _red("override-id-token", "id-token", lambda d: _ojobs(d)["render"]["permissions"].update({"id-token": "write"})),
    _red(
        "override-secret",
        "secrets or id-token",
        lambda d: _ojobs(d)["render"]["steps"][0].update({"env": {"K": "${{ secrets.X }}"}}),
    ),
    _red(
        "override-extra-job", "jobs are not exactly", lambda d: _ojobs(d).update({"extra": {"permissions": {}, "steps": []}})
    ),
    _red(
        "override-env-on-render", "render job names an Environment", lambda d: _ojobs(d)["render"].update({"environment": "e"})
    ),
    _red("override-approve-no-env", "does not name the", lambda d: _ojobs(d)["approve"].pop("environment")),
    _red("override-approve-no-needs", "does not need render", lambda d: _ojobs(d)["approve"].pop("needs")),
    _red(
        "override-unpinned-uses",
        "not pinned",
        lambda d: _ojobs(d)["render"]["steps"][0].update({"uses": "actions/checkout@v7"}),
    ),
    _red(
        "override-sixth-input",
        "five string inputs",
        lambda d: _on(d[3])["workflow_dispatch"]["inputs"].update({"extra": {"type": "string"}}),
    ),
    _red("override-extra-trigger", "not exactly workflow_dispatch", lambda d: _on(d[3]).update({"push": {}})),
    _red("override-render-condition", "render condition", lambda d: _ojobs(d)["render"].update({"if": "true"})),
    _red(
        "override-other-verb",
        "three pinned lines",
        lambda d: _ostep(d, "override-render").update({"run": "python3 -I scripts/personas/run.py post"}),
    ),
    _red(
        "override-other-pip",
        "only pip line",
        lambda d: _ostep(d, "install-override-dependencies").update(
            {"run": "pip install pyyaml requests -c requirements.txt"}
        ),
    ),
    _red(
        "override-pip-in-approve",
        "approve job has a pip line",
        lambda d: _ojobs(d)["approve"]["steps"].append({"id": "p", "run": "pip install x"}),
    ),
    _red(
        "override-token-leak",
        "holds the token",
        lambda d: _ostep(d, "gate-opened").update({"env": {"X": "${{ github.token }}"}}),
    ),
    _red("override-run-without-id", "without an id", lambda d: _ostep(d, "gate-opened").pop("id")),
    _red(
        "override-permissions-widened",
        "permissions differ",
        lambda d: _ojobs(d)["render"]["permissions"].update({"checks": "write"}),
    ),
    _red("missing-prepare-job", "jobs are not exactly", lambda d: _jobs(d).pop("prepare")),
    _red("extra-job", "jobs are not exactly", lambda d: _jobs(d).update({"extra": {"permissions": {}, "steps": []}})),
]


def test_green_on_the_live_pair() -> None:
    assert v.check_tree(LIVE) == ([], 3)


def test_green_on_an_unedited_copy(tmp_path: Path) -> None:
    assert v.check_tree(_tree(tmp_path, None)) == ([], 3)


@pytest.mark.parametrize(("edit", "expect"), RED)
def test_red_fixture_is_refused(tmp_path: Path, edit: Callable[[Docs], None], expect: str) -> None:
    problems, _ = v.check_tree(_tree(tmp_path, edit))
    assert any(expect in p for p in problems), problems


def test_missing_files_are_refused(tmp_path: Path) -> None:
    problems, examined = v.check_tree(tmp_path)
    assert examined == 0 and sum("is missing" in p for p in problems) == 4


def test_stale_evidence_row_is_refused(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        d[2]["artifact_uploads"] = [r for r in d[2]["artifact_uploads"] if r.get("artifact") != "persona-verdict-results"]

    problems, _ = v.check_tree(_tree(tmp_path, edit))
    assert any("missing=" in p for p in problems)


def test_registered_check_reports_and_passes(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    v.validate_persona_verdict_chain(failed)
    assert failed == [] and "PASS: 3 workflow(s)" in capsys.readouterr().out


def test_registered_in_fast_tier() -> None:
    assert "validate_persona_verdict_chain" in [step.name for step in registry.pre_sequence()]
    assert registry.resolve("validate_persona_verdict_chain") is v.validate_persona_verdict_chain


def test_unparseable_workflow_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path, None)
    (root / v.VERDICT).write_text("a: [unclosed\n", encoding="utf-8")
    assert any("does not parse" in p for p in v.check_tree(root)[0])


def test_registered_check_reports_each_problem(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(v, "check_tree", lambda root: (["boom"], 3))
    failed: list[str] = []
    v.validate_persona_verdict_chain(failed)
    assert failed == ["persona verdict chain: boom"] and "FAIL: boom" in capsys.readouterr().out
