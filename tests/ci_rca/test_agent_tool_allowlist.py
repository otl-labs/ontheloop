"""rec-4677: ci-rca.md's Tools Allowed and Read Verbs agree with the workflow step's --allowedTools."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.common.ducklake_named_reads import NAMED_READS

ROOT = Path(__file__).resolve().parents[2]
PROMPT = (ROOT / ".claude" / "agents" / "scheduled" / "ci-rca.md").read_text(encoding="utf-8")
WORKFLOW = ROOT / ".github" / "workflows" / "ci-rca.yml"


def _step_run() -> str:
    for job in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == "Run ci-rca agent":
                return step["run"]
    raise AssertionError("Run ci-rca agent step not found")


def _allowed_tools() -> set[str]:
    m = re.search(r'--allowedTools "([^"]+)"', _step_run())
    assert m
    return {e.strip() for e in m.group(1).split(",")}


def _section(name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", PROMPT, re.S | re.M)
    assert m, name
    return m.group(1)


def _bullet_tokens(section: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^- `([^`]+)`", section, re.M)}


def test_ci_rca_prompt_commands_are_all_permitted_by_allowed_tools() -> None:
    allowed = _allowed_tools()
    assert _bullet_tokens(_section("Tools Allowed")) == allowed

    script = (ROOT / "scripts" / "ci" / "ci_rca_steps.sh").read_text(encoding="utf-8")
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("#")]
    assert not any("enumerate" in ln.lower() for ln in lines)
    bundle_lines = [ln for ln in lines if ln.strip().startswith("EVIDENCE_CONTEXT=") and '$(ls "$BUNDLES"/*.json' in ln]
    assert bundle_lines
    assert 'BUNDLES="${1:-/tmp/ci-rca-bundles}"' in script

    prefixes = [m.group(1) for e in allowed if (m := re.fullmatch(r"Bash\((.*):\*\)", e))]
    for block in re.findall(r"```bash\n(.*?)```", PROMPT, re.S):
        for cmd in re.sub(r"\\\n\s*", " ", block).splitlines():
            cmd = cmd.strip()
            if not cmd or cmd.startswith("#"):
                continue
            assert any(cmd == p or cmd.startswith(p + " ") for p in prefixes), cmd


def test_ci_rca_read_verbs_are_registered_agent_facing() -> None:
    section = _section("Read Verbs")
    verbs = _bullet_tokens(section)
    granted = {e.removeprefix("mcp__ducklake-reads__") for e in _allowed_tools() if e.startswith("mcp__ducklake-reads__")}
    assert verbs and verbs == granted
    for verb in verbs:
        assert verb in NAMED_READS and NAMED_READS[verb].agent_facing
        assert "deprecated" not in NAMED_READS[verb].description.lower()
    for word in ("since_ts", "14 days", "count", "final", "related"):
        assert word in section
    assert re.search(r"\b\d+ read\b", section)
