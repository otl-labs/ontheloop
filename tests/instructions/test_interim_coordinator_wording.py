"""rec-4479: the overseer wording says the coordinator, not the overseer, dispatches each planning or implementation agent."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _agents_sentence() -> str:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    match = re.search(r"`/overseer` is being retired.*?\(Decision 229\s+cl\.1-2\)", text, re.DOTALL)
    assert match is not None
    return _norm(match.group(0))


def _skill_guardrail() -> str:
    text = (ROOT / ".claude/skills/overseer/SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"## Decision Guardrails\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert match is not None
    return _norm(match.group(1).split("- **Decision 55/72:**")[0])


def test_overseer_wording_names_interim_coordinator_runs() -> None:
    for text in (_agents_sentence(), _skill_guardrail()):
        assert "starts no planning or implementation agent of its own" in text
        assert "the coordinator dispatches each" in text
        assert "one human-gated persona step on an operator-chosen rec" in text
        assert "never the rec" in text and "queue, hub and spoke" in text
        assert not re.search(r"overseer (composes|dispatches)", text)
        assert "composes /plan" not in text
    assert "229" in _agents_sentence() and "229" in _skill_guardrail()
