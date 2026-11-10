"""Pins the verdict-persona roadmap re-grounding (PLAN-verdict-persona-roadmap-regrounding).

The plan landed the roadmap half of audits/verdict-personas-on-actions-66ab746e.yaml's findings VPA-01..VPA-14 and
ten bundled recs. Each test reads docs/ROADMAP-PLATFORM.yaml and asserts the criterion text, edges, scope or
decomposition hints one finding or rec needs. Statuses are never pinned, so a later build may flip a criterion to met.
Criterion text, depends_on and met_by survive an item's completion. The other pinned fields (files_in_scope,
decomposition_hints and T4.32's intent) are read only while their item is live, because completion_compaction
(docs/contracts/tier-item-lifecycle.yaml) empties or drops them when an item turns complete or reserved; a hint's
PLAN- token also resolves to its merged docs/plans/ file. So completing an item needs no edit here while each hinted
plan merges under the slug its hint names; a plan merged under another slug edits the hint and this file in its own
PR. rec-5039 carries T4.32's SPEND ruling past T4.32's completion: the spend pin then asks T4.32's compacted intent
to name it until the three ADD criteria carry the ruling. The fixture hydrates archived items from the archive
sibling, so a pin keeps reading an item after lifecycle_archival moves it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.roadmap.roadmap_archive import hydrate_from_disk

ROOT = Path(__file__).resolve().parents[2]
ROADMAP = ROOT / "docs/ROADMAP-PLATFORM.yaml"
SLUG = "PLAN-verdict-persona-roadmap-regrounding"
BRACKET = re.compile(r"\[(?:Amendment|Annotation) \d{4}-\d{2}-\d{2}, " + SLUG + r"[^\]]*\]")
ANY_BRACKET = re.compile(r"\[[^\]]*\]")
PLAN_TOKEN = re.compile(r"PLAN-[a-z0-9]+(?:-[a-z0-9]+)*")
MODEL_ID = re.compile(r"deepseek[-/]|claude-(?:opus|sonnet|haiku|fable)|gpt-\d|gemini-\d", re.IGNORECASE)
PERSONA_ITEMS = ("T4.2", "T4.10a", "T4.15", "T4.28", "T4.29", "T4.30", "T4.31", "T4.32")
FLIPS = (("T4.28", "c6"), ("T4.29", "c5"), ("T4.30", "c5"))
CAPTURES = (("T4.28", "c3"), ("T4.29", "c2"), ("T4.30", "c2"))
DUAL_RUNS = (("T4.28", "c5"), ("T4.29", "c4"), ("T4.30", "c4"))
ADDS = (("T4.28", "c4"), ("T4.29", "c3"), ("T4.30", "c3"))
MOVED = frozenset({"PLAN-persona-plan-agent", "PLAN-persona-implement-agent"})
TERMINAL = frozenset({"complete", "reserved"})


@pytest.fixture(scope="module")
def items() -> dict[str, dict[str, Any]]:
    doc = hydrate_from_disk(yaml.safe_load(ROADMAP.read_text(encoding="utf-8")), ROADMAP)
    return {item["id"]: item for item in doc["tier_items"]}


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _criterion(items: dict[str, dict[str, Any]], item_id: str, crit_id: str) -> str:
    for crit in items[item_id]["exit_criteria"]:
        if isinstance(crit, dict) and crit["id"] == crit_id:
            return _flat(crit["text"])
    raise AssertionError(f"{item_id} has no criterion {crit_id}")


def _bracket(text: str) -> str:
    found = BRACKET.findall(_flat(text))
    assert len(found) == 1, f"expected one {SLUG} bracket, found {len(found)} in: {text[:160]}"
    return found[0]


def _missing(text: str, phrases: tuple[str, ...]) -> list[str]:
    flat = _flat(text)
    return [phrase for phrase in phrases if phrase not in flat]


def _paths(items: dict[str, dict[str, Any]], item_id: str) -> set[str]:
    return {str(entry).split()[0] for entry in items[item_id].get("files_in_scope") or [] if "/" in str(entry).split()[0]}


def _live(items: dict[str, dict[str, Any]], item_id: str) -> bool:
    return items[item_id].get("status") not in TERMINAL


def _scope_missing(items: dict[str, dict[str, Any]], item_id: str, wanted: set[str]) -> list[str]:
    return sorted(wanted - _paths(items, item_id)) if _live(items, item_id) else []


def _heads(items: dict[str, dict[str, Any]], item_id: str) -> list[str]:
    plans = (items[item_id].get("decomposition_hints") or {}).get("atomic_plans") or []
    return [str(entry).split()[0] for entry in plans if str(entry).split()]


def test_t4_10a_c1_rescoped_to_five_personas(items):
    text = _criterion(items, "T4.10a", "c1")
    assert "Each of the five T4.2 personas (plan_agent, plan_critic, decision_scout, implement_agent, code_reviewer)" in text
    unbracketed = ANY_BRACKET.sub("", text)
    assert not re.search(r"\b(?:rca|bookkeeping)\b", unbracketed), unbracketed
    missing = _missing(_bracket(text), ("VPA-09", "T4.10 c1's", "Decision 93", "head-tree snapshot", "governance documents"))
    missing += _missing(_bracket(text), ("pre-computations", "read_input", "T4.2 c8"))
    assert not missing, missing
    assert "rca and bookkeeping" in _criterion(items, "T4.10", "c1")


def test_t4_10a_c6_declares_read_input(items):
    bracket = _bracket(_criterion(items, "T4.10a", "c6"))
    assert not _missing(bracket, ("VPA-13", "read_input:<blob>", "read-only", "workspace is None", "only those blobs"))


def test_t4_10a_c7_met_by_declaration(items):
    bracket = _bracket(_criterion(items, "T4.10a", "c7"))
    assert not _missing(bracket, ("met by this declaration", "docs/contracts/executor-personas.yaml", "T4.32 c1's"))
    assert "T4.10a c7's consumption leg is this criterion's" in _bracket(_criterion(items, "T4.32", "c1"))


def test_verdict_prompts_carry_quoted_content_clause(items):
    c5 = _bracket(_criterion(items, "T4.10a", "c5"))
    assert not _missing(c5, ("VPA-11", "T4.13 c3's clause", "first render", "quoted content", "is data")), c5
    t413 = _bracket(_criterion(items, "T4.13", "c3"))
    assert not _missing(t413, ("decision_scout", "plan_critic", "T4.10a c5", "T4.32 c3", "stays open")), t413


def test_t4_2_c8_names_materialised_main_side_context(items):
    bracket = _bracket(_criterion(items, "T4.2", "c8"))
    phrases = ("VPA-13", "main-side context the shell materialises", "governance documents", "pre-computations")
    assert not _missing(bracket, phrases + ("reads no repository", "holds no credential", "input_schema")), bracket


def test_t4_32_c1_names_credential_leg_and_served_model(items):
    bracket = _bracket(_criterion(items, "T4.32", "c1"))
    credential = ("CREDENTIAL LEG", "OIDC role", "persona-verdict Environment", "persona-verdict.yml@refs/heads/main")
    credential += ("/secret/ SSM", "no Environment secret", "pull_request job cannot assume", "terraform-github-apply.yml")
    credential += ("by literal ARN (Decision 237)", "gate_change", "this_repository", "actions_secret")
    credential += ("accepted exception", "owning_item", "assumes no AWS identity (Decision 212 clause 3)")
    model = ("MODEL", "requested_model", "served model string", "docs/contracts/inference-provider.yaml", "per tier role")
    wake = ("WAKE", "value-free", "idempotent", "(head SHA, persona)", "rec-3335", "wake_signals", "never polls")
    assert not _missing(bracket, credential + model + wake), _missing(bracket, credential + model + wake)
    text = yaml.safe_dump(items["T4.32"], sort_keys=False, width=1_000_000)
    assert not MODEL_ID.search(text), "a model id belongs only in docs/contracts/inference-provider.yaml (Decision 173)"


def test_t4_32_c2_names_image_channel_ruling(items):
    bracket = _bracket(_criterion(items, "T4.32", "c2"))
    phrases = ("VPA-10", "GHCR package", "packages: write", "SHA-pinned actions", "main's CI completion wakes")
    phrases += ("newest main commit whose full tier passed", "Decision 249 clause 2")
    phrases += ("hash-locked requirements", "hash mismatch fails the build", "provenance attestation")
    phrases += ("one repository file", "local twin", "docs/contracts/deploy-paths.yaml")
    assert not _missing(bracket, phrases), _missing(bracket, phrases)


def test_t4_32_c3_fixture_run_proves_wake_and_injection(items):
    bracket = _bracket(_criterion(items, "T4.32", "c3"))
    phrases = ("woken by c1's wake", "never polls", "injection case", "failure_outputs", "never success", "input_schema")
    assert not _missing(bracket, phrases), _missing(bracket, phrases)


def test_t4_32_c4_fingerprint_includes_north_star(items):
    bracket = _bracket(_criterion(items, "T4.32", "c4"))
    assert not _missing(bracket, ("rec-4916", "docs/contracts/north-star.yaml", "FINGERPRINT-COMPLETENESS TEST covers it"))


def test_t4_32_c4_rescouts_on_main_amendment(items):
    bracket = _bracket(_criterion(items, "T4.32", "c4"))
    phrases = ("rec-4946", "push to main", "content hash", "open plan PR", "re-scouts that PR on its current head")
    phrases += ("never a schedule", "superseded text", "Decision 217 clause 8")
    assert not _missing(bracket, phrases), _missing(bracket, phrases)


def test_t4_32_c5_gate_consumers_flag_aware(items):
    text = _criterion(items, "T4.32", "c5")
    phrases = ("GATE CONSUMERS FLAG-AWARE", "gates.<persona>.verdict_source", "{in_session, actions}", "default in_session")
    phrases += ("before any persona item's add step", "docs/contracts/coordinator.yaml", "standing_authorisation")
    phrases += ("parallel_with_code_review_state_machine", "pr_flow", ".claude/hooks/merge_gate.py", "failing closed")
    phrases += ("rec-4624", "rec-4080", "changes only its row's default", "Decision 247", "this_repository field")
    phrases += ("enable_pr_auto_merge", "Decision 83's non-wedging property", "Decision 245 clause 3")
    assert not _missing(text, phrases), _missing(text, phrases)
    assert {83, 245, 246, 247, 248} <= set(items["T4.32"]["related_decisions"]), items["T4.32"]["related_decisions"]
    wanted = {
        "docs/contracts/project-configuration.yaml",
        "docs/contracts/coordinator.yaml",
        "docs/contracts/tier-item-lifecycle.yaml",
        "docs/contracts/project-alignment.yaml",
        ".claude/hooks/merge_gate.py",
    }
    assert not _scope_missing(items, "T4.32", wanted), _scope_missing(items, "T4.32", wanted)


def test_verdict_override_path_is_specified(items):
    text = _criterion(items, "T4.32", "c6")
    phrases = ("HUMAN OVERRIDE", "rec-4944", "FLAGS_FOUND", "BLOCK", "REVISE", "recorded artefact on the plan PR")
    phrases += ("who made it", "never an agent identity", "finding ids", "check-run conclusion")
    phrases += ("without a fingerprint change", "never a plan edit", "never a skipped gate")
    phrases += ("reviewer-gated Environment", "Decision 246 clause 1", "Decision 233 clause 4", "T4.2 c9")
    assert not _missing(text, phrases), _missing(text, phrases)


def test_verdict_runtime_declares_egress_policy(items):
    text = _criterion(items, "T4.32", "c7")
    phrases = ("EGRESS", "rec-4947", "first secret-bearing run", "configured LLM providers", "GitHub API")
    phrases += ("denied by default", "the shell and the container", "red fixture", "network egress")
    assert not _missing(text, phrases), _missing(text, phrases)


def test_flip_items_scope_coordinator_and_capabilities(items):
    assert not _scope_missing(items, "T4.32", {"docs/contracts/coordinator.yaml"})
    assert "docs/contracts/coordinator.yaml" in _criterion(items, "T4.32", "c5")
    assert not _scope_missing(items, "T4.28", {"config/agent/executor/capabilities.yaml"})
    assert not _scope_missing(items, "T4.30", {"docs/contracts/project-alignment.yaml"})
    for item_id, crit_id in FLIPS:
        assert not _scope_missing(items, item_id, {"docs/contracts/project-configuration.yaml"}), item_id
        bracket = _bracket(_criterion(items, item_id, crit_id))
        assert not _missing(bracket, ("rec-4945", "T4.32 c5", "changes only its row's default")), (item_id, bracket)
    assert "config/agent/executor/capabilities.yaml's boundary list" in _bracket(_criterion(items, "T4.28", "c6"))


def test_t4_28_flip_names_handoff_gate_interaction(items):
    bracket = _bracket(_criterion(items, "T4.28", "c6"))
    phrases = ("rec-4943", "HANDOFF GATE", "handoff_evidence_gate", "draft-PR push", "fast-tier evidence")
    phrases += ("full tier still gates ready_for_review and merge", "--verify-head", "PR-CI full-tier verdict")
    phrases += ("rec-4790 option (b)", "for draft pushes only")
    phrases += ("one full-tier run per implementation", "Decision 203", "Decision 235", "Decision 163 clause 3")
    assert not _missing(bracket, phrases), _missing(bracket, phrases)
    assert {163, 203, 235} <= set(items["T4.28"]["related_decisions"]), items["T4.28"]["related_decisions"]
    assert not _scope_missing(items, "T4.28", {"docs/contracts/git-ops.yaml", "scripts/session/handoff_evidence.py"})


def test_capture_criteria_name_rec_4028(items):
    for item_id, crit_id in CAPTURES:
        bracket = _bracket(_criterion(items, item_id, crit_id))
        phrases = ("VPA-08", "rec-4028", "model_call rows", "before the add step's first run")
        assert not _missing(bracket, phrases), (item_id, bracket)


def test_dual_run_criteria_name_reset_rule(items):
    for item_id, crit_id in DUAL_RUNS:
        bracket = _bracket(_criterion(items, item_id, crit_id))
        phrases = ("VPA-12", "restarts", "definition hash (rec-4433)", "served model string", "disposition of each")
        phrases += ("window-validity rule", "non-passing in-session verdicts", "comparison tool's read-back")
        assert not _missing(bracket, phrases), (item_id, _missing(bracket, phrases))


def test_decomposition_hints_resolve_to_one_parent(items):
    heads: dict[str, list[str]] = {}
    for item_id in items:
        for head in _heads(items, item_id):
            if head.startswith("PLAN-"):
                heads.setdefault(head, []).append(item_id)
    twice = {head: owners for head, owners in heads.items() if len(owners) > 1}
    assert not twice, twice
    wanted = {
        "T4.10a": {"PLAN-persona-contracts-verdict-slice", "PLAN-persona-contracts-compute-plane"},
        "T4.15": {"PLAN-persona-implement-agent"},
        "T4.28": {"PLAN-code-reviewer-add-dual-run", "PLAN-code-reviewer-flip", "PLAN-code-reviewer-remove"},
        "T4.29": {"PLAN-plan-critic-add-dual-run", "PLAN-plan-critic-flip", "PLAN-plan-pr-personas-remove"},
        "T4.30": {"PLAN-decision-scout-add-dual-run", "PLAN-decision-scout-flip"},
        "T4.31": {"PLAN-persona-plan-agent"},
        "T4.32": {
            "PLAN-llm-client-litellm-transport",
            "PLAN-persona-verdict-credential",
            "PLAN-scout-input-fingerprint",
            "PLAN-persona-trigger-policy",
            "PLAN-run-persona-litellm-backend",
            "PLAN-persona-image-channel",
            "PLAN-persona-verdict-jobs",
            "PLAN-gate-consumers-flag-aware",
        },
    }
    short = {
        item_id: sorted(plans - set(_heads(items, item_id))) for item_id, plans in wanted.items() if _live(items, item_id)
    }
    assert not any(short.values()), short
    assert not MOVED & set(_heads(items, "T4.2")), _heads(items, "T4.2")
    dependants: dict[str, set[str]] = {}
    for item_id, item in items.items():
        for dep in item.get("depends_on") or []:
            dependants.setdefault(str(dep), set()).add(item_id)
    downstream: dict[str, set[str]] = {}
    for item_id in PERSONA_ITEMS:
        seen: set[str] = set()
        todo = [item_id]
        while todo:
            fresh = dependants.get(todo.pop(), set()) - seen
            seen |= fresh
            todo += sorted(fresh)
        downstream[item_id] = seen
    dangling, cycles = [], []
    for item_id in PERSONA_ITEMS:
        for entry in (items[item_id].get("decomposition_hints") or {}).get("atomic_plans") or []:
            tokens = PLAN_TOKEN.findall(str(entry))
            if tokens and str(entry).startswith(tokens[0]):
                merged = {token for token in tokens[1:] if (ROOT / "docs/plans" / f"{token}.yaml").is_file()}
                dangling += [(item_id, token) for token in tokens[1:] if token not in heads and token not in merged]
                cycles += [(item_id, t) for t in tokens[1:] if set(heads.get(t, ())) & downstream[item_id]]
    assert not dangling, dangling
    assert not cycles, cycles
    if _live(items, "T4.32"):
        hints = items["T4.32"]["decomposition_hints"]
        consumers = next(str(e) for e in hints["atomic_plans"] if str(e).startswith("PLAN-gate-consumers-flag-aware"))
        assert "after PLAN-llm-client-litellm-transport; before any persona item's add step" in consumers, consumers
        assert "binds before any add step" in str(hints["rationale"]), hints["rationale"]
    if _live(items, "T4.30"):
        assert "WP-17 depends on WP-15" in str(items["T4.30"]["decomposition_hints"]["rationale"])
    if _live(items, "T4.29"):
        assert "re-scout required" in str(items["T4.29"]["decomposition_hints"]["rationale"])


def test_t4_17_depends_on_t4_32(items):
    depends_on = items["T4.17"]["depends_on"]
    assert "T4.32" in depends_on and "T4.2" not in depends_on, depends_on


def test_t4_2_and_t4_32_files_in_scope_disjoint(items):
    shared = _paths(items, "T4.2") & _paths(items, "T4.32")
    assert not shared, sorted(shared)
    if _live(items, "T4.2"):
        pointer = [str(entry) for entry in items["T4.2"]["files_in_scope"] if "rec-4499" in str(entry)]
        assert len(pointer) == 1 and "T4.32's files_in_scope" in pointer[0] and "persona-verdict" in pointer[0], pointer


def test_t4_25_c4_rehome_names_all_three_flips(items):
    c4 = next(c for c in items["T4.25"]["exit_criteria"] if c["id"] == "c4")
    assert c4["met_by"] == "T4.28", c4
    assert not _missing(_bracket(c4["text"]), ("rec-4502", "T4.28", "c6", "T4.29 c5", "T4.30 c5"))


def test_t4_32_records_spend_ceiling(items):
    start = ("START", "may start once T4.10a is in_progress", "depends_on orders completion")
    start += ("a start order and not a completion edge", "eligible_items()")
    spend = ("SPEND", "operator ruling", "USD 120 a month", "prepaid provider balance", "weekly report", "per persona")
    spend += ("projected breach pauses the advisory", "never a skipped gate", "re-sets the ceiling")
    spend += ("never stored or published", "per-day dollar cap", "Decision 217 clause 4", "Decision 221 clause 1")
    spend += ("per-PR", "bounds the fixture and smoke runs")
    if _live(items, "T4.32"):
        bracket = _bracket(items["T4.32"]["intent"])
        assert not _missing(bracket, start + spend), _missing(bracket, start + spend)
        edges = [{"ref": e["ref"], "once": e["once"]} for e in items["T4.32"].get("start_edges", [])]
        has_start = "may start once T4.10a is in_progress" in bracket
        assert (edges == [{"ref": "T4.10a", "once": "in_progress"}]) == has_start, (
            "T4.32 START sentence and start_edges drifted"
        )
    else:
        mirrored = all("SPEND" in _criterion(items, item_id, crit_id) for item_id, crit_id in ADDS)
        assert mirrored or "rec-5039" in _flat(items["T4.32"]["intent"]), "T4.32's compacted intent must name rec-5039"


def test_t4_26_c5_points_rca_contract_at_t4_10(items):
    bracket = _bracket(_criterion(items, "T4.26", "c5"))
    assert not _missing(bracket, ("VPA-09", "rca persona contract", "T4.10 c1's", "not T4.10a's")), bracket
