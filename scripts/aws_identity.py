"""Caller-identity helper for cloud sessions behind the agent proxy.

In a cloud session the agent proxy holds the agent-platform-session key as an AWS network secret and re-signs
every call to its allowed hosts as that IAM user (Decision 255). The profile holds placeholder credentials, so
a GetCallerIdentity made from the profile's own session (its configured region, the proxy-signed STS host)
reports the user the proxy signs as.

Rule: no session code calls sts:AssumeRole, GetSessionToken, GetFederationToken or any other API that returns
credentials (Decision 255 cl.1); the proxy refuses them and a role session would put temporary keys in the
sandbox. Identity reads go through this helper. Exit-code-only liveness probes and account-id reads stay exempt
(tests/test_sts_call_sites.py carries the classed allow-list).

The CLI never prints the account ID: caller-arn masks it, so output keeps "user/<Name>" for greps.
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from scripts.aws_profile import resolve_aws_profile
from scripts.ci.redaction_check import mask_account_ids

_IDENTITY_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})


def caller_arn(profile: str | None = None) -> str:
    """Return the caller ARN for the profile's own session (default chain when no profile applies)."""
    session = boto3.Session(profile_name=resolve_aws_profile(profile))
    if session.get_credentials() is None:
        raise RuntimeError("no AWS credentials resolved")
    return session.client("sts", config=_IDENTITY_CONFIG).get_caller_identity()["Arn"]


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
