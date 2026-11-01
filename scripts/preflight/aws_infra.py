"""Credentials and infra-state concern for session_preflight."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import cast

from scripts.preflight import _common
from src.common.ducklake_reader_client import DuckLakeReader


def check_credentials() -> str:
    """Non-blocking credential check for the static-key assume-role chain.

    Runs `aws sts get-caller-identity` with the resolved profile (or the boto3
    default chain on Lambda/CI when resolve_aws_profile returns None). Returns
    "ok" on a clean exit, else "unavailable". There is no "expired" state: the
    static-key chain has no interactive login token -- the PlatformDev/PlatformAdmin
    STS session auto-refreshes from the long-lived agent_static key. Exit-code only,
    so it stays correct under the agent proxy (which re-signs the identity it reports).
    """
    profile = _common.resolve_aws_profile()
    cmd = ["aws", "sts", "get-caller-identity"]
    if profile:
        cmd += ["--profile", profile]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            cwd=_common.ROOT,
        )
        return "ok" if result.returncode == 0 else "unavailable"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unavailable"


def _handle_credentials_startup(creds_status: str) -> str:
    """Report credential health at preflight startup -- non-fatal (Decision 60).

    The static-key model has no interactive login step: the agent_static IAM key is a
    long-lived secret and the PlatformDev/PlatformAdmin STS sessions auto-refresh
    from it. When credentials are unavailable we cannot "log in" to recover, so we
    emit loud, actionable guidance and CONTINUE in degraded mode rather than exiting
    (Decision 60: skip with actionable guidance, never silently weaken a gate).
    Returns *creds_status* unchanged.
    """
    if creds_status != "ok":
        profile = _common.resolve_aws_profile() or "<default-chain>"
        print(
            "[WARN] AWS credentials unavailable (static-key assume-role chain did not resolve).\n"
            f"       Verify the chain: bin/venv-python -m scripts.aws_identity caller-arn --profile {profile}\n"
            "       There is no interactive login to recover; if the agent_static\n"
            "       key was rotated, update the environment's AWS network secret. Continuing in DEGRADED mode:\n"
            "       warehouse reads via the DuckLake reader\n"
            "       fall back to the local cache or empty results.",
            file=sys.stderr,
        )
    return creds_status


def _prime_reader_url(creds_status: str) -> None:
    """Resolve the DuckLake reader URL once and cache it in DUCKLAKE_READER_URL.

    Subsequent _make_reader() calls find the env override and skip SSM
    (Decision 79 first-priority resolution). Non-fatal on failure: if URL
    resolution raises, the env var stays unset and each reader falls through
    to its own SSM resolution as before.
    """
    if creds_status != "ok":
        return
    if os.environ.get("DUCKLAKE_READER_URL"):
        return  # already primed (CI override or earlier call)
    try:
        url = cast(DuckLakeReader, _common._make_reader())._reader_url()
        if isinstance(url, str) and url:
            os.environ["DUCKLAKE_READER_URL"] = url
    except Exception as exc:  # noqa: BLE001
        import logging as _log  # noqa: PLC0415

        _log.getLogger(__name__).warning("session_preflight._prime_reader_url: URL resolution failed: %s", exc)


def check_terraform_pending() -> tuple[bool | None, dict | None]:
    """Read the sandbox convergence record and derive pending-change state.

    Replaces the retired ``terraform -chdir=terraform plan`` invocation (CD.21:
    the terraform/ root is no longer applied; the personal/ sandbox root is
    managed by the CD pipeline). The convergence record at
    convergence/personal/sandbox.json is the authoritative truth for the
    applied-vs-code delta.

    Returns a tuple (pending, convergence_health) where:
      - pending: bool | None
          True  = record is red or unapplied_backlog > 0 (changes pending)
          False = record is green with no backlog
          None  = unavailable (S3 read failed / no credentials)
      - convergence_health: dict | None
          Sub-object surfaced in the preflight report:
          {status, red_age_hours, unapplied_backlog, stuck_approvals, severity}
          or None when the record cannot be fetched.
    """
    try:
        import boto3  # noqa: PLC0415

        from scripts.convergence_health import (  # noqa: PLC0415
            assess_health,
            derive_red_since,
            find_stuck_gated_approvals,
            read_convergence_record,
        )

        profile = _common.resolve_aws_profile()
        session = boto3.Session(profile_name=profile)
        s3 = session.client("s3")

        record = read_convergence_record(s3)
        stuck = find_stuck_gated_approvals()
        verdict = assess_health(record, stuck_approvals=stuck)

        health: dict = {
            "status": verdict.status,
            "red_age_hours": verdict.red_age_hours,
            "unapplied_backlog": verdict.unapplied_backlog,
            "stuck_approvals": len(verdict.stuck_approvals),
            "severity": verdict.severity,
        }

        # PLAN-gated-apply-rca-trigger: carry the record's identity fields so
        # _check_convergence_rca_gap can match on the red episode's start
        # TIMESTAMP (ci_rca recs carry no commit_sha field) while still
        # surfacing commit_sha to the operator in the alert payload. Reuses
        # convergence_health.derive_red_since (the SAME fallback logic
        # red_age_hours() itself is computed from) rather than re-deriving it.
        if record and verdict.status == "red":
            health["commit_sha"] = record.get("commit_sha", "")
            health["run_url"] = record.get("run_url", "")
            health["red_since"] = derive_red_since(record).strftime("%Y-%m-%dT%H:%M:%SZ")

        if verdict.status == "unknown":
            pending = None
        elif verdict.status == "red" or verdict.unapplied_backlog > 0:
            pending = True
        else:
            pending = False

        return pending, health

    except Exception:  # noqa: BLE001
        return None, None
