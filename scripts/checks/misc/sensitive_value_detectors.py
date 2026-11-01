"""Corpus ratchet and loosening gate for the shared sensitive-value detector library (rec-4481 plan 1, Decision 235 cl.1).

validate_detector_ratchet: the registry (config/sensitive_value_detectors.yaml) holds one entry per profile and
positive corpus case, keyed "<profile>.<CLASS>.<case_id>@<8 hex of sha256(text NUL planted_value)>" with value 1 (the
case is masked and attributed to its own class) or 0 (a tombstone: a case or class retired by a marked drop). The key
grammar has no "/", so the shared marker mechanism's ancestor expansion yields no profile or class prefix that could
authorize a whole class: a Decision authorizes one case (Decision 187 cl.1, the tier-demotion-approved precedent).
Legs: (a) every live key is registered and every other registered key is 0; (b) each live key's measured value equals
the registered one; (c) no NEGATIVES text changes; (d) every PROTECTED path is code-owned in .github/CODEOWNERS and
matches a Decision 117 boundary_patterns entry; (e) derived at the base ref, no narrowing of PROTECTED or of the
CODEOWNERS lines and boundary entries covering it.

validate_detector_registry_edits: a 1-to-0 transition or the deletion of a value-1 entry needs a fresh
`# detector-loosening-approved: dec-NNN <reason>` marker whose Decision names the exact case key. Adding a case or a
0-to-1 raise is always free.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Callable

import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.ci_guards.validate_schedule_removal_markers import _batched_base_reader
from scripts.checks.ci_guards.validate_trusted_plan_environment import _owned
from scripts.checks.misc import sensitive_value_corpus as corpus
from src.sensitive_values import PROFILES, mask

TOKEN = "detector-loosening-approved"
REGISTRY_REL = "config/sensitive_value_detectors.yaml"
CHECK_REL = "scripts/checks/misc/sensitive_value_detectors.py"
CODEOWNERS_REL = ".github/CODEOWNERS"
CAPABILITIES_REL = "config/agent/executor/capabilities.yaml"

PROTECTED = (
    "src/sensitive_values/",
    "src/turn_capture/scrub.py",
    "config/sensitive_value_detectors.yaml",
    "scripts/checks/misc/sensitive_value_*.py",
    "tests/sensitive_values/",
    "tests/checks/misc/test_sensitive_value_*.py",
    "src/lambdas/ducklake_telemetry_content_reader/",
    "src/common/ducklake_content_read_verbs.py",
    "docs/contracts/ducklake_telemetry_content_reader.yaml",
    "tests/lambdas/ducklake_telemetry_content_reader/",
)


def case_key(profile: str, case: corpus.Case) -> str:
    digest = hashlib.sha256(f"{case.text}\x00{case.planted_value}".encode("utf-8")).hexdigest()[:8]
    return f"{profile}.{case.class_id}.{case.case_id}@{digest}"


def measure(profile: str, case: corpus.Case) -> int:
    planted, class_id = case.planted_value or "", case.class_id or ""
    result = mask(case.text, profile)
    return int(planted not in result.text and result.counts.get(class_id, 0) >= 1)


def live_keys() -> dict[str, int]:
    return {
        case_key(profile, case): measure(profile, case)
        for profile in sorted(PROFILES)
        for case in corpus.positives()
        if case.class_id in PROFILES[profile]
    }


def registry_violations(registered: dict[str, int], measured: dict[str, int]) -> list[str]:
    out = [f"{key}: live corpus case is not registered" for key in sorted(set(measured) - set(registered))]
    out += [
        f"{key}: registered value {registered[key]} is not 0 for a case no longer in the corpus"
        for key in sorted(registered)
        if key not in measured and registered[key] != 0
    ]
    out += [
        f"{key}: measured {measured[key]} but registered {registered[key]}"
        for key in sorted(set(measured) & set(registered))
        if measured[key] != registered[key]
    ]
    return out


def negative_violations() -> list[str]:
    return [
        f"{profile}: NEGATIVES case {case.case_id} is changed by masking"
        for profile in sorted(PROFILES)
        for case in corpus.negatives()
        if mask(case.text, profile).text != case.text
    ]


def sample_path(spec: str) -> str:
    return spec.replace("*", "x") + ("x.py" if spec.endswith("/") else "")


def ownership_violations(protected: tuple[str, ...], codeowners: str, boundary: list[str]) -> list[str]:
    out = []
    for spec in protected:
        sample = sample_path(spec)
        if not _owned(sample, codeowners):
            out.append(f"{spec}: no CODEOWNERS line owns it")
        if not any(pattern in sample for pattern in boundary):
            out.append(f"{spec}: no boundary_patterns entry covers it")
    return out


def base_protected(base_source: str) -> tuple[str, ...] | None:
    for node in ast.parse(base_source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROTECTED" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    return None


def _codeowners_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def narrowing_violations(
    base_source: str,
    base_codeowners: str,
    base_boundary: list[str],
    protected: tuple[str, ...],
    codeowners: str,
    boundary: list[str],
) -> list[str]:
    if not base_source:
        return []
    old = base_protected(base_source)
    if old is None:
        return []
    out = [f"{spec}: dropped from PROTECTED" for spec in old if spec not in protected]
    head_lines = set(_codeowners_lines(codeowners))
    for line in _codeowners_lines(base_codeowners):
        covers = any(_owned(sample_path(spec), line) for spec in old)
        if covers and line not in head_lines:
            out.append(f"CODEOWNERS line removed: {line}")
    for pattern in base_boundary:
        if any(pattern in sample_path(spec) for spec in old) and pattern not in boundary:
            out.append(f"boundary_patterns entry removed: {pattern}")
    return out


def _boundary(text: str) -> list[str]:
    data = yaml.safe_load(text) if text else {}
    return list(data.get("boundary_patterns") or []) if isinstance(data, dict) else []


def _load_registry(text: str) -> dict[str, int] | None:
    data = yaml.safe_load(text)
    if isinstance(data, dict) and all(isinstance(v, int) for v in data.values()):
        return {str(k): v for k, v in data.items()}
    return None


def _weakened(base: object, head: object) -> bool:
    return isinstance(base, int) and isinstance(head, int) and head < base


def _gates_deletion(base: object) -> bool:
    return isinstance(base, int) and base > 0


def registry_spec() -> _marker_guard.RegistrySpec:
    extractor = _marker_guard.make_flat_extractor(TOKEN, int, require_reason=True)

    def state_extractor(text: str) -> dict[str, _marker_guard.StateEntry]:
        return {k: _marker_guard.StateEntry(e.value, e.marker, e.reason) for k, e in extractor(text).items()}

    return _marker_guard.RegistrySpec(
        rel_path=REGISTRY_REL,
        token=TOKEN,
        gated_direction="down",
        extractor=extractor,
        gates_new_entry=lambda _value: False,
        label="Detector registry loosening gate",
        mention_candidates=lambda key: [key],
        reason_required=True,
        state_extractor=state_extractor,
        weakened=_weakened,
        gates_deletion=_gates_deletion,
    )


def _base_reader(root: Path, rel_paths: list[str]) -> Callable[[str], str] | None:
    if not _common.origin_main_reachable(root):
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI).")
        return None
    reader = _batched_base_reader(root, rel_paths)
    if reader is None:
        print("  SKIP: `git cat-file --batch` failed -- no base blob was read (advisory locally, authoritative in CI).")
    return reader


@registry.register("validate_detector_ratchet", owner="platform")
def validate_detector_ratchet(failed: list[str], root: Path | None = None) -> None:
    """Fail when a detector case slips through, a negative is touched, protection is missing or narrowed."""
    print("\n=== Sensitive-value detector ratchet ===")
    target = root if root is not None else _common.ROOT
    violations: list[str] = []
    try:
        registered = _load_registry((target / REGISTRY_REL).read_text(encoding="utf-8"))
        codeowners = (target / CODEOWNERS_REL).read_text(encoding="utf-8")
        boundary = _boundary((target / CAPABILITIES_REL).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        violations.append(f"inputs unreadable: {exc}")
        registered, codeowners, boundary = {}, "", []
    if registered is None:
        violations.append(f"{REGISTRY_REL}: not a flat mapping of integer values")
        registered = {}
    measured = live_keys()
    violations += registry_violations(registered, measured)
    violations += negative_violations()
    violations += ownership_violations(PROTECTED, codeowners, boundary)

    leg_e = "ran"
    paths = [CHECK_REL, CODEOWNERS_REL, CAPABILITIES_REL]
    reader = _base_reader(target, paths)
    if reader is None:
        leg_e = "skipped"
    else:
        violations += narrowing_violations(
            reader(CHECK_REL), reader(CODEOWNERS_REL), _boundary(reader(CAPABILITIES_REL)), PROTECTED, codeowners, boundary
        )
    for v in violations:
        print(f"  - {v}")
    if violations:
        failed.append("Sensitive-value detector ratchet")
    else:
        print(f"  PASS: {len(measured)} cases, {len(PROTECTED)} protected paths (base-ref leg {leg_e}).")
    registry.examined(len(measured) + len(PROTECTED), unit=f"keys_and_paths (base-ref leg {leg_e})")


@registry.register("validate_detector_registry_edits", owner="platform")
def validate_detector_registry_edits(failed: list[str], root: Path | None = None) -> None:
    """Fail on an unauthorized 1-to-0 transition or the deletion of a value-1 registry entry."""
    print("\n=== Sensitive-value detector registry edits ===")
    target = root if root is not None else _common.ROOT
    reader = _base_reader(target, [REGISTRY_REL])
    if reader is None:
        registry.skipped("origin/main unreachable or base read failed")
        return
    spec = registry_spec()
    violations = _marker_guard.check_state_diff(spec, base_reader=reader) + _marker_guard.check_present_markers(spec)
    for v in violations:
        print(f"  - {v}")
    if violations:
        failed.append("Detector registry loosening gate")
    else:
        print("  PASS: no unauthorized loosening.")
    path = target / REGISTRY_REL
    count = len(spec.extractor(path.read_text(encoding="utf-8"))) if path.exists() else 0
    registry.examined(count, unit="registry_entries")
