"""The verdict App token for the post job (Decision 271 cl.4, Decision 273): one installation token per post run.

session(repo, env) assumes the verdict App role through OIDC, reads the App key from SSM in this process, mints one
installation token (checks write, metadata read), yields it and revokes it on exit, on SIGTERM and on SIGINT. The PEM, the
JWT and the credentials are dropped before the token is yielded. Only fixed lines print: never a key, JWT, token,
credential, ARN, account id or exception text. boto3 and jwt are imported only inside session(), so importing this module
imports neither.
"""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from scripts.personas import oidc_assume, override
from src.data.handlers import github_app_token

KEY_PARAMETER = "/secret/agent-platform-persona-verdict-app-key"
PERMISSIONS = {"checks": "write", "metadata": "read"}
ENV_SUFFIX = ":environment:persona-verdict-app"
WORKFLOW_REF = "/.github/workflows/persona-verdict.yml@refs/heads/main"
CODE = re.compile(r"^[A-Za-z]{1,64}$")


class AppTokenError(RuntimeError):
    """A failure whose message is the fixed APP-TOKEN FAIL line already printed."""


def _class(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
        return code if CODE.match(code) else "other"
    return type(exc).__name__


def _fail(stage: str, cls: str) -> AppTokenError:
    line = f"APP-TOKEN FAIL {stage}:{cls}"
    print(line, flush=True)
    return AppTokenError(line)


class _State:
    def __init__(self) -> None:
        self.api: Any = None
        self.token: str | None = None
        self.done = False
        self.failed: str | None = None

    def revoke(self) -> bool:
        """Revoke once; True when nothing is left to revoke or the revoke succeeded."""
        if self.token is None or self.done:
            return self.failed is None
        self.done = True
        try:
            github_app_token.revoke(self.api, self.token)
        except Exception as exc:  # noqa: BLE001 - reported by class only
            self.failed = _class(exc)
            print(f"APP-TOKEN FAIL revoke:{self.failed}", flush=True)
            return False
        print("APP-TOKEN revoked", flush=True)
        return True


def _mint(repo: str, env: Mapping[str, str], app: int, state: _State) -> None:
    try:
        jwt = oidc_assume.token(env)
        data = oidc_assume.claims(jwt)
    except oidc_assume.OidcError as exc:
        raise _fail("oidc", type(exc).__name__) from None
    sub, ref = str(data.get("sub", "")), str(data.get("job_workflow_ref", ""))
    print(f"OIDC-CLAIMS sub={sub} job_workflow_ref={ref}", flush=True)
    if not sub.endswith(ENV_SUFFIX) or ref != env.get("GITHUB_REPOSITORY", "") + WORKFLOW_REF:
        raise _fail("claims", "mismatch")
    try:
        outcome = oidc_assume.assume(oidc_assume.APP_ROLE, jwt, env)
    except oidc_assume.OidcError as exc:
        raise _fail("assume", type(exc).__name__) from None
    oidc_assume.report(oidc_assume.APP_ROLE, "ok", outcome)
    if outcome.credentials is None:
        raise _fail("assume", outcome.code)
    creds = outcome.credentials
    try:
        import boto3

        boto = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=env.get("PERSONA_AWS_REGION", ""),
        )
        pem = boto.client("ssm").get_parameter(Name=KEY_PARAMETER, WithDecryption=True)["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 - reported by class only
        raise _fail("ssm", _class(exc)) from None
    del creds, outcome, boto, jwt
    try:
        app_jwt = github_app_token.app_jwt(str(app), pem, int(time.time()))
        state.api = github_app_token.GitHubApi()
        state.token = github_app_token.installation_token(state.api, app_jwt, repo, dict(PERMISSIONS))
        del app_jwt
    except Exception as exc:  # noqa: BLE001 - reported by class only
        raise _fail("mint", _class(exc)) from None
    finally:
        pem = ""


@contextmanager
def session(repo: str, env: Mapping[str, str] | None = None) -> Iterator[str]:
    env = os.environ if env is None else env
    try:
        app = override.app_id()
    except override.Refused as exc:
        raise _fail("row", type(exc).__name__) from None
    state = _State()

    def _terminate(signum: int, frame: Any) -> None:
        state.revoke()
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, _terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        _mint(repo, env, app, state)
        print(f"APP-TOKEN minted app_id={app}", flush=True)
        try:
            yield str(state.token)
        except BaseException:
            state.revoke()
            raise
        if not state.revoke():
            raise AppTokenError(f"APP-TOKEN FAIL revoke:{state.failed}")
    finally:
        state.revoke()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
