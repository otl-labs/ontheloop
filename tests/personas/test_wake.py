"""The wake verb: one value-free comment per run, bot-only exact markers, allowlist and fork skips, fault wake."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import wake

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
REPO = "o/r"
ROUTES = ["verdict_probe.proceed", "verdict_probe.revise", "verdict_probe.fail", "verdict_probe.malformed"]
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

    def request(self, method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        if method == "GET" and "/check-runs" in url:
            if self.checks_fail:
                return 500, b""
            return 200, json.dumps({"check_runs": [{"name": f"persona-verdict/{r}"} for r in self.landed]}).encode()
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


def _marker(route: str) -> str:
    return f"<!-- persona-verdict-wake:{SHA}:{route} -->"


def test_one_comment_with_four_markers_and_exact_text(tmp_path: Path, fake: Fake) -> None:
    _prepare(tmp_path)
    assert _run(tmp_path) == 0
    assert len(fake.posts) == 1
    assert fake.posts[0] == "\n".join([*(_marker(r) for r in ROUTES), wake.WAKE_TEXT.format(sha=SHA, url=RUN_URL)])
    assert not any(word in fake.posts[0] for word in FORBIDDEN)


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
    _prepare(tmp_path, routes=["verdict_probe.fail"])
    fake.landed = ["verdict_probe.fail"]
    longer = f"<!-- persona-verdict-wake:{SHA}:verdict_probe.fail_extra -->"
    fake.comments = [
        {"user": {"login": wake.BOT_LOGIN, "type": "Bot"}, "body": longer + "\n" + _marker("verdict_probe.fail") + "x"}
    ]
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
