"""The override module on recorded API shapes: record, digest, extractors, origin, approval, lookup and the render verb."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import override, resolve_pr
from scripts.session import merge_checks

REPO = "o/r"
HEAD = "a" * 40
ROUTE = "verdict_probe.block"
IDS = ["probe:block-1", "probe:block-2"]
DIGEST = override.ids_digest(IDS)
TITLE = f"persona-verdict-override 7 {HEAD} {ROUTE} 55 {DIGEST}"
PRODUCER = f"persona-verdict pull_request {HEAD}"


def _text(**over: Any) -> str:
    rec = {"route": ROUTE, "head_sha": HEAD, "verdict": "BLOCK", "blocking_ids": IDS, "run_id": "900"}
    rec.update(over)
    return f"{override.RECORD_PREFIX}{json.dumps(rec)} -->\n- probe:block-1: first <b>&\n- probe:block-2: second `x`"


class Api:
    def __init__(self) -> None:
        self.routes: dict[str, Any] = {
            "/repos/o/r/pulls/7": {"state": "open", "head": {"sha": HEAD, "repo": {"full_name": REPO}}},
            "/repos/o/r/check-runs/55": {
                "app": {"slug": "github-actions"},
                "name": f"persona-verdict/{ROUTE}",
                "head_sha": HEAD,
                "check_suite": {"id": 120013128966},
                "output": {"text": _text()},
            },
            f"/repos/o/r/commits/{HEAD}/check-runs": {"check_runs": [{"id": 55}, {"id": 40}]},
            "/repos/o/r/actions/runs/900": {
                "path": override.VERDICT_WORKFLOW,
                "event": "workflow_run",
                "head_branch": "main",
                "check_suite_id": 120013228652,
                "display_title": PRODUCER,
            },
            "/repos/o/r/actions/runs/70": {
                "path": override.OVERRIDE_WORKFLOW,
                "event": "workflow_dispatch",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "display_title": TITLE,
                "html_url": "https://github.com/o/r/actions/runs/70",
            },
            "/repos/o/r/actions/runs/70/jobs": {
                "jobs": [{"name": "approve", "conclusion": "success", "completed_at": "2026-11-17T15:00:00Z"}]
            },
            "/repos/o/r/actions/runs/70/approvals": [
                {"state": "approved", "environments": [{"name": override.ENVIRONMENT}], "user": {"login": "op"}}
            ],
        }

    def __call__(self, path: str, token: str | None) -> Any:
        key = path.split("?")[0]
        if "/workflows/" in path:
            return {"workflow_runs": [{"id": 71, "display_title": "other"}, {"id": 70, "display_title": TITLE}]}
        if key not in self.routes:
            raise RuntimeError("404")
        return self.routes[key]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Api:
    fake = Api()
    monkeypatch.setattr(resolve_pr, "get_json", fake)
    return fake


def _env(**over: str) -> dict[str, str]:
    env = {
        "GITHUB_REPOSITORY": REPO,
        "INPUT_PR": "7",
        "INPUT_HEAD_SHA": HEAD,
        "INPUT_ROUTE": ROUTE,
        "INPUT_CHECK_RUN_ID": "55",
        "INPUT_IDS_DIGEST": DIGEST,
    }
    env.update(over)
    return env


def test_record_round_trip_and_malformed_cases() -> None:
    line = override.render_record("r", HEAD, "BLOCK", ["b", "a", "a"], "9")
    rec = override.parse_record(line + "\nrest")
    assert rec == {"route": "r", "head_sha": HEAD, "verdict": "BLOCK", "blocking_ids": ["a", "b"], "run_id": "9"}
    assert override.parse_record("x\n" + line) is None
    assert override.parse_record(line.replace("v1", "v2")) is None
    assert override.parse_record(override.RECORD_PREFIX + "{bad} -->") is None
    assert override.parse_record(override.RECORD_PREFIX + '{"route": 1} -->') is None
    assert override.parse_record(None) is None
    unsorted = line.replace('["a", "b"]', '["b", "a"]')
    assert override.parse_record(unsorted) is None


def test_digest_is_order_independent() -> None:
    assert override.ids_digest(["b", "a"]) == override.ids_digest(["a", "b"]) != override.ids_digest(["a"])


def test_probe_extractor_and_registry() -> None:
    fn = override.extractor_for(ROUTE)
    assert fn is not None and override.extractor_for("code_reviewer") is None
    out = {"verdict": "REVISE", "findings": [{"id": "x", "blocking": True}, {"id": "y", "blocking": False}]}
    assert fn(out) == ("REVISE", ["x"])
    assert fn({**out, "verdict": "FLAGS_FOUND"}) == ("FLAGS_FOUND", ["x", "y"])
    override.register_extractor("zz_persona", lambda o: ("BLOCK", ["q"]))
    try:
        assert override.extractor_for("zz_persona") is not None
    finally:
        del override.EXTRACTORS["zz_persona"]


def test_overridable_follows_persona_class(monkeypatch: pytest.MonkeyPatch) -> None:
    assert override.overridable(ROUTE)
    assert not override.overridable("code_reviewer") and override.overridable("decision_scout")
    monkeypatch.setattr(merge_checks, "PERSONA_CLASSES", {"code_reviewer": "plan"})
    assert override.overridable("code_reviewer") and not override.overridable("decision_scout")


@pytest.mark.parametrize(
    "path,patch",
    [
        ("/repos/o/r/check-runs/55", {"app": {"slug": "other"}}),
        ("/repos/o/r/check-runs/55", {"name": "persona-verdict/x"}),
        ("/repos/o/r/check-runs/55", {"head_sha": "b" * 40}),
        ("/repos/o/r/check-runs/55", {"output": {"text": "no record"}}),
        ("/repos/o/r/check-runs/55", {"output": {"text": _text(route="x")}}),
        ("/repos/o/r/actions/runs/900", {"path": "other.yml"}),
        ("/repos/o/r/actions/runs/900", {"event": "push"}),
        ("/repos/o/r/actions/runs/900", {"head_branch": "feat"}),
        ("/repos/o/r/actions/runs/900", {"display_title": "x"}),
        (f"/repos/o/r/commits/{HEAD}/check-runs", {"check_runs": [{"id": 55}, {"id": 56}]}),
        (f"/repos/o/r/commits/{HEAD}/check-runs", {"check_runs": []}),
    ],
)
def test_origin_refuses(api: Api, path: str, patch: dict[str, Any]) -> None:
    api.routes[path] = {**api.routes[path], **patch}
    with pytest.raises(override.Refused):
        override.origin(REPO, "t", HEAD, ROUTE, 55)


def test_origin_admits_a_check_in_a_different_suite_from_its_producing_run(api: Api) -> None:
    # Live shape (run 44335420386): a workflow_run verdict run on main posts into the head's own check suite.
    check = api.routes["/repos/o/r/check-runs/55"]["check_suite"]["id"]
    assert api.routes["/repos/o/r/actions/runs/900"]["check_suite_id"] != check
    rec, _ = override.origin(REPO, "t", HEAD, ROUTE, 55)
    assert rec["run_id"] == "900"


def test_origin_refuses_an_unreadable_producing_run(api: Api) -> None:
    api.routes["/repos/o/r/actions/runs/900"] = ["not", "a", "run"]
    with pytest.raises(override.Refused, match="not a verdict run"):
        override.origin(REPO, "t", HEAD, ROUTE, 55)


def test_origin_admits_a_genuine_check(api: Api) -> None:
    rec, text = override.origin(REPO, "t", HEAD, ROUTE, 55)
    assert rec["blocking_ids"] == IDS and "probe:block-1" in text


def test_approval_admits_the_approved_shape(api: Api) -> None:
    got = override.approval(REPO, "t", 70)
    assert got and got["approver"] == "op" and got["pr"] == 7 and got["approved_at"] == "2026-11-17T15:00:00Z"


@pytest.mark.parametrize(
    "path,patch",
    [
        ("/repos/o/r/actions/runs/70", {"status": "waiting"}),
        ("/repos/o/r/actions/runs/70", {"conclusion": "failure"}),
        ("/repos/o/r/actions/runs/70", {"conclusion": "cancelled"}),
        ("/repos/o/r/actions/runs/70", {"head_branch": "feat"}),
        ("/repos/o/r/actions/runs/70", {"path": "other.yml"}),
        ("/repos/o/r/actions/runs/70", {"event": "push"}),
        ("/repos/o/r/actions/runs/70", {"display_title": "persona-verdict-override 0 x y z w"}),
        ("/repos/o/r/actions/runs/70/jobs", {"jobs": [{"name": "approve", "conclusion": "failure"}]}),
    ],
)
def test_approval_refuses(api: Api, path: str, patch: dict[str, Any]) -> None:
    api.routes[path] = {**api.routes[path], **patch}
    assert override.approval(REPO, "t", 70) is None


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [{"state": "rejected", "environments": [{"name": override.ENVIRONMENT}], "user": {"login": "op"}}],
        [{"state": "approved", "environments": [{"name": "other"}], "user": {"login": "op"}}],
        {"not": "a list"},
    ],
)
def test_approval_needs_an_approved_entry_for_the_environment(api: Api, entries: Any) -> None:
    api.routes["/repos/o/r/actions/runs/70/approvals"] = entries
    assert override.approval(REPO, "t", 70) is None


def test_lookup_takes_the_matching_approved_run(api: Api) -> None:
    got = override.lookup(REPO, "t", 7, HEAD, ROUTE, DIGEST)
    assert got and got["run_id"] == 70
    assert override.lookup(REPO, "t", 8, HEAD, ROUTE, DIGEST) is None
    assert override.lookup(REPO, "t", 7, HEAD, ROUTE, "0" * 64) is None


def test_render_ok_writes_each_id_escaped(api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "s.md"
    monkeypatch.setattr(override.os, "environ", _env(GITHUB_STEP_SUMMARY=str(summary)))
    assert override.main([]) == 0
    body = summary.read_text()
    assert "probe:block-1: first &lt;b&gt;&amp;" in body and "probe:block-2: second x" in body and "`x`" not in body


@pytest.mark.parametrize(
    "env,mutate",
    [
        ({"INPUT_PR": "x"}, None),
        ({"INPUT_HEAD_SHA": "b" * 40}, None),
        ({"INPUT_IDS_DIGEST": "0" * 64}, None),
        ({}, ("/repos/o/r/pulls/7", {"state": "closed"})),
        ({}, ("/repos/o/r/pulls/7", {"head": {"sha": HEAD, "repo": {"full_name": "fork/r"}}})),
        ({}, ("/repos/o/r/check-runs/55", {"output": {"text": _text(verdict="PROCEED")}})),
        ({}, ("/repos/o/r/check-runs/55", {"output": {"text": _text(blocking_ids=[])}})),
        ({"INPUT_ROUTE": "code_reviewer"}, None),
        ({"INPUT_CHECK_RUN_ID": "56"}, None),
    ],
)
def test_render_refusals(
    api: Api, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, env: dict[str, str], mutate: Any
) -> None:
    if mutate:
        api.routes[mutate[0]] = {**api.routes[mutate[0]], **mutate[1]}
    monkeypatch.setattr(override.os, "environ", _env(**env))
    assert override.main([]) == 1
    assert capsys.readouterr().out.startswith("PERSONA-OVERRIDE refused:")


def test_render_refuses_an_id_set_the_record_does_not_hold(
    api: Api, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(override.os, "environ", _env(INPUT_IDS_DIGEST=override.ids_digest(["probe:block-1"])))
    assert override.main([]) == 1


def test_render_escapes_a_fence_breaking_finding_id(api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = ["x```\n[evil](u)"]
    api.routes["/repos/o/r/check-runs/55"]["output"] = {"text": _text(blocking_ids=ids)}
    summary = tmp_path / "s.md"
    monkeypatch.setattr(
        override.os, "environ", _env(INPUT_IDS_DIGEST=override.ids_digest(ids), GITHUB_STEP_SUMMARY=str(summary))
    )
    assert override.main([]) == 0
    assert summary.read_text().count("```") == 2
