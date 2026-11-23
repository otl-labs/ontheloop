"""rec-5526: guard input check (5c) and consequence-based blocking in both critic lanes."""

import hashlib
import re
from pathlib import Path

import yaml

_SKILL = Path(".claude/skills/plan-critique/SKILL.md")
_PERSONA = Path("docs/contracts/personas/plan_critic.yaml")
_PHASE_2B_SHA256 = "9562fd9ed462b95b5d3cc2ba9f7fd25ece3c0b2004d1b493bfc91b129ea60c35"  # pragma: allowlist secret


def _norm(text: str) -> str:
    return " ".join(text.split())


def _skill() -> str:
    return _norm(_SKILL.read_text(encoding="utf-8"))


def _contract() -> dict:
    return yaml.safe_load(_PERSONA.read_text(encoding="utf-8"))


def _template() -> str:
    return _norm(_contract()["prompt_template"])


def _both() -> list[tuple[str, str]]:
    return [("skill", _skill()), ("persona", _template())]


def test_5c_guard_trigger_list_and_input_forms_in_both_lanes() -> None:
    for lane, text in _both():
        for trigger in ("a hook", "a `scripts/checks` check" if lane == "skill" else "a scripts/checks check"):
            assert trigger in text, (lane, trigger)
        for phrase in (
            "an IAM or boundary statement",
            "a sandbox",
            "a governance cap",
            "unknown, malformed, substituted, grouped or indirect, a present key with a bad value",
            "the identity and limits it runs under",
            "is blocking",
        ):
            assert phrase in text, (lane, phrase)
    assert "5c. **Guard input check (PCL-01, from round 1):**" in _skill()
    assert "[step 5c]" in _template()
    assert "**Guard Input Check (5c):**" in _skill()


def test_consequence_rule_in_both_lanes() -> None:
    for lane, text in _both():
        assert "what reaches main if left unfixed and which control fails first" in text, lane
        assert "(a) wrong behaviour, a wrong guarantee or a wrong operator-read instruction would reach main" in text
        assert "(b) the build could not finish inside the plan's scope" in text, lane
        assert "(c) it needs an operator decision" in text, lane
        assert "when unsure" not in text, lane
        assert "never a nit" in text, lane


def test_closure_steps_clause_removed_from_both_lanes() -> None:
    assert "closure steps must distinguish" not in _skill()
    assert "closure steps must keep apart" not in _template()


def test_persona_contract_version_and_checks_enum() -> None:
    c = _contract()
    assert c["contract_version"] >= 3
    enum = c["output_schema"]["properties"]["checks"]["items"]["properties"]["check"]["enum"]
    assert "5c" in enum
    assert "12" not in enum


def test_constraint_conflicts_merged_into_item_6() -> None:
    skill = _skill()
    assert "6. **Check for decision and constraint conflicts:**" in skill
    assert not re.search(r"(?m)^12\. ", _SKILL.read_text(encoding="utf-8"))
    template = _template()
    assert "[step 6] Decision and constraint conflicts" in template
    assert "[step 12]" not in template


def test_phase_2b_frame_lock_section_is_byte_identical() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    section = text[text.index("### Phase 2b") : text.index("### Phase 3")]
    assert hashlib.sha256(section.encode("utf-8")).hexdigest() == _PHASE_2B_SHA256
