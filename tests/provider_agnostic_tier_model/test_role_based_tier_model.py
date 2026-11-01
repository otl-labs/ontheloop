"""Mirror tests for PLAN-provider-agnostic-tier-model (Decision 173).

The provider-agnostic tier model is a governance commitment whose carriers are four artefacts:
the inference-provider contract (role-keyed tier rows, one provider-identity home), the decision
corpus (the numbered entry plus its three dated annotations), the platform roadmap (the
forward-intent home and its annotations), and the verification registry (the graduated row).
Each test below asserts on the real artefact -- no fixtures, no network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts import verification_graduation

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "inference-provider.yaml"
ROADMAP_PATH = REPO_ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
DECISIONS_PATH = REPO_ROOT / "docs" / "DECISIONS.md"
PLAN_PATH = REPO_ROOT / "docs" / "plans" / "PLAN-provider-agnostic-tier-model.yaml"

PLAN_SLUG = "provider-agnostic-tier-model"
DECISION_NUMBER = 173
ANNOTATION_DATE = "2026-08-19"
FIXED_ALLOWANCE = "fixed_non_rollover_allowance"
METERED = "metered_marginal"
LOCAL = "local"
PRIMARY_ROLE = "primary"
FALLBACK_ROLE = "warm_fetched_escape_hatch"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _normalise_provider(value: str) -> str:
    """Case-fold and collapse hyphen/underscore so 'gemini_byok' and 'Gemini-BYOK' compare equal."""
    return re.sub(r"[-_\s]+", "", value.strip().lower())


def _resolve_selection_ref(contract: dict[str, Any], ref: str) -> dict[str, Any]:
    node: Any = contract
    for part in ref.split("."):
        assert isinstance(node, dict), f"selection_ref {ref} does not resolve: {part} is not a mapping"
        assert part in node, f"selection_ref {ref} does not resolve: missing key {part}"
        node = node[part]
    assert isinstance(node, dict), f"selection_ref {ref} must resolve to a mapping, got {type(node)}"
    return node


def _roles_to_provider(contract: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for row in contract["tier_model"]["tiers"]:
        selection = _resolve_selection_ref(contract, row["selection_ref"])
        resolved[row["role"]] = str(selection["provider"])
    return resolved


def _decision_block(number: int) -> str:
    from scripts.decisions_md import iter_decision_sections

    content = DECISIONS_PATH.read_text(encoding="utf-8")
    for match, block in iter_decision_sections(content):
        if int(match.group(1)) == number:
            return block
    raise AssertionError(f"Decision {number} not found in {DECISIONS_PATH}")


def _tier_items() -> dict[str, dict[str, Any]]:
    roadmap = _load_yaml(ROADMAP_PATH)
    return {str(item["id"]): item for item in roadmap["tier_items"]}


def test_tier_rows_are_role_keyed_with_billing_shape() -> None:
    """Decision 173 clauses 1-2: the tier rows carry ROLES, never provider identity."""
    contract = _load_yaml(CONTRACT_PATH)
    tier_model = contract["tier_model"]
    vocabulary = tier_model["billing_shape_vocabulary"]
    assert set(vocabulary) == {METERED, FIXED_ALLOWANCE, LOCAL}, vocabulary

    rows = tier_model["tiers"]
    assert rows, "tier_model.tiers must not be empty"
    for row in rows:
        for field in ("role", "billing_shape", "cold_start_assertion", "selection_ref"):
            assert field in row, f"tier {row.get('tier')} is missing {field}"
        assert row["billing_shape"] in {METERED, FIXED_ALLOWANCE}, f"no tier carries {LOCAL} (Decision 234 clause 4): {row}"
        assert "provider" not in row, f"tier {row['tier']} still names a provider -- identity has one home"
        assert "current_provider" not in row, f"tier {row['tier']} carries a second copy of provider identity"
        assert row["selection_ref"].startswith("model_id_formats.litellm_tier_models."), row["selection_ref"]
        _resolve_selection_ref(contract, row["selection_ref"])

    resolved = _roles_to_provider(contract)
    assert PRIMARY_ROLE in resolved and FALLBACK_ROLE in resolved, resolved
    primary = _normalise_provider(resolved[PRIMARY_ROLE])
    fallback = _normalise_provider(resolved[FALLBACK_ROLE])
    assert primary != fallback, f"primary and fallback resolve to one vendor: {resolved}"

    retired = {_normalise_provider(str(entry["id"])) for entry in contract["retired_providers"]}
    for role, provider in resolved.items():
        assert _normalise_provider(provider) not in retired, f"role {role} is filled from retired_providers: {provider}"

    # selection_constraints.primary_billing_shape (Decision 173 clause 2(d)). Without this the
    # primary role could be moved to a fixed non-rollover allowance by a contract edit, voiding
    # Decision 164's volume argument and stranding two reversal conditions that presuppose a
    # metered primary.
    primary_rows = [row for row in rows if row["role"] == PRIMARY_ROLE]
    assert len(primary_rows) == 1, f"expected exactly one {PRIMARY_ROLE} row, got {len(primary_rows)}"
    assert primary_rows[0]["billing_shape"] == METERED, (
        f"the {PRIMARY_ROLE} role must bill as {METERED}; moving it to {FIXED_ALLOWANCE} "
        f"requires a Decision superseding Decision 164's volume argument: {primary_rows[0]}"
    )


def test_fixed_allowance_tier_requires_remaining_allowance_assertion() -> None:
    """Decision 173 clause 4: the cold-start assertion follows the billing shape, both directions."""
    contract = _load_yaml(CONTRACT_PATH)
    tier_model = contract["tier_model"]

    assert tier_model["cold_start_assertion_rule"].strip(), "cold_start_assertion_rule must be declared"
    constraints = tier_model["selection_constraints"]
    for key in (
        "retired_providers_excluded",
        "vendor_independence",
        "coupled_reversal_conditions",
        "primary_billing_shape",
    ):
        declared = constraints.get(key)
        assert isinstance(declared, str) and declared.strip(), f"selection_constraints.{key} must be a declared string"

    # Branch order encodes the rule's stated PRECEDENCE: state: Deferred overrides billing_shape.
    # The limbs are not disjoint (tier 3 is metered_marginal AND Deferred), so an order that put
    # billing_shape first would demand remaining_allowance from a Deferred fixed-allowance tier,
    # contradicting the prose that authorises not_applicable for it.
    for row in tier_model["tiers"]:
        shape = row["billing_shape"]
        assertion = row["cold_start_assertion"]
        deferred = str(row.get("state", "")).strip().lower().startswith("deferred")
        if deferred:
            assert assertion == "not_applicable", row
        elif shape == FIXED_ALLOWANCE:
            assert assertion == "remaining_allowance", row
        else:
            assert shape == METERED, row
            assert assertion == "credential_liveness", row
        # Converse direction: a remaining-allowance assertion implies a fixed-allowance tier.
        if assertion == "remaining_allowance":
            assert shape == FIXED_ALLOWANCE, row


def test_valid_provider_set_and_registry_constants_untouched() -> None:
    """The T4.2-owned interim transport boundary and the retired set are outside this amendment."""
    from scripts.llm import model_registry

    contract = _load_yaml(CONTRACT_PATH)
    executor_path = contract["provider_defaults"]["executor_path"]
    assert executor_path["valid_providers"] == ["gemini"], executor_path["valid_providers"]
    assert executor_path["default"] == "gemini", executor_path["default"]
    assert model_registry._VALID_PROVIDERS == frozenset({"gemini"}), model_registry._VALID_PROVIDERS
    assert model_registry._DEFAULT_EXECUTOR_PROVIDER == "gemini", model_registry._DEFAULT_EXECUTOR_PROVIDER

    retired_ids = {str(entry["id"]) for entry in contract["retired_providers"]}
    assert retired_ids == {"bedrock", "copilot-sdk", "gemini_byok"}, retired_ids


def test_decision_entry_states_role_model_and_annotates_116_122_and_164() -> None:
    """The numbered entry carries the commitment; 116/122/164 carry dated additive annotations."""
    from scripts.decisions_md import extract_entry_envelope

    block = _decision_block(DECISION_NUMBER)
    envelope = extract_entry_envelope(block)
    assert envelope is not None, "the new entry must carry a typed metadata envelope"
    assert envelope["number"] == DECISION_NUMBER, envelope
    assert sorted(envelope["amends"]) == [116, 122, 164], envelope
    assert envelope["significance"]["value"] == "numbered_decision", envelope
    assert envelope["significance"]["justification"].strip(), envelope

    assert "tiers are ROLES" in block
    assert "retired_providers" in block
    assert "DIFFERENT providers" in block
    assert "not_applicable" in block
    assert "scripts/llm/client.py" in block and "LLMResponseError" in block
    assert "```yaml reversal-conditions" in block

    for amended in (116, 122, 164):
        amended_block = _decision_block(amended)
        annotations = [
            line
            for line in amended_block.splitlines()
            if line.startswith(f"[Amendment {ANNOTATION_DATE}:") and f"Decision {DECISION_NUMBER}" in line
        ]
        assert len(annotations) == 1, f"Decision {amended} needs exactly one dated Decision-{DECISION_NUMBER} annotation"


def test_provider_variable_tier_item_and_transport_criterion() -> None:
    """The forward intent lands on a tier_item; T4.2 and CD.28 gain additive annotations only."""
    items = _tier_items()
    item = items["T4.17"]
    assert item["depends_on"] == ["T4.2"], item["depends_on"]
    criteria = item["exit_criteria"]
    assert len(criteria) >= 5, len(criteria)
    assert all(isinstance(c, dict) and "id" in c and "text" in c for c in criteria), criteria
    c5 = next(c for c in criteria if c["id"] == "c5")
    assert "docs/contracts/inference-provider.yaml" in c5["text"]
    assert "scripts/llm/client.py" in c5["text"]

    t42 = items["T4.2"]
    assert f"Decision {DECISION_NUMBER}" in t42["intent"]
    assert f"[Annotation {ANNOTATION_DATE}" in t42["intent"]
    assert all(isinstance(c, dict) and "id" in c and "text" in c for c in t42["exit_criteria"]), (
        "T4.2 exit_criteria must be ledger-form ExitCriterion objects"
    )

    roadmap = _load_yaml(ROADMAP_PATH)
    cd28 = next(cd for cd in roadmap["candidate_decisions"] if cd["id"] == "CD.28")
    annotated = [p for p in cd28["discipline_points"] if f"Decision {DECISION_NUMBER}" in p]
    assert len(annotated) == 1, "CD.28 needs exactly one additive Decision-173 annotation"
    assert annotated[0].startswith(f"[Annotation {ANNOTATION_DATE}"), annotated[0]


def test_graduated_registry_row_matches_declared_disposition() -> None:
    """Decision 132 implement-PR leg: the graduate disposition owns exactly one registry row."""
    rows = [e for e in verification_graduation.load_entries(repo_root=REPO_ROOT) if e.get("plan_slug") == PLAN_SLUG]
    assert len(rows) == 1, f"expected exactly one registry row for {PLAN_SLUG}, got {len(rows)}"
    row = rows[0]

    plan = _load_yaml(PLAN_PATH)
    graduating = [s for s in plan["verification_plan"] if s.get("graduation") == "graduate"]
    assert len(graduating) == 1, graduating
    assert row["check_id"] == graduating[0]["graduation_check_id"], (row["check_id"], graduating[0])
    assert row["primitive_slot"] == "test_selector", row
    assert row["guard_target"] == "docs/contracts/inference-provider.yaml", row
    node_id = " ".join(row["check_spec"]["node_id"].split())
    assert node_id.endswith("::test_tier_rows_are_role_keyed_with_billing_shape"), node_id
