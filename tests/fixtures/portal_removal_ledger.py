"""The removal ledger (rec-4158 portal row-rule gate): every portal-side check the gate retired, with the input it
rejected and the contract leg that replaces it.

One row per retired check: the 20 config/agent/data_quality/ops.yaml `write_time` tests (keyed by column and test
type), the two explicit validators and the update_rec status fast-fail (keyed by check name, so no row spells a deleted
symbol). `leg` is the contract rule kind (or schema_gate for a required or typed input the schema gate refuses) that
refuses `input` on insert; `update_path` names the writer nodes that prove a dated leg on the update path.
Completeness against origin/main's ops.yaml is proven PR-relative by the plan's closure sweep.
"""

from __future__ import annotations

_DATED = (
    "tests/common/test_ducklake_write_rules.py::test_write_scd2_update_binds_dated_rule_only_when_column_changes",
    "tests/common/test_ducklake_write_rules.py::test_older_rows_verdicts_on_the_real_projection",
)


def _row(column: str, check: str, value: object, leg: str, *, dated: bool = False) -> dict[str, object]:
    return {"column": column, "check": check, "input": value, "leg": leg, "update_path": _DATED if dated else None}


LEDGER: tuple[dict[str, object], ...] = (
    _row("title", "not_null", None, "schema_gate"),
    _row("title", "min_length", "123456789", "min_length", dated=True),
    _row("source", "not_null", None, "not_null", dated=True),
    _row("effort", "not_null", None, "not_null", dated=True),
    _row("effort", "accepted_values", "Q", "accepted_values", dated=True),
    _row("priority", "not_null", None, "not_null", dated=True),
    _row("priority", "accepted_values", "Z", "accepted_values", dated=True),
    _row("status", "not_null", None, "schema_gate"),
    _row("automatable", "not_null", "", "schema_gate"),
    _row("file", "not_null", None, "not_null", dated=True),
    _row("file", "path_syntax", "/abs/x.py", "pattern", dated=True),
    _row("context", "not_null", None, "not_null", dated=True),
    _row("context", "min_length", "short", "min_length", dated=True),
    _row("acceptance", "not_null", None, "not_null", dated=True),
    _row("acceptance", "acceptance_lint", "grep -q x scripts/x.py", "acceptance_lint"),
    _row("dependencies", "array_element_format", ["nonsense"], "array_element_format"),
    _row("dependencies", "array_element_reference", ["rec-999999999"], "array_element_reference"),
    _row("tags", "array_element_format", ["Bad_Tag"], "array_element_format", dated=True),
    _row("risk", "not_null", None, "not_null", dated=True),
    _row("risk", "accepted_values", "extreme", "accepted_values", dated=True),
    _row("file", "explicit file path", "scripts\\x.py", "pattern", dated=True),
    _row("context", "explicit context length", "too short", "min_length", dated=True),
    _row("status", "status fast-fail", "bogus", "accepted_values"),
)
