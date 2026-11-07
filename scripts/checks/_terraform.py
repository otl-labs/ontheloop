"""Terraform validation gate: creds-free init/validate/fmt, proxy-403 detection, and the
credential-needing drift check (Decision 104 sibling-module extraction from
scripts/checks/_scaffolding.py).

This module owns the terraform concern exclusively so scripts/checks/_scaffolding.py's non-check
CLI orchestration stays under the SLOC ratchet (Decision 102) without touching
scripts/validate.py, which continues to import these symbols (now re-exported by
_scaffolding's facade block). Decision 119: CC-web's outbound proxy scopes github.com to
repo-scoped API calls, so `terraform init` of a root using a third-party (github.com-hosted)
provider permanently 403s fetching the provider's authentication checksums -- a PERMANENT
condition, never added to _TRANSIENT_INIT_SIGNATURES. _is_proxy_blocked_init stays a narrow
three-marker co-occurrence check (never a bare "403" substring) so a non-github 403 is never
masked, and run_terraform_creds_free keeps deferring `terraform validate` to the required
terraform-validate CI job on a proxy_blocked outcome.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from scripts.checks import _common
from scripts.checks.iam_tf.validate_terraform_try import validate_terraform_try

# Provider dependency drift terraform reports when `-lockfile=readonly` refuses to rewrite the
# committed lock (measured, Terraform 1.10.5, planning session): a distinct, permanent failure --
# never retried, never routed through _is_proxy_blocked_init's three-marker check.
_LOCK_DRIFT_MARKER = "Provider dependency changes detected"


def tracked_lock_files(root: Path) -> list[str] | None:
    """Repo-relative paths of every git-tracked terraform/**/.terraform.lock.hcl file.

    Calls subprocess.run DIRECTLY -- never _common.run -- so the many unrelated _common.run
    mocks across the test suite (patched for their own purposes) never intercept this call;
    under those tests real git runs and finds the real tracked locks. Returns None when git
    fails or is absent (FileNotFoundError), never an empty list -- an unmeasurable corpus must
    not be conflated with "no tracked locks" (Decision 55).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--", "terraform/**/.terraform.lock.hcl"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=root,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def _readonly_flag_for(root: str, tracked: list[str] | None) -> list[str]:
    """`["-lockfile=readonly"]` iff `root`'s lock file is tracked (or, when tracked_lock_files
    returned None because git is unavailable, iff the lock file exists on disk at all -- fail
    closed toward never writing a lock the caller cannot prove is untracked)."""
    lock_path = _common.ROOT / root / ".terraform.lock.hcl"
    if tracked is None:
        return ["-lockfile=readonly"] if lock_path.is_file() else []
    return ["-lockfile=readonly"] if f"{root}/.terraform.lock.hcl" in tracked else []


# Transient terraform registry.terraform.io 5xx signatures, plus provider-download network
# transients (connection reset / timeout / handshake / truncated stream); used by
# _terraform_init_with_retry and by the bounded retry loops in terraform-drift.yml and the
# .github/actions/terraform-init-retry/action.yml composite action (parity required). Parity is
# substring (Python `in`) vs ERE (bash `grep -qE`) and therefore holds only for metacharacter-free
# signatures.
_TRANSIENT_INIT_SIGNATURES: tuple[str, ...] = (
    "502",
    "Bad Gateway",
    "Gateway Timeout",
    "could not query provider registry",
    "failed after ",
    "connection reset by peer",
    "i/o timeout",
    "TLS handshake timeout",
    "unexpected EOF",
)

# CC-web outbound proxy scopes github.com to repo-scoped API calls, so `terraform init` of a
# root using a third-party (github.com-hosted) provider permanently 403s fetching the
# provider's authentication checksums. This is a PERMANENT condition, never added to
# _TRANSIENT_INIT_SIGNATURES. Detection below requires co-occurrence of all three markers
# (never a bare "403" substring) so a non-github 403 (e.g. an S3 backend 403) is never masked.
_PROXY_BLOCK_FORBIDDEN_MARKERS: tuple[str, ...] = ("403", "Forbidden")
_PROXY_BLOCK_HOST_MARKERS: tuple[str, ...] = ("github.com",)
_PROXY_BLOCK_CHECKSUM_MARKERS: tuple[str, ...] = ("failed to retrieve", "checksum", "authentication")


def _is_proxy_blocked_init(output: str) -> bool:
    """True iff `output` is the CC-web permanent proxy-403 on a third-party provider fetch.

    Requires co-occurrence of a 403/Forbidden marker AND a github.com marker AND an
    auth-checksum/"failed to retrieve" marker. Never a bare "403" substring check -- an
    any()-over-tuple here would skip on ANY 403 (e.g. an S3/backend 403) and mask a genuine
    failure (Decision 55).
    """
    return (
        any(m in output for m in _PROXY_BLOCK_FORBIDDEN_MARKERS)
        and any(m in output for m in _PROXY_BLOCK_HOST_MARKERS)
        and any(m in output for m in _PROXY_BLOCK_CHECKSUM_MARKERS)
    )


# Each terraform root is standalone (own provider + required_providers).
# terraform/personal/ is the applied root.
# terraform/github/ is the isolated GitHub-settings module (T2.12), CI-applied behind one operator approval
# by terraform-github-apply.yml (Decision 248).
# terraform/bootstrap/ is the CI/CD bootstrap root (admin-only, NEVER auto-apply -- CD.35 Wave 4 / T2.23).
_TERRAFORM_ROOTS = ("terraform/personal", "terraform/github", "terraform/bootstrap")


def _terraform_init_with_retry(label: str, cmd: list[str], failed: list[str]) -> str:
    """Run a terraform init command with bounded retry on transient registry 5xx.

    Returns one of four outcomes:
    - "success": init succeeded; caller runs validate + fmt (never appends to failed).
    - "proxy_blocked": permanent CC-web proxy-403 on a third-party provider's checksum fetch
      (_is_proxy_blocked_init); caller must skip validate (deferred to the terraform-validate
      CI job) but STILL run fmt-check (never appends to failed -- this is not a failure).
    - "failed": genuine permanent failure; label is appended to failed, caller skips both.
    - transient 5xx (_TRANSIENT_INIT_SIGNATURES): retried up to max_attempts before falling
      through to "failed"; parity with the workflow retry loop.
    Matches invoke_step output format for the step header.
    """
    print(f"\n=== {label} ===")
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        result = _common.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=_common.ROOT)
        if result.returncode == 0:
            print(result.stdout, end="")
            return "success"
        combined = result.stdout + result.stderr
        if _LOCK_DRIFT_MARKER in combined:
            print(combined, end="")
            print(
                f"FAIL: {label} -- the committed lock file is inconsistent with the configuration's "
                "declared providers and -lockfile=readonly refuses to rewrite it. Regenerate with "
                "`terraform init -backend=false` (no -upgrade) and commit the lock; see "
                "validate_terraform_lock_coherence."
            )
            failed.append(label)
            return "failed"
        if _is_proxy_blocked_init(combined):
            print(combined, end="")
            print(
                f"SKIP: {label} -- CC-web outbound proxy blocks the third-party provider's "
                "github.com checksum fetch (permanent, not retried). `terraform validate` for "
                "this root is deferred to the required terraform-validate CI job; `terraform "
                "fmt -check` still runs locally (no provider install needed)."
            )
            return "proxy_blocked"
        is_transient = any(sig in combined for sig in _TRANSIENT_INIT_SIGNATURES)
        if is_transient and attempt < max_attempts:
            delay = min(2**attempt, 16)
            print(f"transient registry error (attempt {attempt}/{max_attempts}); retrying in {delay}s...")
            print(combined, end="")
            time.sleep(delay)
            continue
        print(combined, end="")
        failed.append(label)
        return "failed"
    return "failed"  # pragma: no cover -- unreachable: loop body always returns on the final attempt


def run_terraform_creds_free(failed: list[str], roots: tuple[str, ...] = _TERRAFORM_ROOTS) -> None:
    """Credential-free terraform gate: init -backend=false + validate + fmt -check per root.

    -backend=false skips backend initialisation (no AWS credentials required); validate and
    fmt are offline operations. Tool-gated on terraform presence with a visible SKIP so the
    check degrades cleanly where terraform is absent (the terraform-validate CI job enforces it).
    This is the single source of truth for terraform validation -- both the full presubmit tier
    and `--terraform-only` (CI) call it; there is no parallel/duplicate validation.
    """
    if not shutil.which("terraform"):
        print("\n=== Terraform checks skipped (terraform not found in PATH) ===")
        print("Terraform validate/fmt run in the terraform-validate CI job.")
        return
    tracked = tracked_lock_files(_common.ROOT)
    for root in roots:
        chdir = f"-chdir={root}"
        outcome = _terraform_init_with_retry(
            f"Terraform init [{root}]",
            ["terraform", chdir, "init", "-backend=false", "-input=false", "-no-color"] + _readonly_flag_for(root, tracked),
            failed,
        )
        if outcome == "failed":
            continue
        if outcome == "success":
            _common.invoke_step(f"Terraform validate [{root}]", ["terraform", chdir, "validate", "-no-color"], failed)
        _common.invoke_step(f"Terraform fmt check [{root}]", ["terraform", chdir, "fmt", "-check", "-no-color"], failed)


def run_terraform_checks(failed: list[str]) -> None:
    """Full-presubmit terraform gate: creds-free checks on both roots, plus a creds-needing
    drift check (plan -detailed-exitcode) on the applied terraform/personal root only."""
    validate_terraform_try(failed)
    run_terraform_creds_free(failed)
    if not shutil.which("terraform"):
        return
    # Informational drift check on the APPLIED root only (terraform/ is no longer applied per
    # CD.21). Creds-needing: re-init the local backend, then plan. Never blocks -- when creds or
    # backend are unavailable the step degrades to a visible skip (Decision 60 actionable note).
    print("\n=== Terraform changes pending check (terraform/personal, informational) ===")
    tracked = tracked_lock_files(_common.ROOT)
    init_res = _common.run(
        ["terraform", "-chdir=terraform/personal", "init", "-input=false", "-no-color", "-reconfigure"]
        + _readonly_flag_for("terraform/personal", tracked),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_common.ROOT,
    )
    if init_res.returncode != 0:
        combined = init_res.stdout + init_res.stderr
        if _LOCK_DRIFT_MARKER in combined:
            print("Terraform plan skipped: lock drift (readonly init refused) -- see the creds-free init failure above.")
        else:
            print("Terraform plan skipped: backend/init unavailable (credentials missing) -- non-blocking.")
        return
    result = _common.run(
        ["terraform", "-chdir=terraform/personal", "plan", "-detailed-exitcode", "-no-color", "-input=false"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_common.ROOT,
    )
    if result.returncode == 2:
        print("WARNING: Terraform changes pending in terraform/personal. Run `terraform apply` before merge.")
    elif result.returncode not in (0, 2):
        print("Terraform plan skipped or failed (credentials unavailable) -- non-blocking.")
