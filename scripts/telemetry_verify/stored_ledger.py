"""The stored-row ledger leg: Claude Code's per-model cost-state ledger against the model_call rows the warehouse holds.

The transcript ledger leg (ledger.reconcile_ledger) reconciles the ledger with the transcript prefix at each cost-state record;
this leg reconciles it with the STORED rows of the very calls that prefix counts. The caller
(scripts/telemetry_capture/verify.py) splits each record's prefix calls per model into four ref sets, so this module
never imports the producer:

* selected   refs of the calls ledger.prefix_calls counts that the expected emission at the cursor holds;
* pending    refs the producer defers at the cursor and emits once the stream closes (a session_final emission holds them);
* inherited  calls on inherited fork lines, which the producer never stores;
* unemitted  every other prefix call: neither emission holds it.

Per record and model, in this order: a transcript class the ledger leg itself alarms on or cannot compare, a selected ref whose
stored row the row comparison found missing or column-mismatched, an inherited call and a pending call each read
stored_ledger_not_compared with their reason (one cause never files twice); an unemitted call is the alarm
stored_ledger_call_unemitted; a model with any untested selected row (written before parser_version 4, or
usage_final null) reads only stored_ledger_untested; otherwise the transcript class decides: an exact class compares
exactly, a bounded class as a bound. Only the cursor's parser_version is summed, one row per ref. Pure; no I/O.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Collection, Mapping, Sequence

from scripts.telemetry_verify.ledger import ALARMS as LEDGER_ALARMS
from scripts.telemetry_verify.ledger import CLASSES, Finding

TESTED_FROM_PARSER_VERSION = 4
_STORED_KEYS = {
    "input": "tokens_input",
    "output": "tokens_output",
    "cache_read": "tokens_cache_read",
    "cache_creation": "tokens_cache_creation",
}
_EXACT = ("ledger_exact", "ledger_output_residual_nonfinal")
_BOUNDED = ("ledger_bounded_compaction", "ledger_bounded_background", "ledger_bounded_auxiliary")
_UNCOMPARED = ("ledger_auxiliary_only", "ledger_unavailable", *LEDGER_ALARMS)

DECLARED = {
    "stored_ledger_exact": "the stored rows of the counted calls sum to the ledger on all four classes",
    "stored_ledger_output_residual_final_false": "rec-4354: output short and a selected row has usage_final false",
    "stored_ledger_bounded": "a model the ledger leg bounds, with stored totals at or below the ledger",
    "stored_ledger_not_compared": "the ledger leg alarms on the model, or the prefix cannot be compared (reason counted)",
    "stored_ledger_untested": "a selected row written before parser_version 4 or with usage_final null",
}
ALARMS = (
    "stored_ledger_input_class_mismatch",
    "stored_ledger_output_residual_unexplained",
    "stored_ledger_output_excess",
    "stored_ledger_bound_exceeded",
    "stored_ledger_call_unemitted",
)


@dataclass(frozen=True)
class ModelRefs:
    selected: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    inherited: tuple[str, ...] = ()
    unemitted: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordInput:
    cut_index: int
    inherited: bool
    models: Mapping[str, ModelRefs] = field(default_factory=dict)


@dataclass
class StoredLedgerResult:
    counts: Counter[str] = field(default_factory=Counter)
    reasons: Counter[str] = field(default_factory=Counter)
    models: list[tuple[int, str, str]] = field(default_factory=list)
    records_inherited: int = 0
    other_generation: int = 0
    untested_rows: int = 0
    tested_models: int = 0
    held_cut_indexes: set[int] = field(default_factory=set)

    def _add(self, cut_index: int, model: str, cls: str, reason: str | None = None) -> None:
        self.counts[cls] += 1
        self.models.append((cut_index, model, cls))
        if reason is not None:
            self.reasons[reason] += 1


def _row_of(rows: Sequence[Mapping[str, Any]], parser_version: int) -> Mapping[str, Any] | None:
    at_version = [row for row in rows if row.get("parser_version") == parser_version]
    return min(at_version, key=lambda row: str(row.get("event_id"))) if at_version else None


def _untested(row: Mapping[str, Any]) -> bool:
    return (row.get("parser_version") or 0) < TESTED_FROM_PARSER_VERSION or row.get("usage_final") is None


def _number(row: Mapping[str, Any], name: str) -> int:
    value = row.get(_STORED_KEYS[name])
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _classify(finding: Finding, rows: Sequence[Mapping[str, Any]]) -> str:
    seen = {name: sum(_number(row, name) for row in rows) for name in CLASSES}
    ledger = finding.ledger
    if finding.cls in _BOUNDED:
        return "stored_ledger_bounded" if all(seen[n] <= ledger[n] for n in CLASSES) else "stored_ledger_bound_exceeded"
    if any(seen[n] != ledger[n] for n in CLASSES if n != "output"):
        return "stored_ledger_input_class_mismatch"
    shortfall = ledger["output"] - seen["output"]
    if shortfall == 0:
        return "stored_ledger_exact"
    if shortfall < 0:
        return "stored_ledger_output_excess"
    if any(row.get("usage_final") is False for row in rows):
        return "stored_ledger_output_residual_final_false"
    return "stored_ledger_output_residual_unexplained"


def reconcile(
    records: Sequence[RecordInput],
    stored_model_calls: Mapping[str, Sequence[Mapping[str, Any]]],
    transcript_classes: Mapping[tuple[int, str], Finding],
    *,
    parser_version: int,
    compare_defects: Collection[str] = (),
) -> StoredLedgerResult:
    """Classify every model of every non-inherited cost-state record; *stored_model_calls* maps a model_call external_ref to
    every stored row of that ref (any generation), *transcript_classes* the ledger leg's Finding per (cut_index, model)."""
    result = StoredLedgerResult()
    for record in records:
        if record.inherited:
            result.records_inherited += 1
            continue
        for model, refs in sorted(record.models.items()):
            finding = transcript_classes.get((record.cut_index, model))
            if finding is None or finding.cls in _UNCOMPARED:
                result._add(record.cut_index, model, "stored_ledger_not_compared", "ledger_leg")
                continue
            held = [_row_of(stored_model_calls.get(ref, ()), parser_version) for ref in refs.selected]
            result.other_generation += sum(
                any(row.get("parser_version") != parser_version for row in stored_model_calls.get(ref, ()))
                for ref in refs.selected
            )
            if any(row is None for row in held) or any(ref in compare_defects for ref in refs.selected):
                result._add(record.cut_index, model, "stored_ledger_not_compared", "compare_defect")
            elif refs.inherited:
                result._add(record.cut_index, model, "stored_ledger_not_compared", "inherited_prefix")
            elif refs.pending:
                result.held_cut_indexes.add(record.cut_index)
                result._add(record.cut_index, model, "stored_ledger_not_compared", "pending_emission")
            elif refs.unemitted:
                result._add(record.cut_index, model, "stored_ledger_call_unemitted")
            else:
                rows = [row for row in held if row is not None]
                stale = sum(_untested(row) for row in rows)
                if stale:
                    result.untested_rows += stale
                    result._add(record.cut_index, model, "stored_ledger_untested")
                else:
                    result.tested_models += 1
                    result._add(record.cut_index, model, _classify(finding, rows))
    return result
