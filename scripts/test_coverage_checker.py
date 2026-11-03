#!/usr/bin/env python3
"""AST-based test coverage checker.

For each changed Python source file, verifies:
1. A corresponding test file exists.
2. Per-file line coverage is 100% (when --check-coverage is set).

Usage:
    python scripts/test_coverage_checker.py --check-tests
    python scripts/test_coverage_checker.py --check-coverage
    python scripts/test_coverage_checker.py --check-tests --check-coverage
    python scripts/test_coverage_checker.py --check-tests --files src/common/config.py
"""

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_definitions(file_path: Path) -> list[str]:
    """Extract public function, async function, and class names at module level.

    Skips private definitions (names starting with '_').
    """
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, OSError):
        return []

    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
    return names


# scripts/checks/_scaffolding.py, _terraform.py, and _pytest_diff.py (Decision 170: extracted
# from _scaffolding.py under Decision 128 decompose-by-default, same orchestration-internal
# concern) are orchestration-internal helper modules -- not themselves registered checks --
# whose tests have always lived alongside the orchestrator's own tests (never with the per-check
# mirrors). Once "test_validate.py" retires from _RETIRING_GRANDFATHER_HOMES (rec-2709 Wave 1),
# they route to the SAME tests/validate/ concern-split package scripts/validate.py itself
# resolves to (see the "scripts/validate.py" entry in _CONCERN_SPLIT_TEST_PACKAGES below), rather
# than falling through to the generic drop-root mirror rule (which would otherwise compute
# tests/checks/test__scaffolding.py -- wrong; their real home is the orchestrator package).
_ORCHESTRATION_SCAFFOLDING_FILES = {"_scaffolding.py", "_terraform.py", "_pytest_diff.py"}

# The four ducklake_runtime split-out modules (PLAN-sloc-ducklake-layer) route to the
# pre-decomposition monolith's test file, mirroring the Decision 104 scripts/checks/** precedent:
# they are the write/table-DDL/read/metrics behavior oracle, exercised via the facade re-export.
# KEPT post rec-2709 Wave 7 (test_ducklake_runtime.py retired from _RETIRING_GRANDFATHER_HOMES):
# this special-case branch is exactly the Wave 1 scripts/checks/** treatment -- once the
# grandfather home retires, these four route through the mirror branch (drop-root, non-concern-split)
# to their own tests/common/test_ducklake_<stem>.py, rather than falling through to the FLAT
# per-file grandfather rule (which would wrongly resolve to tests/test_ducklake_writes.py etc.).
_DUCKLAKE_RUNTIME_SPLIT_MODULES = {
    "ducklake_writes.py",
    "ducklake_tables.py",
    "ducklake_reads.py",
    "ducklake_metrics.py",
}

# Nested scripts/ subpackages (RS-01 / rec-164): each len==3 module maps to a kept-in-place flat
# test file. session_*/sync_* strip the family prefix (module stem alone re-prefixed here);
# roadmap keeps full names (empty-suffix -> test_<stem>); llm is mixed (client/utils strip the
# llm_ prefix, model_registry/github_models_client keep their names) but a single "test_llm_"
# prefix reproduces all four via the two renamed tests (test_llm_model_registry.py,
# test_llm_github_models_client.py). agent_sdk keeps full names (test_agent_sdk_<stem>.py), the
# same convention as roadmap. backlog_health keeps full names (test_backlog_health_<stem>.py),
# the same convention as roadmap/agent_sdk (PLAN-backlog-health-detection). Whitelisted to the
# seven known subpackages so a NEW scripts/<pkg>/ does not silently inherit a mapping (do not
# generalise to any len==3).
_NESTED_SUBPACKAGE_TEST_PREFIX = {
    "ci_rca": "test_ci_rca_",
    "session": "test_session_",
    "sync": "test_sync_",
    "roadmap": "test_",
    "llm": "test_llm_",
    "agent_sdk": "test_agent_sdk_",
    "backlog_health": "test_backlog_health_",
}


def _grandfathered_source_to_test(source_path: Path) -> Path | None:
    """Pre-inversion (Decision 104 colocation) mapping -- verbatim body, preserved as the
    grandfather oracle every _RETIRING_GRANDFATHER_HOMES entry still resolves through.

    src/**/*.py           -> tests/test_{module}.py  (e.g. src/common/config.py -> tests/test_config.py)
    scripts/*.py          -> tests/test_{name}.py     (e.g. scripts/validate.py -> tests/test_validate.py)
    scripts/checks/**/*.py -> tests/test_validate.py  (every extracted check's tests live there,
                              colocated with the pre-decomposition monolith's test file -- Decision 104).
                              registry.py/_common.py are CONCERN-SPLIT (_CONCERN_SPLIT_TEST_PACKAGES
                              below) and resolve to their own test package directories instead
                              (Decision 169, folding the former dec-159 mechanism-files special
                              case into the concern-split set now that the pre-decomposition
                              monolith's test file no longer exists).
    scripts/convergence_health/*.py -> tests/test_convergence_health.py (facade decomposition of
                              the former single-file convergence_health monolith, same Decision 104
                              colocation precedent -- PLAN-convergence-health-sloc-decompose-guardrails).
    src/common/ducklake_{writes,tables,reads,metrics}.py -> tests/test_ducklake_runtime.py (split-out
                              from ducklake_runtime.py, PLAN-sloc-ducklake-layer, same Decision 104 precedent).
    src/lambdas/<slug>/*.py -> tests/test_{slug}_handler.py (parent-qualified rule, RS-08): every
                              file under a src/lambdas/<slug>/ directory -- handler.py plus any
                              split-out sibling (e.g. ducklake_writer/smoke_actions.py) -- maps to
                              that lambda's single test home, keyed off the parent slug rather than
                              the file's own stem. Generalizes the former ducklake_writer-only
                              special case so every src/lambdas/*/handler.py resolves to its own
                              distinct, real test home instead of colliding on the stem-based
                              tests/test_handler.py fallback (retired).
    scripts/{ci_rca,session,sync,roadmap,llm,agent_sdk,backlog_health}/<name>.py -> the module's
                              kept-in-place flat test (nested subpackages, RS-01 / rec-164):
                              ci_rca/session/sync strip the family prefix ->
                              test_ci_rca_/test_session_/test_sync_<name>.py; roadmap, agent_sdk
                              and backlog_health keep full names -> test_<name>.py /
                              test_agent_sdk_<name>.py / test_backlog_health_<name>.py; llm is
                              mixed (client/utils strip the llm_ prefix, model_registry/
                              github_models_client keep their names) but all four resolve via the
                              single test_llm_ prefix -> test_llm_client.py, test_llm_utils.py,
                              test_llm_model_registry.py, test_llm_github_models_client.py. See
                              _NESTED_SUBPACKAGE_TEST_PREFIX (whitelisted to these seven; no
                              general len==3 rule).

    Returns None for paths not under src/ or scripts/ -- including scripts/executor/** and
    scripts/ops_portal/**, which deliberately have no source-to-test mapping (Decision 124).
    """
    try:
        rel = source_path.resolve().relative_to(ROOT)
    except ValueError:
        return None

    parts = rel.parts
    if not parts:
        return None

    if parts[0] == "src" and len(parts) >= 3 and parts[1] == "common" and rel.name in _DUCKLAKE_RUNTIME_SPLIT_MODULES:
        return ROOT / "tests" / "test_ducklake_runtime.py"
    if parts[0] == "src" and len(parts) >= 4 and parts[1] == "lambdas":
        return ROOT / "tests" / f"test_{parts[2]}_handler.py"
    if parts[0] == "src" and len(parts) >= 2:
        stem = rel.stem
        return ROOT / "tests" / f"test_{stem}.py"
    if parts[0] == "scripts" and len(parts) >= 2 and parts[1] == "checks":
        return ROOT / "tests" / "test_validate.py"
    if parts[0] == "scripts" and len(parts) == 3 and parts[1] == "convergence_health":
        return ROOT / "tests" / "test_convergence_health.py"
    if parts[0] == "scripts" and len(parts) == 3 and parts[1] in _NESTED_SUBPACKAGE_TEST_PREFIX:
        # Nested scripts/ subpackages (RS-01 / rec-164): ci_rca/session/sync strip the family
        # prefix, roadmap keeps full names -- each module keeps its flat test (see the prefix map).
        # One dict-lookup branch (not four parallel elifs) keeps this function under the
        # Decision 43 cyclomatic-complexity ceiling; still whitelisted (no general len==3 rule).
        return ROOT / "tests" / f"{_NESTED_SUBPACKAGE_TEST_PREFIX[parts[1]]}{rel.stem}.py"
    if parts[0] == "scripts" and len(parts) == 2:
        stem = rel.stem
        return ROOT / "tests" / f"test_{stem}.py"

    return None


# The fixed rec-2709 roster: the 23 tests/ basenames grandfathered whole-repo by Decision 130
# (config/sloc_budgets.yaml). A frozenset -- membership only, never mutated.
_ALL_MIRROR_TARGET_HOMES: frozenset[str] = frozenset(
    {
        "test_build_lambda_deploy.py",
        "test_ci_rca_evidence.py",
        "test_contracts_enforcement.py",
        "test_convergence_health.py",
        "test_ducklake_maintenance_handler.py",
        "test_ducklake_neon_smoke_test.py",
        "test_ducklake_runtime.py",
        "test_ducklake_writer_handler.py",
        "test_execute_recommendation.py",
        "test_executor_plan.py",
        "test_executor_postflight.py",
        "test_executor_step_runner.py",
        "test_ducklake_reader_client.py",
        "test_lambda_manifest.py",
        "test_ops_data_portal.py",
        "test_platform_roadmap_state.py",
        "test_s3_log_store.py",
        "test_scheduled_agent_handler.py",
        "test_session_postflight.py",
        "test_session_preflight.py",
        "test_sync_ops.py",
        "test_validate.py",
        "test_verify_ci_workflow.py",
    }
)

# The not-yet-decomposed subset of _ALL_MIRROR_TARGET_HOMES -- a home in this set still
# resolves through the grandfather (colocation) rule; a wave RETIRES a target by deleting
# its one line here, which switches every source path mapping to that home over to the
# mirror rule. Seeded == _ALL_MIRROR_TARGET_HOMES on day one, so the mirror branch below is
# dormant and map_source_to_test is byte-identical to the pre-inversion function for every
# input (proven by TestMapSourceToTest / TestCheckTestFileExists staying green unchanged).
# Kept a plain set literal, one basename per line, for minimal per-wave merge conflict.
_RETIRING_GRANDFATHER_HOMES: set[str] = set()

# Repo-relative (POSIX, ROOT-relative) source paths whose mirror target is a test PACKAGE
# DIRECTORY rather than a single test_<stem>.py file: single-file monoliths with no
# per-submodule source to mirror 1:1, so their eventual decomposition splits the TEST by
# concern into multiple test_*.py modules under one directory. Entries are dormant until
# their _RETIRING_GRANDFATHER_HOMES line is deleted; each wave finalises its own membership
# (this seed is the known monolith roster at foundation time, not a closed list).
# Membership here is independently sufficient to route a source to its test package
# directory -- it no longer depends on the source's grandfathered home having retired (see
# map_source_to_test's direct membership check, which fires before the final `return home`).
_CONCERN_SPLIT_TEST_PACKAGES: frozenset[str] = frozenset(
    {
        "scripts/checks/registry.py",
        "scripts/checks/_common.py",
        "scripts/ops_data_portal.py",
        "scripts/s3_log_store.py",
        "scripts/contracts_enforcement.py",
        "scripts/platform_roadmap_state.py",
        "scripts/platform_roadmap_models.py",
        "scripts/build_lambda_deploy.py",
        "scripts/lambda_manifest.py",
        "scripts/ducklake_neon_smoke_test.py",
        "src/common/ducklake_reader_client.py",
        "src/common/ducklake_event_reads.py",
        "src/lambdas/ducklake_maintenance/handler.py",
        "src/lambdas/ducklake_writer/handler.py",
        "src/data/handlers/scheduled_agent_handler.py",
        "scripts/checks/iam_tf/validate_ci_refresh_read_coverage.py",
        "scripts/validate.py",
        "scripts/execute_recommendation.py",
        "scripts/session/preflight.py",
        "scripts/sync/ops.py",
        "scripts/session/postflight.py",
        "scripts/checks/deps/affected_tests.py",
        "scripts/test_coverage_checker.py",
        "scripts/sync/recommendations.py",
        "scripts/ci_rca/taxonomy.py",
        "scripts/decisions_md.py",
        "scripts/checks/contracts/validate_contract_drift.py",
        "scripts/roadmap/plan_document.py",
        "scripts/convergence_health/code_drift.py",
        "scripts/convergence_health/budget_ingest.py",
        "scripts/verification_graduation.py",
        "scripts/checks/verification/validate_graduation_completeness.py",
        "scripts/checks/verification/validate_scope_boundary.py",
        "scripts/checks/hygiene/validate_placement.py",
        "scripts/checks/verification/validate_vp_replay.py",
        "scripts/checks/verification/validate_red_case_floor.py",
        "scripts/checks/deps/validate_pre_glob_closure.py",
    }
)


def _mirror_source_to_test(source_path: Path) -> Path | None:
    """Mirror-convention target for `source_path`, used once its grandfather home retires.

    Drops the leading src/scripts root segment, keeps the remaining directory sub-path, and
    names the test test_<stem>.py in that mirrored directory -- e.g.
    scripts/checks/hygiene/validate_prose_allowlist.py ->
    tests/checks/hygiene/test_validate_prose_allowlist.py; scripts/executor/step_runner.py ->
    tests/executor/test_step_runner.py; src/common/config.py -> tests/common/test_config.py.
    A root script (no sub-path, e.g. scripts/validate.py) mirrors to tests/test_validate.py --
    identical to the flat rule, so retiring a root-script home is a no-op unless the source is
    a declared concern-split monolith. A declared concern-split monolith
    (_CONCERN_SPLIT_TEST_PACKAGES) instead resolves to its test PACKAGE DIRECTORY (no test_
    prefix, no .py suffix) -- e.g. scripts/sync/ops.py -> tests/sync/ops/;
    src/common/ducklake_reader_client.py -> tests/common/ducklake_reader_client/.
    """
    try:
        rel = source_path.resolve().relative_to(ROOT)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) < 2:
        return None

    mirror_subdir = Path(*parts[1:-1]) if len(parts) > 2 else Path()
    stem = rel.stem

    if rel.as_posix() in _CONCERN_SPLIT_TEST_PACKAGES:
        return ROOT / "tests" / mirror_subdir / stem
    return ROOT / "tests" / mirror_subdir / f"test_{stem}.py"


def map_source_to_test(source_path: Path) -> Path | None:
    """Map a source file path to its expected test file (or test package directory) path.

    Two rules, gated by a retiring grandfather-table (Decision 131, amends Decision 104):

    1. COLOCATION (grandfathered, retiring): while a source path's grandfathered test home's
       basename is still in _RETIRING_GRANDFATHER_HOMES, the pre-inversion colocation rule
       applies unchanged -- see _grandfathered_source_to_test for its full per-pattern table.
    2. MIRROR (post-retirement): once a wave deletes that basename from
       _RETIRING_GRANDFATHER_HOMES, every source path that grandfathers to it instead resolves
       via _mirror_source_to_test -- drop the leading src/scripts root, keep the remaining
       sub-path, name the file test_<stem>.py (or, for a declared concern-split monolith, the
       test PACKAGE DIRECTORY <mirror-subpath>/<stem>/). Examples: scripts/checks/hygiene/
       validate_prose_allowlist.py -> tests/checks/hygiene/test_validate_prose_allowlist.py;
       scripts/executor/step_runner.py -> tests/executor/test_step_runner.py;
       src/common/config.py -> tests/common/test_config.py; scripts/sync/ops.py (concern-split)
       -> tests/sync/ops/.

    WHY drop-root is safe/chosen: it matches established repo precedent (tests/checks/,
    tests/test_verifiers/) and standard pytest tree-mirroring, and is collision-free for the
    fixed 23-home roster because no scripts/<x>/ vs src/<x>/ subdirectory-name overlap exists.
    KNOWN BOUNDARY: a future scripts/<x>/ vs src/<x>/ collision would need a preserve-root
    exception for that pair -- out of scope now, flagged for the map's maintainer.

    scripts/executor/** and scripts/ops_portal/** deliberately return None on both rules
    (Decision 124) -- their grandfathered home is already None, so neither the retiring nor the
    mirror branch ever fires for them; a source path with no grandfathered home never joins the
    roster.

    Day one, _RETIRING_GRANDFATHER_HOMES == _ALL_MIRROR_TARGET_HOMES (every target was still
    retiring), so the mirror branch below was never taken and every result was byte-identical to
    the pre-inversion function (the TestMapSourceToTest / TestCheckTestFileExists oracle proved
    this on day one; see TestGrandfatherRetiringTable for the current, post-Wave-1 state). Each
    wave retires exactly one basename (a one-line, low-merge-conflict edit to
    _RETIRING_GRANDFATHER_HOMES) -- rec-2709 Wave 1 (PLAN-sloc-test-validate) retired
    "test_validate.py", the first of the 24-home roster to flip: every scripts/checks/**/*.py
    (and scripts/validate.py itself) now resolves via the mirror rule instead of colocating in
    the now-deleted tests/test_validate.py.

    scripts/checks/_scaffolding.py, _terraform.py, and _pytest_diff.py are a special case within
    the retired "test_validate.py" home: they are orchestration-internal helpers, not registered
    checks, so once "test_validate.py" retires they route to the SAME tests/validate/
    concern-split package scripts/validate.py resolves to (not the generic per-file mirror
    target) -- see _ORCHESTRATION_SCAFFOLDING_FILES.

    3. DIRECT CONCERN-SPLIT (independent of retirement): a source path listed in
       _CONCERN_SPLIT_TEST_PACKAGES resolves to its test PACKAGE DIRECTORY even if its
       grandfathered home's basename is still in _RETIRING_GRANDFATHER_HOMES (i.e. has not
       retired) or has no grandfathered home under _ALL_MIRROR_TARGET_HOMES at all. This
       decouples "is this source a declared concern-split monolith?" from "has this source's
       grandfathered home retired?" -- previously the only entry point into
       _CONCERN_SPLIT_TEST_PACKAGES was inside _mirror_source_to_test, reachable solely via
       the MIRROR branch above, so registering a file whose grandfathered home was not itself
       in _ALL_MIRROR_TARGET_HOMES was inert (the membership check was never consulted).

    4. FLAT-HOME-ABSENCE FALLBACK (fall-through only, rec-3809): when none of the three rules
       above fires, the plain fall-through home is a FLAT grandfathered path outside the fixed
       roster entirely. If that flat file does not exist on disk AND its mirror target does, use
       the mirror instead -- a source whose real test already lives at the mirror location (e.g.
       one added post-hoc under the mirror convention) must not be reported as missing a test file
       it already has. Requiring the mirror to also exist keeps this inert for a hypothetical path
       where neither location is real (a pure structural-mapping check has nothing to redirect to).
       This check is pinned strictly AFTER the _RETIRING_GRANDFATHER_HOMES branch, never ahead of
       it: placed earlier it would fail OPEN, silently flipping a still-retiring roster monolith's
       sources to a mirror target that may already exist mid-wave (Decision 131 clause 1 wave
       discipline). A flat home that DOES exist is returned unchanged, so every currently-green
       source keeps resolving exactly as before.

    Returns None for paths not under src/ or scripts/, or with no grandfathered home (unless
    the path is itself a declared concern-split entry, which is checked independently of
    having a grandfathered home).
    """
    home = _grandfathered_source_to_test(source_path)
    if home is not None:
        if home.name in _RETIRING_GRANDFATHER_HOMES:
            return home
        if home.name in _ALL_MIRROR_TARGET_HOMES:
            try:
                rel_name = source_path.resolve().relative_to(ROOT).name
            except ValueError:
                rel_name = source_path.name
            if home.name == "test_validate.py" and rel_name in _ORCHESTRATION_SCAFFOLDING_FILES:
                return ROOT / "tests" / "validate"
            # smoke_actions.py shares ducklake_writer's concern-split handler package (Edit C, rec-2709 Wave 8).
            if home.name == "test_ducklake_writer_handler.py" and rel_name == "smoke_actions.py":
                return ROOT / "tests" / "lambdas" / "ducklake_writer" / "handler"
            return _mirror_source_to_test(source_path)
    try:
        rel_str = source_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel_str = None
    if rel_str is not None and rel_str in _CONCERN_SPLIT_TEST_PACKAGES:
        return _mirror_source_to_test(source_path)
    if home is not None and not home.exists():
        mirror = _mirror_source_to_test(source_path)
        if mirror is not None and mirror.exists():
            return mirror
    return home


def check_test_file_exists(source_path: Path) -> tuple[bool, str]:
    """Check whether a test file (or, for a concern-split target, a test package directory)
    exists for the given source file.

    Returns (True, reason) if the test file/package exists or the path is unmapped.
    Returns (False, reason) if the test file/package is missing.
    """
    expected = map_source_to_test(source_path)
    if expected is None:
        return True, "skipped: not in src/ or scripts/"
    if expected.suffix != ".py":
        # Concern-split mirror target: a test PACKAGE DIRECTORY, not a single file.
        if expected.is_dir() and any(expected.glob("test_*.py")):
            return True, "test package found"
        try:
            display = expected.relative_to(ROOT)
        except ValueError:
            display = expected
        return False, f"missing test package: {display}"
    if expected.exists():
        return True, "test file found"
    try:
        display = expected.relative_to(ROOT)
    except ValueError:
        display = expected
    return False, f"missing test file: {display}"


def _is_empty_directory_target(test_path: Path) -> bool:
    """True if `test_path` is a concern-split test PACKAGE DIRECTORY not yet populated with
    any test_*.py -- factored out so check_per_file_coverage's own branch count (Decision 43)
    absorbs one Call node instead of the nested is_dir()/any() pair."""
    return test_path.is_dir() and not any(test_path.glob("test_*.py"))


# The subprocess/pytest measurement mechanics live in coverage_baseline.py (Decision 102/128
# decompose-by-default: this file stays under its SLOC budget). ROOT/map_source_to_test/
# _is_empty_directory_target/subprocess are passed in explicitly (not imported by the callee) so
# the lookup happens on THIS module's own globals -- what keeps `patch("test_coverage_checker.
# <name>")` intercepting correctly, including the ad hoc module copy the legacy split test suite
# (tests/fixtures/coverage_checker_module.py) loads under a bare module name.
def _measure_and_check(source_files: list[Path]) -> tuple[dict[str, float | None], list[str]]:
    from scripts.checks.misc import coverage_baseline

    return coverage_baseline.measure_and_check(
        source_files,
        root=ROOT,
        map_source_to_test=map_source_to_test,
        is_empty_dir=_is_empty_directory_target,
        subprocess_module=subprocess,
    )


def measure_per_file_coverage(source_files: list[Path]) -> dict[str, float | None]:
    """Single measurement entry point -- {str(resolved path): measured pct | None}."""
    pcts, _ = _measure_and_check(source_files)
    return pcts


def check_per_file_coverage(source_files: list[Path]) -> list[str]:
    """Run pytest coverage for each source file and return baseline-aware failures.

    A changed file WITH a `config/coverage_baseline.yaml` entry fails iff measured < that
    entry's threshold; a file WITHOUT an entry fails iff measured < 100 (unchanged default).
    """
    from scripts.checks.misc import coverage_baseline

    pcts, errors = _measure_and_check(source_files)
    baseline = coverage_baseline.load_baseline()

    for source_path in source_files:
        key = str(source_path.resolve())
        pct = pcts.get(key)
        if pct is None:
            continue  # already accounted for (hard error) or silently skipped upstream
        try:
            rel = source_path.resolve().relative_to(ROOT)
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")
        threshold = baseline.get(rel_str, 100.0)
        if not coverage_baseline.compare(pct, rel_str, baseline):
            if threshold >= 100.0:
                errors.append(f"{rel}: {pct:.1f}% line coverage (expected 100%)")
            else:
                errors.append(f"{rel}: {pct:.1f}% line coverage (expected >= {threshold:.1f}%)")

    return errors


def get_changed_source_files(files: list[str] | None = None, *, root: Path | None = None) -> list[Path]:
    """Return changed Python source files under src/ or scripts/.

    Delegates to scripts.checks._common.get_status_aware_diff(root=base_root) for the git
    diff (push-context-base -> merge-base -> HEAD fallback, plus untracked "??" files), keeping
    every status except "D" so a brand-new untracked module is measured. Excludes __init__.py,
    conftest.py, and test files.

    If files is provided, uses that list instead of git diff.

    `root` scopes every git probe and both path filters (existence and the src/scripts
    relative_to check) to one shared base -- defaulting to ROOT when None, so existing callers
    are byte-identical.
    """
    base_root = root if root is not None else ROOT

    if files is not None:
        paths: list[Path] = []
        for f in files:
            p = Path(f)
            if not p.is_absolute():
                p = base_root / f
            paths.append(p.resolve())
    else:
        from scripts.checks import _common  # noqa: PLC0415

        entries = _common.get_status_aware_diff(root=base_root)
        paths = [base_root / path for status, path in entries if status != "D" and path.endswith(".py")]

    result: list[Path] = []
    excluded_names = {"__init__.py", "conftest.py"}
    for p in paths:
        if not p.suffix == ".py":
            continue
        if p.name in excluded_names:
            continue
        try:
            rel = p.resolve().relative_to(base_root.resolve())
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0] in ("src", "scripts"):
            if p.resolve().exists():
                result.append(p.resolve())

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that changed Python source files have test files and 100%% coverage.")
    parser.add_argument(
        "--check-tests",
        action="store_true",
        help="Verify that a test file exists for each changed source file.",
    )
    parser.add_argument(
        "--check-coverage",
        action="store_true",
        help="Verify that per-file line coverage is 100%% for each changed source file.",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        help="Explicit list of source files to check instead of git diff.",
    )
    args = parser.parse_args()

    if not args.check_tests and not args.check_coverage:
        parser.print_help()
        sys.exit(0)

    source_files = get_changed_source_files(args.files)

    if not source_files:
        print("No source file changes to check.")
        sys.exit(0)

    print(f"Checking {len(source_files)} source file(s)...")

    errors: list[str] = []

    if args.check_tests:
        missing_tests: list[str] = []
        for src in source_files:
            ok, msg = check_test_file_exists(src)
            if not ok:
                missing_tests.append(f"  {src.relative_to(ROOT)}: {msg}")
        if missing_tests:
            errors.append("Missing test files:")
            errors.extend(missing_tests)
        else:
            print(f"  Test files: all {len(source_files)} source files have test files.")

    if args.check_coverage:
        coverage_errors = check_per_file_coverage(source_files)
        if coverage_errors:
            errors.append("Coverage failures (< 100%):")
            for e in coverage_errors:
                errors.append(f"  {e}")
        else:
            print(f"  Coverage: all {len(source_files)} source files at 100%.")

    if errors:
        print("\nTest coverage check FAILED:")
        for e in errors:
            print(e)
        print("DIAGNOSTIC VERDICT: FAIL (not an authoritative validation gate)")
        sys.exit(1)

    print(f"\nTest coverage check: {len(source_files)} source files checked, 0 missing test files, 0 below 100% coverage")
    print("DIAGNOSTIC VERDICT: PASS (not an authoritative validation gate)")
    sys.exit(0)


if __name__ == "__main__":
    main()
