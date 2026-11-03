"""Tests for scripts.session.deployment_approval_probe (rec-4695): classification, pinning, hygiene. No network."""

from __future__ import annotations

import inspect
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from scripts.session import deployment_approval_probe as probe

REPO = "otl-labs/ontheloop"
SENTINEL = "sentinel-token-value-1234567890"
RID = {"x-github-request-id": "ABCD:1"}


class FakeResponse:
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers if headers is not None else dict(RID)
        self._raw = json.dumps(body).encode() if body is not None else b""

    def read(self) -> bytes:
        return self._raw


class FakeOpener:
    """Routes (method, path-suffix) to a response; records every request."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.requests: list[Any] = []

    def open(self, req: Any, timeout: float = 0) -> FakeResponse:
        self.requests.append(req)
        path = req.full_url.removeprefix(probe._API)
        for (method, suffix), resp in self.routes.items():
            if req.get_method() == method and path.endswith(suffix):
                if isinstance(resp, Exception):
                    raise resp
                if resp.status >= 300:
                    raise urllib.error.HTTPError(req.full_url, resp.status, "x", resp.headers, io.BytesIO(resp._raw))
                return resp
        raise urllib.error.URLError("no route")

    def posts(self) -> list[Any]:
        return [r for r in self.requests if r.get_method() == "POST"]


def run_obj(**over: Any) -> dict[str, Any]:
    base = {
        "path": probe._WORKFLOW_PATH,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "status": "waiting",
    }
    return {**base, **over}


def pending(name: str = "deployment-approval-probe", can: bool | None = True) -> list[dict[str, Any]]:
    entry: dict[str, Any] = {"environment": {"id": 77, "name": name}, "reviewers": [{"reviewer": {"login": "op"}}]}
    if can is not None:
        entry["current_user_can_approve"] = can
    return [entry]


def routes(post: Any, *, run: Any = None, pend: Any = None, repo: Any = None) -> dict[tuple[str, str], Any]:
    return {
        ("GET", f"/repos/{REPO}"): repo or FakeResponse(200, {"full_name": REPO, "permissions": {"admin": True}}),
        ("GET", "/runs/5"): FakeResponse(200, run or run_obj()),
        ("GET", "/runs/5/pending_deployments"): pend or FakeResponse(200, pending()),
        ("POST", "/runs/5/pending_deployments"): post,
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(probe, "_TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr(probe, "_sleep", lambda s: None)


def invoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opener: FakeOpener, *argv: str
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr(probe, "_make_opener", lambda: opener)
    code = probe.main(["--repo", REPO, *argv])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def leg(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opener: FakeOpener, *extra: str
) -> tuple[int, dict]:
    return invoke(monkeypatch, capsys, opener, "--leg", "proxy", "--run-id", "5", *extra)


class TestPostClassification:
    @pytest.mark.parametrize(
        ("post", "can", "verdict", "code"),
        [
            (FakeResponse(200, []), True, "APPROVED", 1),
            (FakeResponse(301, None, {**RID, "location": "x"}), True, "PROBE_INVALID", 2),
            (FakeResponse(307, None, {**RID, "location": "x"}), True, "PROBE_INVALID", 2),
            (FakeResponse(422, {"message": "nope"}), False, "NOT_REVIEWER", 0),
            (FakeResponse(403, {"message": "nope"}), False, "NOT_REVIEWER", 0),
            (FakeResponse(422, {"message": "nope"}), True, "INCONCLUSIVE", 2),
            (FakeResponse(404, {"message": "nope"}), True, "INCONCLUSIVE", 2),
            (FakeResponse(403, {"message": "Resource not accessible by integration"}), True, "TOKEN_LACKS_PERMISSION", 0),
            (
                FakeResponse(403, {"message": "x"}, {**RID, "x-accepted-github-permissions": "deployments=write"}),
                True,
                "TOKEN_LACKS_PERMISSION",
                0,
            ),
            (
                FakeResponse(422, {"message": "x"}, {**RID, "x-accepted-github-permissions": "deployments=write"}),
                True,
                "INCONCLUSIVE",
                2,
            ),
            (
                FakeResponse(422, {"message": "x"}, {**RID, "x-accepted-github-permissions": "deployments=write"}),
                False,
                "NOT_REVIEWER",
                0,
            ),
            (
                FakeResponse(404, {"message": "x"}, {**RID, "x-accepted-github-permissions": "deployments=write"}),
                True,
                "INCONCLUSIVE",
                2,
            ),
            (FakeResponse(403, {"message": "blocked"}, {}), True, "PROXY_BLOCKED", 0),
            (FakeResponse(401, {"message": "Bad credentials"}), True, "UNAVAILABLE", 0),
            (FakeResponse(503, {"message": "down"}), True, "INCONCLUSIVE", 2),
            (
                FakeResponse(403, {"message": "API rate limit exceeded"}, {**RID, "x-accepted-github-permissions": "a=b"}),
                True,
                "INCONCLUSIVE",
                2,
            ),
            (FakeResponse(403, {"message": "x"}, {**RID, "x-ratelimit-remaining": "0"}), False, "INCONCLUSIVE", 2),
        ],
    )
    def test_verdicts(self, monkeypatch, capsys, post, can, verdict, code) -> None:
        opener = FakeOpener(routes(post, pend=FakeResponse(200, pending(can=can))))
        got_code, out = leg(monkeypatch, capsys, opener)
        assert (out["verdict"], got_code) == (verdict, code)
        assert len(opener.posts()) == 1

    def test_transport_error_after_send_is_inconclusive(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(urllib.error.URLError("reset")))
        code, out = leg(monkeypatch, capsys, opener)
        assert (out["verdict"], code) == ("INCONCLUSIVE", 2)

    def test_pending_entry_gone_after_post_is_inconclusive(self, monkeypatch, capsys) -> None:
        r = routes(FakeResponse(422, {"message": "x"}))
        reads = iter([FakeResponse(200, pending(can=False)), FakeResponse(200, [])])

        class Opener(FakeOpener):
            def open(self, req: Any, timeout: float = 0) -> FakeResponse:
                if req.get_method() == "GET" and req.full_url.endswith("/pending_deployments"):
                    self.requests.append(req)
                    return next(reads)
                return super().open(req, timeout)

        code, out = leg(monkeypatch, capsys, Opener(r))
        assert (out["verdict"], code) == ("INCONCLUSIVE", 2)


class TestPinning:
    @pytest.mark.parametrize(
        "over",
        [
            {"head_branch": "feature"},
            {"event": "push"},
            {"path": ".github/workflows/other.yml"},
            {"status": "completed"},
        ],
    )
    def test_bad_run_means_no_post(self, monkeypatch, capsys, over) -> None:
        opener = FakeOpener(routes(FakeResponse(200, []), run=run_obj(**over)))
        code, out = leg(monkeypatch, capsys, opener)
        assert (out["verdict"], code) == ("PROBE_INVALID", 2)
        assert not opener.posts()

    @pytest.mark.parametrize("repo", [FakeResponse(200, {"full_name": "someone/else"}), FakeResponse(301, None, RID)])
    def test_repo_mismatch_or_redirect(self, monkeypatch, capsys, repo) -> None:
        opener = FakeOpener(routes(FakeResponse(200, []), repo=repo))
        _, out = leg(monkeypatch, capsys, opener)
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_in_progress_with_no_pending_entry_after_wait(self, monkeypatch, capsys) -> None:
        ticks = iter(range(0, 1000, 100))
        monkeypatch.setattr(probe, "_clock", lambda: next(ticks))
        opener = FakeOpener(routes(FakeResponse(200, []), run=run_obj(status="in_progress"), pend=FakeResponse(200, [])))
        _, out = leg(monkeypatch, capsys, opener, "--wait-pending", "180")
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_other_environment_pending(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(200, []), pend=FakeResponse(200, pending("tf-gated-apply"))))
        _, out = leg(monkeypatch, capsys, opener)
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_two_pending_environments(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(200, []), pend=FakeResponse(200, pending() + pending("tf-gated-apply"))))
        _, out = leg(monkeypatch, capsys, opener)
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_in_progress_with_one_pending_proceeds(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(422, {"message": "x"}), run=run_obj(status="in_progress")))
        _, out = leg(monkeypatch, capsys, opener)
        assert out["post"]["status"] == 422

    def test_blocked_pending_read_uses_environment_id_with_unknown_can_approve(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(422, {"message": "x"}), pend=FakeResponse(403, {"message": "b"}, {})))
        _, out = leg(monkeypatch, capsys, opener, "--environment-id", "77")
        assert out["pending_read"] == "PROXY_BLOCKED"
        assert out["verdict"] == "INCONCLUSIVE"
        assert json.loads(opener.posts()[0].data)["environment_ids"] == [77]

    def test_blocked_pending_read_without_environment_id_is_invalid(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(200, []), pend=FakeResponse(403, {"message": "b"}, {})))
        _, out = leg(monkeypatch, capsys, opener)
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_environment_id_mismatch(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(200, [])))
        _, out = leg(monkeypatch, capsys, opener, "--environment-id", "99")
        assert out["verdict"] == "PROBE_INVALID"
        assert not opener.posts()

    def test_post_body_carries_only_the_probe_environment(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(422, {"message": "x"})))
        leg(monkeypatch, capsys, opener)
        body = json.loads(opener.posts()[0].data)
        assert body["environment_ids"] == [77]
        assert body["state"] == "approved"

    def test_repo_is_required(self) -> None:
        with pytest.raises(SystemExit):
            probe.main(["--leg", "proxy", "--run-id", "5"])


class TestModes:
    def test_capability_maps_installations(self, monkeypatch, capsys) -> None:
        def cap(inst: Any) -> dict[str, Any]:
            r = {
                ("GET", f"/repos/{REPO}"): FakeResponse(200, {"full_name": REPO}),
                ("GET", "/user/installations"): inst,
                ("GET", "/environments/deployment-approval-probe"): FakeResponse(200, {"can_admins_bypass": False}),
            }
            opener = FakeOpener(r)
            _, out = invoke(monkeypatch, capsys, opener, "--capability", "--leg", "proxy")
            assert all(req.get_method() == "GET" for req in opener.requests)
            return out

        yes = cap(FakeResponse(200, {"installations": [{"app_slug": "a", "permissions": {"administration": "write"}}]}))
        no = cap(FakeResponse(200, {"installations": [{"app_slug": "a", "permissions": {"actions": "write"}}]}))
        unknown = cap(FakeResponse(403, {"message": "b"}, {}))
        assert (yes["environment_write_capability"], no["environment_write_capability"]) == ("YES", "NO")
        assert unknown["environment_write_capability"] == "UNKNOWN"
        assert unknown["proxy_blocked"] == ["installations"]

    def test_observe_is_get_only(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(
            {
                ("GET", f"/repos/{REPO}"): FakeResponse(200, {"full_name": REPO}),
                ("GET", "/runs/5/approvals"): FakeResponse(200, []),
                ("GET", "/runs/5/jobs"): FakeResponse(200, {"jobs": [{"name": "gated", "status": "queued"}]}),
            }
        )
        _, out = invoke(monkeypatch, capsys, opener, "--observe", "--run-id", "5")
        assert out["approvals"] == []
        assert out["jobs"][0]["name"] == "gated"
        assert all(r.get_method() == "GET" for r in opener.requests)


class TestCredentialLegs:
    @pytest.mark.parametrize("name", ["GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"])
    def test_absent_env_variable_sends_nothing(self, monkeypatch, capsys, name) -> None:
        opener = FakeOpener({})
        code, out = invoke(monkeypatch, capsys, opener, "--leg", f"env:{name}", "--run-id", "5")
        assert (out["verdict"], code) == ("UNAVAILABLE", 0)
        assert not opener.requests

    def test_placeholder_token_file_sends_nothing(self, monkeypatch, capsys) -> None:
        probe._TOKEN_FILE.write_text(probe._PLACEHOLDER)
        opener = FakeOpener({})
        _, out = invoke(monkeypatch, capsys, opener, "--leg", "token-file", "--run-id", "5")
        assert out["verdict"] == "UNAVAILABLE"
        assert not opener.requests

    def test_empty_git_credential_sends_nothing(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(probe, "_run_cmd", lambda args, stdin, env: (0, "protocol=https\nhost=github.com\n"))
        opener = FakeOpener({})
        _, out = invoke(monkeypatch, capsys, opener, "--leg", "git-credential", "--run-id", "5")
        assert out["verdict"] == "UNAVAILABLE"
        assert not opener.requests

    def test_proxy_leg_sends_no_authorization_header(self, monkeypatch, capsys) -> None:
        opener = FakeOpener(routes(FakeResponse(422, {"message": "x"})))
        leg(monkeypatch, capsys, opener)
        assert opener.requests
        assert all(not r.has_header("Authorization") for r in opener.requests)

    def test_gh_cli_parses_include_output(self, monkeypatch, capsys) -> None:
        seen: list[list[str]] = []

        def fake(args: list[str], stdin: str | None, env: Any) -> tuple[int, str]:
            seen.append(args)
            path = args[5]
            if path == f"/repos/{REPO}":
                return 0, 'HTTP/2.0 200 OK\r\nX-Github-Request-Id: A\r\n\r\n{"full_name": "%s"}' % REPO
            if path.endswith("/runs/5"):
                return 0, "HTTP/2.0 200 OK\nX-Github-Request-Id: A\n\n" + json.dumps(run_obj())
            if args[4] == "GET":
                return 0, "HTTP/2.0 200 OK\nX-Github-Request-Id: A\n\n" + json.dumps(pending(can=False))
            return 1, 'HTTP/2.0 422 Unprocessable\nX-Github-Request-Id: A\n\n{"message": "no"}'

        monkeypatch.setattr(probe, "_run_cmd", fake)
        code, out = invoke(monkeypatch, capsys, FakeOpener({}), "--leg", "gh-cli", "--run-id", "5")
        assert (out["verdict"], code) == ("NOT_REVIEWER", 0)
        assert any(a[4] == "POST" for a in seen)

    def test_gh_cli_without_http_response_is_unavailable(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(probe, "_run_cmd", lambda args, stdin, env: (4, "gh: not logged in"))
        opener = FakeOpener(routes(FakeResponse(200, [])))
        _, out = invoke(monkeypatch, capsys, opener, "--leg", "gh-cli", "--run-id", "5", "--environment-id", "77")
        assert out["verdict"] in ("UNAVAILABLE", "PROBE_INVALID")
        assert not opener.posts()


class TestHygiene:
    def test_sentinel_never_printed(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("GH_TOKEN", SENTINEL)
        leaky = FakeResponse(422, {"message": f"bad {SENTINEL}"})
        opener = FakeOpener(routes(leaky, pend=FakeResponse(200, pending(can=False))))
        monkeypatch.setattr(probe, "_make_opener", lambda: opener)
        probe.main(["--repo", REPO, "--leg", "env:GH_TOKEN", "--run-id", "5"])
        cap = capsys.readouterr()
        assert SENTINEL not in cap.out + cap.err

    def test_sentinel_not_in_transport_exception(self, monkeypatch) -> None:
        class Boom:
            def open(self, req: Any, timeout: float = 0) -> Any:
                raise urllib.error.URLError(f"failed {SENTINEL}")

        monkeypatch.setattr(probe, "_make_opener", lambda: Boom())
        with pytest.raises(probe._NoResponse) as exc:
            probe._HttpTransport(SENTINEL).call("GET", "/x")
        assert SENTINEL not in str(exc.value)

    def test_no_public_function_takes_environment_or_state(self) -> None:
        banned = {"environment", "environment_name", "env_name", "state", "workflow", "workflow_path"}
        for name in probe.__all__:
            params = set(inspect.signature(getattr(probe, name)).parameters)
            assert not params & banned
        public = [
            n
            for n, v in vars(probe).items()
            if inspect.isfunction(v) and v.__module__ == probe.__name__ and not n.startswith("_")
        ]
        assert public == ["main"]

    def test_module_constants_pin_environment_and_workflow(self) -> None:
        assert probe._ENV_NAME == "deployment-approval-probe"
        assert probe._WORKFLOW_PATH == ".github/workflows/deployment-approval-probe.yml"


class TestCoverageGaps:
    def _post_routes(self) -> FakeOpener:
        return FakeOpener(routes(FakeResponse(422, {"message": "x"}), pend=FakeResponse(200, pending(can=False))))

    def test_token_file_with_token_is_used_as_bearer(self, monkeypatch, capsys) -> None:
        probe._TOKEN_FILE.write_text(SENTINEL + "\n")
        opener = self._post_routes()
        _, out = invoke(monkeypatch, capsys, opener, "--leg", "token-file", "--run-id", "5")
        assert out["verdict"] == "NOT_REVIEWER"
        assert opener.requests[0].get_header("Authorization") == f"Bearer {SENTINEL}"

    def test_git_credential_password_is_used_as_bearer(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(probe, "_run_cmd", lambda a, s, e: (0, f"host=github.com\npassword={SENTINEL}\n"))
        opener = self._post_routes()
        _, out = invoke(monkeypatch, capsys, opener, "--leg", "git-credential", "--run-id", "5")
        assert out["verdict"] == "NOT_REVIEWER"
        assert SENTINEL not in json.dumps(out)

    def test_ci_bot_leg_is_marked_ci(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", SENTINEL)
        _, out = invoke(monkeypatch, capsys, self._post_routes(), "--leg", "ci-bot", "--run-id", "5")
        assert out["kind"] == "ci"

    def test_missing_run_id_exits_2(self, capsys) -> None:
        assert probe.main(["--repo", REPO, "--leg", "proxy"]) == 2

    def test_git_credential_helper_failure_is_unavailable(self, monkeypatch, capsys) -> None:
        def boom(a: Any, s: Any, e: Any) -> Any:
            raise OSError("no git")

        monkeypatch.setattr(probe, "_run_cmd", boom)
        _, out = invoke(monkeypatch, capsys, FakeOpener({}), "--leg", "git-credential", "--run-id", "5")
        assert out["verdict"] == "UNAVAILABLE"

    def test_redirects_are_not_followed(self) -> None:
        assert probe._NoRedirect().redirect_request(None, None, 301, "m", {}, "http://x") is None
        assert probe._make_opener() is not None
        assert probe._parse_body(b"not json") is None

    def test_read_failure_is_probe_invalid(self, monkeypatch, capsys) -> None:
        _, out = leg(monkeypatch, capsys, FakeOpener({}))
        assert out["verdict"] == "PROBE_INVALID"

    def test_run_cmd_runs_a_subprocess(self) -> None:
        assert probe._run_cmd(["echo", "hi"], None, None) == (0, "hi\n")

    def test_gh_cli_failing_to_launch_is_inconclusive_after_send(self, monkeypatch, capsys) -> None:
        def boom(a: Any, s: Any, e: Any) -> Any:
            raise OSError("gh missing")

        monkeypatch.setattr(probe, "_run_cmd", boom)
        _, out = invoke(monkeypatch, capsys, self._post_routes(), "--leg", "gh-cli", "--run-id", "5")
        assert out["verdict"] == "INCONCLUSIVE"

    def test_missing_token_file_is_unavailable(self, monkeypatch, capsys) -> None:
        _, out = invoke(monkeypatch, capsys, FakeOpener({}), "--leg", "token-file", "--run-id", "5")
        assert out["verdict"] == "UNAVAILABLE"

    def test_run_read_failure_is_probe_invalid(self, monkeypatch, capsys) -> None:
        r = routes(FakeResponse(200, []))
        r[("GET", "/runs/5")] = FakeResponse(404, {"message": "x"})
        _, out = leg(monkeypatch, capsys, FakeOpener(r))
        assert out["verdict"] == "PROBE_INVALID"

    def test_unreachable_pending_read_without_id_is_probe_invalid(self, monkeypatch, capsys) -> None:
        r = routes(FakeResponse(200, []))
        del r[("GET", "/runs/5/pending_deployments")]
        r[("GET", "/pending_deployments")] = urllib.error.URLError("down")
        _, out = leg(monkeypatch, capsys, FakeOpener(r))
        assert out["pending_read"] == "REFUSED"
        assert out["verdict"] == "PROBE_INVALID"

    @pytest.mark.parametrize("mode", [["--capability"], ["--observe", "--run-id", "5"]])
    def test_read_modes_reject_repo_mismatch(self, monkeypatch, capsys, mode) -> None:
        opener = FakeOpener({("GET", f"/repos/{REPO}"): FakeResponse(200, {"full_name": "x/y"})})
        _, out = invoke(monkeypatch, capsys, opener, *mode)
        assert out["verdict"] == "PROBE_INVALID"

    def test_capability_without_credential_is_unavailable(self, monkeypatch, capsys) -> None:
        _, out = invoke(monkeypatch, capsys, FakeOpener({}), "--capability", "--leg", "env:GH_TOKEN")
        assert out["verdict"] == "UNAVAILABLE"
