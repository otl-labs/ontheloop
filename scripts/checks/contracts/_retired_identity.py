"""Row evaluators for rec-3903's static rows of docs/contracts/github-org-transfer.yaml (invariant (2)).

validate_github_org_transfer merges ROW_EVALUATORS. Outside historical records and the locations of static rows
owned by other recs, every tracked file naming the retired identity (the old full name, a quoted old owner, or a
quoted old repository name bound to a repository identifier) is pinned: to one of ROWS in SWEEP_PINS (the
post-transfer sweep's scope, PLAN-retired-repo-identity-sweep) or as a named permanent survivor in SURVIVOR_PINS.
A pin with no match fails as stale. From pre_staged on, the dependabot gate accepts the target full name; at
predecessor_removed no pin cites those rows. The phase rules read identity.phase, never staged_phase.

SWEEP_PINS and SURVIVOR_PINS stay literal str -> str dicts: the sweep plan parses SWEEP_PINS with ast.literal_eval.
Evaluators only append to `failed`; the parent declares registry.examined()/skipped() (Decision 170).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LABEL = "Org transfer contract (rec-3903)"
_REC = "rec-3903"
ROWS = ("dependabot_repository_guard", "owner_literal_defaults", "codeowners", "descriptive_literals")
_PATTERNS = (
    re.compile(r"(?<![\w.-])benjamin-blake/theseus(?![\w-])"),
    re.compile(r"[\"']benjamin-blake[\"']"),
    re.compile(r"\b\w*(?:repo|REPO)\w*\s*[:=][^\n]{0,30}?[\"']theseus[\"']"),
)
_EXEMPT_PREFIXES = ("docs/plans/", "audits/", "docs/audit-prompts/", "tests/fixtures/")
_EXEMPT_FILES = frozenset(
    {
        "docs/DECISIONS.md",
        "docs/DECISIONS_ARCHIVE.md",
        "docs/SESSION_LOG_ARCHIVE.md",
        "docs/decisions-index.json",
    }
)
_DEPENDABOT_WORKFLOW = ".github/workflows/dependabot-auto-merge.yml"
_TARGET_GATE_PHASES = frozenset({"pre_staged", "transferred", "proven", "predecessor_removed"})

SWEEP_PINS: dict[str, str] = {
    ".github/workflows/dependabot-auto-merge.yml": "dependabot_repository_guard",
    ".github/workflows/ghas-probe.yml": "descriptive_literals",
    "COMMERCIAL-LICENSE.md": "descriptive_literals",
    "config/agent/verification_registry/entries/licensed-work-mark-and-historical-split.yaml": "descriptive_literals",
    "config/agent/verification_registry/entries/roadmap-retired-brand-tokens-absent.yaml": "descriptive_literals",
    "docs/ROADMAP-PLATFORM.yaml": "descriptive_literals",
    "docs/contracts/package-registry-reservation.yaml": "descriptive_literals",
    "packages/ontheloop-js-scoped/package.json": "descriptive_literals",
    "packages/ontheloop-js/package.json": "descriptive_literals",
    "packages/ontheloop-py/pyproject.toml": "descriptive_literals",
    "packages/ontheloop-py/src/ontheloop/__init__.py": "descriptive_literals",
    "packages/ontheloop-rs/Cargo.toml": "descriptive_literals",
    "packages/ontheloop-rs/src/lib.rs": "descriptive_literals",
    "scripts/checks/hygiene/validate_licence_consistency.py": "descriptive_literals",
    "scripts/convergence_health/escalate.py": "owner_literal_defaults",
    "scripts/repo_identity.py": "owner_literal_defaults",
    "terraform/github/CLAUDE.md": "descriptive_literals",
    "tests/checks/ci_guards/test_validate_dependabot_automation.py": "dependabot_repository_guard",
    "scripts/telemetry_verify/completeness.py": "owner_literal_defaults",
    "tests/checks/misc/test_validate_ghas_probe.py": "owner_literal_defaults",
    "tests/telemetry_verify/test_completeness.py": "owner_literal_defaults",
    "tests/test_ontheloop_placeholder_packages.py": "descriptive_literals",
    "tests/test_repo_identity.py": "owner_literal_defaults",
}

SURVIVOR_PINS: dict[str, str] = {
    "docs/contracts/github-org-transfer.yaml": "the identity record: names the retired path it moves away from",
    "scripts/checks/contracts/_retired_identity.py": "this guard names the retired identity to detect it",
    "tests/checks/contracts/test__retired_identity.py": "fixtures for this guard's patterns",
    "tests/checks/hygiene/test_validate_licence_consistency.py": "later-slug fixtures of the licence check",
    "tests/convergence_health/budget_ingest/conftest.py": "synthetic repository field of a captured manifest",
    "tests/convergence_health/budget_ingest/test_collect.py": "synthetic repository field of a captured manifest",
    "tests/session/preflight/test_main_branches.py": "captured run URL in an alert fixture",
    "tests/test_convergence_classify.py": "captured run URL",
}


def _row_rec_ok(contract: dict, row_id: str, failed: list[str]) -> None:
    rows = {r.get("id"): r for r in contract.get("breakage_map") or [] if isinstance(r, dict)}
    if rows.get(row_id, {}).get("rec") != _REC:
        failed.append(f"{_LABEL}: breakage_map row {row_id!r} must exist with rec {_REC}")


def _phase(contract: dict) -> Any:
    identity = contract.get("identity")
    return identity.get("phase") if isinstance(identity, dict) else None


def _no_pins_at_predecessor_removed(row_id: str, contract: dict, failed: list[str]) -> None:
    cited = sorted(path for path, row in SWEEP_PINS.items() if row == row_id)
    if cited and _phase(contract) == "predecessor_removed":
        failed.append(f"{_LABEL}: at predecessor_removed no sweep pin may cite {row_id!r}: {cited}")


def _exempt(contract: dict) -> tuple[frozenset[str], tuple[str, ...]]:
    files, prefixes = set(_EXEMPT_FILES), list(_EXEMPT_PREFIXES)
    for row in contract.get("breakage_map") or []:
        if not isinstance(row, dict) or row.get("detection") != "static" or row.get("rec") == _REC:
            continue
        for location in row.get("locations") or []:
            text = str(location)
            if text.split() != [text] or ("/" not in text and "." not in text):
                continue
            if text.endswith("/"):
                prefixes.append(text)
            else:
                files.add(text)
    return frozenset(files), tuple(prefixes)


def _scan(repo_root: Path, failed: list[str]) -> dict[str, int] | None:
    """First matching line per tracked file, or None after a fail-closed git failure."""
    command = ["git", "grep", "-n", "-I", "-F", "-z", "-e", "benjamin-blake", "-e", "theseus"]
    try:
        result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        failed.append(f"{_LABEL}: git grep could not run: {exc}")
        return None
    if result.returncode not in (0, 1):
        failed.append(f"{_LABEL}: git grep failed (exit {result.returncode}); the retired-identity scan fails closed")
        return None
    hits: dict[str, int] = {}
    for record in result.stdout.split("\n"):
        parts = record.split("\0", 2)
        if len(parts) == 3 and parts[0] not in hits and any(p.search(parts[2]) for p in _PATTERNS):
            hits[parts[0]] = int(parts[1])
    return hits


def _evaluate_descriptive_literals(contract: dict, repo_root: Path, failed: list[str]) -> None:
    _row_rec_ok(contract, "descriptive_literals", failed)
    _no_pins_at_predecessor_removed("descriptive_literals", contract, failed)
    for path, row in sorted(SWEEP_PINS.items()):
        if row not in ROWS:
            failed.append(f"{_LABEL}: sweep pin {path} cites {row!r}, which is not one of rec-3903's rows {list(ROWS)}")
    hits = _scan(repo_root, failed)
    if hits is None:
        return
    files, prefixes = _exempt(contract)
    for path, line in sorted(hits.items()):
        if path in files or path.startswith(prefixes) or path in SWEEP_PINS or path in SURVIVOR_PINS:
            continue
        failed.append(f"{_LABEL}: {path}:{line} names the retired identity but is in neither SWEEP_PINS nor SURVIVOR_PINS")
    for kind, pins in (("sweep", SWEEP_PINS), ("survivor", SURVIVOR_PINS)):
        for path in sorted(pins):
            if path in files or path.startswith(prefixes):
                failed.append(f"{_LABEL}: {kind} pin {path} is on an exempt path; remove the pin")
            elif path not in hits:
                failed.append(f"{_LABEL}: stale {kind} pin {path} no longer names the retired identity; remove the pin")


def _evaluate_dependabot_repository_guard(contract: dict, repo_root: Path, failed: list[str]) -> None:
    _row_rec_ok(contract, "dependabot_repository_guard", failed)
    _no_pins_at_predecessor_removed("dependabot_repository_guard", contract, failed)
    if _phase(contract) not in _TARGET_GATE_PHASES:
        return
    target = (contract.get("identity") or {}).get("target") or {}
    expected = f"github.repository == '{target.get('owner')}/{target.get('repository')}'"
    try:
        import yaml

        workflow = yaml.safe_load((repo_root / _DEPENDABOT_WORKFLOW).read_text(encoding="utf-8"))
        gate = str(workflow["jobs"]["auto-merge"]["if"])
    except Exception as exc:  # noqa: BLE001 -- never raise at check time; fail closed instead
        failed.append(f"{_LABEL}: {_DEPENDABOT_WORKFLOW} auto-merge gate unreadable: {type(exc).__name__}")
        return
    if expected not in gate:
        failed.append(f"{_LABEL}: from pre_staged on the dependabot auto-merge gate must contain {expected}")


def _evaluate_owner_literal_defaults(contract: dict, repo_root: Path, failed: list[str]) -> None:
    _row_rec_ok(contract, "owner_literal_defaults", failed)
    _no_pins_at_predecessor_removed("owner_literal_defaults", contract, failed)


def _evaluate_codeowners(contract: dict, repo_root: Path, failed: list[str]) -> None:
    _row_rec_ok(contract, "codeowners", failed)
    cited = sorted(path for path, row in SWEEP_PINS.items() if row == "codeowners")
    if cited:
        failed.append(f"{_LABEL}: codeowners carries no repository literal (the person is retained); unpin {cited}")


ROW_EVALUATORS: dict[str, Callable[[dict, Path, list[str]], None]] = {
    "dependabot_repository_guard": _evaluate_dependabot_repository_guard,
    "owner_literal_defaults": _evaluate_owner_literal_defaults,
    "codeowners": _evaluate_codeowners,
    "descriptive_literals": _evaluate_descriptive_literals,
}
