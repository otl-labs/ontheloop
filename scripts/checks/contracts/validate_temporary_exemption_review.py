"""Review-date check for temporary write-time exemptions (rec-4158; Decision 210 cl.3, Decision 163 update).

A `class: temporary` exemption carries a review_by date and an owner rec. When the date passes the exemption is stale:
this check fails the FULL tier only (no --pre membership), so no PR's own merge gate depends on the calendar. A lapsed date
is meant to be loud -- it reddens main's post-merge full tier (ci-rca files a critical rec) and every implementing session's
pre-handoff full tier until the owner acts. The remedy is one small PR: delete the exemption with its rule work done, or
renew review_by with a field amendment entry (governance_note_add) naming the new date and why.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from scripts import contract_rules
from scripts.checks import _common, registry
from scripts.checks.contracts import _rule_ratchet


def temporary_exemptions(root: Path) -> list[tuple[str, str, str, dict[str, Any]]]:
    contracts = root / "docs" / "contracts"
    found: list[tuple[str, str, str, dict[str, Any]]] = []
    for table in _rule_ratchet.in_scope_tables(root):
        intents = contract_rules.read_intents(
            table, {}, contracts_dir=contracts, repo_root=root, registry_key_ref=contract_rules.REGISTRY_KEY_REF
        )
        for column, intent in intents.items():
            for kind, why in (intent.get("write_time_exemptions") or {}).items():
                if isinstance(why, dict) and why.get("class") == "temporary":
                    found.append((table, column, kind, why))
    return found


def lapsed_errors(root: Path, today: dt.date) -> tuple[list[str], int]:
    errors: list[str] = []
    found = temporary_exemptions(root)
    for table, column, kind, why in found:
        try:
            due = dt.date.fromisoformat(str(why.get("review_by")))
        except ValueError:
            errors.append(
                f"{table}.{column}.{kind}: temporary exemption review_by {why.get('review_by')!r} is not an ISO date"
            )
            continue
        if due < today:
            errors.append(
                f"{table}.{column}.{kind}: temporary exemption review_by {due} has passed (owner {why.get('owner')}); "
                "delete the exemption with its rule work done, or renew review_by with a governance_note_add field "
                "entry naming the new date and why"
            )
    return errors, len(found)


@registry.register("validate_temporary_exemption_review", owner="platform")
def validate_temporary_exemption_review(failed: list[str], *, root: Path | None = None, today: dt.date | None = None) -> None:
    """Fail when a temporary write-time exemption's review_by date is in the past (UTC)."""
    print("\n=== Temporary exemption review ===")
    root = root if root is not None else _common.ROOT
    today = today if today is not None else dt.datetime.now(dt.timezone.utc).date()
    try:
        errors, examined = lapsed_errors(root, today)
    except (OSError, ValueError) as exc:
        errors, examined = [f"inputs unreadable: {exc}"], 0
    registry.examined(examined, unit="temporary_exemptions")
    for err in errors:
        print(f"  FAIL: {err}")
        failed.append(f"Temporary exemption review: {err}")
    if not errors:
        print(f"  PASS: {examined} temporary exemption(s), none past review_by.")
