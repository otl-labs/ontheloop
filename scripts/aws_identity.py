"""Caller-identity helper for cloud sessions behind the agent proxy.

In a cloud session the agent proxy holds the agent_static key as an AWS network secret and re-signs every
call to the project region's STS host as the IAM user. AssumeRole through the profile still works (the
profile's own region stays on the proxy-allowed host), but a GetCallerIdentity sent to that same host with
the role credentials is re-signed too and reports the IAM user, not the role. The identity call is therefore
made from a SEPARATE STS client in IDENTITY_PROBE_REGION, which the proxy leaves alone.

Rule: an STS call made with role credentials whose result depends on the caller identity, or that chains
AssumeRole, must never target the project region's STS host. A future terraform provider assume_role block,
or an identity-dependent data source (aws_caller_identity arn or user_id, aws_iam_session_context) read in
a local ADMIN plan, falls under the same rule. Exit-code-only liveness probes and account-id reads are
exempt: the identity is wrong there but the account and the exit code are right (tests/test_sts_call_sites.py
carries the classed allow-list).

The CLI never prints the account ID: caller-arn masks it, so output keeps "assumed-role/<Role>/" for greps.

Never override the profile's region and never pass --endpoint-url to pin the identity call; both move the
inner AssumeRole off the proxy-allowed host.
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from scripts.aws_profile import resolve_aws_profile
from scripts.ci.redaction_check import mask_account_ids

IDENTITY_PROBE_REGION = "eu-west-1"

_IDENTITY_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})


def caller_arn(profile: str | None = None) -> str:
    """Return the real caller ARN for the profile's role credentials (default chain when no profile applies)."""
    session = boto3.Session(profile_name=resolve_aws_profile(profile))
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("no AWS credentials resolved")
    frozen = creds.get_frozen_credentials()
    client = boto3.client(
        "sts",
        region_name=IDENTITY_PROBE_REGION,
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
        config=_IDENTITY_CONFIG,
    )
    return client.get_caller_identity()["Arn"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.aws_identity")
    sub = parser.add_subparsers(dest="command", required=True)
    arn_parser = sub.add_parser("caller-arn", help="print the caller ARN with the account ID masked")
    arn_parser.add_argument("--profile", default=None)
    args = parser.parse_args(argv)
    try:
        print(mask_account_ids(caller_arn(args.profile)))
    except Exception as exc:
        detail = type(exc).__name__
        if isinstance(exc, ClientError):
            detail = f"{detail} {exc.response.get('Error', {}).get('Code', '')}".strip()
        print(f"caller-arn failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
