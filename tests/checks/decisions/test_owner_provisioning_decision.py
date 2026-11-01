"""Decision 216 (one owner per DuckLake metadata schema): entry, envelope, clauses, reversal ids, annotations.

N is resolved from the maintenance-smoke handler docstring citation, never hard-coded, so a renumber keeps one source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_REVERSAL_IDS = ("owner-identity-changes", "ducklake-ddl-on-dml-path", "telemetry-schema-churn", "production-ci-ddl-mechanism")


def _decision_number() -> int:
    docstring = (_REPO / "src/lambdas/ducklake_maintenance_smoke/handler.py").read_text(encoding="utf-8")
    match = re.search(r"\(Decision (\d+), amending Decision 143 clause 2\)", " ".join(docstring.split()))
    assert match is not None, "the handler docstring must cite '(Decision <N>, amending Decision 143 clause 2)'"
    return int(match.group(1))


def _entry(text: str, number: int) -> str:
    match = re.search(rf"^## Decision {number}:.*?(?=^## Decision \d+:)", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"Decision {number} not found"
    return match.group(0)


def test_decision_entry_and_annotations() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = _entry(text, n)

    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == n and envelope["amends"] == [143, 213] and envelope["status"] == "Decided"
    assert envelope["significance"]["value"] == "numbered_decision"

    decision = entry.split("**Decision:**", 1)[1].split("**Rationale:**", 1)[0]
    assert re.findall(r"^(\d)\. ", decision, re.MULTILINE) == ["1", "2", "3", "4"]
    assert re.search(r"^\*\*Coverage \(Decision 181 cl\.2\):\*\*", entry, re.MULTILINE)
    for token in ("create_telemetry_tables", "scoped-login-blocks-verbs", "rec-4292", "rec-4316", "rec-4297", "rec-4071"):
        assert token in entry, token

    stanza = re.search(r"```yaml reversal-conditions\n(.*?)\n```", entry, re.DOTALL)
    assert stanza is not None
    conditions = yaml.safe_load(stanza.group(1))
    assert conditions["decision"] == n and [c["id"] for c in conditions["conditions"]] == list(_REVERSAL_IDS)
    assert entry.rstrip().endswith("---")

    for amended in (143, 213):
        body = _entry(text, amended)
        assert re.search(rf"^\[Amendment \d{{4}}-\d{{2}}-\d{{2}}, Decision {n}:", body, re.MULTILINE), amended
        assert body.rstrip().endswith("---")
