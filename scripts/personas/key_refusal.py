"""Verb key-refusal: a job holding id-token: write without the persona-verdict Environment is refused by the role.

Stdlib only; runs on the system python with no pip. ATTEMPTS lists (role name, expected STS error code); a later plan adds
rows. Passes only when every attempt returns HTTP 403 with its code. Prints no token, credential, ARN or account id.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from scripts.personas import oidc_assume

ATTEMPTS: tuple[tuple[str, str], ...] = ((oidc_assume.ROLE, "AccessDenied"), (oidc_assume.APP_ROLE, "AccessDenied"))
WORKFLOW_REF = "/.github/workflows/persona-verdict.yml@refs/heads/main"


def main(argv: Sequence[str]) -> int:
    try:
        jwt = oidc_assume.token()
        data = oidc_assume.claims(jwt)
    except oidc_assume.OidcError as exc:
        print(f"KEY-REFUSAL FAIL {exc}")
        return 1
    sub, ref = str(data.get("sub", "")), str(data.get("job_workflow_ref", ""))
    print(f"OIDC-CLAIMS sub={sub} job_workflow_ref={ref}", flush=True)
    if ":environment:" in sub or ref != os.environ.get("GITHUB_REPOSITORY", "") + WORKFLOW_REF:
        print("KEY-REFUSAL FAIL claims")
        return 1
    ok = True
    for role_name, expect in ATTEMPTS:
        try:
            outcome = oidc_assume.assume(role_name, jwt)
        except oidc_assume.OidcError as exc:
            print(f"KEY-REFUSAL FAIL {exc}")
            return 1
        oidc_assume.report(role_name, expect, outcome)
        ok = ok and outcome.status == 403 and outcome.code == expect
    print("KEY-REFUSAL PASS" if ok else "KEY-REFUSAL FAIL")
    return 0 if ok else 1
