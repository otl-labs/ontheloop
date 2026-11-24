"""Verb inference-smoke: the first credentialed use of the persona-verdict role, one tier role per process.

The OIDC token is claim-checked before any assume; the assumed credentials live only in a boto3 Session in this process and
reach scripts.smoke_test_inference_credentials.run, which reads the tier role's key from SSM in-process. From before run()
until the process ends, file descriptors 1 and 2 point at a temporary file, so nothing the smoke, litellm or a late writer
prints reaches the log; only a failure class from a closed set is taken from that file, and only fixed marker lines print.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence

import boto3

from scripts import smoke_test_inference_credentials as smoke
from scripts.personas import oidc_assume

TIER_ROLES = ("primary", "warm_fetched_escape_hatch")
ENV_SUFFIX = ":environment:persona-verdict"
WORKFLOW_REF = "/.github/workflows/persona-verdict.yml@refs/heads/main"
NAME = re.compile(r"^[A-Za-z_]+$")
FAIL_PATTERNS = (
    ("contract", re.compile(r"^FAIL \[[a-z_]+\] contract load raised ([^\s:()]+)", re.M)),
    ("ssm", re.compile(r"^FAIL \[[a-z_]+\] SSM error \(([^\s:()]+)\)", re.M)),
    ("cold_start", re.compile(r"^FAIL \[[a-z_]+\] cold start raised ([^\s:()]+)", re.M)),
    ("assertion", re.compile(r"^FAIL \[[a-z_]+\] assertion=([^\s:()]+)", re.M)),
)
EMPTY = re.compile(r"^FAIL \[[a-z_]+\] Parameter .* has an empty value", re.M)


def failure_class(text: str) -> str:
    for label, pattern in FAIL_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group(1)
            return f"{label}:{name if NAME.match(name) else 'other'}"
    return "empty_value" if EMPTY.search(text) else "unclassified"


def _line(role: str, passed: bool, cls: str) -> str:
    return f"INFERENCE-SMOKE role={role} outcome={'PASS' if passed else 'FAIL'} class={cls}"


def _prelude(env: Mapping[str, str]) -> tuple[str, str | None, dict[str, str]]:
    """Return (role, failure line or None, credentials) after the claim checks; prints OIDC-CLAIMS and OIDC-ASSUME lines."""
    role = env.get("PERSONA_SMOKE_ROLE", "")
    if role not in TIER_ROLES:
        return role, _line("unknown", False, "bad_role"), {}
    try:
        jwt = oidc_assume.token(env)
        data = oidc_assume.claims(jwt)
        sub, ref = str(data.get("sub", "")), str(data.get("job_workflow_ref", ""))
        print(f"OIDC-CLAIMS sub={sub} job_workflow_ref={ref}", flush=True)
        if not sub.endswith(ENV_SUFFIX) or ref != env.get("GITHUB_REPOSITORY", "") + WORKFLOW_REF:
            return role, _line(role, False, "claims"), {}
        denied = oidc_assume.assume(oidc_assume.APP_ROLE, jwt, env)
        oidc_assume.report(oidc_assume.APP_ROLE, "AccessDenied", denied)
        if denied.status != 403 or denied.code != "AccessDenied":
            return role, _line(role, False, "app_role"), {}
        outcome = oidc_assume.assume(oidc_assume.ROLE, jwt, env)
    except oidc_assume.OidcError:
        return role, _line(role, False, "oidc"), {}
    oidc_assume.report(oidc_assume.ROLE, "ok", outcome)
    if outcome.credentials is None:
        return role, _line(role, False, "assume"), {}
    return role, None, outcome.credentials


def work(env: Mapping[str, str], keep_redirect: bool = False) -> tuple[int, str, int | None]:
    """Run the smoke. Returns (exit code, final marker line, saved fd 1 when keep_redirect leaves the redirect in place)."""
    role, failed, creds = _prelude(env)
    if failed is not None:
        return 1, failed, None
    region = env.get("PERSONA_AWS_REGION", "")
    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    saved_err = os.dup(2)
    scratch = tempfile.NamedTemporaryFile("w+b", dir=env.get("RUNNER_TEMP") or None, delete=False)
    os.dup2(scratch.fileno(), 1)
    os.dup2(scratch.fileno(), 2)
    try:
        code = smoke.run(role, region=region, session=session)
    except BaseException:
        code = 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
    with open(scratch.name, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    scratch.close()
    os.unlink(scratch.name)
    cls = "none" if code == 0 else failure_class(text)
    line = _line(role, code == 0, cls)
    if keep_redirect:
        os.close(saved_err)
        return code, line, saved
    os.dup2(saved, 1)
    os.dup2(saved_err, 2)
    os.close(saved)
    os.close(saved_err)
    return code, line, None


def main(argv: Sequence[str]) -> int:
    code, line, saved = work(os.environ, keep_redirect=True)
    if saved is None:
        print(line, flush=True)
        return code
    os.write(saved, (line + "\n").encode())
    os._exit(code)
