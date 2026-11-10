"""CLI scaffolding steps that are not registered checks (Decision 104).

These implement the non-check scaffolding steps referenced by
scripts/checks/registry.py's pre_sequence()/full_sequence() (lint, precommit,
dependency health, DQ freshness, verifier-coverage report, budget-breach/bypass
rec filing, and the unit-test command builder); the terraform gate lives in
scripts/checks/_terraform.py, and the pytest-diff heavy-dependency-deferral machinery lives in
scripts/checks/_pytest_diff.py (Decision 128 decompose-by-default extraction) -- both are
re-exported here for facade back-compat. They stay outside the check registry (no @register
decorator, not a `validate_*(failed)` uniform check signature in every case) but outside
scripts/validate.py too, so the CLI entrypoint stays thin. scripts/validate.py imports and
re-exports all of these for back-compat (`patch("validate.<name>")` / `from scripts.validate
import <name>` keep resolving).
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import time
from pathlib import Path

import yaml

from scripts.checks import _common, registry, validation_result
from scripts.checks._budget_recs import _file_budget_breach_rec, _file_budget_bypass_rec  # noqa: F401
from scripts.checks._pytest_diff import (  # noqa: F401
    _PYTEST_FLAGS,
    _PYTEST_RANDOMLY_SEED,
    _REACTIVE_PROBE_MAX_WORKERS,
    _attribute_batched_collect_errors,
    _attribute_failed_test_files,
    _dist_to_import_name,
    _excluded_and_absent,
    _excluded_heavy_import_names,
    _expand_directory_test_targets,
    _match_changed_test_path,
    _parse_requirement_dist_names,
    _print_deferred_warning,
    _reactive_heavy_dep_signature,
    _runtime_heavy_dep_defer_reason,
    partition_changed_tests_by_collectability,
    run_pytest_diff,
)
from scripts.checks._terraform import (  # noqa: F401
    _TERRAFORM_ROOTS,
    _TRANSIENT_INIT_SIGNATURES,
    _terraform_init_with_retry,
    run_terraform_checks,
    run_terraform_creds_free,
)

# Transient Claude API error signatures; parity with _is_transient() in scripts/ci/claude_p_retry.sh.
# Distinct from _TRANSIENT_INIT_SIGNATURES (terraform registry 5xx). Decision 73, Decision 92.
_TRANSIENT_CLAUDE_SIGNATURES: tuple[str, ...] = ("500", "502", "503", "API Error: 5", "Internal server error", "overloaded")

_DQ_FRESHNESS_SECONDS = 3600  # 1 hour


_PRECOMMIT_CONFIG_PATH = ".pre-commit-config.yaml"


def _precommit_global_inputs(base_ref: str) -> frozenset[str] | None:
    """Return the derived global-input set that escalates run_precommit_checks to --all-files:
    the pre-commit config's own path, plus every path a hook's `--baseline` arg names (today:
    `.secrets.baseline`, via detect-secrets). Read at `base_ref` -- never HEAD or the working
    tree -- so a PR cannot weaken its own escalation trigger by editing the config it is
    escalated on (Decision 170).

    Returns None on any read or parse failure (config absent at `base_ref`, unreadable, or not
    valid YAML) -- the caller must treat None as FAIL CLOSED and escalate rather than skip,
    mirroring _should_run_in_pre's own "never silently skip on doubt" doctrine (dec-55).
    """
    result = _common.run(
        ["git", "show", f"{base_ref}:{_PRECOMMIT_CONFIG_PATH}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_common.ROOT,
    )
    if result.returncode != 0:
        return None
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    inputs = {_PRECOMMIT_CONFIG_PATH}
    for repo in data.get("repos") or []:
        for hook in (repo or {}).get("hooks") or []:
            args = (hook or {}).get("args") or []
            for i, arg in enumerate(args):
                if arg == "--baseline" and i + 1 < len(args):
                    inputs.add(str(args[i + 1]))
    return frozenset(inputs)


def precommit_escalates(files: list[str], base_ref: str = "origin/main") -> bool:
    """True iff `files` should escalate a not-already-all_files pre-commit run to --all-files
    (Decision 170): the derived global-input set at `base_ref` is unreadable/unparseable (fail
    closed, dec-55) OR `files` intersects it. Pure predicate, called from both
    `_run_precommit_body` (the real run) and the plan-time CLI (`selection_budget._report`, which
    has no pre-commit run to observe): the two can never disagree by construction.
    """
    global_inputs = _precommit_global_inputs(base_ref)
    return global_inputs is None or bool(set(files) & global_inputs)


def _run_precommit_body(failed: list[str], *, all_files: bool, files: list[str] | None) -> bool:
    """Returns whether this call escalated to --all-files (False on every skip path and when the
    caller already requested all_files=True)."""
    name = "pre-commit hooks"
    if importlib.util.find_spec("pre_commit") is None:
        print(f"\n=== {name} ===\nWARNING: pre-commit not installed; skipping (install requirements-dev.txt).")
        registry.skipped("pre-commit not installed")
        return False

    escalated = False
    target: list[str] = []
    if not all_files:
        target = files if files is not None else _common.get_changed_files()
        if not target:
            print(f"\n=== {name} ===\nNo changed files vs origin/main; skipping.")
            registry.skipped("no changed files vs origin/main")
            return False
        # This branch (all_files=False) is the --pre call site; it runs in push context for plans-only
        # pushes to main (Decision 205 cl.5), so the diff base is the push base there. Probed only when the
        # CI event is a push, so every other context keeps its git call sequence unchanged.
        push_base = _common.push_context_base() if os.environ.get("GITHUB_EVENT_NAME") == "push" else None
        if precommit_escalates(target, push_base or "origin/main"):
            all_files = True
            escalated = True

    cmd = [_common.PYTHON, "-m", "pre_commit", "run", "--show-diff-on-failure", "--color", "never"]
    if all_files:
        cmd.append("--all-files")
    else:
        cmd += ["--files", *target]

    print(f"\n=== {name} ===")
    if escalated:
        print("Escalated to --all-files: diff touches a derived pre-commit global input (Decision 170).")
    env = {**os.environ, "SKIP": "no-commit-to-branch"}
    result = _common.run(cmd, cwd=_common.ROOT, env=env)
    if result.returncode != 0:
        failed.append(name)
    registry.examined(1, unit="precommit_invocations")
    return escalated


def run_precommit_checks(failed: list[str], *, all_files: bool, files: list[str] | None = None) -> bool:
    """Run the pre-commit hook suite (detect-secrets, shape denylist, file hygiene).

    pre-commit is the single home for detect-secrets and the shape-based
    never-commit identifier denylist. Routing it through validate.py keeps
    validate.py the single source of truth: the same hooks run in the --pre edit
    loop, the pr-validate CI gate, and the main-validate full tier -- so a failing
    detect-secrets result can no longer merge unseen (it reddens the authoritative
    gate the way every other check does, instead of only the advisory pre_commit
    workflow that push-to-main never blocked on).

    no-commit-to-branch is skipped via SKIP: it is a commit-time guard already
    covered by .claude/hooks/never_on_main.py, and it would always fail on the
    push-to-main main-validate run (which legitimately runs on the main branch).

    Escalation (Decision 170): when not already all_files, a diff that touches a derived global
    input (see precommit_escalates) escalates to --all-files -- a pre-commit-config-only change
    (e.g. a hook-repo version pin bump) must not be able to slip past its own hook suite by
    touching no other file. Fail-closed: an unreadable or unparseable base-ref config escalates
    rather than skips.

    Returns the escalation decision this call actually applied (Decision 208): False on every skip
    path and when the caller already passed all_files=True -- so scripts/validate.py's budget
    scaffold reads the OBSERVED fact instead of re-evaluating the predicate with a second git show.
    """
    before = len(failed)
    escalated = False
    with registry.outcome_scope("run_precommit_checks", kind="scaffold"):
        escalated = _run_precommit_body(failed, all_files=all_files, files=files)
    validation_result.record_scaffold_outcome("run_precommit_checks", before, failed)
    return escalated


_LINT_TARGETS: tuple[str, ...] = ("src/", "tests/", "scripts/")


def run_lint_checks(failed: list[str], files: list[str] | None = None) -> None:
    """Lint the whole tree unconditionally -- both --pre and the full tier call this with the
    SAME `_LINT_TARGETS`, so a lint-relevant diff can never escape by touching no .py file. Before
    this (rec-3861/rec-3863), --pre filtered `files` to its own .py subset and no-opped on an
    empty/all-non-.py changed set, so a linter-version-only bump (ruff 0.15.20 -> 0.16.7, commit
    1c50e26b: five requirements-pin files, zero .py) passed --pre unlinted and only reddened the
    whole-tree full tier post-merge.

    `files` is retained but IGNORED for target selection: scripts/validate.py's --pre call site
    passes it positionally (`run_lint_checks(failed, files=changed)`), and dropping the parameter
    would TypeError there. Do not resurrect a files-based target filter here -- that is exactly
    the escape this plan closes.
    """
    before = len(failed)
    with registry.outcome_scope("run_lint_checks", kind="scaffold"):
        _common.invoke_step("Lint (ruff check)", [_common.PYTHON, "-m", "ruff", "check", *_LINT_TARGETS], failed)
        _common.invoke_step(
            "Format check (ruff format)", [_common.PYTHON, "-m", "ruff", "format", "--check", *_LINT_TARGETS], failed
        )
        # Unconditional and whole-tree: there is no reachable non-execution path left to declare
        # as a skip (Decision 170) -- this always examines the same fixed target set.
        registry.examined(len(_LINT_TARGETS), unit="lint_targets")
    validation_result.record_scaffold_outcome("run_lint_checks", before, failed)


def _mirror_budget_notice_to_summary(title: str, message: str) -> None:
    """Print a budget-gate notice and mirror it to CI's step summary (Decision 153). No rec, no exit."""
    print(message)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {title}\n\n{message}\n")


def _build_unit_test_cmd() -> list[str]:
    """Return the pytest command for the 'Unit tests + coverage' step.

    VTS-10/13 (audit validate-test-suite-4df4d48): full-tier parity with the fast tier's
    _PYTEST_FLAGS -- adds -n auto (xdist parallelism) and the SAME fixed --randomly-seed in place
    of "last" (rec-2653: a fixed integer seed overrides pyproject.toml's addopts
    "--randomly-seed=last" so every -n auto worker resolves an identical collection order on a
    cold .pytest_cache; validate_hermeticity_flags' widened guard and VP step 6's 5x consecutive
    xdist-collection check both key off this same fixed seed) and --timeout/--timeout-method
    (120s -- wider than the fast tier's 60s, since the full suite includes heavier/
    integration-adjacent units the fast tier's requirements-fast.txt excludes). This is a
    fast->full parity fix, not a double-add: distinct from A.1/Decision 153's
    _mirror_budget_notice_to_summary and the budget-assertion branch, left untouched here.

    --junitxml (ci-rca-identity-lifecycle): emits a junit XML report both tiers' full-suite run
    can hand to scripts.ci_rca.evidence for v2 fingerprint cause-group parsing on a post-merge
    failure. Additive to the hermeticity flags (validate_hermeticity_flags checks presence only).
    """
    return [
        _common.PYTHON,
        "-m",
        "pytest",
        "tests/",
        "-v",
        "-m",
        "not integration",
        "-n",
        "auto",
        "--timeout",
        "120",
        "--timeout-method=thread",
        f"--randomly-seed={_PYTEST_RANDOMLY_SEED}",
        "--cov=src",
        "--cov-report=term-missing",
        "--disable-socket",
        "--junitxml=logs/debug/pytest-junit.xml",
    ]


def run_dependency_checks() -> None:
    print("\n=== Dependency health -- CVE scan (informational) ===")
    try:
        result = _common.run(["pip-audit", "--strict"], cwd=_common.ROOT)
        if result.returncode != 0:
            print("pip-audit: vulnerabilities found (see above)")
    except FileNotFoundError:
        print("pip-audit not installed. Run: pip install pip-audit")

    print("\n=== Dependency health -- outdated packages (informational) ===")
    try:
        _common.run(["pip", "list", "--outdated"], cwd=_common.ROOT)
    except FileNotFoundError:
        print("Could not check outdated packages.")


# Named module-level credential-unavailability classifier (Decision 155/170), mirroring
# scripts.ops_portal.reader_transient.is_reader_unavailable's isinstance-guarded-by-ImportError
# plus message-fallback shape. botocore's credential exception TYPES are not reliably importable
# at classify time (tests/validate/test_scaffold_gates.py stubs sys.modules["boto3"] with a bare
# MagicMock precisely because boto3 -- and transitively botocore -- may be absent there), so the
# isinstance tier degrades to nothing under that condition and the message-pattern tier carries
# the discrimination instead. Deliberately narrow: this classifier decides whether ONE exception
# means "credentials unavailable" -- it must never launder an unrelated bug (a real defect in
# profile resolution or the STS call) into a silent skip.
_CREDENTIAL_UNAVAILABLE_MESSAGE_RE = re.compile(
    r"(token (has )?expired|profile.*(not found|could not be found)|unable to locate credentials|"
    r"no credentials|unauthorized.*sso|token.*retriev|expiredtoken)",
    re.IGNORECASE,
)


def _is_credentials_unavailable(exc: BaseException) -> bool:
    """True iff `exc` indicates AWS credentials are unavailable (expired/missing SSO token, an
    unresolvable profile, or an STS ExpiredToken response) -- the Decision 57 auto-invoke's own
    stated precondition, never a genuine bug elsewhere in the credential-check path."""
    try:
        import botocore.exceptions as _botocore_exceptions  # noqa: PLC0415

        if isinstance(exc, _botocore_exceptions.ClientError):
            return exc.response.get("Error", {}).get("Code") == "ExpiredToken"
        credential_types = (
            _botocore_exceptions.NoCredentialsError,
            _botocore_exceptions.ProfileNotFound,
            _botocore_exceptions.UnauthorizedSSOTokenError,
            _botocore_exceptions.TokenRetrievalError,
        )
        if isinstance(exc, credential_types):
            return True
    except ImportError:
        pass
    return bool(_CREDENTIAL_UNAVAILABLE_MESSAGE_RE.search(str(exc)))


def _ensure_fresh_dq_body(failed: list[str], dq_file: Path) -> None:
    if dq_file.exists():
        age_seconds = time.time() - dq_file.stat().st_mtime
        if age_seconds <= _DQ_FRESHNESS_SECONDS:
            print(f"DQ cache fresh ({age_seconds / 60:.1f}m old) -- skipping data_quality_runner.")
            registry.skipped("DQ cache fresh -- runner not needed")
            return
        print(f"DQ cache stale ({age_seconds / 3600:.1f}h old) -- re-running data_quality_runner.")
    else:
        print("DQ cache missing -- running data_quality_runner.")

    try:
        import boto3

        from scripts.aws_profile import resolve_aws_profile

        profile = resolve_aws_profile(default="agent_platform")
        boto3.Session(profile_name=profile).client("sts", region_name="eu-west-2").get_caller_identity()
    except Exception as exc:  # noqa: BLE001 -- discrimination is the point; see constraint 5a.
        if _is_credentials_unavailable(exc):
            print(
                "AWS credentials not available -- skipping data_quality_runner auto-invoke. "
                "Ensure AWS credentials are configured to enable DQ refresh (Decision 57)."
            )
            registry.skipped(f"AWS credentials unavailable: {type(exc).__name__}")
        else:
            print(f"FAIL: unexpected error checking AWS credentials: {type(exc).__name__}: {exc}")
            failed.append(f"Ensure fresh DQ results: unexpected credential-check error ({type(exc).__name__}: {exc})")
        return

    registry.examined(1, unit="dq_refresh_invocations")
    _common.invoke_step("Data quality runner", [_common.PYTHON, "-m", "scripts.data_quality_runner"], failed)


def ensure_fresh_dq_results(failed: list[str]) -> None:
    """Auto-invoke data_quality_runner if logs/debug/dq-latest.json is missing or stale.

    Called during the presubmit tier so the DQ verifier sees fresh data instead
    of SKIPPING on staleness or absence.

    Decision 57: when credentials are unavailable, prints an actionable message and skips rather
    than crashing. Decision 170: wrapped in registry.outcome_scope so this scaffold's declared
    outcome (skip / examined / undeclared-turned-failed) is harvested into the run's accounting
    evidence alongside every registered check's.
    """
    print("\n=== Ensure fresh DQ results ===")
    dq_file = _common.ROOT / "logs" / "debug" / "dq-latest.json"
    before = len(failed)
    with registry.outcome_scope("ensure_fresh_dq", kind="scaffold"):
        _ensure_fresh_dq_body(failed, dq_file)
    validation_result.record_scaffold_outcome("ensure_fresh_dq", before, failed)


def run_coverage_check(changed_files: list[str] | None = None) -> None:
    """Print scope files not covered by any registered verifier (advisory only).

    Surfaces V3 verifier coverage gaps (Decision 48).
    Never appends to the failed list -- exit 0 unconditionally.

    changed_files: reuse an already-computed diff (e.g. the --pre closure's `changed`) to
    avoid a redundant git call; falls back to _common.get_changed_files() when omitted.
    """
    print("\n=== Verifier coverage report (advisory) ===")
    changed = changed_files if changed_files is not None else _common.get_changed_files()
    if not changed:
        print("No changed files detected on this branch -- coverage check has nothing to report.")
        return

    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        from scripts.verifiers import check_coverage as _check_coverage

        uncovered = _check_coverage(changed)
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)

    if not uncovered:
        print(f"All scope files covered by at least one verifier ({len(changed)} files checked).")
        return

    print(f"{len(uncovered)} of {len(changed)} scope files lack verifier coverage:")
    for f in uncovered:
        print(f"  - {f}")
    print("\n(Advisory only -- this does not fail the build.)")
