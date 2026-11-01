"""rec-4122: parser_version registry and golden-corpus CI check per producer (Decision 207 R1).

Stdlib + yaml, fast tier. The CASES tuple names every golden case file basename literally: those literals are what
make --pre's data-edge channel select this module when only a golden case file changes.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from src.turn_capture.record_turn import FULL_OUTPUT_CAP_BYTES, PARSER_VERSION, PRODUCER, record_turn
from tests.fixtures.turn_capture_corpus import (
    GOLDEN_DIR,
    PROJECT_REF,
    case_cap,
    case_names,
    golden_digest,
    load_case,
    mem_tree,
    normalise,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REL = "config/telemetry/parser_versions.yaml"
CASES = (
    "pin_first_record_without_uuid.json",
    "multiblock_response_interleaved_tool_results.json",
    "meta_and_companion_records.json",
    "tool_result_error.json",
    "tool_result_interrupted.json",
    "hook_blocked_tool_call.json",
    "sidecar_present.json",
    "sidecar_absent.json",
    "subagent_sync.json",
    "subagent_background_with_completion.json",
    "subagent_background_without_completion.json",
    "nested_subagent_depth2.json",
    "hooks_resume_and_compact.json",
    "empty_thinking_and_image_blocks.json",
    "orphan_tool_result.json",
    "unknown_record_and_attachment_types.json",
    "trailing_partial_line.json",
    "malformed_line.json",
    "large_tool_result_over_64k.json",
    "observed_real_shapes.json",
    "secrets.json",
    "hooks_before_first_record.json",
    "mcp_overflow_saved_output.json",
    "full_output_omitted_over_cap.json",
    "reasoning_visibility_forms.json",
    "gate_and_precommit_markers.json",
    "close_cancelled_mid_stream.json",
    "close_cancelled_running_tool.json",
    "close_failed_api_error.json",
    "resume_after_interrupt.json",
    "fork_branch_copied_head.json",
    "fork_session_copied_head.json",
    "fork_session_nested.json",
    "hook_command_names.json",
    "no_reply_session.json",
    "subagent_usage_final_and_spawn_correction.json",
    "background_notice_statuses.json",
    "resume_segment_closes.json",
    "session_provenance_facts.json",
    "model_call_request_fields.json",
    "stop_witness_closes.json",
    "stop_blocked_witness_first.json",
    "stop_blocked_feedback_first.json",
    "stop_blocked_then_interrupted.json",
    "api_error_self_close.json",
    "stop_witness_not_at_rest.json",
    "prompt_open_row.json",
    "workflow_from_skill.json",
    "subagent_workflow_skills.json",
    "status_markers.json",
    "late_startup_group.json",
    "late_resume_group.json",
    "resume_hook_twins.json",
)


def _cap_of(entry: dict) -> int | None:
    """The producer input cap recorded in force for a history entry; a missing key reads as 'no cap'."""
    return entry.get("full_output_cap_bytes")


def _contract_sources() -> list[str]:
    """The process_event name sources, read from the contract's pattern_when (never restated here)."""
    contract = yaml.safe_load((ROOT / "docs/contracts/telemetry_observations.yaml").read_text(encoding="utf-8"))
    pattern = contract["fields"]["name"]["dq_intent"]["pattern_when"][0]["pattern"]
    match = re.match(r"\^\(([A-Za-z|]+)\):", pattern)
    assert match, pattern
    return match.group(1).split("|")


def registry_problems(registry: dict, producer: str, code_version: int, recomputed: str, expected: str) -> list[str]:
    """Every way the registry, the producer constant and the golden corpus can disagree."""
    entry = registry["producers"][producer]
    history = entry["history"]
    problems: list[str] = []
    versions = [h["version"] for h in history]
    if versions != sorted(set(versions)):
        problems.append("history versions are not strictly increasing")
    if entry["current"] != code_version or versions[-1] != code_version:
        problems.append("current, the last history version and the producer constant disagree")
    if history[-1]["golden_digest"] != recomputed:
        problems.append("output drift without a bump: the recomputed golden digest differs from the registry")
    if history[-1]["golden_digest"] != expected:
        problems.append("expected files edited without a bump: the committed expected_rows digest differs from the registry")
    prefixes = history[-1].get("signature_prefixes")
    if not isinstance(prefixes, list) or not prefixes:
        problems.append("the current version declares no signature_prefixes")
    elif len(set(prefixes)) != len(prefixes) or not set(prefixes) <= set(_contract_sources()):
        problems.append("signature_prefixes repeats a prefix or names a source outside the contract's")
    for before, after in zip(history, history[1:]):
        if before["golden_digest"] == after["golden_digest"] and _cap_of(before) == _cap_of(after):
            problems.append(f"bump without drift: v{after['version']} repeats v{before['version']}'s digest")
    return problems


def append_only_problems(base: dict | None, current: dict) -> list[str]:
    """History is append-only versus the merge-base copy (a path absent at the base passes)."""
    if base is None:
        return []
    problems: list[str] = []
    for producer, entry in base["producers"].items():
        now = current["producers"].get(producer)
        if now is None:
            problems.append(f"producer {producer} was removed")
            continue
        if now["history"][: len(entry["history"])] != entry["history"]:
            problems.append(f"{producer}: a past history entry was rewritten or removed")
        if now["current"] < entry["current"]:
            problems.append(f"{producer}: current went backwards")
    return problems


def _live_registry() -> dict:
    return yaml.safe_load((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))


def _synthetic() -> dict:
    return {"producers": {"p": {"current": 2, "history": [
        {"version": 1, "date": "2026-01-01", "golden_digest": "d1", "reason": "first"},
        {"version": 2, "date": "2026-01-02", "golden_digest": "d2", "reason": "second", "signature_prefixes": ["hook"]},
    ]}}}  # fmt: skip


def test_output_drift_without_bump_fails() -> None:
    registry = _synthetic()
    assert registry_problems(registry, "p", 2, "d2", "d2") == []
    problems = registry_problems(registry, "p", 2, "d3", "d2")
    assert len(problems) == 1 and problems[0].startswith("output drift without a bump")


def test_bump_without_drift_fails() -> None:
    registry = copy.deepcopy(_synthetic())
    registry["producers"]["p"]["history"][1]["golden_digest"] = "d1"
    problems = registry_problems(registry, "p", 2, "d1", "d1")
    assert len(problems) == 1 and problems[0].startswith("bump without drift")
    stale = registry_problems(_synthetic(), "p", 3, "d2", "d2")
    assert stale == ["current, the last history version and the producer constant disagree"]
    unordered = copy.deepcopy(_synthetic())
    unordered["producers"]["p"]["history"][1]["version"] = 1
    assert "history versions are not strictly increasing" in registry_problems(unordered, "p", 1, "d2", "d2")


def test_signature_prefixes_are_required_and_validated_on_the_current_version() -> None:
    assert registry_problems(_synthetic(), "p", 2, "d2", "d2") == []
    missing = copy.deepcopy(_synthetic())
    del missing["producers"]["p"]["history"][1]["signature_prefixes"]
    assert registry_problems(missing, "p", 2, "d2", "d2") == ["the current version declares no signature_prefixes"]
    for bad in (["hook", "hook"], ["hook", "invented"], []):
        broken = copy.deepcopy(_synthetic())
        broken["producers"]["p"]["history"][1]["signature_prefixes"] = bad
        assert registry_problems(broken, "p", 2, "d2", "d2"), bad
    live = _live_registry()["producers"][PRODUCER]["history"]
    assert "signature_prefixes" not in live[0] and "signature_prefixes" not in live[1]


def test_expected_edit_without_bump_fails() -> None:
    problems = registry_problems(_synthetic(), "p", 2, "d2", "d9")
    assert len(problems) == 1 and problems[0].startswith("expected files edited without a bump")


def _base_registry() -> tuple[str, dict | None]:
    """('absent' | 'unreadable' | 'present', parsed base copy) read from the merge base with origin/main."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)

    base = git("merge-base", "HEAD", "origin/main")
    if base.returncode != 0 or not base.stdout.strip():
        return "unreadable", None
    sha = base.stdout.strip()
    if not git("ls-tree", "-r", "--name-only", sha, "--", REGISTRY_REL).stdout.strip():
        return ("absent", None) if git("cat-file", "-e", sha).returncode == 0 else ("unreadable", None)
    shown = git("show", f"{sha}:{REGISTRY_REL}")
    return ("present", yaml.safe_load(shown.stdout)) if shown.returncode == 0 else ("unreadable", None)


def test_registry_history_is_append_only_against_main() -> None:
    older = _synthetic()
    grown = copy.deepcopy(older)
    grown["producers"]["p"]["history"].append({"version": 3, "date": "2026-01-03", "golden_digest": "d3", "reason": "third"})
    grown["producers"]["p"]["current"] = 3
    assert append_only_problems(older, grown) == [] and append_only_problems(None, grown) == []
    rewritten = copy.deepcopy(grown)
    rewritten["producers"]["p"]["history"][0]["golden_digest"] = "other"
    assert append_only_problems(older, rewritten)
    assert append_only_problems(grown, older) and append_only_problems(older, {"producers": {}})
    state, base = _base_registry()
    if state == "unreadable":
        if os.environ.get("CI"):
            pytest.fail("origin/main is unreadable in CI: the append-only check cannot run (Decision 208 cl.1)")
        pytest.skip("origin/main unreadable locally: advisory skip (Decision 208 cl.1)")
    assert (state == "absent") == (base is None)
    assert append_only_problems(base, _live_registry()) == []


def _live_digests() -> tuple[str, str]:
    names = case_names()
    recomputed: dict[str, list] = {}
    committed: dict[str, list] = {}
    for name in names:
        files, expected = load_case(name)
        with case_cap(name):
            recomputed[name] = normalise(
                record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True).batches
            )
        committed[name] = expected
    return golden_digest(recomputed, names), golden_digest(committed, names)


def test_registry_matches_producer() -> None:
    registry = _live_registry()
    entry = registry["producers"][PRODUCER]
    assert PARSER_VERSION == entry["current"] == entry["history"][-1]["version"]
    recomputed, committed = _live_digests()
    assert recomputed == committed == entry["history"][-1]["golden_digest"]
    history = entry["history"]
    assert all(a["golden_digest"] != b["golden_digest"] or _cap_of(a) != _cap_of(b) for a, b in zip(history, history[1:])), (
        "a history entry repeats its predecessor's digest without a different producer input cap"
    )
    assert registry_problems(registry, PRODUCER, PARSER_VERSION, recomputed, committed) == []


def test_declared_signature_prefixes_match_the_corpus() -> None:
    declared = _live_registry()["producers"][PRODUCER]["history"][-1]["signature_prefixes"]
    emitted = set()
    for name in case_names():
        for row in load_case(name)[1]:
            if row.get("table") == "telemetry_observations" and row.get("observation_type") == "process_event":
                emitted.add(row["name"].split(":", 1)[0])
    assert emitted == set(declared) == {"hook", "gate", "precommit", "status"}
    assert set(declared) <= set(_contract_sources())


def test_cases_match_index() -> None:
    index = json.loads((GOLDEN_DIR / "index.json").read_text(encoding="utf-8"))
    assert list(CASES) == index["cases"]
    on_disk = sorted(p.name for p in GOLDEN_DIR.glob("*.json") if p.name != "index.json")
    assert on_disk == sorted(CASES)
    assert len(set(CASES)) == len(CASES)


def test_digest_repeating_bump_is_accepted_iff_the_cap_differs() -> None:
    registry = copy.deepcopy(_synthetic())
    registry["producers"]["p"]["history"][1]["golden_digest"] = "d1"
    assert len(registry_problems(registry, "p", 2, "d1", "d1")) == 1
    registry["producers"]["p"]["history"][1]["full_output_cap_bytes"] = 1024
    assert registry_problems(registry, "p", 2, "d1", "d1") == []
    registry["producers"]["p"]["history"][0]["full_output_cap_bytes"] = 1024
    assert len(registry_problems(registry, "p", 2, "d1", "d1")) == 1


def test_full_output_cap_agrees_with_contract() -> None:
    entry = _live_registry()["producers"][PRODUCER]["history"][-1]
    contract = yaml.safe_load((ROOT / "docs/contracts/telemetry_transcripts.yaml").read_text(encoding="utf-8"))
    ceiling = contract["fields"]["content"]["dq_intent"]["full_output_cap_bytes"]
    assert entry["full_output_cap_bytes"] == FULL_OUTPUT_CAP_BYTES <= ceiling
