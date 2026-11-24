"""GitHub OIDC token request and unsigned AssumeRoleWithWebIdentity for the persona-verdict role (T4.32 c1 credential leg).

Stdlib only. The region arrives only as PERSONA_AWS_REGION (keyprep's aws-region output, Decision 224 cl.5); the role ARN is
built in memory from AWS_ACCOUNT_ID and the role name and is never printed. Only fixed marker lines print: never a token,
credential, response body, ARN or account id.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ROLE = "agent-platform-github-ci-persona-verdict"
APP_ROLE = "agent-platform-github-ci-persona-verdict-app"
AUDIENCE = "sts.amazonaws.com"
TOKEN_HOST = "actions.githubusercontent.com"
TIMEOUT_SECONDS = 20
REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]+$")
ACCOUNT = re.compile(r"^[0-9]{12}$")
SESSION_CHARS = re.compile(r"[^A-Za-z0-9+=,.@_-]")
CODE = re.compile(r"^[A-Za-z]{1,64}$")


class OidcError(RuntimeError):
    """A failure with a fixed message; never carries a body, token or identifier."""


@dataclass(frozen=True)
class Outcome:
    status: int
    code: str
    credentials: dict[str, str] | None = None


def urlopen(request: urllib.request.Request, timeout: int) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https URLs built or host-checked here


def _fetch(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urlopen(request, TIMEOUT_SECONDS) as response:
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), bytes(exc.read())


def token(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == TOKEN_HOST or host.endswith("." + TOKEN_HOST)):
        raise OidcError("the OIDC token request URL is not an Actions host")
    if not bearer:
        raise OidcError("no OIDC request token")
    sep = "&" if parsed.query else "?"
    request = urllib.request.Request(  # noqa: S310
        f"{url}{sep}{urllib.parse.urlencode({'audience': AUDIENCE})}",
        headers={"Authorization": f"bearer {bearer}", "Accept": "application/json"},
    )
    try:
        status, body = _fetch(request)
        value = json.loads(body).get("value")
    except (OSError, ValueError, AttributeError) as exc:
        raise OidcError(f"the OIDC token request failed ({type(exc).__name__})") from None
    if status != 200 or not isinstance(value, str) or value.count(".") != 2:
        raise OidcError(f"the OIDC token request returned HTTP {status}")
    return value


def claims(jwt: str) -> dict[str, Any]:
    """The unverified payload; for printing sub and job_workflow_ref only."""
    try:
        payload = jwt.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise OidcError(f"the OIDC token does not decode ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise OidcError("the OIDC token payload is not an object")
    return data


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(body: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(body)  # noqa: S314 - STS response, no DTD use
    except ET.ParseError:
        return {}
    return {_local(el.tag): (el.text or "").strip() for el in root.iter() if len(el) == 0}


def assume(role_name: str, jwt: str, env: Mapping[str, str] | None = None) -> Outcome:
    env = os.environ if env is None else env
    region, account = env.get("PERSONA_AWS_REGION", ""), env.get("AWS_ACCOUNT_ID", "")
    if not REGION.match(region):
        raise OidcError("PERSONA_AWS_REGION is missing or malformed")
    if not ACCOUNT.match(account):
        raise OidcError("AWS_ACCOUNT_ID is missing or malformed")
    session = SESSION_CHARS.sub("-", f"persona-verdict-{env.get('GITHUB_RUN_ID', '0')}")[:64]
    form = urllib.parse.urlencode(
        {
            "Action": "AssumeRoleWithWebIdentity",
            "Version": "2011-06-15",
            "RoleArn": f"arn:aws:iam::{account}:role/{role_name}",
            "RoleSessionName": session,
            "WebIdentityToken": jwt,
            "DurationSeconds": "900",
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310
        f"https://sts.{region}.amazonaws.com/",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/xml"},
        method="POST",
    )
    try:
        status, body = _fetch(request)
    except TimeoutError:
        return Outcome(0, "timeout")
    except OSError:
        return Outcome(0, "neterror")
    fields = _parse(body)
    if status == 200:
        keys = ("AccessKeyId", "SecretAccessKey", "SessionToken")
        if all(fields.get(k) for k in keys):
            return Outcome(200, "ok", {k: fields[k] for k in keys})
        return Outcome(200, "malformed")
    code = fields.get("Code", "")
    return Outcome(status, code if CODE.match(code) else "other")


def report(role_name: str, expect: str, outcome: Outcome) -> None:
    print(f"OIDC-ASSUME role={role_name} expect={expect} got={outcome.code}", flush=True)
