"""CONCERN: src/common/event_usage_fields.py -- the usage verbs' derived output columns."""

from __future__ import annotations

from src.common import event_usage_fields as fields
from src.common.ducklake_reader_client import VERB_FIELDS

_BY_VERB = {
    "model_usage_rollup": fields.MODEL_USAGE_FIELDS,
    "tool_use_rollup": fields.TOOL_USE_FIELDS,
    "session_startup_baseline": fields.STARTUP_BASELINE_FIELDS,
}


def test_each_tuple_is_unique_and_precedes_the_stamps_in_verb_fields() -> None:
    for verb, columns in _BY_VERB.items():
        assert len(set(columns)) == len(columns)
        assert VERB_FIELDS[verb] == (*columns, "generations", "conflicted_grain_keys", "derivation_conflicted")


def test_no_usage_column_carries_content_cost_or_a_summed_total() -> None:
    banned = ("content", "cost", "tokens_total")
    assert not [c for columns in _BY_VERB.values() for c in columns if any(b in c for b in banned)]
