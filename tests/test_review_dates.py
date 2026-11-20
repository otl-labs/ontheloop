"""review_dates: per-date load and the next free review date (rec-5264)."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.preflight import review_dates as rd
from scripts.preflight.decision_conditions import DecisionConditionState

TODAY = date(2026, 11, 18)


def _states(*pairs: tuple[int, str]) -> list[DecisionConditionState]:
    return [DecisionConditionState(decision_id=i, state="not-due", review_by=d) for i, d in pairs]


def test_review_date_load_groups_ids() -> None:
    assert rd.review_date_load(_states((1, "2027-01-01"), (2, "2027-01-01"), (3, "2027-01-02"))) == {
        "2027-01-01": [1, 2],
        "2027-01-02": [3],
    }


def test_next_review_date_skips_a_full_date_and_never_moves_later() -> None:
    full = _states((1, "2027-03-31"), (2, "2027-03-31"), (3, "2027-03-31"), (4, "2027-03-30"))
    assert rd.next_review_date(date(2027, 3, 31), full, TODAY) == date(2027, 3, 30)
    assert rd.next_review_date(date(2027, 3, 31), [], TODAY) == date(2027, 3, 31)


def test_target_not_after_today_is_refused() -> None:
    with pytest.raises(ValueError):
        rd.next_review_date(TODAY, [], TODAY)


def test_every_date_full_is_refused() -> None:
    full = _states(*[(i, "2026-11-19") for i in range(3)])
    with pytest.raises(ValueError, match="already carries"):
        rd.next_review_date(date(2026, 11, 19), full, TODAY)


def test_cli_prints_next_date(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rd, "evaluate", lambda gate=False: [])
    assert rd._cli(["--next", "2999-01-01"]) == 0
    assert capsys.readouterr().out.strip() == "2999-01-01"
    assert rd._cli(["--next", "2000-01-01"]) == 1
    assert rd._cli([]) == 2
