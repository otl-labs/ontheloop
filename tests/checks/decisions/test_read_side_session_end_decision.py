"""The read-side session-end Decision (amends 199): entry, envelope, clauses, reversal ids, annotation on Decision 199.

N is resolved from the sessions contract's state citation, never hard-coded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_REVERSAL_IDS = ("resume-after-threshold", "turn-evidence-lost", "upstream-session-end")


def _decision_number() -> int:
    contract = yaml.safe_load((_REPO / "docs/contracts/telemetry_sessions.yaml").read_text(encoding="utf-8"))
    text = " ".join(contract["fields"]["state"]["semantics"].split())
    match = re.search(r"Decision (\d+) \(PLAN-telemetry-read-side-session-end\)", text)
    assert match is not None
    return int(match.group(1))


def test_decision_entry_and_annotation() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = decision_entry(text, n)
    assert authored_bytes(entry) <= cap_bytes()

    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == n and envelope["amends"] == [199] and envelope["status"] == "Decided"
    assert envelope["significance"]["value"] == "numbered_decision"

    decision = entry.split("**Decision:**", 1)[1].split("**Rationale:**", 1)[0]
    assert re.findall(r"^(\d)\. ", decision, re.MULTILINE) == ["1", "2", "3", "4"]
    assert re.search(r"^\*\*Coverage \(Decision 181 cl\.2\):\*\*", entry, re.MULTILINE)

    stanza = re.search(r"```yaml reversal-conditions\n(.*?)\n```", entry, re.DOTALL)
    assert stanza is not None
    conditions = yaml.safe_load(stanza.group(1))
    assert conditions["decision"] == n and [c["id"] for c in conditions["conditions"]] == list(_REVERSAL_IDS)
    assert entry.rstrip().endswith("---")

    body = decision_entry(text, 199)
    assert body.rstrip().endswith("---")
    annotations = [ln for ln in body.splitlines() if ln.startswith("[Amendment ")]
    assert any(f"Decision {n}:" in ln for ln in annotations)
