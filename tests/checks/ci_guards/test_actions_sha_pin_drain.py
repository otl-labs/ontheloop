"""rec-5288 drain: no floating uses: reference outside CODEOWNERS-covered paths remains."""

from __future__ import annotations

from pathlib import Path

from scripts.checks import _common
from scripts.checks.ci_guards.validate_actions_sha_pinned import BASELINE_REL, parse_baseline, render_baseline


def test_no_baseline_entry_outside_codeowners_paths() -> None:
    root = Path(_common.ROOT)
    _, shipped, errors = parse_baseline((root / BASELINE_REL).read_text(encoding="utf-8"))
    assert not errors, errors
    assert "rec-5288" not in set(shipped.values()), sorted(k for k, v in shipped.items() if v == "rec-5288")
    _, live, errors = parse_baseline(render_baseline(root))
    assert not errors, errors
    assert "rec-5288" not in set(live.values()), sorted(k for k, v in live.items() if v == "rec-5288")
