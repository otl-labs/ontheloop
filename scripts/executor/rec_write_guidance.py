"""Precision context injection for recommendation writes (Decision 66).

Surfaces ops.yaml field semantics and the source registry to agents before they
call file_rec(), preventing semantically thin but structurally valid records.

Public API:
    load_source_registry()   -- parse source_registry.yaml; cached after first call
    validate_source()        -- raise ValueError for unregistered source values
    get_rec_write_guidance() -- return field semantics + registry for agent context
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_REGISTRY = _ROOT / "config" / "agent" / "data_quality" / "source_registry.yaml"
_DEFAULT_OPS_YAML = _ROOT / "config" / "agent" / "data_quality" / "ops.yaml"
_DEFAULT_GLOSSARY = _ROOT / "docs" / "contracts" / "glossary.yaml"


@lru_cache(maxsize=4)
def _load_registry_cached(registry_path: Path) -> tuple[dict, ...]:
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    return tuple(data.get("entries", []))


def load_source_registry(registry_path: Path | None = None) -> list[dict]:
    """Return all entries from source_registry.yaml as a list of dicts."""
    path = registry_path or _DEFAULT_REGISTRY
    return list(_load_registry_cached(path))


def validate_source(value: str, registry_path: Path | None = None) -> None:
    """Raise ValueError if value is not a registered canonical_id.

    Args:
        value: The source string to validate.
        registry_path: Override path for source_registry.yaml (used in tests).

    Raises:
        ValueError: If value is not in the registry's canonical_id list.
    """
    entries = load_source_registry(registry_path)
    valid_ids = {e["canonical_id"] for e in entries}
    if value not in valid_ids:
        raise ValueError(
            f"Unknown source '{value}'. Register in config/agent/data_quality/source_registry.yaml before filing."
        )


def get_rec_write_guidance(
    ops_yaml_path: Path | None = None,
    registry_path: Path | None = None,
    source: str | None = None,
    glossary_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return field semantics from ops.yaml augmented with registry data for source.

    Agents MUST call this before file_rec() so that field semantics are in context
    at composition time (Decision 66 -- Precision Context Injection).

    When source='ci_rca', returns the CiRcaContext structured schema fields as
    authoritative write-time guidance. This is a dedicated code path rather than
    an auto-populated ops.yaml column entry because the ci_rca schema is a nested
    object, not a flat column -- intentional divergence from the Wave-2 auto-population pattern.

    Args:
        ops_yaml_path: Override path for ops.yaml.
        registry_path: Override path for source_registry.yaml.
        source: Optional source identifier. Pass 'ci_rca' to return the structured
            context schema fields alongside the standard column semantics.
        glossary_path: Override path for docs/contracts/glossary.yaml (used in tests).

    Returns:
        Dict keyed by column name. Each value has at minimum:
            "description": str
            "semantics": str
        The "source" entry additionally carries:
            "registered_values": list[str]  -- all canonical_ids from source_registry.yaml
        When source='ci_rca', additionally carries a "context_v2_json" entry with
            "schema_fields": dict  -- CiRcaContext field names mapped to their semantics.
        When the glossary contract exists, a "glossary" entry projects its terms and relative-word say_instead values.
    """
    # Positional-arg compat: if first arg is a string that is not a file path, treat as source.
    # Supports g('ci_rca') calling convention in VP verification commands.
    if isinstance(ops_yaml_path, str) and not Path(ops_yaml_path).exists():
        source = ops_yaml_path
        ops_yaml_path = None

    ops_path = ops_yaml_path or _DEFAULT_OPS_YAML
    data = yaml.safe_load(ops_path.read_text(encoding="utf-8"))

    guidance: dict[str, dict[str, Any]] = {}
    tables = data.get("tables", {})
    for _table_name, table_def in tables.items():
        columns = table_def.get("columns", {})
        for col_name, col_def in columns.items():
            if col_name in guidance:
                continue
            entry: dict[str, Any] = {
                "description": col_def.get("description", ""),
                "semantics": col_def.get("semantics", ""),
            }
            if col_name == "source":
                entries = load_source_registry(registry_path)
                entry["registered_values"] = [e["canonical_id"] for e in entries]
            guidance[col_name] = entry

    if source == "ci_rca":
        guidance["context_v2_json"] = {
            "description": (
                "Structured RCA context for source=ci_rca recs (CiRcaContext schema, INTENT Section 1). "
                "Enforced in warn mode; raises in strict mode (CI_RCA_STRICT_MODE flag)."
            ),
            "semantics": (
                "Compose a CiRcaContext object with all required fields. "
                "The portal validates it against the Pydantic model at write time."
            ),
            "schema_fields": {
                "schema_version": (
                    "int, ==2. Monotonic version; portal rejects schema_version > 2. "
                    "New recs file at version 2; historical schema_version=1 recs still validate."
                ),
                "proximate_cause": (
                    "str, 100-600 chars. The observable fact the failing check reported -- "
                    "NOT an inference. Anti-example: 'file is too big'. "
                    "Example: 'validate_sloc_limits() raised: scripts/foo.py is 810 SLOC, exceeds 500 limit'."
                ),
                "why_chain": (
                    "list[str], 3-7 entries. Iterative 'but why?' descent from proximate_cause to a "
                    "systemic gap. Final entry MUST contain a systemic keyword AND a file:line citation. "
                    "Per-entry length ceiling is version-gated: 40-250 chars at schema_version 1, "
                    "40-400 chars at schema_version 2 (loosening-only; no historical row is affected)."
                ),
                "why_chain_terminus_override": (
                    "optional object: {reason: str, 80-400 chars}. A conformant reason bypasses the "
                    "terminus systemic-keyword/citation checks; a missing reason or one outside 80-400 "
                    "chars fails validation (the depth floor is never disabled by a bare truthy value)."
                ),
                "detection_gap": (
                    "object: {earliest_viable_gate: pre|presubmit|CI|undetermined, "
                    "actual_gate_that_caught_it: pre|presubmit|CI|unknown, "
                    "gap_explanation: str 120-600 chars with file:line citation, "
                    "escape_mode: check_ran_vacuously|tier_misplaced"
                    "|no_premerge_gate_by_design|undetermined (optional)}. "
                    "actual_gate_that_caught_it: mirror the evidence bundle's value; write 'unknown' "
                    "when the bundle emits null for this field (not_a_gate workflows such as "
                    "terraform-apply-sandbox have no CI-gate concept to report). Do not fabricate "
                    "pre/presubmit/CI when the bundle gives no gate."
                ),
                "recurrence_class": "str enum: novel | instance_of_known_pattern | regression.",
                "prior_art_citation": "optional str. Shape-validated only (existence check deferred to Phase 2).",
                "corrective_action": "str, 100-600 chars. The tactical fix that restores service.",
                "repair_track": (
                    "str enum: test_defect | product_regression. test_defect only when merged product behaviour "
                    "is right and the corrective changes only tests/**; absent or unsure reads as "
                    "product_regression; the portal derives priority from it (Decision 226)."
                ),
                "preventive_action": (
                    "str, 100-800 chars; omit only for a Track A red -- repair_track test_defect on a CI-tier "
                    "workflow with no regression (none owed, Decision 226); required otherwise."
                ),
                "evidence_bundle_ref": (
                    "optional object: {sha256: 64 hex chars, s3_uri: s3://..., upload_status: str}. "
                    "Shape-validated only (S3 existence check deferred to Phase 2)."
                ),
                "escape_mode": (
                    "str enum: check_ran_vacuously | tier_misplaced"
                    " | no_premerge_gate_by_design | undetermined. "
                    "MIRROR from evidence_bundle.escape_mode (bundle-wins). When bundle abstains ('undetermined'), "
                    "set escape_mode='undetermined' here. Do NOT free-choose this value."
                ),
                "rca_confidence": (
                    "str enum: high | medium | low | undetermined. Set to 'undetermined' when the evidence "
                    "bundle's earliest_viable_gate='undetermined' (probe abstained). This surfaces the rec for "
                    "mandatory human review in session_preflight. Set 'high' when bundle values are concrete and "
                    "agent values match; 'low' when significant ambiguity remains."
                ),
                "unobserved_steps": (
                    "optional list[{job_id: int, step_index: int}] pairs -- NEVER a flat index space "
                    "(a step_index is unique only within its job). Your comprehension echo: derive it from "
                    "the evidence bundle's retrieval_evidence.scope entries where log_retrieved is false. "
                    "Set to [] when every scope entry has log_retrieved=true. The portal cross-checks this "
                    "echo against a code-computed authoritative set and tags a disagreement; do not narrate "
                    "about a step you listed here as if you had read its log. "
                    "unobserved_steps_authoritative is a SEPARATE, portal-derived field -- never author it."
                ),
            },
        }
    elif source == "ci_rca_evidence_dispute":
        # Flat top-level guidance entries for the dispute schema fields (Decision 66 Precision Context Injection).
        # These are the ONLY fields needed in context_v2_json when source=ci_rca_evidence_dispute; no CiRcaContext
        # fields are required (the check-8 carve-out in file_rec() bypasses ci_rca checks 1-7 automatically).
        guidance["parent_rec_id"] = {
            "description": "The existing source=ci_rca rec whose detection_gap cross-check result is being disputed.",
            "semantics": (
                "str, pattern ^rec-\\d+$. Identifies the parent ci_rca rec. Example: 'rec-1234'. "
                "The dispute is filed against this rec's cross-check values; close the dispute by updating "
                "this parent rec when the disagreement is resolved."
            ),
        }
        guidance["disputed_field"] = {
            "description": "Which of the cross-check points is in dispute.",
            "semantics": (
                "str enum: earliest_viable_gate | actual_gate_that_caught_it | failure_category. "
                "These are the CiRcaContext detection_gap fields subject to the INTENT Section-2 "
                "cross-check. Widening the enum requires a schema bump (explicit human direction). "
                "CiRcaEvidenceDispute.disputed_field pattern: "
                "^(earliest_viable_gate|actual_gate_that_caught_it|failure_category)$"
            ),
        }
        guidance["agent_value"] = {
            "description": "The value the ci-rca agent reported for the disputed_field.",
            "semantics": (
                "str, non-empty. Example: 'pre' (agent claimed the check could have run at pre tier). "
                "This is the value the agent put into the detection_gap object of the parent rec."
            ),
        }
        guidance["bundle_value"] = {
            "description": "The value the deterministic evidence bundle produced for the disputed_field.",
            "semantics": (
                "str, non-empty. Example: 'CI' (bundle shows the check only runs in CI). "
                "The disagreement between agent_value and bundle_value is what triggers the dispute."
            ),
        }
        guidance["evidence_for_dispute"] = {
            "description": "A reproducible, cited argument proving the bundle value is correct and the agent value is wrong.",
            "semantics": (
                "str, min 120 chars. Must be specific: name the script, flag, or config that proves the bundle is right. "
                "Anti-example: 'I think the agent is wrong'. "
                "Example: 'scripts/collect_ci_evidence.py:142 shows earliest_viable_gate is derived from validate.py "
                "--pre output; the agent value pre is wrong because the check is guarded by a CI-only env var at "
                "validate.py:87, making pre impossible.'"
            ),
        }

    glossary = glossary_path or _DEFAULT_GLOSSARY
    if glossary.is_file():
        contract = yaml.safe_load(glossary.read_text(encoding="utf-8"))
        words = contract["relative_words"]
        guidance["glossary"] = {
            "description": "Names to use in rec text (docs/contracts/glossary.yaml).",
            "semantics": words["rule"],
            "terms": {
                key: {field: term[field] for field in ("written", "reserved_meaning", "do_not_use_for")}
                for key, term in contract["terms"].items()
            },
            "relative_words": {word: words[word]["say_instead"] for word in ("platform", "product")},
        }

    return guidance
