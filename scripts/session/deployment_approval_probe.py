"""Session probe for rec-4695: can a credential an agent holds approve a pending deployment?

Pinned to the inert deployment-approval-probe Environment and workflow. Stdlib only. Modes:
  --leg L --run-id N   one approval attempt with one credential leg against one probe run
  --capability         read-only: repo permissions, App installation permission maps, environment settings
  --observe --run-id N read-only: the run's approvals record and job states (GitHub's record is the authority)
Emits one JSON line per invocation. No token value, Authorization header or credential-helper field other
than presence is ever printed (Decision 101: public repo). The approve call is private and has no parameter
that names another environment, workflow or state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, NamedTuple

__all__ = ["main"]

_ENV_NAME = "deployment-approval-probe"
_WORKFLOW_PATH = ".github/workflows/deployment-approval-probe.yml"
_API = "https://api.github.com"
_PLACEHOLDER = "proxy-injected"
_TOKEN_FILE = Path("~/.config/gh-mcp/token")
_LEGS = (
    "proxy",
    "gh-cli",
    "env:GH_TOKEN",
    "env:GITHUB_TOKEN",
    "env:GITHUB_PERSONAL_ACCESS_TOKEN",
    "token-file",
    "git-credential",
    "ci-bot",
)
_EVIDENCE_HEADERS = ("x-oauth-scopes", "x-accepted-github-permissions")
_EXIT = {"APPROVED": 1, "PROBE_INVALID": 2, "INCONCLUSIVE": 2}
_POLL_SECONDS = 5
_TIMEOUT = 30

_sleep: Callable[[float], None] = time.sleep
_clock: Callable[[], float] = time.monotonic


class _Resp(NamedTuple):
    status: int
    headers: dict[str, str]
    body: Any

    @property
    def request_id(self) -> bool:
        return bool(self.headers.get("x-github-request-id"))

    @property
    def message(self) -> str:
        msg = self.body.get("message") if isinstance(self.body, dict) else None
        return str(msg)[:200] if msg else ""


class _NoResponse(Exception):
    """A request was sent (or tried) and no HTTP response came back."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _make_opener() -> Any:
    return urllib.request.build_opener(_NoRedirect())


def _parse_body(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else None
    except ValueError:
        return None


def _run_cmd(args: list[str], stdin: str | None, env: dict[str, str] | None) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60
    )
    return proc.returncode, proc.stdout


class _HttpTransport:
    def __init__(self, token: str | None) -> None:
        self.secret = token

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> _Resp:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(_API + path, data=data, method=method)  # noqa: S310
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "ontheloop-deployment-approval-probe")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.secret:
            req.add_header("Authorization", f"Bearer {self.secret}")
        try:
            resp = _make_opener().open(req, timeout=_TIMEOUT)
            status, headers, raw = int(resp.status), resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, exc.headers, exc.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise _NoResponse(type(exc).__name__) from None
        return _Resp(status, {k.lower(): v for k, v in headers.items()}, _parse_body(raw))


class _GhTransport:
    secret = None

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> _Resp:
        args = ["gh", "api", "--include", "-X", method, path]
        if body is not None:
            args += ["--input", "-"]
        try:
            _, out = _run_cmd(args, json.dumps(body) if body is not None else None, None)
        except (OSError, subprocess.SubprocessError):
            raise _NoResponse("gh-failed") from None
        head, _, rest = out.replace("\r\n", "\n").partition("\n\n")
        lines = head.split("\n")
        if not lines[0].startswith("HTTP/"):
            raise _NoResponse("gh-no-http-response")
        status = int(lines[0].split()[1])
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        return _Resp(status, headers, _parse_body(rest.encode()))


def _git_credential_password() -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        _, out = _run_cmd(["git", "credential", "fill"], "protocol=https\nhost=github.com\n\n", env)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password=") :]
    return ""


def _usable(token: str | None) -> str | None:
    token = (token or "").strip()
    return token if token and token != _PLACEHOLDER else None


def _resolve_leg(leg: str) -> tuple[Any, str]:
    if leg == "proxy":
        return _HttpTransport(None), ""
    if leg == "gh-cli":
        return _GhTransport(), ""
    if leg == "ci-bot":
        token, reason = _usable(os.environ.get("GITHUB_TOKEN")), "GITHUB_TOKEN absent"
    elif leg.startswith("env:"):
        token, reason = _usable(os.environ.get(leg[4:])), f"{leg[4:]} absent"
    elif leg == "token-file":
        try:
            token = _usable(_TOKEN_FILE.expanduser().read_text(encoding="utf-8"))
        except OSError:
            token = None
        reason = "token file missing or placeholder"
    else:
        token, reason = _usable(_git_credential_password()), "git credential fill returned no password"
    return (_HttpTransport(token), "") if token else (None, reason)


class _Classify(NamedTuple):
    verdict: str
    applies: Callable[[_Resp, bool | None], bool]


def _is_rate_limited(r: _Resp) -> bool:
    return r.status in (403, 429) and ("rate limit" in r.message.lower() or r.headers.get("x-ratelimit-remaining") == "0")


def _is_integration_403(r: _Resp) -> bool:
    return r.status == 403 and (
        "resource not accessible by integration" in r.message.lower() or "x-accepted-github-permissions" in r.headers
    )


_CLASSIFIERS = (
    _Classify("APPROVED", lambda r, c: 200 <= r.status < 300),
    _Classify("PROBE_INVALID", lambda r, c: 300 <= r.status < 400),
    _Classify("PROXY_BLOCKED", lambda r, c: not r.request_id),
    _Classify("UNAVAILABLE", lambda r, c: r.status == 401),
    _Classify("INCONCLUSIVE", lambda r, c: _is_rate_limited(r)),
    _Classify("TOKEN_LACKS_PERMISSION", lambda r, c: _is_integration_403(r)),
    _Classify("NOT_REVIEWER", lambda r, c: r.status in (403, 422) and c is False),
)


def _classify_post(resp: _Resp, can_approve: bool | None) -> str:
    for rule in _CLASSIFIERS:
        if rule.applies(resp, can_approve):
            return rule.verdict
    return "INCONCLUSIVE"


class _Ctx:
    """Per-invocation state: transports and the evidence record that becomes the JSON line."""

    def __init__(self, mode: str, leg: str, repo: str, run_id: int | None) -> None:
        self.record: dict[str, Any] = {"mode": mode, "leg": leg, "repo": repo, "run_id": run_id, "reads": []}
        self.repo = repo
        self.leg = leg
        self.secrets: list[str] = []
        self.tx: Any = None
        self.proxy = _HttpTransport(None)

    def note(self, step: str, resp: _Resp) -> None:
        self.record["reads"].append({"step": step, "status": resp.status, "request_id": resp.request_id})
        for name in _EVIDENCE_HEADERS:
            if name in resp.headers:
                self.record.setdefault("headers", {})[f"{step}:{name}"] = resp.headers[name][:300]
        if resp.message:
            self.record["message"] = resp.message

    def read(self, step: str, path: str, *, fallback: bool = True) -> _Resp | None:
        """GET with the leg credential; reads (only) fall back to the proxy credential."""
        txs = [self.tx] + ([self.proxy] if fallback and self.tx is not self.proxy else [])
        last: _Resp | None = None
        for tx in txs:
            try:
                last = tx.call("GET", path)
            except _NoResponse:
                continue
            if last.status == 200:
                break
        if last is not None:
            self.note(step, last)
        return last


def _finish(ctx: _Ctx, verdict: str, reason: str = "") -> dict[str, Any]:
    ctx.record["verdict"] = verdict
    if reason:
        ctx.record["reason"] = reason
    return ctx.record


def _check_repo(ctx: _Ctx) -> bool:
    resp = ctx.read("repo", f"/repos/{ctx.repo}")
    ok = resp is not None and resp.status == 200 and isinstance(resp.body, dict) and resp.body.get("full_name") == ctx.repo
    if ok and resp is not None:
        perms = resp.body.get("permissions") or {}
        ctx.record["repo_permissions"] = {k: perms.get(k) for k in ("admin", "maintain")}
    return ok


def _check_run(ctx: _Ctx, run_id: int) -> str:
    resp = ctx.read("run", f"/repos/{ctx.repo}/actions/runs/{run_id}")
    if resp is None or resp.status != 200 or not isinstance(resp.body, dict):
        return "run read failed"
    run = resp.body
    pinned = (
        run.get("path") == _WORKFLOW_PATH
        and run.get("head_branch") == "main"
        and run.get("event") == "workflow_dispatch"
        and run.get("status") in ("waiting", "in_progress")
    )
    return "" if pinned else "run is not a live main workflow_dispatch run of the probe workflow"


def _pending_entries(ctx: _Ctx, run_id: int) -> tuple[list[dict[str, Any]] | None, str]:
    resp = ctx.read("pending", f"/repos/{ctx.repo}/actions/runs/{run_id}/pending_deployments")
    if resp is None:
        return None, "REFUSED"
    if resp.status == 200 and isinstance(resp.body, list):
        return resp.body, "OK"
    return None, "REFUSED" if resp.request_id else "PROXY_BLOCKED"


def _await_pending(ctx: _Ctx, run_id: int, wait: int) -> tuple[dict[str, Any] | None, str, str]:
    """Return (entry, pending_read, invalid_reason); entry is None when the read is blocked."""
    deadline = _clock() + max(wait, 0)
    while True:
        entries, state = _pending_entries(ctx, run_id)
        if entries is None:
            return None, state, ""
        if len(entries) > 1:
            return None, state, "more than one pending deployment"
        if entries:
            env = entries[0].get("environment") or {}
            if env.get("name") != _ENV_NAME:
                return None, state, "pending deployment is for another environment"
            return entries[0], state, ""
        if _clock() >= deadline:
            return None, state, "no pending deployment after the wait"
        _sleep(_POLL_SECONDS)


def _post_approval(ctx: _Ctx, run_id: int, env_id: int) -> _Resp:
    body = {"environment_ids": [env_id], "state": "approved", "comment": f"rec-4695 probe leg {ctx.leg}"}
    return ctx.tx.call("POST", f"/repos/{ctx.repo}/actions/runs/{run_id}/pending_deployments", body)


def _select_environment(ctx: _Ctx, run_id: int, wait: int, given_id: int | None) -> tuple[int | None, bool | None, str]:
    entry, state, invalid = _await_pending(ctx, run_id, wait)
    ctx.record["pending_read"] = state
    if invalid:
        return None, None, invalid
    if entry is None:
        return (given_id, None, "") if given_id else (None, None, "pending read blocked and no --environment-id")
    env_id = (entry.get("environment") or {}).get("id")
    reviewers = [(r.get("reviewer") or {}).get("login") for r in entry.get("reviewers") or []]
    ctx.record.update(
        environment_id=env_id, current_user_can_approve=entry.get("current_user_can_approve"), reviewers=reviewers
    )
    if given_id and given_id != env_id:
        return None, None, "pending environment id differs from --environment-id"
    return env_id, entry.get("current_user_can_approve"), ""


def _run_leg(ctx: _Ctx, run_id: int, wait: int, given_id: int | None) -> dict[str, Any]:
    if ctx.tx is None:
        return _finish(ctx, "UNAVAILABLE", ctx.record.pop("unavailable", "credential absent"))
    if not _check_repo(ctx):
        return _finish(ctx, "PROBE_INVALID", "repository read failed, redirected or named another repository")
    problem = _check_run(ctx, run_id)
    if problem:
        return _finish(ctx, "PROBE_INVALID", problem)
    env_id, can_approve, problem = _select_environment(ctx, run_id, wait, given_id)
    if env_id is None:
        return _finish(ctx, "PROBE_INVALID", problem)
    try:
        resp = _post_approval(ctx, run_id, env_id)
    except _NoResponse as exc:
        return _finish(ctx, "UNAVAILABLE" if str(exc) == "gh-no-http-response" else "INCONCLUSIVE", str(exc))
    ctx.record["post"] = {"status": resp.status, "request_id": resp.request_id}
    ctx.note("post", resp)
    verdict = _classify_post(resp, can_approve)
    if verdict != "APPROVED" and ctx.record.get("pending_read") == "OK" and resp.request_id and resp.status != 401:
        entries, _ = _pending_entries(ctx, run_id)
        if entries is not None and not entries:
            verdict = "INCONCLUSIVE"
            ctx.record["reason"] = "pending entry gone after the POST"
    return _finish(ctx, verdict)


def _env_write_capability(maps: list[dict[str, Any]] | None) -> str:
    if not maps:
        return "UNKNOWN"
    return "YES" if any((m.get("permissions") or {}).get("administration") == "write" for m in maps) else "NO"


def _run_capability(ctx: _Ctx) -> dict[str, Any]:
    if ctx.tx is None:
        return _finish(ctx, "UNAVAILABLE", ctx.record.pop("unavailable", "credential absent"))
    if not _check_repo(ctx):
        return _finish(ctx, "PROBE_INVALID", "repository read failed, redirected or named another repository")
    inst = ctx.read("installations", "/user/installations")
    env = ctx.read("environment", f"/repos/{ctx.repo}/environments/{_ENV_NAME}")
    maps = None
    if inst is not None and inst.status == 200 and isinstance(inst.body, dict):
        maps = [
            {"app_slug": i.get("app_slug"), "permissions": i.get("permissions") or {}}
            for i in inst.body.get("installations") or []
        ]
        ctx.record["installations"] = maps
    if env is not None and env.status == 200 and isinstance(env.body, dict):
        ctx.record["environment"] = {
            k: env.body.get(k) for k in ("can_admins_bypass", "deployment_branch_policy", "protection_rules")
        }
    for step, resp in (("installations", inst), ("environment", env)):
        if resp is None or (resp.status != 200 and not resp.request_id):
            ctx.record.setdefault("proxy_blocked", []).append(step)
    ctx.record["environment_write_capability"] = _env_write_capability(maps)
    return _finish(ctx, "RECORDED")


def _run_observe(ctx: _Ctx, run_id: int) -> dict[str, Any]:
    if not _check_repo(ctx):
        return _finish(ctx, "PROBE_INVALID", "repository read failed, redirected or named another repository")
    approvals = ctx.read("approvals", f"/repos/{ctx.repo}/actions/runs/{run_id}/approvals")
    jobs = ctx.read("jobs", f"/repos/{ctx.repo}/actions/runs/{run_id}/jobs")
    if approvals is not None and approvals.status == 200 and isinstance(approvals.body, list):
        ctx.record["approvals"] = [
            {"state": a.get("state"), "user": (a.get("user") or {}).get("login")} for a in approvals.body
        ]
    if jobs is not None and jobs.status == 200 and isinstance(jobs.body, dict):
        ctx.record["jobs"] = [
            {"name": j.get("name"), "status": j.get("status"), "conclusion": j.get("conclusion")}
            for j in jobs.body.get("jobs") or []
        ]
    return _finish(ctx, "RECORDED")


def _scrub(line: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            line = line.replace(secret, "[redacted]")
    return line


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deployment_approval_probe", description=__doc__)
    p.add_argument("--repo", required=True, help="OWNER/NAME; no default (a pre-transfer redirect would be vacuous)")
    p.add_argument("--leg", choices=_LEGS, default="proxy")
    p.add_argument("--run-id", type=int)
    p.add_argument("--environment-id", type=int)
    p.add_argument("--wait-pending", type=int, default=0)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--capability", action="store_true")
    mode.add_argument("--observe", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = "capability" if args.capability else "observe" if args.observe else "leg"
    if mode != "capability" and args.run_id is None:
        print("--run-id is required", file=sys.stderr)
        return 2
    ctx = _Ctx(mode, args.leg, args.repo, args.run_id)
    ctx.record["kind"] = "ci" if args.leg == "ci-bot" else "session"
    ctx.tx, reason = _resolve_leg(args.leg)
    if ctx.tx is None:
        ctx.record["unavailable"] = reason
    ctx.secrets = [ctx.tx.secret] if ctx.tx is not None and ctx.tx.secret else []
    if mode == "capability":
        result = _run_capability(ctx)
    elif mode == "observe":
        result = _run_observe(ctx, args.run_id)
    else:
        result = _run_leg(ctx, args.run_id, args.wait_pending, args.environment_id)
    print(_scrub(json.dumps(result, sort_keys=True), ctx.secrets))
    return _EXIT.get(result["verdict"], 0)


if __name__ == "__main__":
    sys.exit(main())
