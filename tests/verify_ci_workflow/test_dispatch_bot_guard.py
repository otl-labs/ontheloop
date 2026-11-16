"""Deny-by-default refusal-guard classification (Decision 267 clause 3, PLAN-scheduled-workflow-dispatch PR 2).

Every workflow declaring workflow_dispatch is either a register workflow (the scheduled workflow dispatcher may start
it) or refuses a workflow_dispatch run whose github.actor is the dispatcher App's bot on each root job and each job
whose if calls a status function. Register workflows keep their cron, never name the bot, and stay deterministic
(Decision 205 cl.1).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / ".github" / "workflows"
_REGISTER = yaml.safe_load((_REPO / "config/lambda/scheduled_workflow_dispatcher/schedules.yaml").read_text(encoding="utf-8"))
BOT = _REGISTER["app"]["bot_login"]
REGISTER_WORKFLOWS = {row["workflow"] for row in _REGISTER["schedules"].values()}
_STATUS = re.compile(r"\b(always|cancelled|failure|success)\(\)")
_LLM_RUN = re.compile(r"\bclaude\s+(-p|--print)\b|scripts\.llm|run_persona|litellm")
_LLM_SECRET = re.compile(r"secrets\.[A-Za-z0-9_]*(CLAUDE|ANTHROPIC|DEEPSEEK|OPENAI)", re.IGNORECASE)


def guard_expr(bot: str) -> str:
    return f"(github.event_name != 'workflow_dispatch' || github.actor != '{bot}')"


def _load(text: str) -> dict[str, Any]:
    return yaml.load(text, Loader=yaml.BaseLoader)


def _triggers(doc: dict[str, Any]) -> set[str]:
    on = doc.get("on")
    if isinstance(on, str):
        return {on}
    return set(on or [])


def _normalise(expr: str) -> str:
    text = " ".join(expr.split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text


def _closes_at_end(text: str, open_index: int) -> bool:
    depth = 0
    quoted = False
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def guard_problems(doc: dict[str, Any], bot: str) -> list[str]:
    guard = guard_expr(bot)
    problems = []
    for name, job in (doc.get("jobs") or {}).items():
        cond = job.get("if")
        if job.get("needs") and not (cond and _STATUS.search(cond)):
            continue
        text = _normalise(cond or "")
        if text != guard and not (text.startswith(guard + " && (") and _closes_at_end(text, len(guard) + 4)):
            problems.append(name)
    return problems


def register_problems(doc: dict[str, Any], text: str, bot: str) -> list[str]:
    problems = []
    if bot in text or bot.removesuffix("[bot]") in text:
        problems.append("names the bot")
    triggers = _triggers(doc)
    if "workflow_dispatch" not in triggers:
        problems.append("no workflow_dispatch")
    if "schedule" not in triggers:
        problems.append("no on.schedule")
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "claude-code-action" in str(step.get("uses", "")):
                problems.append("claude-code-action")
            if _LLM_RUN.search(str(step.get("run", ""))):
                problems.append("llm run")
            if _LLM_SECRET.search(str(step.get("env", "")) + str(step.get("with", ""))):
                problems.append("llm secret")
    return problems


def _dispatchable() -> list[Path]:
    return [p for p in sorted(_WORKFLOWS.glob("*.yml")) if "workflow_dispatch" in _triggers(_load(p.read_text()))]


@pytest.mark.parametrize("path", _dispatchable(), ids=lambda p: p.name)
def test_dispatchable_workflow_is_registered_or_guarded(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    doc = _load(text)
    if path.name in REGISTER_WORKFLOWS:
        assert register_problems(doc, text, BOT) == []
    else:
        assert guard_problems(doc, BOT) == []


def test_every_register_row_names_an_existing_workflow() -> None:
    for workflow in REGISTER_WORKFLOWS:
        assert (_WORKFLOWS / workflow).is_file(), workflow


_GUARD = guard_expr(BOT)


@pytest.mark.parametrize(
    "jobs",
    [
        "  a:\n    runs-on: x\n",
        f"  a:\n    if: {_GUARD}\n    runs-on: x\n  b:\n    needs: a\n    if: always()\n",
        "  a:\n    if: (github.event_name != 'workflow_dispatch' || github.actor != 'someone[bot]')\n",
        f"  a:\n    if: (github.ref == 'refs/heads/main') && {_GUARD}\n",
        f"  a:\n    if: github.actor != '{BOT}'\n",
        f"  a:\n    if: {_GUARD} && (github.ref == 'refs/heads/main') || (github.event_name == 'workflow_dispatch')\n",
        f"  a:\n    if: {_GUARD} && (github.ref == 'x'\n",
    ],
    ids=["unguarded", "always-job-unguarded", "other-login", "wrong-position", "actor-only", "or-escape", "unclosed"],
)
def test_guard_red_cases(jobs: str) -> None:
    doc = _load(f"on:\n  workflow_dispatch:\njobs:\n{jobs}")
    assert guard_problems(doc, BOT)


def test_guard_green_cases() -> None:
    jobs = f"  a:\n    if: {_GUARD}\n  b:\n    needs: a\n    if: ${{{{ {_GUARD} && (always()) }}}}\n  c:\n    needs: a\n"
    assert guard_problems(_load(f"on:\n  workflow_dispatch:\njobs:\n{jobs}"), BOT) == []


_REG_BASE = "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 * * * *'\njobs:\n  a:\n    steps:\n"


@pytest.mark.parametrize(
    "text",
    [
        _REG_BASE + f"      - run: echo {BOT}\n",
        "on:\n  workflow_dispatch:\njobs:\n  a:\n    steps:\n      - run: echo hi\n",
        _REG_BASE + "      - run: claude -p hello\n",
        _REG_BASE + "      - uses: anthropics/claude-code-action@v1\n",
        _REG_BASE + "      - run: x\n        env:\n          K: ${{ secrets.ANTHROPIC_API_KEY }}\n",
    ],
    ids=["names-bot", "no-schedule", "claude-print", "claude-action", "anthropic-secret"],
)
def test_register_red_cases(text: str) -> None:
    assert register_problems(_load(text), text, BOT)


def test_register_green_case() -> None:
    text = _REG_BASE + "      - run: echo hi\n"
    assert register_problems(_load(text), text, BOT) == []
