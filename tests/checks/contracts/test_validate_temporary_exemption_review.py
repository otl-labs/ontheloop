"""Mirror test for validate_temporary_exemption_review: fixture-only with an injected clock (it never runs the check on
the real tree, so the real review_by dates cannot reach the --pre pytest_diff)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_temporary_exemption_review as check
from scripts.checks.contracts._manifest import ENTRIES
from scripts.checks.contracts._rule_ratchet import FIELD_SEMANTICS_REL

_TODAY = dt.date(2026, 10, 15)


def _tree(root: Path, exemptions: dict[str, dict[str, Any]]) -> Path:
    contracts = root / "docs" / "contracts"
    contracts.mkdir(parents=True)
    fields = {
        column: {"dq_intent": {"min_length": {"value": 2}, "write_time_exemptions": {"min_length": why}}}
        for column, why in exemptions.items()
    }
    (contracts / "demo.yaml").write_text(
        yaml.safe_dump({"governance": {"table_class": "SCD2"}, "fields": fields}), encoding="utf-8"
    )
    fs = root / FIELD_SEMANTICS_REL
    fs.parent.mkdir(parents=True)
    fs.write_text(yaml.safe_dump({"ops_tables": {"demo": {}}}), encoding="utf-8")
    return root


def _why(review_by: str, **over: Any) -> dict[str, Any]:
    return {"class": "temporary", "reason": "staged", "owner": "rec-77", "review_by": review_by, **over}


def _run(root: Path, today: dt.date = _TODAY) -> list[str]:
    failed: list[str] = []
    check.validate_temporary_exemption_review(failed, root=root, today=today)
    return failed


def test_past_review_by_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"late": _why("2026-10-14"), "fine": _why("2026-10-15"), "future": _why("2027-01-31")})
    failed = _run(root)
    assert len(failed) == 1
    message = failed[0]
    assert "demo.late.min_length" in message and "2026-10-14" in message and "rec-77" in message
    assert "delete the exemption" in message and "governance_note_add" in message


def test_a_future_or_same_day_review_passes_and_a_later_clock_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, {"c": _why("2027-01-31")})
    assert _run(root) == []
    assert "1 temporary exemption(s), none past review_by" in capsys.readouterr().out
    assert any("2027-01-31" in f for f in _run(root, dt.date(2027, 2, 1)))


def test_no_temporary_exemption_is_vacuous_and_other_classes_are_ignored(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"c": {"class": "cross_row", "reason": "r", "owner": "rec-1"}})
    registry.pop_declaration()
    assert _run(root) == []
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.count == 0 and declaration.unit == "temporary_exemptions"


def test_a_malformed_date_is_reported(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"c": _why("soon")})
    assert any("not an ISO date" in f for f in _run(root))


def test_unreadable_inputs_fail_closed(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"c": _why("2027-01-31")})
    (root / "docs" / "contracts" / "demo.yaml").write_text("- not a mapping\n", encoding="utf-8")
    assert any("inputs unreadable" in f for f in _run(root))


def test_default_clock_is_utc_today(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"c": _why("2000-01-01")})
    failed: list[str] = []
    check.validate_temporary_exemption_review(failed, root=root)
    assert any("2000-01-01" in f for f in failed)


def test_registered_full_tier_only() -> None:
    entry = next(e for e in ENTRIES if e.name == "validate_temporary_exemption_review")
    assert (entry.module, entry.attr, entry.pre, entry.full_segment) == (
        "scripts.checks.contracts.validate_temporary_exemption_review",
        "validate_temporary_exemption_review",
        False,
        "full_after_lint",
    )
    assert not entry.pre_globs
