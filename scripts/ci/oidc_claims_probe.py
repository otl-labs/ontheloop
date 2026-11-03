#!/usr/bin/env python3
"""Print the allowlisted claims of this job's GitHub OIDC token, never the token itself.

Run as `python3 -I scripts/ci/oidc_claims_probe.py`. The token is fetched here from the Actions
token endpoint, so it never enters a shell variable or a log. Only the payload segment is decoded;
the signature segment is never read.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence

ALLOWED_CLAIMS: tuple[str, ...] = (
    "sub",
    "ref",
    "ref_type",
    "base_ref",
    "head_ref",
    "event_name",
    "environment",
    "job_workflow_ref",
    "workflow_ref",
    "repository_id",
    "repository_owner_id",
    "runner_environment",
)


class ProbeError(Exception):
    """Carries a fixed message that never includes token text."""


def decode_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        raise ProbeError("malformed token")
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProbeError("malformed token") from exc
    if not isinstance(payload, dict):
        raise ProbeError("malformed token")
    return payload


def _printable(value: str) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in value)


def select_claims(payload: Mapping[str, object]) -> list[tuple[str, str]]:
    return [(name, _printable(str(payload[name]))) for name in ALLOWED_CLAIMS if name in payload]


def check_required(selected: Sequence[tuple[str, str]], required: Sequence[str]) -> list[str]:
    present = dict(selected)
    return [name for name in required if not present.get(name)]


def fetch_token(audience: str, environ: Mapping[str, str]) -> str:
    url = environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise ProbeError("token request environment is not set")
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{separator}{urllib.parse.urlencode({'audience': audience})}",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            value = json.load(response).get("value")
    except Exception as exc:
        raise ProbeError("token fetch failed") from exc
    if not isinstance(value, str) or not value:
        raise ProbeError("token fetch returned no token")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--require", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        selected = select_claims(decode_payload(fetch_token(args.audience, os.environ)))
    except ProbeError as exc:
        print(f"oidc_claims_probe: {exc}", file=sys.stderr)
        return 1
    for name, value in selected:
        print(f"{name}={value}")
    missing = check_required(selected, args.require)
    if missing:
        print(f"oidc_claims_probe: missing or empty required claim: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
