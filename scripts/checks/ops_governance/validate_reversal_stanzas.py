"""Reversal-conditions stanza well-formedness and review-date ceiling gate (audit SEQ-02 follow-on to Decision 133)."""

from __future__ import annotations

from scripts.checks import registry
from scripts.preflight.decision_conditions import evaluate
from scripts.preflight.review_dates import REVIEW_DATE_CEILING, review_date_load


@registry.register("validate_reversal_stanzas", owner="platform")
def validate_reversal_stanzas(failed: list[str]) -> None:
    """Gate stanza WELL-FORMEDNESS and the per-date review ceiling.

    Runs every gate-safe predicate (evaluate(gate=True): a gate=False predicate, which reads git history, is
    checked for binding but not called, so the verdict never depends on clone depth). Fails any review date
    carrying more than REVIEW_DATE_CEILING stanzas. Never gates on review date or fired conditions -- that stays
    surfacing-only, owned by scripts.preflight.decision_conditions.preflight_bucket() (session preflight + the orient
    skill's Status Digest). A kind: manual condition with no predicate/params is well-formed
    (Decision 133's platform-mvp-closes condition ships id/kind/description only) -- the shared
    evaluate() well-formedness rules never require predicate/params on a manual condition.
    """
    print("\n=== Reversal-conditions stanza well-formedness gate (SEQ-02) ===")
    try:
        results = evaluate(gate=True)
    except Exception as exc:  # noqa: BLE001 -- fail loud at the call site, never silently pass
        msg = f"validate_reversal_stanzas: evaluate() raised: {exc}"
        failed.append(msg)
        print(f"  FAIL: {msg}")
        return

    registry.examined(len(results), unit="reversal_stanzas")
    malformed = [r for r in results if r.state == "MALFORMED"]
    crowded = {d: ids for d, ids in sorted(review_date_load(results).items()) if len(ids) > REVIEW_DATE_CEILING}
    for review_by, ids in crowded.items():
        msg = (
            f"review date {review_by} carries {len(ids)} stanzas (ceiling {REVIEW_DATE_CEILING}): Decisions "
            f"{', '.join(str(i) for i in sorted(ids))}; pick another date with "
            "`bin/venv-python -m scripts.preflight.review_dates --next <YYYY-MM-DD>`"
        )
        failed.append(msg)
        print(f"  FAIL: {msg}")
    if not malformed:
        if not crowded:
            print(f"  PASS: {len(results)} monitored reversal-conditions stanza(s), all well-formed.")
        return

    for r in malformed:
        msg = f"Decision {r.decision_id}: MALFORMED reversal-conditions stanza -- {r.error}"
        failed.append(msg)
        print(f"  FAIL: {msg}")
