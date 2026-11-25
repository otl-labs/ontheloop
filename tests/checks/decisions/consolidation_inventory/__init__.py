"""Mirror test package; also holds the fixture repository the tests share (Decision numbers 9000 and up)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REAL_ROOT = Path(__file__).resolve().parents[4]

DECISIONS = """# Decisions

## Decision 9003: Live amender of the first victim (amends Decision 9001) (Decided)

```yaml
number: 9003
status: Decided
decided_date: "2026-01-03"
kind:
  value: direction
  justification: fixture
amends: [9001]
```

**Status:** Decided
**Date:** 2026-01-03

**Decision:** The amender cites Decision 9001 point 2 and Decision 9002 here.

---

## Decision 9002: Second victim (Decided)

**Status:** Decided
**Date:** 2026-01-02

**Decision:** The pins ZZZ-BODY-PIN and YYY-BODY-PIN live here.

---

## Decision 9001: First victim (Decided)

```yaml
number: 9001
status: Decided
decided_date: "2026-01-01"
kind:
  value: direction
  justification: fixture
delegates:
  - key: docs/contracts/thing.yaml#knob
    change_rule: contract_amendment
```

**Status:** Decided
**Date:** 2026-01-01

**Decision:** First victim body.
"""

PIN_TESTS = """import re

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs" / "DECISIONS.md"
PINS = ("ZZZ-BODY-PIN", "YYY-BODY-PIN")


def _section(number):
    return LOG.read_text(encoding="utf-8").split(f"## Decision {number}:")[1].split("## Decision")[0]


def test_body_pin():
    assert "ZZZ-BODY-PIN" in _section(9002)


@pytest.mark.parametrize("pin", PINS)
def test_parametrized_body_pin(pin):
    assert pin in _section(9002)


def test_regex_entry_pin():
    match = re.search(r"^## Decision 9002:.*?(?=^---$)", LOG.read_text(encoding="utf-8"), re.M | re.S)
    assert match and "ZZZ-BODY-PIN" in match.group(0)


def test_number_pin():
    assert "## Decision 9002:" in LOG.read_text(encoding="utf-8")


def test_red_before():
    assert "## Decision 9001:" in LOG.read_text(encoding="utf-8")
    assert len(LOG.read_text(encoding="utf-8")) < 0


class TestInClass:
    def test_method_pin(self):
        assert "ZZZ-BODY-PIN" in _section(9002)


def test_plain_number():
    assert 9001 > 0
"""

UNRELATED_TESTS = """def test_unrelated():
    assert 9001 == 9001
"""

NORTH_STAR = """contract:
  ratified_via: "Decision 8000"
mission:
  carried_by: [9001, 5]
semantics:
  carried_by: >-
    The numbered Decisions whose clauses carry the entry (Decision 9002 is named here as prose only).
principles:
  - id: NS.9
    carried_by: [9002]
"""

FILES = {
    "tests/__init__.py": "",
    "tests/test_pin.py": PIN_TESTS,
    "tests/test_unrelated.py": UNRELATED_TESTS,
    "docs/DECISIONS.md": DECISIONS,
    "docs/decisions-index.json": json.dumps(
        {
            "decisions": [
                {"number": 9003, "live": True, "amends": [9001], "supersedes": [], "currency": "current"},
                {"number": 9002, "live": True, "amends": [], "supersedes": [], "currency": "current"},
                {"number": 9001, "live": True, "amends": [], "supersedes": [], "currency": "current"},
            ]
        }
    ),
    "docs/contracts/north-star.yaml": NORTH_STAR,
    "docs/contracts/glossary.yaml": "read_as:\n  noted_decisions: [9001, 9002, 5]\n",
    "docs/contracts/other.yaml": "decisions_cited: [9001, 5]\nnote: Decision 9002 cl.3 is cited here\n",
    "docs/contracts/approval-routing.yaml": (
        "gated:\n  files:\n    - docs/DECISIONS.md\n    - '**/CLAUDE.md'\n  yaml_keys:\n"
        "    docs/contracts/other.yaml: [decisions_cited]\n  md_sections: {}\ndemotions:\n  rows: []\n"
    ),
    "config/decision_supersession_waivers.yaml": "waivers:\n  - {superseder: 9003, victim: 9001, reason: fixture}\n",
    "config/structural_size_budgets.yaml": "tests/test_pin.py: 50\n",
    "config/limits.yaml": "size: 1  # raise-approved: dec-9001 fixture reason\n",
    "audits/standards-cleanup-census.yaml": "rows: []\nexceptions:\n  - {id: S5-9002, class: S5, root: 9002}\n",
    "docs/decision-migration/consolidations/m.yaml": (
        "supersedes: [9001]\ncarried_forward: [9003]\nunits:\n  - {id: '9001 pt1', held: ['Decision 9002']}\n"
    ),
    "docs/ROADMAP-PLATFORM.yaml": (
        "tier_items:\n"
        "  - {id: T9.1, status: planned, exit_criteria: ['cites Decision 9001'], related_decisions: [9002]}\n"
        "  - {id: T9.2, status: complete, exit_criteria: ['cites Decision 9001'], related_decisions: [9001]}\n"
    ),
    "config/agent/verification_registry/entries/pin-record.yaml": (
        "check_id: pin-record\nprimitive_slot: command_exit_zero\nguard_target: docs/DECISIONS.md\nplan_slug: fixture\n"
        "graduated_at: '2026-01-01'\ncheck_spec:\n  command:\n    - bin/venv-python\n    - -c\n    - |\n"
        "      import sys\n      t = open('docs/DECISIONS.md').read()\n"
        "      sys.exit(0 if 'ZZZ-BODY-PIN' in t.split('## Decision 9002:')[1] else 1)\n"
    ),
    "AGENTS.md": "Rule per Decision 9001 point 2.\nAlso Decision 9002, then Decision 9002 again, and dec-009001.\n",
    "scripts/mod.py": '# Decision 9002 in a comment\nNAME = "Decision 9002 cl.4"\n',
}


def build_repo(root: Path, extra: dict[str, str] | None = None, *, with_tools: bool = False) -> Path:
    files = {**FILES, **(extra or {})}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if with_tools:
        (root / "bin").mkdir(exist_ok=True)
        shutil.copy(REAL_ROOT / "bin/venv-python", root / "bin/venv-python")
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts/__init__.py").write_text("", encoding="utf-8")
        for name in ("decisions_md.py", "decisions_index.py"):
            shutil.copy(REAL_ROOT / "scripts" / name, root / "scripts" / name)
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], cwd=root, check=True)
    return root
