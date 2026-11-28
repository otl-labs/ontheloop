"""The wake verb: one comment per run with a typed TLDR, bot-only exact markers, allowlist and fork skips, fault wake."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import findings_record, wake

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
REPO = "o/r"
ROUTES = ["route_a", "route_b", "route_c", "route_d"]
FORBIDDEN = ("success", "failure", "PROCEED", "REVISE", "BLOCK", "NO_FLAGS", "FLAGS_FOUND")
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GH_TOKEN": "t",
    "POST_RESULT": "success",
    "GITHUB_RUN_ID": "77",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_SERVER_URL": "https://github.com",
}
EVENT = {"workflow_run": {"head_repository": {"full_name": REPO}}}
RUN_URL = "https://github.com/o/r/actions/runs/77"


class Fake:
    def __init__(self, comments: list[dict[str, Any]] | None = None, landed: list[str] | None = None) -> None:
        self.comments = comments or []
        self.landed = ROUTES if landed is None else landed
        self.posts: list[str] = []
        self.checks_fail = False
        self.comments_fail = False
        self.post_statuses: list[int] = []
        self.runs: list[dict[str, Any]] | None = None

    def request(self, method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        if method == "GET" and "/check-runs" in url:
            if self.checks_fail:
                return 500, b""
            rows = self.runs if self.runs is not None else [{"name": f"persona-verdict/{r}"} for r in self.landed]
            return 200, json.dumps({"check_runs": rows}).encode()
        if method == "GET":
            if self.comments_fail:
                return 500, b""
            return 200, json.dumps(self.comments).encode()
        status = self.post_statuses.pop(0) if self.post_statuses else 201
        if status == 201:
            self.posts.append(body["body"])
            self.comments.append({"user": {"login": wake.BOT_LOGIN, "type": "Bot"}, "body": body["body"]})
        return status, b"{}"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(wake.resolve_pr, "request", f.request)
    return f


def _prepare(work: Path, resolution: dict[str, Any] | None = None, routes: list[Any] | None = None) -> None:
    prepared = work / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    res = {"resolved": True, "allowlisted": True, "head_sha": SHA, "number": 5}
    res.update(resolution or {})
    (prepared / "resolution.json").write_text(json.dumps(res), encoding="utf-8")
    (prepared / "routes.json").write_text(json.dumps({"routes": ROUTES if routes is None else routes}), encoding="utf-8")


def _run(work: Path, env: dict[str, str] | None = None, event: dict[str, Any] | None = None) -> int:
    return wake.wake(work, event or EVENT, {**ENV, **(env or {})}, sleep=lambda _s: None)


def test_a_carried_only_run_wakes(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path, routes=[])
    (tmp_path / "prepared" / "routes.json").write_text(json.dumps({"routes": [], "carried": [{"route": "route_a"}, 5]}))
    fake.landed = ["route_a"]
    assert _run(tmp_path) == 0
    assert len(fake.posts) == 1 and _marker("route_a") in fake.posts[0]
    (tmp_path / "prepared" / "routes.json").write_text(json.dumps({"routes": [], "carried": []}))
    fake.posts.clear()
    assert _run(tmp_path) == 0 and fake.posts == []


def test_wake_routes_path_equals_the_route_verbs() -> None:
    from scripts.personas import route

    assert wake.ROUTES == route.ROUTES


def test_wake_and_override_import_without_pydantic() -> None:
    code = (
        "import sys; sys.modules['pydantic'] = None; sys.modules['jsonschema'] = None\n"
        "import scripts.personas.wake, scripts.personas.override\n"
        "assert 'scripts.personas.rescout' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def _marker(route: str) -> str:
    return f"<!-- persona-verdict-wake:{SHA}:{route} -->"


def test_one_comment_with_four_markers_and_exact_text(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    assert _run(tmp_path) == 0
    assert len(fake.posts) == 1
    assert fake.posts[0] == "\n".join([*(_marker(r) for r in ROUTES), wake.WAKE_TEXT.format(sha=SHA, url=RUN_URL)])
    assert "carries no verdict" not in fake.posts[0] and "authoritative" in fake.posts[0]


APP = 99


def _record_text(route: str, verdict: str = "REVISE", head: str = SHA) -> str:
    result = {
        "verdict": verdict,
        "findings": [{"id": "a:1:testing", "severity": "High", "origin": "judgement", "anchor": "a:1"}],
    }
    facts = {"route": route, "head_sha": head, "run_ref": "litellm:gha:77/1/run/1/run", "run_outcome": "verdict"}
    return "<!-- persona-verdict-record v1 {} -->\n" + findings_record.render(facts, result) + "\n- a:1:testing: SECRET TITLE"


def _run_row(route: str, cid: int, app: int = APP, text: str | None = None, conclusion: str = "failure") -> dict[str, Any]:
    out = {"title": "SECRET TITLE", "summary": "SECRET SUMMARY", "text": _record_text(route) if text is None else text}
    return {"name": f"persona-verdict/{route}", "id": cid, "conclusion": conclusion, "app": {"id": app}, "output": out}


def test_the_wake_comment_carries_a_tldr_from_verdict_app_runs_only(
    tmp_path: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake.override, "app_id", lambda: APP)
    _prepare(tmp_path)
    fake.runs = [
        _run_row("route_a", 5),
        _run_row("route_a", 3, text='<!-- persona-verdict-findings v1 {"route":"route_a","run_outcome":"verdict"} -->'),
        _run_row("route_b", 6, app=7),
        _run_row("route_c", 8, text="no record here", conclusion="success"),
        _run_row("route_d", 9, text=_record_text("route_other")),
    ]
    assert _run(tmp_path) == 0
    body = fake.posts[0]
    link = "https://github.com/o/r/runs/"
    assert "For people; the check run is authoritative. Snapshot at run " + RUN_URL + "." in body
    assert f"persona-verdict/route_a (failure): REVISE 1 findings High=1 judgement=1 {link}5" in body
    assert "route_b" not in body.split("For people;")[1]
    assert f"persona-verdict/route_c (success): no findings record {link}8" in body
    assert f"persona-verdict/route_d (failure): no findings record {link}9" in body
    assert "SECRET" not in body
    fake.posts.clear()
    fake.comments.clear()
    monkeypatch.setattr(wake.override, "app_id", lambda: (_ for _ in ()).throw(wake.override.Refused("no verdict App")))
    assert _run(tmp_path) == 0 and len(fake.posts) == 1 and "For people" not in fake.posts[0]
    fake.comments.clear()
    fake.posts.clear()
    monkeypatch.setattr(wake.override, "app_id", lambda: APP)
    fake.runs = [_run_row("elsewhere", 4)]
    assert _run(tmp_path) == 0 and "For people" not in fake.posts[0]


def test_a_second_call_posts_nothing(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    _run(tmp_path)
    _run(tmp_path)
    assert len(fake.posts) == 1


def test_a_marker_by_another_author_does_not_suppress(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.comments = [{"user": {"login": "someone", "type": "User"}, "body": _marker(ROUTES[0])}]
    _run(tmp_path)
    assert len(fake.posts) == 1 and _marker(ROUTES[0]) in fake.posts[0]


def test_a_marker_that_only_prefixes_a_longer_one_does_not_count(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path, routes=["route_c"])
    fake.landed = ["route_c"]
    longer = f"<!-- persona-verdict-wake:{SHA}:route_c_extra -->"
    fake.comments = [{"user": {"login": wake.BOT_LOGIN, "type": "Bot"}, "body": longer + "\n" + _marker("route_c") + "x"}]
    _run(tmp_path)
    assert len(fake.posts) == 1


def test_any_branch_is_woken_and_no_branch_is_read(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    event = {"workflow_run": {"head_repository": {"full_name": REPO}, "head_branch": "whatever/odd-branch"}}
    _run(tmp_path, event=event)
    assert len(fake.posts) == 1


@pytest.mark.parametrize(
    "case",
    ["not_allowlisted", "fork", "unresolved", "empty", "absent", "bad_sha"],
)
def test_skips_post_nothing(tmp_path: Path, fake: Fake, case: str, capsys: pytest.CaptureFixture[str]) -> None:
    event = EVENT
    if case == "absent":
        pass
    elif case == "not_allowlisted":
        _prepare(tmp_path, {"allowlisted": False})
    elif case == "unresolved":
        _prepare(tmp_path, {"resolved": False})
    elif case == "empty":
        _prepare(tmp_path, routes=[])
    elif case == "bad_sha":
        _prepare(tmp_path, {"head_sha": "A" * 40})
    else:
        _prepare(tmp_path)
        event = {"workflow_run": {"head_repository": {"full_name": "fork/r"}}}
    assert _run(tmp_path, {"POST_RESULT": "failure"}, event) == 0
    assert fake.posts == []
    assert "wake skipped reason=" in capsys.readouterr().out


def test_wake_reads_no_git_ops_contract() -> None:
    tree = ast.parse((ROOT / "scripts/personas/wake.py").read_text(encoding="utf-8"))
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("git-ops" in s or "agent_branch_prefixes" in s for s in strings)
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count("open") == 1


def test_a_comment_listing_failure_still_posts(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.comments_fail = True
    _run(tmp_path)
    assert len(fake.posts) == 1


def test_a_check_run_listing_failing_three_times_posts_the_fault_only(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.checks_fail = True
    _run(tmp_path)
    assert fake.posts == ["<!-- persona-verdict-fault:77.1 -->\n" + wake.FAULT_TEXT.format(sha=SHA, url=RUN_URL)]
    assert "persona-verdict-wake:" not in fake.posts[0]


def test_a_failed_post_result_or_unlanded_route_posts_the_fault_once(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.landed = ROUTES[:3]
    _run(tmp_path)
    _run(tmp_path)
    faults = [p for p in fake.posts if "persona-verdict-fault:77.1" in p]
    assert len(faults) == 1 and len(fake.posts) == 2
    assert not any(word in faults[0] for word in FORBIDDEN)
    fake.posts.clear()
    fake.comments.clear()
    _run(tmp_path, {"POST_RESULT": "failure"})
    assert sum("persona-verdict-fault:" in p for p in fake.posts) == 1


def test_a_bad_route_id_is_never_interpolated_and_posts_the_fault(tmp_path: Path, fake: Fake) -> None:
    bad = "x --> <!-- evil"
    _prepare(tmp_path, routes=[ROUTES[0], bad])
    fake.landed = [ROUTES[0], bad]
    _run(tmp_path)
    assert len(fake.posts) == 2
    assert all("evil" not in p for p in fake.posts)
    assert any("persona-verdict-fault:77.1" in p for p in fake.posts)
    assert re.search(re.escape(_marker(ROUTES[0])), "\n".join(fake.posts))


def test_a_post_retry_relists_and_does_not_duplicate(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.post_statuses = [500, 201]
    _run(tmp_path)
    assert len(fake.posts) == 1
    fake2 = Fake()
    fake2.post_statuses = [500]
    fake2.comments = []
    posted_by_failed_call = {
        "user": {"login": wake.BOT_LOGIN, "type": "Bot"},
        "body": "\n".join(_marker(r) for r in ROUTES),
    }
    orig = fake2.request

    def request(method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        status, raw = orig(method, url, token, body)
        if method == "POST":
            fake2.comments.append(posted_by_failed_call)
        return status, raw

    wake.resolve_pr.request = request  # type: ignore[assignment]
    try:
        _prepare(tmp_path / "b")
        _run(tmp_path / "b")
    finally:
        wake.resolve_pr.request = fake.request  # type: ignore[assignment]
    assert fake2.posts == []


def test_a_final_post_failure_exits_non_zero(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    fake.post_statuses = [500, 500, 500]
    with pytest.raises(RuntimeError):
        _run(tmp_path)
