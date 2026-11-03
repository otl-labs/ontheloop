"""Mirror tests for scripts/telemetry_verify/stored_ledger.py over plain rows and hand-built ledger findings."""

from __future__ import annotations

from typing import Any

from scripts.telemetry_verify.ledger import Finding
from scripts.telemetry_verify.stored_ledger import ALARMS, DECLARED, ModelRefs, RecordInput, reconcile

MODEL = "claude-test-1"
LEDGER = {"input": 10, "output": 20, "cache_read": 30, "cache_creation": 40}


def _row(ref: str, *, version: int = 4, final: bool | None = True, **over: Any) -> dict[str, Any]:
    base = {
        "external_ref": ref,
        "event_id": "E" + ref,
        "parser_version": version,
        "usage_final": final,
        "tokens_input": 5,
        "tokens_output": 10,
        "tokens_cache_read": 15,
        "tokens_cache_creation": 20,
    }
    return {**base, **over}


def _stored(*rows: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["external_ref"], []).append(row)
    return out


def _finding(cls: str = "ledger_exact", model: str = MODEL, ledger: dict[str, int] | None = None, cut: int = 0) -> Finding:
    return Finding(model, cls, cut, 3, ledger=dict(LEDGER if ledger is None else ledger))


def _run(
    refs: ModelRefs,
    stored: dict[str, list[dict[str, Any]]],
    finding: Finding | None = None,
    *,
    inherited: bool = False,
    defects: tuple[str, ...] = (),
    version: int = 4,
):
    finding = finding or _finding()
    record = RecordInput(0, inherited, {finding.model: refs})
    return reconcile([record], stored, {(0, finding.model): finding}, parser_version=version, compare_defects=defects)


def _two(**over: Any) -> tuple[ModelRefs, dict[str, list[dict[str, Any]]]]:
    return ModelRefs(selected=("a/model_call", "b/model_call")), _stored(
        _row("a/model_call", **over), _row("b/model_call", **over)
    )


def test_stored_ledger_classes() -> None:
    refs, stored = _two()
    assert _run(refs, stored).counts == {"stored_ledger_exact": 1}
    assert _run(refs, stored).tested_models == 1
    shifted = {**stored, "b/model_call": [_row("b/model_call", tokens_input=6)]}
    assert _run(refs, shifted).counts == {"stored_ledger_input_class_mismatch": 1}
    short = _stored(_row("a/model_call"), _row("b/model_call", tokens_output=5))
    assert _run(refs, short).counts == {"stored_ledger_output_residual_unexplained": 1}
    nonfinal = _stored(_row("a/model_call"), _row("b/model_call", tokens_output=5, final=False))
    nonfinal_finding = _finding("ledger_output_residual_nonfinal")
    assert _run(refs, nonfinal, nonfinal_finding).counts == {"stored_ledger_output_residual_final_false": 1}
    excess = _stored(_row("a/model_call"), _row("b/model_call", tokens_output=11))
    assert _run(refs, excess).counts == {"stored_ledger_output_excess": 1}
    assert set(_run(refs, short).counts) <= set(ALARMS)
    assert set(_run(refs, stored).counts) <= set(DECLARED)


def test_rows_after_the_cut_are_never_summed() -> None:
    refs, stored = _two()
    stored["c/model_call"] = [_row("c/model_call", tokens_input=500)]
    assert _run(refs, stored).counts == {"stored_ledger_exact": 1}, "only the selected refs are summed"


def test_pre_v4_rows_count_as_untested() -> None:
    refs, old = _two(version=3)
    result = _run(refs, old, version=3)
    assert result.counts == {"stored_ledger_untested": 1} and result.untested_rows == 2 and result.tested_models == 0
    null_final = _two(final=None)
    result = _run(*null_final)
    assert result.counts == {"stored_ledger_untested": 1} and result.untested_rows == 2
    sub = ModelRefs(selected=("s/model_call",))
    row = _row(
        "s/model_call", tokens_input=10, tokens_output=20, tokens_cache_read=30, tokens_cache_creation=40, requested_model=None
    )
    result = _run(sub, _stored(row), _finding(model="claude-sub-1"))
    assert result.counts == {"stored_ledger_exact": 1} and result.tested_models == 1, "a null requested_model is still tested"


def test_an_inherited_record_is_not_reconciled() -> None:
    refs, stored = _two()
    result = _run(refs, stored, inherited=True)
    assert result.records_inherited == 1 and result.counts == {} and result.models == []


def test_only_the_cursor_generation_is_summed() -> None:
    refs, stored = _two()
    stored["a/model_call"].append(_row("a/model_call", version=3, tokens_input=99, event_id="Eold"))
    result = _run(refs, stored)
    assert result.counts == {"stored_ledger_exact": 1} and result.other_generation == 1
    only_old = {"a/model_call": [_row("a/model_call", version=3)], "b/model_call": stored["b/model_call"]}
    assert _run(refs, only_old).counts == {"stored_ledger_not_compared": 1}, "a ref with no row at the generation is a defect"


def test_bounded_models_compare_as_bounds() -> None:
    refs, stored = _two()
    for cls in ("ledger_bounded_compaction", "ledger_bounded_background", "ledger_bounded_auxiliary"):
        higher = _finding(cls, ledger={"input": 100, "output": 200, "cache_read": 300, "cache_creation": 400})
        assert _run(refs, stored, higher).counts == {"stored_ledger_bounded": 1}, cls
        exact_input = _finding(cls)
        assert _run(refs, stored, exact_input).counts == {"stored_ledger_bounded": 1}, cls
        lower = _finding(cls, ledger={"input": 9, "output": 20, "cache_read": 30, "cache_creation": 40})
        assert _run(refs, stored, lower).counts == {"stored_ledger_bound_exceeded": 1}, cls


def test_a_ledger_alarm_is_not_compared_again() -> None:
    refs, stored = _two()
    for cls in ("ledger_input_class_mismatch", "ledger_output_excess", "ledger_model_unexplained", "ledger_auxiliary_only"):
        result = _run(refs, stored, _finding(cls))
        assert result.counts == {"stored_ledger_not_compared": 1} and result.reasons == {"ledger_leg": 1}, cls
    assert _run(refs, stored, _finding("ledger_unavailable")).counts == {"stored_ledger_not_compared": 1}


def test_inherited_pending_and_unemitted_calls_are_classified() -> None:
    _, stored = _two()
    selected = ("a/model_call", "b/model_call")
    inherited = _run(ModelRefs(selected, inherited=("x/model_call",)), stored)
    assert inherited.counts == {"stored_ledger_not_compared": 1} and inherited.reasons == {"inherited_prefix": 1}
    pending = _run(ModelRefs(selected, pending=("p/model_call",)), stored)
    assert pending.counts == {"stored_ledger_not_compared": 1} and pending.reasons == {"pending_emission": 1}
    assert pending.held_cut_indexes == {0}
    unemitted = _run(ModelRefs(selected, unemitted=("u/model_call",)), stored)
    assert unemitted.counts == {"stored_ledger_call_unemitted": 1} and unemitted.held_cut_indexes == set()
    defect = _run(ModelRefs(selected), stored, defects=("a/model_call",))
    assert defect.counts == {"stored_ledger_not_compared": 1} and defect.reasons == {"compare_defect": 1}
    missing = _run(ModelRefs(selected + ("gone/model_call",)), stored)
    assert missing.reasons == {"compare_defect": 1}, "a selected ref with no stored row is the comparison's defect"


def test_a_ref_held_twice_is_summed_once() -> None:
    refs, stored = _two()
    stored["a/model_call"].append(_row("a/model_call", event_id="Ezzz"))
    stored["b/model_call"].append(_row("b/model_call", event_id="Ezzz"))
    assert _run(refs, stored).counts == {"stored_ledger_exact": 1}
