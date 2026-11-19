"""Review-date spread for reversal-condition stanzas (rec-5264, Decision 128 size relief for decision_conditions)."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from scripts.preflight.decision_conditions import DecisionConditionState, evaluate

REVIEW_DATE_TARGET = 3
REVIEW_DATE_CEILING = 10


def review_date_load(results: Iterable[DecisionConditionState]) -> dict[str, list[int]]:
    """Map each review_by date to the decision ids whose stanza carries it."""
    load: dict[str, list[int]] = defaultdict(list)
    for r in results:
        if r.review_by:
            load[r.review_by].append(r.decision_id)
    return dict(load)


def next_review_date(target: date, results: Iterable[DecisionConditionState], today: date) -> date:
    """Latest date on or before target and after today carrying fewer than REVIEW_DATE_TARGET stanzas."""
    if target <= today:
        raise ValueError(f"target {target.isoformat()} is not after today {today.isoformat()}")
    load = review_date_load(results)
    day = target
    while day > today:
        if len(load.get(day.isoformat(), [])) < REVIEW_DATE_TARGET:
            return day
        day -= timedelta(days=1)
    raise ValueError(f"every date in ({today.isoformat()}, {target.isoformat()}] already carries {REVIEW_DATE_TARGET} stanzas")


def _cli(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "--next":
        print("usage: review_dates --next YYYY-MM-DD", file=sys.stderr)
        return 2
    today = datetime.now(timezone.utc).date()
    try:
        print(next_review_date(date.fromisoformat(args[1]), evaluate(gate=True), today).isoformat())
    except ValueError as exc:
        print(f"review_dates: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
