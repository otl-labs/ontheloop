#!/usr/bin/env python3
"""Testable classifier for the advisory terraform-converged status (Decision 172 PR-2).

Extracted from terraform-apply-sandbox.yml's advisory-status inline `run:` body (Decision 162 R3
thin-adapter obligation): the pre-extraction body read the convergence record with
`aws s3 cp ... 2>/dev/null || echo ""` and treated ANY read failure -- a genuinely absent record
(NoSuchKey) OR a credential/authorization failure -- identically as "pass-on-absent" GREEN. That
conflation is exactly what published a green `terraform-converged` status throughout the
2026-08-15 OIDC outage this plan recovers from, even though main was in fact unverifiable, not
converged.

classify() discriminates SEVEN mutually-exclusive outcomes:
  1. NoSuchKey / genuinely absent record -> success, pass-on-absent (first-apply-allowed).
  2. Any OTHER read failure (credential/authorization) -> error, UNVERIFIED -- never a green.
  3. Empty response body -> error, UNVERIFIED (today folds into pass-on-absent green; must not).
  4. Unparseable / non-object JSON -> error, UNVERIFIED (today aborts under inherited errexit,
     reding a job that must never red; here it degrades to a printed state instead).
  5. status == "red" -> failure (advisory only; never a required check, Decision 83), NAMING THE
     MEASURED CAUSE (Decision 190) via scripts.ci.convergence_classify.
     render_convergence_advisory_red_description rather than unconditionally asserting "last
     sandbox apply RED at {commit}" -- false on a drift-caused red, where commit is the last
     SUCCESSFUL apply, not the failing one.
  6. status != "red" with a pending_gated marker -> success, distinguishing a routed-pending
     episode (DEP-11) from plain green.
  7. status != "red" with a pending_codification marker (Decision 190) -> success, distinguishing
     a benign code-behind-state delta (measured resource_changes with no resource_drift) from
     plain green, the same way outcome 6 already does for pending_gated.

classify() is a pure function of (return code, stderr text, response body) -- no I/O, no AWS
call -- so all seven outcomes are unit-provable (tests/test_convergence_advisory.py). The `--post`
mode (the workflow's one-line body, extracted for terraform-apply-sandbox.yml's structural size
budget) performs the S3 read and the status POST around classify(), reusing the
NoSuchKey-vs-other-error pattern already at terraform-drift.yml:198-225.

main() never raises (Decision 55/83: the advisory-status job must never red a PR) -- any
unexpected exception classifies as `error`/UNVERIFIED rather than propagating, per this plan's
constraint that the job stays green by the classifier never raising, not by adding
continue-on-error to the adapter step.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional

from scripts.ci.convergence_classify import render_convergence_advisory_red_description

SUCCESS = "success"
FAILURE = "failure"
ERROR = "error"

_NOT_FOUND_RE = re.compile(r"NoSuchKey|Not Found|404", re.IGNORECASE)


def classify(rc: int, stderr: str, body: str) -> tuple[str, str]:
    """Classify a convergence-record read into (state, description) for the
    `terraform-converged` commit status. See module docstring for the six outcomes."""
    if rc != 0:
        if _NOT_FOUND_RE.search(stderr or ""):
            return (
                SUCCESS,
                "No convergence record yet (pass-on-absent); main treated as converged. Advisory only.",
            )
        return (
            ERROR,
            (
                "UNVERIFIED -- could not read the convergence record (credential/authorization "
                "failure), not evidence of convergence. Advisory only."
            ),
        )

    text = (body or "").strip()
    if not text:
        return (
            ERROR,
            "UNVERIFIED -- convergence record body is empty, not evidence of pass-on-absent green. Advisory only.",
        )

    try:
        record = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return (
            ERROR,
            "UNVERIFIED -- convergence record could not be parsed as JSON, not evidence of convergence. Advisory only.",
        )
    if not isinstance(record, dict):
        return (
            ERROR,
            "UNVERIFIED -- convergence record JSON is not an object, not evidence of convergence. Advisory only.",
        )

    status = record.get("status", "")
    if status == "red":
        return (FAILURE, render_convergence_advisory_red_description(record))

    pending_gated = record.get("pending_gated")
    pending_sha = pending_gated.get("commit_sha") if isinstance(pending_gated, dict) else None
    if pending_sha:
        return (
            SUCCESS,
            (
                f"main is pending-gated (routed change {pending_sha} awaits gated-apply approval; "
                "not a failure). Advisory only."
            ),
        )

    pending_codification = record.get("pending_codification")
    first_seen = pending_codification.get("first_seen") if isinstance(pending_codification, dict) else None
    if first_seen:
        return (
            SUCCESS,
            (f"main is pending-codification (code-behind-state delta since {first_seen}; not a failure). Advisory only."),
        )

    return SUCCESS, "main is converged (last sandbox apply GREEN). Advisory only."


def _read_record() -> tuple[int, str, str]:
    """Read the convergence record with `aws s3api get-object`; returns (rc, stderr, body)."""
    if os.environ.get("OIDC_FAILED", "") == "true":
        return 1, "assume-role failed -- OIDC credentials unavailable", ""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rec.json")
            result = subprocess.run(
                [
                    "aws",
                    "s3api",
                    "get-object",
                    "--bucket",
                    os.environ.get("CONVERGENCE_BUCKET", ""),
                    "--key",
                    os.environ.get("CONVERGENCE_KEY", ""),
                    out,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            body = ""
            if result.returncode == 0:
                with open(out, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            return result.returncode, result.stderr.strip(), body
    except OSError as exc:
        return 1, str(exc), ""


def post() -> int:
    """Read the record, classify it and POST the terraform-converged status; a failing POST exits non-zero."""
    try:
        state, description = classify(*_read_record())
    except Exception as exc:  # noqa: BLE001 -- never raise past the POST; the advisory job must stay green (Decision 83)
        state, description = (
            ERROR,
            f"UNVERIFIED -- convergence_advisory classifier raised {type(exc).__name__}; treating as unverified.",
        )
    env = os.environ.get
    repo, head_sha = env("REPO", ""), env("HEAD_SHA", "")
    target = f"{env('SERVER_URL', '')}/{repo}/actions/runs/{env('RUN_ID', '')}"
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/statuses/{head_sha}",
                "--method",
                "POST",
                "--field",
                f"state={state}",
                "--field",
                "context=terraform-converged",
                "--field",
                f"description={description}",
                "--field",
                f"target_url={target}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        print(f"status POST failed: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint: read RC / S3_STDERR / REC_JSON from the environment, print
    "<state>\\n<description>" for the calling `run:` body to parse.

    Always exits 0 and never raises -- classification itself must never fail the advisory-status
    job (Decision 83: it must never red a PR).
    """
    if argv and argv[0] == "--post":
        return post()
    try:
        rc_raw = os.environ.get("RC", "0")
        try:
            rc = int(rc_raw)
        except ValueError:
            rc = 1
        stderr = os.environ.get("S3_STDERR", "")
        body = os.environ.get("REC_JSON", "")
        state, description = classify(rc, stderr, body)
    except Exception as exc:  # noqa: BLE001 -- never raise; the advisory job must stay green (Decision 83)
        state, description = (
            ERROR,
            (
                f"UNVERIFIED -- convergence_advisory classifier raised {type(exc).__name__}; "
                "treating as unverified rather than crashing the advisory-status job."
            ),
        )
    print(state)
    print(description)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
