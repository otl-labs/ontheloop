"""The /secret/ SSM parameters Decision (amends 101, 213, 230): entry, envelope, clauses, reversal ids, annotations.

N is resolved from validate_secret_parameter_grants.py's docstring citation, never hard-coded, so a renumber keeps one source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_REVERSAL_IDS = ("managed-rotation-wanted", "pattern-grant-needed", "second-gate-wanted")
_TOKENS = ("/secret/", "SecureString", "SecretParameterAdmin", "aws/ssm", "Decision 181 cl.2", "GetParametersByPath")


def _decision_number() -> int:
    docstring = (_REPO / "scripts/checks/iam_tf/validate_secret_parameter_grants.py").read_text(encoding="utf-8")
    match = re.search(r"\(Decision (\d+), secret parameters\)", " ".join(docstring.split()))
    assert match is not None, "the module docstring must cite '(Decision <N>, secret parameters)'"
    return int(match.group(1))


def test_decision_entry_and_annotations() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = decision_entry(text, n)
    assert authored_bytes(entry) <= cap_bytes()

    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == n and envelope["amends"] == [101, 213, 230] and envelope["status"] == "Decided"
    assert envelope["significance"]["value"] == "numbered_decision"

    decision = entry.split("**Decision:**", 1)[1].split("**Rationale:**", 1)[0]
    assert re.findall(r"^(\d)\. ", decision, re.MULTILINE) == ["1", "2", "3", "4", "5", "6"]
    assert re.search(r"^\*\*Coverage \(Decision 181 cl\.2\):\*\*", entry, re.MULTILINE)
    for token in _TOKENS:
        assert token in entry, token

    stanza = re.search(r"```yaml reversal-conditions\n(.*?)\n```", entry, re.DOTALL)
    assert stanza is not None
    conditions = yaml.safe_load(stanza.group(1))
    assert conditions["decision"] == n and [c["id"] for c in conditions["conditions"]] == list(_REVERSAL_IDS)
    assert entry.rstrip().endswith("---")

    for amended in (101, 213, 230):
        body = decision_entry(text, amended)
        assert re.search(rf"^\[Amendment \d{{4}}-\d{{2}}-\d{{2}}, Decision {n}:", body, re.MULTILINE), amended
        assert body.rstrip().endswith("---")
