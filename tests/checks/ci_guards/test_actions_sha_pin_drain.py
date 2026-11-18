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


def test_baseline_is_empty() -> None:
    import yaml

    from scripts.checks.ci_guards.validate_actions_sha_pinned import scan

    root = Path(_common.ROOT)
    live, unreadable, _ = scan(root)
    assert not live, dict(live)
    assert not unreadable, unreadable
    baseline = root / BASELINE_REL
    if baseline.is_file():
        drains = (yaml.safe_load(baseline.read_text(encoding="utf-8")) or {}).get("drains") or {}
        assert not any(drains.values()), {k: sorted(v) for k, v in drains.items() if v}
