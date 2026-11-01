"""The telemetry column-additions Decision (amends 216): entry, envelope, clauses, reversal ids, annotations.

N is resolved from the maintenance-smoke verb's docstring citation, never hard-coded, so a renumber keeps one source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_REVERSAL_IDS = ("ducklake-alter-breaks-table", "change-beyond-additive", "production-additive-path")


def _decision_number() -> int:
    docstring = (_REPO / "src/lambdas/ducklake_maintenance_smoke/telemetry_actions.py").read_text(encoding="utf-8")
    match = re.search(r"\(Decision (\d+), amending Decision 216 clause 2\)", " ".join(docstring.split()))
    assert match is not None, "the verb docstring must cite '(Decision <N>, amending Decision 216 clause 2)'"
    return int(match.group(1))


def test_decision_entry_and_annotations() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = decision_entry(text, n)
    assert authored_bytes(entry) <= cap_bytes()

    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == n and envelope["amends"] == [216] and envelope["status"] == "Decided"
    assert envelope["significance"]["value"] == "numbered_decision"

    decision = entry.split("**Decision:**", 1)[1].split("**Rationale:**", 1)[0]
    assert re.findall(r"^(\d)\. ", decision, re.MULTILINE) == ["1", "2", "3"]
    assert re.search(r"^\*\*Coverage \(Decision 181 cl\.2\):\*\*", entry, re.MULTILINE)
    for token in ("create_telemetry_tables", "ADD COLUMN", "rec-4071", "rec-4316"):
        assert token in entry, token

    stanza = re.search(r"```yaml reversal-conditions\n(.*?)\n```", entry, re.DOTALL)
    assert stanza is not None
    conditions = yaml.safe_load(stanza.group(1))
    assert conditions["decision"] == n and [c["id"] for c in conditions["conditions"]] == list(_REVERSAL_IDS)
    assert entry.rstrip().endswith("---")

    for amended in (216, 143):
        body = decision_entry(text, amended)
        assert re.search(rf"^\[Amendment \d{{4}}-\d{{2}}-\d{{2}}, Decision {n}:", body, re.MULTILINE), amended
        assert body.rstrip().endswith("---")
        if amended == 143:
            annotation = body.rstrip().removesuffix("---").rstrip().splitlines()[-1]
            assert annotation.startswith("[Amendment ") and f"Decision {n}:" in annotation, amended
