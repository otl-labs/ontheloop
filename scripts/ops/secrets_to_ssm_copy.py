#!/usr/bin/env python3
"""Copy the eight out-of-band Secrets Manager values to /secret/<name> SSM SecureString parameters (Decision 237).

Run in the ADMIN container only, on the operator's word on the exact command. Never prints a value or a digest: output
is the target name, the outcome and the tier. A differing existing parameter is never overwritten without --overwrite;
--verify is read-only.
"""

from __future__ import annotations

import argparse
import hmac
import sys
from pathlib import Path
from typing import Any

SOURCE_NAMES = (
    "neon-api-key",
    "agent-platform-terraform-personal-tfvars",
    "agent-platform-deepseek-api-key",
    "agent-platform-anthropic-api-key",
    "agent-platform-ducklake-telemetry-writer-dsn",
    "agent-platform-ducklake-telemetry-writer-prod-dsn",
    "agent-platform-ducklake-telemetry-reader-dsn",
    "agent-platform-ducklake-smoke-maintainer-dsn",
)
STANDARD_TIER_MAX_BYTES = 4096
GOOD_OUTCOMES = ("created", "unchanged", "overwritten")
_TAGS = [{"Key": "Purpose", "Value": "secret-value"}, {"Key": "Source", "Value": "secrets-manager-copy"}]


def _tier(value: str) -> str:
    return "Standard" if len(value.encode("utf-8")) <= STANDARD_TIER_MAX_BYTES else "Advanced"


def _read_target(ssm: Any, target: str) -> str | None:
    try:
        return ssm.get_parameter(Name=target, WithDecryption=True)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def copy_one(sm: Any, ssm: Any, name: str, *, verify: bool, overwrite: bool) -> tuple[str, str]:
    """Return (outcome, tier) for one secret; outcome is created, unchanged, mismatch, absent or overwritten."""
    source = sm.get_secret_value(SecretId=name)["SecretString"]
    target = f"/secret/{name}"
    tier = _tier(source)
    current = _read_target(ssm, target)
    if current is None:
        if verify:
            return "absent", tier
        ssm.put_parameter(Name=target, Value=source, Type="SecureString", Tier=tier, Overwrite=False, Tags=_TAGS)
        return "created", tier
    if hmac.compare_digest(source.encode("utf-8"), current.encode("utf-8")):
        return "unchanged", tier
    if verify or not overwrite:
        return "mismatch", tier
    grown = {"Tier": "Advanced"} if tier == "Advanced" else {}
    ssm.put_parameter(Name=target, Value=source, Type="SecureString", Overwrite=True, **grown)
    return "overwritten", tier


def _clients(profile: str) -> tuple[Any, Any]:
    import boto3

    region = (Path(__file__).resolve().parents[2] / "config" / "aws-region").read_text(encoding="utf-8").strip()
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client("secretsmanager"), session.client("ssm")


def main(argv: list[str] | None = None, clients: tuple[Any, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="agent_platform_admin")
    parser.add_argument("--verify", action="store_true", help="read-only: report created/absent/mismatch without writing")
    parser.add_argument("--overwrite", action="store_true", help="replace a parameter whose value differs from the source")
    args = parser.parse_args(argv)
    sm, ssm = clients if clients is not None else _clients(args.profile)
    good = 0
    for name in SOURCE_NAMES:
        target = f"/secret/{name}"
        try:
            outcome, tier = copy_one(sm, ssm, name, verify=args.verify, overwrite=args.overwrite)
        except Exception as exc:  # noqa: BLE001 -- report the class and name only, never the message
            print(f"{target} error={type(exc).__name__}")
            continue
        print(f"{target} {outcome} tier={tier}")
        good += outcome in (("unchanged",) if args.verify else GOOD_OUTCOMES)
    total = len(SOURCE_NAMES)
    if good == total:
        print(f"SECRETS_TO_SSM_COPY_OK {good}/{total}")
        return 0
    print(f"SECRETS_TO_SSM_COPY_FAIL {total - good}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
