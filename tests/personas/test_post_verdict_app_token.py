"""post_all through the real app_token session: reads on GH_TOKEN first, one App token for the POSTs only, revoke always."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import app_token, oidc_assume, override, post_verdict, resolve_pr
from src.data.handlers import github_app_token

ROOT = Path(__file__).resolve().parents[2]
REPO = "o/r"
SHA = "e" * 40
APP_ID = 424242
GH_TOKEN = "gh-read-token"  # pragma: allowlist secret
APP_TOKEN = "ghs_" + "B" * 36
SUB = "repo:o/r:environment:persona-verdict-app"
REF = f"{REPO}/.github/workflows/persona-verdict.yml@refs/heads/main"
BLOCK = "verdict_probe.block"
BLOCK_OUT = json.dumps(
    {"outcome": "success", "verdict": "BLOCK", "findings": [{"id": "probe:block-1", "title": "one", "blocking": True}]}
)
PROCEED_OUT = json.dumps({"outcome": "success", "verdict": "PROCEED"})


class FakeApi:
    instances: list[FakeApi] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        self.calls: list[tuple[str, str]] = []
        FakeApi.instances.append(self)

    def call(self, method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        if method == "GET":
            return 200, {"id": 9}
        if method == "POST":
            return 201, {"token": APP_TOKEN, "permissions": dict(app_token.PERMISSIONS), "repository_selection": "selected"}
        return 204, None


class FakeBoto:
    class Session:
        def __init__(self, **kw: Any) -> None:
            pass

        def client(self, name: str) -> Any:
            return self

        def get_parameter(self, **kw: Any) -> dict[str, Any]:
            return {"Parameter": {"Value": "pem"}}


def _prepare(tmp_path: Path, routes: dict[str, str]) -> Path:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "results").mkdir()
    res = {"resolved": True, "head_sha": SHA, "allowlisted": True, "number": 7}
    (work / "prepared" / "resolution.json").write_text(json.dumps(res))
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": list(routes), "plan_paths": []}))
    for route, out in routes.items():
        result = {"route": route, "exit_code": 0, "timed_out": False, "stdout": out}
        (work / "results" / f"{route}.json").write_text(json.dumps(result))
    return work


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"log": [], "app": APP_ID, "post_error": None}
    FakeApi.instances = []
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("PERSONA_AWS_REGION", "xx-test-1")
    monkeypatch.setattr(override, "app_id", lambda: APP_ID)
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: state["log"].append(("oidc", "token")) or "a.b.c")
    monkeypatch.setattr(oidc_assume, "claims", lambda jwt: {"sub": SUB, "job_workflow_ref": REF})
    creds = {"AccessKeyId": "k", "SecretAccessKey": "s", "SessionToken": "t"}  # pragma: allowlist secret
    monkeypatch.setattr(oidc_assume, "assume", lambda role, jwt, env=None: oidc_assume.Outcome(200, "ok", creds))
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto)
    monkeypatch.setattr(github_app_token, "app_jwt", lambda client_id, pem, now: "a.b.c")
    monkeypatch.setattr(github_app_token, "GitHubApi", FakeApi)

    def get_json(path: str, token: str | None) -> Any:
        state["log"].append(("read", token))
        return {}

    def request(method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        state["log"].append((method, token))
        if state["post_error"] and len([e for e in state["log"] if e[0] == "POST"]) == 2:
            raise state["post_error"]
        return 201, json.dumps({"app": {"id": state["app"]}}).encode()

    monkeypatch.setattr(resolve_pr, "get_json", get_json)
    monkeypatch.setattr(resolve_pr, "request", request)
    return state


def test_every_gh_token_read_happens_before_the_first_oidc_request(wired: dict[str, Any], tmp_path: Path) -> None:
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT, "verdict_probe.proceed": PROCEED_OUT})
    assert post_verdict.post_all(ROOT, work, REPO, GH_TOKEN) == [
        f"persona-verdict/{BLOCK}",
        "persona-verdict/verdict_probe.proceed",
    ]
    log = wired["log"]
    first_oidc = next(i for i, e in enumerate(log) if e[0] == "oidc")
    assert any(e == ("read", GH_TOKEN) for e in log[:first_oidc])
    assert not [e for e in log[first_oidc:] if e[0] == "read"]


def test_the_posts_carry_the_app_token_and_the_reads_carry_gh_token(wired: dict[str, Any], tmp_path: Path) -> None:
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT})
    post_verdict.post_all(ROOT, work, REPO, GH_TOKEN)
    assert [e for e in wired["log"] if e[0] == "POST"] == [("POST", APP_TOKEN)]
    assert {e[1] for e in wired["log"] if e[0] == "read"} == {GH_TOKEN}
    assert [c[0] for c in FakeApi.instances[0].calls][-1] == "DELETE"


def test_zero_bodies_make_no_oidc_request(wired: dict[str, Any], tmp_path: Path) -> None:
    work = _prepare(tmp_path, {})
    assert post_verdict.post_all(ROOT, work, REPO, GH_TOKEN) == []
    assert not [e for e in wired["log"] if e[0] == "oidc"] and FakeApi.instances == []


def test_a_created_run_from_another_app_stops_before_the_second_post_and_revokes(
    wired: dict[str, Any], tmp_path: Path
) -> None:
    wired["app"] = 15368
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT, "verdict_probe.proceed": PROCEED_OUT})
    with pytest.raises(RuntimeError, match="not from the verdict App"):
        post_verdict.post_all(ROOT, work, REPO, GH_TOKEN)
    assert len([e for e in wired["log"] if e[0] == "POST"]) == 1
    assert [c[0] for c in FakeApi.instances[0].calls][-1] == "DELETE"


def test_an_exception_mid_post_revokes(wired: dict[str, Any], tmp_path: Path) -> None:
    wired["post_error"] = OSError("network")
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT, "verdict_probe.proceed": PROCEED_OUT})
    with pytest.raises(OSError):
        post_verdict.post_all(ROOT, work, REPO, GH_TOKEN)
    assert [c[0] for c in FakeApi.instances[0].calls][-1] == "DELETE"


def test_a_revoke_that_raises_makes_main_exit_non_zero(
    wired: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Failing(FakeApi):
        def call(self, method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
            if method == "DELETE":
                raise github_app_token.GitHubApiError(500, method, path, "boom")
            return super().call(method, path, token, body)

    monkeypatch.setattr(github_app_token, "GitHubApi", Failing)
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT})
    monkeypatch.chdir(work)
    monkeypatch.setenv("GH_TOKEN", GH_TOKEN)
    assert post_verdict.main([]) == 1
    out = capsys.readouterr().out
    assert "APP-TOKEN FAIL revoke:GitHubApiError" in out and "PERSONA-VERDICT posted" not in out


def test_a_hostile_results_file_changes_only_its_own_route_body(wired: dict[str, Any], tmp_path: Path) -> None:
    work = _prepare(tmp_path, {"verdict_probe.proceed": PROCEED_OUT})
    (work / "results" / "other.json").write_text(json.dumps({"route": "other", "stdout": PROCEED_OUT}))
    (work / "results" / "verdict_probe.proceed.json").unlink()
    (work / "results" / "verdict_probe.proceed.json").symlink_to(work / "results" / "other.json")
    hostile = {
        "route": "verdict_probe.proceed",
        "exit_code": 0,
        "stdout": "../../etc/x",
        "env": {"GH_TOKEN": "x"},
        "path": "/x",
    }
    (work / "results" / "other.json").write_text(json.dumps(hostile))
    bodies = post_verdict.plan_all(ROOT, work, REPO, GH_TOKEN)
    assert [b["name"] for b in bodies] == ["persona-verdict/verdict_probe.proceed"]
    assert bodies[0]["conclusion"] == "failure" and bodies[0]["head_sha"] == SHA
    assert not [e for e in wired["log"] if e[0] == "oidc"]


SIGTERM_CHILD = textwrap.dedent(
    """
    import os, signal, sys
    sys.path.insert(0, {root!r})
    from scripts.personas import app_token, oidc_assume, override
    from src.data.handlers import github_app_token

    revoked = []
    class Api:
        def call(self, method, path, token, body=None):
            if method == "GET": return 200, {{"id": 9}}
            if method == "POST":
                return 201, {{"token": "t", "permissions": dict(app_token.PERMISSIONS), "repository_selection": "selected"}}
            revoked.append(1); return 204, None
    class Boto:
        class Session:
            def __init__(self, **kw): pass
            def client(self, name): return self
            def get_parameter(self, **kw): return {{"Parameter": {{"Value": "pem"}}}}
    sys.modules["boto3"] = Boto
    override.app_id = lambda: 5
    oidc_assume.token = lambda env=None: "a.b.c"
    oidc_assume.claims = lambda jwt: {{"sub": {sub!r}, "job_workflow_ref": {ref!r}}}
    CREDS = {{"AccessKeyId": "k", "SecretAccessKey": "s", "SessionToken": "t"}}
    oidc_assume.assume = lambda role, jwt, env=None: oidc_assume.Outcome(200, "ok", CREDS)
    github_app_token.app_jwt = lambda c, p, n: "a.b.c"
    github_app_token.GitHubApi = Api
    os.environ.update(GITHUB_REPOSITORY={repo!r}, PERSONA_AWS_REGION="xx-test-1")
    try:
        with app_token.session({repo!r}):
            os.kill(os.getpid(), signal.SIGTERM)
    except KeyboardInterrupt:
        print("interrupted revoked=%d" % len(revoked))
    """
)


def test_a_sigterm_after_the_mint_revokes_once() -> None:
    script = SIGTERM_CHILD.format(root=str(ROOT), sub=SUB, ref=REF, repo=REPO)
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.stdout.splitlines()[-1] == "interrupted revoked=1", done.stdout + done.stderr[-400:]


def test_a_foreign_app_failure_prints_one_fixed_line_and_exits_one(
    wired: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wired["app"] = 15368
    work = _prepare(tmp_path, {BLOCK: BLOCK_OUT})
    monkeypatch.chdir(work)
    monkeypatch.setenv("GH_TOKEN", GH_TOKEN)
    assert post_verdict.main([]) == 1
    assert "PERSONA-VERDICT FAIL RuntimeError" in capsys.readouterr().out
