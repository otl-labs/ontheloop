"""Read-only plan scope registration-closure engine plus its advisory CLI (plan-obligation-closure).

Derives which of docs/contracts/plan-obligations.yaml's `registration_surfaces` obligations a
plan's `scope` rows have or have not satisfied. Holds no second copy of the obligation map --
every rule (trigger pattern, required companion paths) is read from the contract at check time,
never hardcoded here.

Reads plans as PLAIN YAML (schema_version/plan_type/scope only), not through the strict
scripts.roadmap.plan_document.PlanDocument loader: registration closure is structurally derivable
from those three fields alone, and validate_plan_documents already separately owns full-schema
validity. This also lets the engine evaluate a minimal in-memory fixture dict (the graduated VP
steps in this plan build fixtures from inline dicts, not full PlanDocument-valid ones) without
tripping on unrelated required fields.

Exception-contained throughout: scripts/checks/validation_result.py calls a check's function with
no try/except, so an unhandled raise here would abort the whole --pre/full tier and skip
write_completed_visible, destroying CI-RCA's attribution substrate. A missing/malformed contract
or an unreadable/malformed plan yields a reported finding string, never an exception.

The sole registered check (scripts/checks/roadmap/validate_plan_scope_closure.py) is a thin
delegate over this module's `evaluate_plan`/`net_new_v4_implementation_plan_paths`. This module
also exposes an advisory CLI: `bin/venv-python -m scripts.roadmap.plan_obligations --plan <path>`
always exits 0, including when the plan has unmet obligations -- the registered check is the
gate, this CLI is a report.

Derived findings (companions and headroom computed from repo state, rows in the same contract's
`derived_findings`) live in scripts/roadmap/plan_lint: evaluate_plan appends its gating lines,
build_report prints its advisory ones.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import yaml

from scripts.checks import _common
from scripts.roadmap import plan_lint

ROOT = _common.ROOT
_CONTRACT_PATH = ROOT / "docs" / "contracts" / "plan-obligations.yaml"

# The schema_version floor a plan must meet before this check evaluates it at all -- mirrors
# validate_plan_documents' own _MIN_NEW_PLAN_SCHEMA_VERSION grandfathering precedent. 65 of 329
# existing plans create a check while omitting an obligated row (7 of them at v4); without this
# floor plus the net-new-in-diff gate below, every one of them would red on first touch.
MIN_SCHEMA_VERSION = 4
_IMPLEMENTATION = "IMPLEMENTATION"


def load_obligation_map(contract_path: Path | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Read the contract's `registration_surfaces` mapping. Returns (rules, error) -- never
    raises; a missing file, unparseable YAML, or malformed shape yields ([], "<reason>")."""
    path = contract_path if contract_path is not None else _CONTRACT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"could not read {path}: {exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], f"could not parse {path}: {exc}"
    if not isinstance(data, dict):
        return [], f"{path} is not a YAML mapping"
    surfaces = data.get("registration_surfaces")
    if not isinstance(surfaces, dict) or not surfaces:
        return [], f"{path} is missing a non-empty 'registration_surfaces' mapping"
    return [rule for rule in surfaces.values() if isinstance(rule, dict)], None


def _read_plan_dict(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Plain YAML read of a plan file -- never raises. Returns (data, error)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {path}: {exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"could not parse {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} is not a YAML mapping"
    return data, None


def _scope_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    scope = data.get("scope")
    if not isinstance(scope, list):
        return []
    rows: list[dict[str, str]] = []
    for row in scope:
        if isinstance(row, dict) and isinstance(row.get("file"), str) and isinstance(row.get("action"), str):
            rows.append(row)
    return rows


def _is_grandfathered(data: dict[str, Any]) -> bool:
    schema_version = data.get("schema_version")
    meets_floor = isinstance(schema_version, int) and schema_version >= MIN_SCHEMA_VERSION
    return not (meets_floor and data.get("plan_type") == _IMPLEMENTATION)


def _derive_findings(scope: list[dict[str, str]], rules: list[dict[str, Any]]) -> list[str]:
    """Pure derivation: which rule-declared companion paths are absent from `scope`.

    Each rule names a `trigger.file_pattern` (matched against a scope row's `file`, optionally
    gated by `trigger.action`) and a `requires` list of `path_template` strings formatted with
    the trigger's regex capture groups. A required path missing from the scope's file set is one
    finding, naming the missing path and the triggering row. `rule.reason` (falling back to "a
    new check module" for a rule that omits it) is formatted into the finding message so a
    non-check-module trigger (e.g. new_workflow_file) reads correctly -- purely a message
    parameter over rule-structural fields, never file-content inspection.
    """
    scope_files = {row["file"] for row in scope}
    findings: list[str] = []
    for rule in rules:
        trigger = rule.get("trigger")
        if not isinstance(trigger, dict):
            continue
        pattern = trigger.get("file_pattern")
        required_action = trigger.get("action")
        if not isinstance(pattern, str) or not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        requirements = rule.get("requires")
        if not isinstance(requirements, list):
            continue
        reason = rule.get("reason")
        if not isinstance(reason, str) or not reason:
            reason = "a new check module"
        for row in scope:
            if required_action and row.get("action") != required_action:
                continue
            match = compiled.match(row["file"])
            if not match:
                continue
            groups = match.groupdict()
            for requirement in requirements:
                if not isinstance(requirement, dict):
                    continue
                template = requirement.get("path_template")
                if not isinstance(template, str) or not template:
                    continue
                try:
                    required_path = template.format(**groups)
                except (KeyError, IndexError):
                    continue
                if required_path not in scope_files:
                    label = requirement.get("label", required_path)
                    findings.append(f"missing {label} ({required_path}) -- required because {row['file']} is {reason}")
    return findings


def _iter_enforced_elsewhere_entries(
    rules: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any], Any]]:
    """Yield (rule_name, rule, raw_entry) for every rule's `enforced_elsewhere` list member,
    whatever its shape -- callers validate/format the entry, this only walks the contract."""
    for rule_name, rule in rules.items():
        if not isinstance(rule, dict):
            continue
        entries = rule.get("enforced_elsewhere")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            yield rule_name, rule, entry


def _requires_grammar_findings(rule_name: str, rule: dict[str, Any]) -> list[str]:
    """G1 (requires side): every `requires` entry carries a non-empty `path_template`."""
    findings: list[str] = []
    requirements = rule.get("requires")
    if not isinstance(requirements, list):
        return findings
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        label = requirement.get("label", "<unlabeled>")
        if not requirement.get("path_template"):
            findings.append(
                f"{rule_name}: requires entry {label!r} carries no path_template "
                "(G1: a requires row must name a derivable companion path)"
            )
    return findings


def _enforced_by_grammar_findings(
    rule_name: str, label: str, enforced_by: Any, all_checks: Any, sequenced_names: set[str]
) -> list[str]:
    """G2/G3: `enforced_by` must resolve in registry.all_checks() AND be dispatched."""
    if not isinstance(enforced_by, str) or not enforced_by:
        return [
            (
                f"{rule_name}: enforced_elsewhere entry {label!r} names no enforced_by "
                "(G2: enforced_by must resolve in registry.all_checks())"
            )
        ]
    if enforced_by not in all_checks:
        return [
            (
                f"{rule_name}: enforced_elsewhere entry {label!r} names enforced_by={enforced_by!r}, "
                "which is not a registered check (G2: enforced_by must resolve in registry.all_checks())"
            )
        ]
    if enforced_by not in sequenced_names:
        return [
            (
                f"{rule_name}: enforced_elsewhere entry {label!r} names enforced_by={enforced_by!r}, "
                "which is registered but dispatched in neither pre_sequence() nor full_sequence() "
                "(G3: an enforcer that never runs can never fail anything)"
            )
        ]
    return []


def _enforced_elsewhere_entry_findings(
    rule_name: str, trigger_action: Any, entry: Any, all_checks: Any, sequenced_names: set[str]
) -> list[str]:
    """G1 (enforced_elsewhere side) + G2 + G3 + G4 + G5 for one enforced_elsewhere entry."""
    if not isinstance(entry, dict):
        return [f"{rule_name}: enforced_elsewhere entry is not a mapping"]
    label = entry.get("label", "<unlabeled>")
    findings: list[str] = []

    # G1: an enforced_elsewhere entry must carry no path_template.
    if entry.get("path_template"):
        findings.append(
            f"{rule_name}: enforced_elsewhere entry {label!r} carries a path_template "
            "(G1: a derivable companion path makes it a requires row, not enforced_elsewhere)"
        )

    findings.extend(_enforced_by_grammar_findings(rule_name, label, entry.get("enforced_by"), all_checks, sequenced_names))

    # G4: why_not_a_scope_row present and non-empty.
    why = entry.get("why_not_a_scope_row")
    if not isinstance(why, str) or not why.strip():
        findings.append(f"{rule_name}: enforced_elsewhere entry {label!r} carries no non-empty why_not_a_scope_row (G4)")

    # G5: declared fires_on must contain the enclosing rule's trigger.action -- a
    # declaration-consistency guard, not a reachability proof (see validate_obligation_grammar's
    # own docstring): it only checks what the author states, not whether an enforcer satisfying
    # G2/G3 can actually fire for this trigger's action.
    fires_on = entry.get("fires_on")
    if not isinstance(fires_on, list) or trigger_action not in fires_on:
        findings.append(
            f"{rule_name}: enforced_elsewhere entry {label!r} declares fires_on={fires_on!r}, "
            f"which omits the enclosing rule's trigger.action={trigger_action!r} "
            "(G5: a Modify-only enforcer is not admissible under a Create-only rule)"
        )

    return findings


def validate_obligation_grammar(contract_path: Path | None = None) -> list[str]:
    """Validate docs/contracts/plan-obligations.yaml's own grammar -- never raises; a malformed
    contract yields findings, never an exception.

    Two shapes are checked (delegated to helpers to keep this function's own branch count low):
      - every `requires` entry carries a non-empty `path_template` (it names a derivable
        companion path) -- `_requires_grammar_findings`;
      - every `enforced_elsewhere` entry is admissible only when all FIVE guards hold: G1 it
        carries NO `path_template` (a derivable companion path makes it a requires row instead);
        G2 `enforced_by` names a check that resolves in `registry.all_checks()`; G3 `enforced_by`
        is actually dispatched (present in `registry.pre_sequence()` or `registry.full_sequence()`
        -- an enforcer that never runs can never fail anything, so "enforced elsewhere" would be a
        false claim); G4 `why_not_a_scope_row` is present and non-empty; G5 the entry's declared
        `fires_on` list contains the enclosing rule's `trigger.action` -- a DECLARATION-CONSISTENCY
        guard (not a reachability proof) -- `_enforced_elsewhere_entry_findings`.

    `registry` is imported here, at CHECK-BODY time, never at this module's own import time:
    every check module does `from scripts.checks import registry`, so an import-time call from
    this module (imported by scripts.checks.roadmap.validate_plan_scope_closure) would be a
    genuine circular import.
    """
    from scripts.checks import registry  # noqa: PLC0415

    path = contract_path if contract_path is not None else _CONTRACT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"could not read {path}: {exc}"]
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"could not parse {path}: {exc}"]
    if not isinstance(data, dict):
        return [f"{path} is not a YAML mapping"]
    surfaces = data.get("registration_surfaces")
    if not isinstance(surfaces, dict) or not surfaces:
        return [f"{path} is missing a non-empty 'registration_surfaces' mapping"]

    findings: list[str] = []
    for rule_name, rule in surfaces.items():
        if not isinstance(rule, dict):
            findings.append(f"{rule_name}: rule is not a mapping")
            continue
        findings.extend(_requires_grammar_findings(rule_name, rule))

    all_checks = registry.all_checks()
    sequenced_names = {step.name for step in registry.pre_sequence()} | {step.name for step in registry.full_sequence()}

    for rule_name, rule, entry in _iter_enforced_elsewhere_entries(surfaces):
        trigger = rule.get("trigger")
        trigger_action = trigger.get("action") if isinstance(trigger, dict) else None
        findings.extend(_enforced_elsewhere_entry_findings(rule_name, trigger_action, entry, all_checks, sequenced_names))

    return findings


def _derive_enforced_elsewhere_pointers(scope: list[dict[str, str]], rules: dict[str, Any]) -> list[str]:
    """Pure derivation: for every scope row whose trigger fires, name the rule's declared
    `enforced_elsewhere` pointers -- report-only, NEVER a `failed` finding (the obligation is file
    content inside the triggering row's own file, so no scope row could ever satisfy it)."""
    pointers: list[str] = []
    for rule_name, rule in rules.items():
        if not isinstance(rule, dict):
            continue
        trigger = rule.get("trigger")
        if not isinstance(trigger, dict):
            continue
        pattern = trigger.get("file_pattern")
        required_action = trigger.get("action")
        if not isinstance(pattern, str) or not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        entries = rule.get("enforced_elsewhere")
        if not isinstance(entries, list) or not entries:
            continue
        for row in scope:
            if required_action and row.get("action") != required_action:
                continue
            if not compiled.match(row["file"]):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                label = entry.get("label", "<unlabeled>")
                enforced_by = entry.get("enforced_by", "<unknown>")
                pointers.append(
                    f"{row['file']} ({rule_name}) obligates {label!r}, enforced elsewhere by "
                    f"{enforced_by} -- not a scope-row requirement, see docs/contracts/plan-obligations.yaml"
                )
    return pointers


def evaluate_plan(path: Path, rules: list[dict[str, Any]] | None = None) -> list[str]:
    """Evaluate one plan file's scope against the obligation map. Never raises.

    Grandfathers (returns []) any plan below MIN_SCHEMA_VERSION or not plan_type IMPLEMENTATION.
    An unreadable/unparseable plan becomes a single finding string rather than propagating.
    """
    data, error = _read_plan_dict(path)
    if error or data is None:
        return [f"{path.name}: {error}"]
    if _is_grandfathered(data):
        return []
    if rules is None:
        rules, rule_error = load_obligation_map()
        if rule_error:
            return [f"{path.name}: {rule_error}"]
    findings = _derive_findings(_scope_rows(data), rules)
    findings.extend(plan_lint.derive(data, ROOT, report=False).gating)
    return [f"{path.name}: {finding}" for finding in findings]


def net_new_v4_implementation_plan_paths() -> list[Path]:
    """Net-new (git status A) docs/plans/PLAN-*.yaml paths, schema_version >= MIN_SCHEMA_VERSION
    and plan_type IMPLEMENTATION -- derived from the status-aware git diff, NEVER a docs/plans/
    glob (validate_plan_documents globs the whole 329-plan corpus at ~6.24s per call; this
    function must stay one hop, not a directory walk).
    """
    paths: list[Path] = []
    for status, rel_path in _common.get_status_aware_diff():
        if status != "A" or not _common.PLAN_PATH_RE.match(rel_path):
            continue
        candidate = ROOT / rel_path
        data, error = _read_plan_dict(candidate)
        if error or data is None or _is_grandfathered(data):
            continue
        paths.append(candidate)
    return paths


def build_report(path: Path) -> str:
    """Advisory, human-readable report for one plan path. Never raises.

    Always emits both sections: the gating `requires` findings (or "no unmet ... obligations"),
    PLUS an `enforced_elsewhere` pointer section whenever a rule's trigger matches a scope row --
    including when the plan has zero unmet `requires` findings, since a closure-complete plan can
    still owe a non-scope-row obligation (e.g. the Decision 170 accounting declaration). The
    pointer section is report-only: it never feeds the registered check's gating verdict.
    """
    findings = evaluate_plan(path)
    if not findings:
        lines = [
            (
                f"{path}: no unmet registration obligations "
                f"(or plan is grandfathered / below schema_version {MIN_SCHEMA_VERSION})."
            )
        ]
    else:
        lines = [f"{path}: {len(findings)} unmet registration obligation(s):"]
        lines.extend(f"  - {finding}" for finding in findings)

    data, error = _read_plan_dict(path)
    if not error and data is not None and not _is_grandfathered(data):
        rules_path = _CONTRACT_PATH
        try:
            contract_text = rules_path.read_text(encoding="utf-8")
            contract_data = yaml.safe_load(contract_text)
        except (OSError, yaml.YAMLError):
            contract_data = None
        surfaces = contract_data.get("registration_surfaces") if isinstance(contract_data, dict) else None
        if isinstance(surfaces, dict) and surfaces:
            pointers = _derive_enforced_elsewhere_pointers(_scope_rows(data), surfaces)
            if pointers:
                lines.append(f"{path}: {len(pointers)} obligation(s) enforced elsewhere (never gating):")
                lines.extend(f"  - {pointer}" for pointer in pointers)

    if not error and data is not None and not _is_grandfathered(data):
        advisory = plan_lint.derive(data, ROOT, report=True).advisory
        if advisory:
            lines.append(f"{path}: derived findings (advisory, never gating):")
            lines.extend(f"  - {line}" for line in advisory)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Advisory CLI. ALWAYS exits 0, including on unmet obligations -- the registered check
    (validate_plan_scope_closure) is the gate this report only informs."""
    parser = argparse.ArgumentParser(description="Plan registration-obligation report (advisory; always exits 0)")
    parser.add_argument("--plan", required=True, help="Path to a docs/plans/PLAN-{slug}.yaml file")
    args = parser.parse_args(argv)
    print(build_report(Path(args.plan)))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
