"""Rec hygiene rules (PLAN-silent-failure-prevention): authored in the contracts, served through get_rec_write_guidance.

T2.29 c7: when get_rec_write_guidance moves to reading the contract, the c7 implementer carries these sentences into what it
reads. Write-time refusal of either rule is rec-4603's.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.executor.rec_write_guidance import get_rec_write_guidance

FILE_RULE = (
    "file names the surface the acceptance probe checks: the probe's verdict depends on that file's content, directly or "
    "through a test that exercises it, so file-keyed discovery (recs_related) returns the rec. A probe whose verdict never "
    "depends on file means the rec is mis-keyed."
)
CONTEXT_RULE = (
    "Never append unrelated work to an existing rec (a rider): file a new rec and cite the first in its context; use "
    "dependencies only when the new work cannot start before the first is done. A context change through update_rec "
    "refines the rec's own problem only."
)
LEDGER_RULE = (
    "Naming an EXISTING rec defers to it only when that rec's acceptance fails while the deferred work is undone; "
    "otherwise file a new follow-on rec (no riders, ops_recommendations.yaml context)."
)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _contract_field(name: str) -> dict:
    data = yaml.safe_load(Path("docs/contracts/ops_recommendations.yaml").read_text(encoding="utf-8"))
    return data["fields"][name]


def test_file_rule_is_authored_and_served() -> None:
    assert FILE_RULE in _norm(_contract_field("file")["governance_notes"])
    assert FILE_RULE in _norm(get_rec_write_guidance()["file"]["semantics"])


def test_context_rule_is_authored_and_served() -> None:
    assert CONTEXT_RULE in _norm(_contract_field("context")["governance_notes"])
    assert CONTEXT_RULE in _norm(get_rec_write_guidance()["context"]["semantics"])


def test_ledger_rule_is_authored() -> None:
    ledger = yaml.safe_load(Path("docs/contracts/exit-criteria-ledger.yaml").read_text(encoding="utf-8"))
    notes = _norm(_find(ledger, "plan_followon_recs")["governance_notes"])
    assert LEDGER_RULE in notes


def _find(node: object, key: str) -> dict:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            try:
                return _find(value, key)
            except KeyError:
                continue
    raise KeyError(key)
