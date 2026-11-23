"""Mirror test for scripts/checks/roadmap/_plan_critique_end.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.checks.roadmap import _plan_critique_end as module

pytestmark = pytest.mark.unit


def _row(verdict: str = "PROCEED", nits: int = 0, choice: str | None = None, dispositions: tuple[str, ...] | None = None):
    findings = None if dispositions is None else [SimpleNamespace(severity="nit", disposition=d) for d in dispositions]
    return SimpleNamespace(verdict=verdict, nits=nits, escalation_choice=choice, findings=findings)


@pytest.mark.parametrize("version", [6, 7])
def test_clean_proceed_confirming_round_and_deferral_end_at_both_versions(version: int) -> None:
    assert module.ended([_row()], version)
    assert module.ended([_row(nits=1), _row()], version)
    assert module.ended([_row("REVISE", choice="accept-with-deferral")], version)
    assert not module.ended([_row("REVISE")], version)
    assert not module.ended([_row("REVISE", nits=1), _row("REVISE")], version)


def test_proceed_with_nits_needs_a_round_at_6_but_disposed_nits_end_at_7() -> None:
    disposed = _row(nits=2, dispositions=("applied-wording", "build-note"))
    assert not module.ended([disposed], 6)
    assert module.ended([disposed], 7)
    assert not module.ended([_row(nits=2, dispositions=("applied-wording", "fixed"))], 7)
    assert not module.ended([_row(nits=1, dispositions=("disputed",))], 7)
    assert not module.ended([_row(nits=1)], 7)


def test_gap_message_names_the_plan_and_keeps_the_pinned_substrings() -> None:
    msg = module.gap_message("PLAN-x.yaml")
    assert msg.startswith("PLAN-x.yaml:")
    assert "must end with a plan-critique PROCEED that ends Step 9" in msg
    assert "or the confirming round after a PROCEED listing nits" in msg
