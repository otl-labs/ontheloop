"""Plan document schema validation (Decision 104)."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.checks import _common, registry

_NON_BEHAVIOR_PREFIXES = ("tests/", "docs/plans/")
_DOCUMENTATION_SUFFIXES = {".md", ".rst", ".txt"}

# Two independently pinned floors (PLAN-vp-expected-literal-carrier split this from one shared
# constant): _MIN_NEW_PLAN_SCHEMA_VERSION is the AUTHORING floor a freshly-authored plan must
# declare -- this is what the canonical template in .claude/skills/planning/SKILL.md is pinned to.
# _TEST_OBLIGATION_MIN_SCHEMA_VERSION gates the per-scope-row test-obligation check below and MUST
# NEVER track the authoring floor: bumping them together would return [] from the obligation gate
# for every plan authored between the two floors (137 v4 plans at the time of this split), silently
# disabling a Decision 181 verifier for the whole population.
_MIN_NEW_PLAN_SCHEMA_VERSION = 5
_TEST_OBLIGATION_MIN_SCHEMA_VERSION = 4


def _added_plan_names() -> set[str]:
    """Filenames of docs/plans/PLAN-*.yaml files ADDED (not modified) in this diff.

    Matching on filename rather than repo-relative path keeps the comparison valid under the
    plans_dir test seam, where the fixture directory is a tmp path; PLAN-*.yaml slugs are
    already globally unique because plan_path must equal docs/plans/PLAN-{slug}.yaml.
    """
    return {
        Path(path).name
        for status, path in _common.get_status_aware_diff()
        if status in ("A", "??") and _common.PLAN_PATH_RE.match(path)
    }


def _new_plan_version_failures(path: Path, doc: Any, added_names: set[str]) -> list[str]:
    """Refuse a brand-new plan authored below the current schema version.

    Historical plans keep the version they were authored at (1-4 all stay schema-valid); the
    gate binds only files new in this diff, which is what makes the v4-gated (see
    _TEST_OBLIGATION_MIN_SCHEMA_VERSION) obligation check actually fire for the plans being
    written from now on.
    """
    if path.name not in added_names or doc.schema_version >= _MIN_NEW_PLAN_SCHEMA_VERSION:
        return []
    return [
        (
            f"{path.name}: newly-added plans must declare schema_version {_MIN_NEW_PLAN_SCHEMA_VERSION} "
            f"(got {doc.schema_version}) -- the test-obligation gate binds at "
            f"v{_TEST_OBLIGATION_MIN_SCHEMA_VERSION} and above"
        )
    ]


def _gate_round_gaps(path: Path, doc: Any, added_names: set[str]) -> list[str]:
    """A newly-added plan must carry its gate record: a scout row, a critique that ended per Step 9, no open escalation.

    The record is only complete once critique ends, and the planner runs --pre between rounds, so the
    gaps fail only on the plan PR (opened after PROCEED) and are advisories elsewhere -- locally, and on
    the push to main, where a plan PR that went green before this check existed must not turn main red.
    """
    if path.name not in added_names or doc.schema_version < _MIN_NEW_PLAN_SCHEMA_VERSION:
        return []
    critique = [row for row in doc.gate_rounds if row.gate == "plan-critique"]
    gaps = []
    if not any(row.gate == "decision-scout" for row in doc.gate_rounds):
        gaps.append(f"{path.name}: gate_rounds has no decision-scout row (planning SKILL.md Decision Scout Gate)")
    if not critique or not _critique_ended(critique):
        gaps.append(
            f"{path.name}: gate_rounds must end with a plan-critique PROCEED that ends Step 9 (no nits, or the confirming "
            "round after a PROCEED listing nits) or an accept-with-deferral answer (planning SKILL.md Step 9)"
        )
    gaps += [
        f"{path.name}: gate_rounds {row.gate} round {row.round} escalated with no escalation_choice (an open escalation)"
        for row in doc.gate_rounds
        if row.escalated and row.escalation_choice is None
    ]
    return gaps


def _critique_ended(critique: list[Any]) -> bool:
    last = critique[-1]
    if last.escalation_choice == "accept-with-deferral":
        return True
    confirming = len(critique) > 1 and critique[-2].verdict == "PROCEED" and critique[-2].nits > 0
    return last.verdict == "PROCEED" and (last.nits == 0 or confirming)


def _changed_sidecar_digest(slug: str, base: str) -> str | None:
    """The approved-text digest of docs/plans/approved/<slug>.yaml when this diff adds or changes it, else None."""
    from scripts.roadmap import approved_text  # noqa: PLC0415

    if approved_text.sidecar_state(slug, _common.ROOT, base) not in ("added", "changed"):
        return None
    return approved_text.digest(approved_text.load_sidecar(slug, _common.ROOT))


def _wording_check_gaps(path: Path, doc: Any, base: str) -> list[str]:
    """A plan whose approved-text sidecar the diff adds or changes needs a fresh PASS wording-check row.

    The row is the planner's recorded rehearsal (scripts.roadmap.plan_wording_check); it is read here and never re-run.
    A build PR never changes the sidecar, so it needs none. Routed like _gate_round_gaps (Decision 163).
    """
    if doc.schema_version < _MIN_NEW_PLAN_SCHEMA_VERSION:
        return []
    digest = _changed_sidecar_digest(doc.slug, base)
    if digest is None:
        return []
    rows = [row for row in doc.gate_rounds if row.gate == "wording-check"]
    if rows and rows[-1].verdict == "PASS" and rows[-1].selection == "complete" and rows[-1].digest == digest:
        return []
    return [
        (
            f"{path.name}: the diff adds or changes docs/plans/approved/{doc.slug}.yaml, so gate_rounds must end its "
            "wording-check rows with a PASS, selection complete, digest equal to the sidecar's (planning SKILL.md Step 8b)"
        )
    ]


def _is_documentation(file: str) -> bool:
    """True for prose that carries no runtime or agent behavior.

    The suffix alone does not settle it: a prompt file is executable instruction text (Layer 5 of
    docs/contracts/instruction-architecture.yaml), so config/agent/executor/prompts/*.prompt.md
    and .github/prompts/**/*.prompt.md change behavior exactly the way a .py file does and never
    inherit the documentation exemption.
    """
    path = PurePosixPath(file)
    if path.suffix.lower() not in _DOCUMENTATION_SUFFIXES:
        return False
    return ".prompt" not in path.suffixes and "prompts" not in path.parts


def _behavior_scope_files(doc: Any) -> list[str]:
    return [
        entry.file
        for entry in doc.scope
        if entry.action != "Delete" and not entry.file.startswith(_NON_BEHAVIOR_PREFIXES) and not _is_documentation(entry.file)
    ]


def _test_obligation_failures(path: Path, doc: Any) -> list[str]:
    """Every behavior-capable scope row of a v4 IMPLEMENTATION plan needs its own obligation.

    Waivers are per-source (TestObligation.waiver_reason) by design: a single plan-level opt-out
    string would let one sentence disable the gate for every file in the plan.
    """
    if doc.schema_version < _TEST_OBLIGATION_MIN_SCHEMA_VERSION or doc.plan_type != "IMPLEMENTATION":
        return []
    covered = {obligation.source for obligation in doc.test_obligations}
    return [
        f"{path.name}: behavior-changing scope {source!r} lacks a linked test obligation or per-source waiver"
        for source in _behavior_scope_files(doc)
        if source not in covered
    ]


@registry.register("validate_plan_documents", owner="platform")
def validate_plan_documents(
    failed: list[str], plans_dir: Path | None = None, added_plan_names: set[str] | None = None
) -> None:
    """Validate every docs/plans/PLAN-*.yaml against the PlanDocument Pydantic schema (T1.11 / CD.22).

    Runs in BOTH --pre and full presubmit: pure Python over a handful of YAML files,
    well under the Decision 60 fast-tier budget, and PLAN-*.yaml is an active editing
    surface (same placement rationale as validate_platform_roadmap). Historical PLAN-*.md
    files are out of scope -- only the YAML artefact class is schema-governed.

    plans_dir overrides the scanned directory and added_plan_names overrides the git-derived
    new-plan set (test seams for malformed-fixture and new-plan proofs).
    """
    print("\n=== Plan document schema validation ===")

    target_dir = plans_dir if plans_dir is not None else _common.ROOT / "docs" / "plans"
    plan_paths = sorted(target_dir.glob("PLAN-*.yaml"))
    if not plan_paths:
        print("  PASS: no PLAN-*.yaml files to validate.")
        registry.examined(0, unit="plan_documents")
        return

    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        from scripts.roadmap.plan_document import (  # noqa: PLC0415
            CONTEXT_BLOCK_LINE_ADVISORY,
            context_block_lines,
            load,
        )

        added_names = _added_plan_names() if added_plan_names is None else added_plan_names
        errors: list[str] = []
        advisories: list[str] = []
        on_plan_pr = os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
        wording_base = _common.status_diff_base()
        if wording_base is None:
            advisories.append("no merge base resolves, so no wording-check row is required (planning SKILL.md Step 8b)")
        for path in plan_paths:
            # One parse per plan feeds both the schema verdict and the semantic gates -- this
            # check rides the --pre tier, so a second load() per passing plan is pure waste.
            try:
                doc = load(path)
            except Exception as exc:  # noqa: BLE001 -- any parse/validation error is a failure verdict
                errors.append(f"{path.name}: {exc}")
                continue
            errors.extend(_new_plan_version_failures(path, doc, added_names))
            errors.extend(_test_obligation_failures(path, doc))
            gaps = _gate_round_gaps(path, doc, added_names)
            if wording_base is not None:
                gaps += _wording_check_gaps(path, doc, wording_base)
            (errors if on_plan_pr else advisories).extend(gaps)
            if path.name in added_names:
                span = context_block_lines(path)
                if span > CONTEXT_BLOCK_LINE_ADVISORY:
                    advisories.append(
                        f"{path.name}: context block is {span} rendered lines "
                        f"(advisory cap {CONTEXT_BLOCK_LINE_ADVISORY}) -- link evidence, do not restate it"
                    )
        registry.examined(len(plan_paths), unit="plan_documents")
        for advisory in advisories:
            print(f"  WARN: {advisory}")
        for error in errors:
            print(f"  FAIL: {error}")
        if errors:
            failed.append("Plan document schema validation")
        else:
            print(f"  PASS: {len(plan_paths)} plan document(s) validate against PlanDocument schema.")
    except ImportError as exc:
        registry.skipped(f"plan_document import failed: {exc}")
        print(f"  ERROR: Could not import plan_document: {exc}")
        failed.append("Plan document schema validation")
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)
