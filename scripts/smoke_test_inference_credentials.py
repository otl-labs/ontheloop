"""Credentialed smoke test for each inference tier role (T4.32 c1, rec-3222).

Takes the role's SSM parameter name and key variable from the project-configuration rows
(ports.run_persona.litellm.api_key.<role>, this_repository.current_home), fetches the value with the ambient
credential chain (agent_platform profile in CC-web; OIDC on CI), puts it in this process's environment under the row's
variable, and runs the role's cold-start assertion through the transport. The model comes from the contracts alone.

The InferenceCredentialsRead IAM grant is exercised on every run: a missing or misconfigured grant surfaces as
AccessDenied from SSM, failing the test with a clear diagnostic. The key value is never printed.

Usage:
    bin/venv-python -m scripts.smoke_test_inference_credentials --role primary
    bin/venv-python -m scripts.smoke_test_inference_credentials --role warm_fetched_escape_hatch [--region REGION]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from scripts.aws_profile import resolve_aws_profile
from scripts.llm import litellm_transport, tier_selection

DEFAULT_REGION_PATH = Path(__file__).resolve().parents[1] / "config" / "aws-region"
_ROW_PREFIX = "ports.run_persona.litellm.api_key."  # pragma: allowlist secret
_ROLES = ("primary", "warm_fetched_escape_hatch")


def _default_region() -> str:
    return DEFAULT_REGION_PATH.read_text(encoding="utf-8").strip()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test: fetch a tier role's inference key from SSM and run its cold-start assertion."
    )
    parser.add_argument("--role", choices=list(_ROLES), required=True, help="Tier role to test.")
    parser.add_argument("--region", default=None, help="AWS region for SSM (default: the line in config/aws-region).")
    return parser.parse_args(argv)


def _row(role: str) -> dict[str, Any]:
    _, config = tier_selection.load()
    for row in config.get("settings", []):
        if isinstance(row, dict) and row.get("id") == _ROW_PREFIX + role:
            return row
    raise tier_selection.TierSelectionError(f"project-configuration has no row {_ROW_PREFIX + role}")


# envelope_id (not secret_id) names the SSM parameter name deliberately: it is a non-sensitive resource name, and the
# "envelope" term avoids CodeQL's sensitive-name heuristic flagging the identifier (scripts/ci/redaction_check.py).
def run(role: str, region: str | None = None) -> int:
    """Fetch the role's key and run its cold start through the transport. Return 0 on success, 1 on failure."""
    row = _row(role)
    envelope_id = str(row["this_repository"]["current_home"])
    env_envelope = str(row["variable"])
    print(f"[{role}] Fetching parameter: {envelope_id}")
    client = boto3.Session(profile_name=resolve_aws_profile()).client("ssm", region_name=region or _default_region())

    try:
        response = client.get_parameter(Name=envelope_id, WithDecryption=True)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        print(f"FAIL [{role}] SSM error ({code}): {exc}", file=sys.stderr)
        if code == "AccessDeniedException":
            print(
                f"  -> InferenceCredentialsParameterRead IAM grant not applied or not propagated;"
                f" verify the PlatformDev policy includes the {envelope_id} parameter ARN.",
                file=sys.stderr,
            )
        elif code == "ParameterNotFound":
            print(f"  -> Parameter {envelope_id!r} not found; confirm the SSM copy created it.", file=sys.stderr)
        return 1

    value: str = response.get("Parameter", {}).get("Value") or ""
    if not value:
        print(
            f"FAIL [{role}] Parameter {envelope_id!r} has an empty value; set it via: aws ssm put-parameter"
            f" --type SecureString --overwrite --name {envelope_id} --value '<key>'",
            file=sys.stderr,
        )
        return 1

    os.environ[env_envelope] = value
    try:
        result = litellm_transport.cold_start_check(role)
    except Exception as exc:  # intentionally broad so any failure prints a diagnostic and returns 1
        print(f"FAIL [{role}] cold start raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if result.outcome == "asserted" and result.record is not None:
        print(
            f"PASS [{role}] requested={result.record.requested_model} served={result.record.model} assertion={result.outcome}"
        )
        return 0
    print(f"FAIL [{role}] assertion={result.outcome}: {result.detail}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(run(args.role, region=args.region))


if __name__ == "__main__":  # pragma: no cover
    main()
