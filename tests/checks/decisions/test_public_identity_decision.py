"""The public-identity re-decision (supersedes 286; amends 127, 171, 209): entry, lists, stubs and AGENTS.md pointer.

N is resolved from AGENTS.md's confidentiality-boundary line, never hard-coded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import decision_entry, live_block, resolve_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_CONTRACT = "docs/contracts/public-identity.yaml"


def _agents() -> str:
    return (_REPO / "AGENTS.md").read_text(encoding="utf-8")


def _decision_number() -> int:
    match = re.search(r"Confidentiality boundary \(Decision (\d+) cl\.2\)", _agents())
    assert match is not None, "AGENTS.md must name the confidentiality boundary's Decision"
    return int(match.group(1))


def _envelope(entry: str) -> dict:
    return yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))


def _key(doc: dict, dotted: str) -> object:
    for part in dotted.split("."):
        doc = doc[part]
    return doc


def test_entry_supersedes_286_and_delegates_both_lists() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    entry = decision_entry(text, n)
    envelope = _envelope(entry)
    assert envelope["supersedes"] == [286] and envelope["amends"] == [127, 171, 209]
    assert envelope["kind"]["value"] == "direction"
    contract = yaml.safe_load((_REPO / _CONTRACT).read_text(encoding="utf-8"))
    keys = {d["key"]: d["change_rule"] for d in envelope["delegates"]}
    assert keys == {
        f"{_CONTRACT}#confidential.classes": "operator_approval",
        f"{_CONTRACT}#frozen_identifiers.families": "operator_approval",
    }
    for key in keys:
        assert _key(contract, key.split("#", 1)[1]), key
    decision = " ".join(entry.split("**Decision:**", 1)[1].split("**Options considered:**", 1)[0].split())
    assert re.findall(r"(?:^|\s)(\d)\. \*\*", decision) == [str(i) for i in range(1, 9)]
    for phrase in (
        "carries financial or investment reporting",
        "only under its own explicit decision",
        "Operational figures (spending limits",
        "project_id trading-system leaves the freeze",
    ):
        assert phrase in decision, phrase


def test_286_compacts_to_the_new_entry_and_the_amended_entries_carry_notes() -> None:
    n = _decision_number()
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    stub = resolve_entry(text, 286)
    assert stub.compacted and stub.successor == n
    assert resolve_entry(text, 101).successor == n
    for amended in (127, 171, 209):
        block = live_block(text, amended, naming=(n,))
        assert block is None or f"> **Amended by Decision {n} (" in block, amended


def test_freeze_list_is_complete_and_agents_points_at_the_list() -> None:
    contract = yaml.safe_load((_REPO / _CONTRACT).read_text(encoding="utf-8"))
    families = " ".join(f["family"] for f in contract["frozen_identifiers"]["families"])
    for family in (
        "agent-platform-*",
        "/agent-platform/*",
        "platform-security-*",
        "/secret/",
        "bootstrap-apply",
        "github-settings-apply",
        "telemetry-content-reader",
        "PlatformAdmin",
        "ducklake-api-access-logs",
        "OrganizationAdmin",
    ):
        assert family in families, family
    assert "trading-system" not in families
    assert f"`{_CONTRACT}#confidential`" in _agents()
    assert "NEVER publish: AWS account IDs" not in _agents()
