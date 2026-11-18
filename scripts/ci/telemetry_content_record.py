#!/usr/bin/env python3
"""Record job entry point for the telemetry content deploy channel (Decision 249 as amended for this channel).

Runs under agent-platform-github-ci-deploy after the operator-approved deploy job and writes
deploy-records/telemetry-content/agent-platform-ducklake-telemetry-content.json through the existing writer, from the
deploy job's read-back digest. Exits 2 on a refused input; the writer exits 1 on a failed write.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys

from scripts.build_lambda_deploy import _write_ducklake_deploy_record

FUNCTION = "agent-platform-ducklake-telemetry-content"
BUCKET = "agent-platform-data-lake"
CHANNEL = "telemetry-content"


def _valid_digest(value: str) -> bool:
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def main() -> int:
    code_sha256 = os.environ.get("CODE_SHA256", "")
    artifact_sha = os.environ.get("LAMBDA_ARTIFACT_SHA", "")
    if not _valid_digest(code_sha256):
        print("::error::TELEMETRY_CONTENT_RECORD refused: CODE_SHA256 is empty or not base64 of 32 bytes", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9a-f]{40}", artifact_sha):
        print("::error::TELEMETRY_CONTENT_RECORD refused: LAMBDA_ARTIFACT_SHA is not a 40-hex sha", file=sys.stderr)
        return 2
    _write_ducklake_deploy_record(
        FUNCTION, json.dumps({"CodeSha256": code_sha256}), BUCKET, "", os.environ["AWS_REGION"], channel=CHANNEL
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
