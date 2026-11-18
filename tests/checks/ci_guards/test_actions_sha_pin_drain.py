"""rec-5288 and rec-5289 drains: no floating uses: reference remains outside, then inside, CODEOWNERS-covered paths."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

from scripts.checks import _common
from scripts.checks.ci_guards.validate_actions_sha_pinned import scan

_BASELINE = "config/actions_sha_pin_baseline.yaml"


def _codeowners_patterns(root: Path) -> list[str]:
    lines = (root / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines()
    return [line.split()[0].lstrip("/") for line in (s.strip() for s in lines) if line and not line.startswith("#")]


def _covered(path: str, patterns: list[str]) -> bool:
    return any(path.startswith(p) if p.endswith("/") else fnmatch.fnmatch(path, p) for p in patterns)


def test_no_baseline_entry_outside_codeowners_paths() -> None:
    root = Path(_common.ROOT)
    patterns = _codeowners_patterns(root)
    live, unreadable, _ = scan(root)
    outside = sorted(k for k in live if not _covered(k.split("::", 1)[0], patterns))
    assert not outside and not unreadable, (outside, unreadable)


def test_baseline_is_empty() -> None:
    root = Path(_common.ROOT)
    path = root / _BASELINE
    if path.exists():
        drains = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("drains") or {}
        assert not any(bucket for bucket in drains.values()), drains
    live, unreadable, _ = scan(root)
    assert not live and not unreadable, (sorted(live), unreadable)
