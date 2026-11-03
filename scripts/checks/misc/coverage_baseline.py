"""Coverage baseline: one-time grandfather roster + measurement engine (Decision 130 style,
tamper guard mirrors Decision 128's validate_sloc_budget_raises).

Collocates the baseline's semantics (load/compare) with its enforcement
(`validate_coverage_baseline_edits`) in one file (AGENTS.md collocation rule), and hosts the
per-file pytest+coverage measurement mechanics that used to live in
scripts/test_coverage_checker.py (moved here under the Decision 102/128 decompose-by-default
SLOC rule -- that file re-exports `measure_per_file_coverage` as a one-line facade).

`measure_and_check`/`measure_per_file_coverage` deliberately access `map_source_to_test`,
`_is_empty_directory_target`, `ROOT`, and `subprocess` via the scripts.test_coverage_checker
MODULE OBJECT (an attribute lookup at call time), not a direct name import -- this is what lets
existing `unittest.mock.patch("test_coverage_checker.<name>")` call sites keep intercepting
correctly after the relocation (the patch mutates the attribute on that module object, and an
attribute lookup on the same object sees the patched value; a `from ... import name` binding
would not).

A changed file WITHOUT a config/coverage_baseline.yaml entry keeps the 100% standard
(Decision 48 V2 row, narrowed by forward reference in the carrying Decision); a file WITH an
entry passes iff measured >= that entry's threshold. Lowering an entry, or registering a new
sub-100 entry, requires an inline `# baseline-lowered: dec-NNN <reason>` marker naming a real
`## Decision NNN:` header whose body actually authorizes the entry (Decision 165 marker-guard
consolidation upgraded this from existence to authorization) -- raises and removals are always
unrestricted. When config/coverage_baseline.yaml does not exist at origin/main (e.g. the
seeding PR itself), the guard SKIPs rather than treating "missing base" as "empty base"
(mirrors scripts.checks._marker_guard.default_base_reader's contract).

The tamper-guard half (`validate_coverage_baseline_edits`) delegates its diff/authorization
mechanics to scripts.checks._marker_guard (shared across all five raise-marker guards); this
module owns only the registry's own RegistrySpec binding plus the measurement engine below.

The coverage child runs in its own session (``start_new_session=True``) so a timeout or an
interrupt while waiting kills its whole process group through ``_process_group.kill_and_reap``
without ever signalling this process's group. Accepted residuals: (a) a SIGTERM/SIGKILL delivered
to validate itself raises no Python exception, so a still-running detached coverage child is
orphaned and may later write .coverage.json or .coverage into the repo root (before this change a
group-directed kill took it down with validate; moving those artefacts out of the root is owned by
rec-3615/rec-3282); (b) the timeout arm is POSIX-only, matching the vp-replay "every surface is
Linux" precedent.

The child selects tests with `-m "not integration"`, the same expression the full-tier unit-test command uses, so a
live-boundary test never runs inside a coverage measurement (rec-4273).

Every per-file coverage child runs on coverage.py's sysmon core (rec-4365): PEP 669 monitoring
disables line events for untraced code, where the C tracer still pays a per-event callback --
the same choice and rationale as scripts/checks/_pytest_diff.py:68-82.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, Optional

import yaml

from scripts.checks import _common, _marker_guard, _process_group, registry

_BASELINE_REL_PATH = "config/coverage_baseline.yaml"

# Not raised to fit a slow target: rec-4370 owns runtime drift from causes other than the YAML
# parse cost rec-4365 fixed, and a timeout is a recorded failure.
COVERAGE_TIMEOUT_SECONDS = 300

# PEP 669 monitoring core (rec-4365): disables per-line callbacks for untraced code, where the
# default C tracer still pays one per event. Scopes the switch to the coverage child alone via
# the environment variable below -- never a [run] core setting in pyproject.toml, which would
# also move CI's whole-tree unit-test --cov run. coverage.py falls back to its default core with
# a warning, never an error, if sysmon is unusable.
COVERAGE_CORE = "sysmon"


def load_baseline(path=None) -> dict[str, float]:
    """Load {repo-relative path: baseline pct} from config/coverage_baseline.yaml."""
    p = path or (_common.ROOT / _BASELINE_REL_PATH)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries = data.get("entries") or {}
    return {str(k): round(float(v), 1) for k, v in entries.items()}


def compare(measured_pct: float, rel_path: str, baseline: dict[str, float] | None = None) -> bool:
    """True (pass) iff measured_pct >= the file's baseline threshold (100.0 if unbaselined)."""
    table = baseline if baseline is not None else load_baseline()
    threshold = table.get(rel_path, 100.0)
    return round(measured_pct, 1) >= threshold


def measure_and_check(source_files: list, *, root, map_source_to_test, is_empty_dir, subprocess_module) -> tuple:
    """Run pytest coverage for each source file.

    Returns ({str(resolved path): measured pct | None}, hard-error strings unrelated to the
    baseline compare -- coverage timeouts and "no tests exercise this file"). A None value
    means no usable measurement was produced.

    `root`/`map_source_to_test`/`is_empty_dir`/`subprocess_module` are passed in by the caller
    (scripts/test_coverage_checker.py) rather than imported here, so the lookup happens on the
    CALLER's own module globals -- this is what keeps `patch("test_coverage_checker.<name>")`
    (which patches an attribute on whichever module object loaded that file) intercepting
    correctly, including the ad hoc `importlib.spec_from_file_location` copy the legacy split
    test suite (tests/fixtures/coverage_checker_module.py) loads under a bare module name.
    """
    pcts: dict = {}
    errors: list = []
    baseline_table = load_baseline(root / _BASELINE_REL_PATH)

    for source_path in source_files:
        try:
            rel = source_path.resolve().relative_to(root)
        except ValueError:
            continue
        key = str(source_path.resolve())

        # rec-943: --cov must be the file's PARENT DIRECTORY, never a single .py path --
        # coverage.py treats a --cov/source entry as an importable name or a directory; a
        # single-file dotted/path spec silently collects no data at all (module-not-imported +
        # no-data-collected), which would otherwise masquerade as a pass.
        cov_target = str(rel.parent)

        test_path = map_source_to_test(source_path)
        if test_path is None or not test_path.exists():
            continue
        if is_empty_dir(test_path):
            continue
        test_path_str = str(test_path.relative_to(root)).replace("\\", "/")
        print(f"  mapping: {rel} -> {test_path_str} (--cov={cov_target})")

        child_env = os.environ.copy()
        child_env["_COVERAGE_SUBPROCESS"] = "1"
        child_env["COVERAGE_CORE"] = COVERAGE_CORE

        with subprocess_module.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                test_path_str,
                "-m",
                "not integration",
                f"--cov={cov_target}",
                "--cov-report=json:.coverage.json",
                "-q",
                "--no-header",
            ],
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
            env=child_env,
            start_new_session=True,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=COVERAGE_TIMEOUT_SECONDS)
            except subprocess_module.TimeoutExpired:
                out, err = _process_group.kill_and_reap(proc)
                print(
                    f"  [diagnostic] {rel}: coverage subprocess killed at {COVERAGE_TIMEOUT_SECONDS}s; "
                    f"output tail={(out + err)[-500:]!r}"
                )
                errors.append(f"{rel}: coverage check timed out ({COVERAGE_TIMEOUT_SECONDS}s)")
                pcts[key] = None
                continue
            except BaseException:
                _process_group.kill_and_reap(proc)
                raise
            returncode = proc.returncode

        coverage_json = root / ".coverage.json"
        if not coverage_json.exists():
            print(f"  [info] no coverage data for {rel} (coverage.py unavailable or file not tracked)")
            pcts[key] = None
            continue

        try:
            data = json.loads(coverage_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pcts[key] = None
            continue
        finally:
            try:
                coverage_json.unlink()
            except OSError:
                pass

        rel_str = str(rel).replace("\\", "/")
        matched: dict[str, float] = {}
        missing_by_key: dict[str, list] = {}
        for file_key, file_data in data.get("files", {}).items():
            normalised_key = file_key.replace("\\", "/")
            if rel_str in normalised_key or normalised_key.endswith(rel_str):
                matched[file_key] = file_data.get("summary", {}).get("percent_covered", 0.0)
                missing_by_key[file_key] = file_data.get("missing_lines", [])

        if not matched:
            errors.append(f"{rel}: 0% coverage (no tests exercise this file)")
            pcts[key] = None
            continue

        worst_key, pct = min(matched.items(), key=lambda kv: kv[1])
        pcts[key] = pct

        # "sub-threshold" is decided by the load_baseline/compare pair, never by "has missing
        # lines" -- config/coverage_baseline.yaml carries live sub-100 entries that legitimately
        # PASS, so gating on missing-lines presence would newly redden every one of them. Printed
        # (never appended to `errors`) -- scripts/test_coverage_checker.py:443 unpacks the 2-tuple
        # and its own check_per_file_coverage() separately appends its own baseline-aware message
        # to this same `errors` list; that file is OUT of scope (Decision 59), so a second entry
        # here would silently break its exact-match assertions instead of adding real value.
        if not compare(pct, rel_str, baseline_table):
            tail = (stdout + stderr)[-500:]
            print(
                f"  [diagnostic] {rel}: {pct:.1f}% line coverage below threshold -- missing lines "
                f"{missing_by_key[worst_key]}; coverage subprocess exit {returncode}; output tail={tail!r}"
            )

    return pcts, errors


_SPEC = _marker_guard.RegistrySpec(
    rel_path=_BASELINE_REL_PATH,
    token="baseline-lowered",
    gated_direction="down",
    extractor=_marker_guard.make_flat_extractor("baseline-lowered", value_type=float),
    gates_new_entry=lambda value: value < 100.0,
    label="Coverage baseline tamper guard",
)


@registry.register("validate_coverage_baseline_edits", owner="platform")
def validate_coverage_baseline_edits(
    failed: list[str],
    base_reader: Optional[Callable[[str], Optional[str]]] = None,
) -> None:
    """Fail on an unauthorized config/coverage_baseline.yaml lowering, new sub-100 entry, or a
    currently-committed marker that no longer authorizes its entry."""
    print(f"\n=== {_SPEC.label} ===")
    violations = _marker_guard.check_diff(_SPEC, base_reader=base_reader) + _marker_guard.check_present_markers(_SPEC)

    if violations:
        print("Coverage baseline tamper-guard violations:")
        for v in violations:
            print(f"  - {v}")
        failed.append(_SPEC.label)
    else:
        print("No unauthorized coverage baseline lowering or new sub-100 registration.")

    current_path = _common.ROOT / _SPEC.rel_path
    entries_examined = len(_SPEC.extractor(current_path.read_text(encoding="utf-8"))) if current_path.exists() else 0
    registry.examined(entries_examined, unit="baseline_entries")
