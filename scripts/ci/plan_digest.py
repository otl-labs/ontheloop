#!/usr/bin/env python3
"""DEP-07 saved-plan digest emit/verify helper (T2.46 c2, terraform-apply-chain-consolidation).

Closes the no-TOCTOU integrity gap: a saved tfplan/personal/<pr>/<sha>.bin object persisted by a
PR's plan job could previously be overwritten between review and merge-time apply with
no control catching the substitution. This module is the pure, in-process digest primitive both
sides of that gap share:

  emit(path)            -> sha256 hex digest of the plan bytes. Called at persist time
                            (terraform-plan-trusted.yml's plan job) to produce the
                            reference posted as BOTH a PR comment line and a `terraform-plan-digest`
                            commit status on the PR head sha.
  verify(path, reference) -> None on match; raises PlanDigestError (FAIL CLOSED) on any mismatch,
                            or on an empty/missing/whitespace-only reference. Called by the
                            fetch-saved-plan composite action at every apply fetch site, BEFORE
                            `terraform show -json` / `terraform apply` ever sees the fetched bytes.

No S3, no network -- the compare path is pure bytes-in, hex-out. The composite action fetches
plan.bin and looks up the commit-status reference; this module only computes and compares.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


class PlanDigestError(RuntimeError):
    """Fail-closed error: digest mismatch, or an empty/missing reference (DEP-07)."""


def emit(path: str | Path) -> str:
    """Return the sha256 hex digest of the file at path. Raises OSError if unreadable."""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def verify(path: str | Path, reference: str | None) -> None:
    """Raise PlanDigestError unless emit(path) == reference (fail closed on mismatch or empty ref).

    A None/empty/whitespace-only reference is treated identically to a mismatch -- both fail
    closed. This is deliberate: a missing reference (e.g. the terraform-plan-digest commit status
    was never posted, or the lookup failed) must never be silently treated as "nothing to verify."
    """
    ref = (reference or "").strip()
    if not ref:
        raise PlanDigestError(f"no reference digest supplied for {path} -- failing closed (missing/empty reference)")
    actual = emit(path)
    if actual != ref:
        raise PlanDigestError(f"digest mismatch for {path}: computed={actual} reference={ref} -- failing closed")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (consumed by .github/actions/fetch-saved-plan and the trusted plan job).

    Usage:
      plan_digest.py emit <path>              -- print the sha256 hex digest, exit 0
      plan_digest.py verify <path> <reference> -- exit 0 on match, exit 1 (fail closed) otherwise
    """
    parser = argparse.ArgumentParser(prog="plan_digest.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_emit = sub.add_parser("emit")
    p_emit.add_argument("path")

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("path")
    p_verify.add_argument("reference")

    args = parser.parse_args(argv)

    if args.command == "emit":
        try:
            print(emit(args.path))
        except OSError as exc:
            print(f"plan_digest: cannot read {args.path!r}: {exc}", file=sys.stderr)
            return 1
        return 0

    # the remaining branch handles command "verify"
    try:
        verify(args.path, args.reference)
    except PlanDigestError as exc:
        print(f"plan_digest: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"plan_digest: cannot read {args.path!r}: {exc}", file=sys.stderr)
        return 1
    print(f"plan_digest: OK -- {args.path} matches the reference digest.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
