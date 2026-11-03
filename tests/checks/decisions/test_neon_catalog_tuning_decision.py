"""Decision 214 (the DuckLake catalog stays on Neon, tuned now): entry, envelope, clauses, reversal ids, annotations.

N is resolved from the contract's verb-entry citation, never hard-coded, so a renumber at rebase keeps one source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_REVERSAL_IDS = (
    "capped-bill-exceeds-rds",
    "wall-p95-over-budget-after-tuning",
    "neon-tier-or-terms-change",
    "private-network-required",
    "file-catalog-conditions-met",
)


def _decision_number() -> int:
    contract = yaml.safe_load((_REPO / "docs/contracts/ducklake_maintenance.yaml").read_text(encoding="utf-8"))
    ref = contract["verbs"]["ensure_catalog_indexes"]["payload_schema_ref"]
    match = re.search(r"Decision (\d+), amending Decision 88 invariant v", " ".join(ref.split()))
    assert match is not None, "the verb entry must cite '(Decision <N>, amending Decision 88 invariant v)'"
    return int(match.group(1))


def test_decision_entry_and_annotations():
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = decision_entry(text, n)
    assert authored_bytes(entry) <= cap_bytes()

    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == n and envelope["amends"] == [107, 88]
    assert envelope["significance"]["value"] == "numbered_decision"

    clauses = re.findall(r"^(\d)\. ", entry.split("**Decision:**", 1)[1].split("**Rationale:**", 1)[0], re.MULTILINE)
    assert clauses == ["1", "2", "3", "4", "5"]
    for token in ("rec-4223", "rec-4220", "ensure_catalog_indexes", "StatsIndexValid", "2,000 ms", "3 s", "2c"):
        assert token in entry, token

    stanza = re.search(r"```yaml reversal-conditions\n(.*?)\n```", entry, re.DOTALL)
    assert stanza is not None
    conditions = yaml.safe_load(stanza.group(1))
    assert conditions["decision"] == n and [c["id"] for c in conditions["conditions"]] == list(_REVERSAL_IDS)

    for amended in (107, 88):
        body = decision_entry(text, amended)
        assert re.search(rf"^\[Amendment \d{{4}}-\d{{2}}-\d{{2}}, Decision {n}:", body, re.MULTILINE), amended
