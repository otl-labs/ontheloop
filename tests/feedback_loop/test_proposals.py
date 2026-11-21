"""Every draft is fileable as written, and no draft spells anything a committed rule does not name."""

from __future__ import annotations

import json
import shlex
from datetime import date
from pathlib import Path
from typing import Any

from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.executor.jsonl_store import Recommendation
from scripts.executor.rec_write_guidance import validate_source
from scripts.feedback_loop import friction, proposals
from scripts.ops_portal.risk_scoring import _derive_computed_fields
from scripts.ops_portal.write_validators import gate_write
from src.common.ducklake_event_read_verbs import FRICTION_RULES
from tests.fixtures import feedback_loop_rows as fx

ROOT = Path(__file__).resolve().parents[2]
WINDOW = ("2026-11-14", "2026-11-20")
VERSIONS = {"classifier_version": fx.VERSION, "detector_version": friction.DETECTOR_VERSION}
SCAFFOLD = {"gate:lint", "gate:pytest_diff", "gate:precommit_changed"}


def findings() -> list[friction.Finding]:
    rows = fx.recurring(3, 6) + fx.repeat_run(3, 6) + [fx.generic_row(9, 100)]
    return friction.detect(rows, date(2026, 11, 14), date(2026, 11, 20)).findings


def drafts() -> list[dict[str, Any]]:
    return [proposals.draft(f, WINDOW, VERSIONS) for f in findings()]


def test_every_draft_passes_file_recs_pre_write_sequence() -> None:
    assert len(drafts()) == 3
    for d in drafts():
        fields = dict(d["fields"])
        _derive_computed_fields(fields)
        validate_source(fields["source"])
        gate_write("ops_recommendations", fields, insert=True)
        context = d["context_v2_json"]
        assert json.loads(json.dumps(context)) == context
        merged = {**fields, "context_v2_json": json.dumps(context), "date": "2026-11-21"}
        Recommendation.model_validate({**merged, "id": "rec-0"})


def test_acceptance_is_decidable_discriminating_and_carries_the_version() -> None:
    for d in drafts():
        acceptance = d["fields"]["acceptance"]
        ok, reason = lint_acceptance_command(acceptance, require_discrimination=True, require_decidable=True)[:2]
        assert ok, (acceptance, reason)
        assert f"--classifier-version {fx.VERSION}" in acceptance


def test_context_v2_json_keys_and_the_evidence_not_proof_sentence() -> None:
    for d in drafts():
        context = d["context_v2_json"]
        for key in ("detector_id", "subject_key", "classifier_version", "detector_version", "window_from", "window_to"):
            assert context[key] is not None
        assert context["probe_role"] == "evidence_not_proof"
        assert "evidence the friction stopped, not proof" in d["fields"]["context"]


def test_a_subject_with_a_quote_or_space_is_shell_quoted() -> None:
    finding = friction.Finding(friction.RECURRING, "r01|it's a b", {"signature": "x"})
    command = proposals._command(finding, 3)
    assert shlex.split(command)[-3] == "r01|it's a b"


def test_file_lookup_for_each_signature_kind() -> None:
    assert proposals.source_file("hook:never_on_main") == ".claude/hooks/never_on_main.py"
    assert proposals.source_file("hook:no_such_hook_zq") == proposals.RULES_FILE
    assert proposals.source_file("gate:validate_cc_limits") == "scripts/checks/sloc/cc_limits.py"
    assert proposals.source_file("gate:lint") == proposals.RULES_FILE
    assert proposals.source_file("precommit:detect-secrets") == ".pre-commit-config.yaml"
    assert proposals.source_file(None) == proposals.RULES_FILE


def test_every_gate_rule_resolves_to_its_module_or_the_scaffold_fallback() -> None:
    for rule in FRICTION_RULES["rules"]:
        signature = rule[2]
        if signature is None or not signature.startswith("gate:"):
            continue
        path = proposals.source_file(signature)
        if signature in SCAFFOLD:
            assert path == proposals.RULES_FILE, signature
        else:
            assert path != proposals.RULES_FILE and (ROOT / path).is_file(), signature


def test_a_draft_holds_no_planted_value_and_the_opaque_subject_hides_the_tool() -> None:
    for d in drafts():
        text = json.dumps(d)
        for planted in (fx.PLANTED_TOOL, fx.PLANTED_SESSION, fx.PLANTED_ROOT, fx.PLANTED_BRANCH):
            assert planted not in text
    opaque = [f for f in findings() if f.subject_key.startswith("run|")]
    assert len(opaque) == 1 and opaque[0].subject_key.startswith("run|sha256:")


def test_the_source_literal_is_written_once_and_friction_reads_it() -> None:
    text = (ROOT / "scripts" / "feedback_loop" / "proposals.py").read_text(encoding="utf-8")
    assert text.count('"source": "feedback_loop"') == 1
    assert proposals.SOURCE == "feedback_loop"
    assert "from scripts.feedback_loop import friction" not in text.split("TYPE_CHECKING")[0]
