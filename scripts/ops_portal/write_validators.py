"""The portal's derived row-rule pre-check (rec-4158, Decision 210 cl.3).

Owner-concern: gate_write runs the DuckLake writer's own pure gate (schema_gate and check_write) over the repo's
field_semantics.yaml projection of the Class A contract, so file_rec, update_rec, file_decision and update_decision
refuse early with the rule the writer would apply. It is a derived pre-check, never authoritative: the writer
re-evaluates every rule (Decision 81 cl.5 as widened by Decision 210 cl.5). The producer-side rules no row-local
evaluator can run (acceptance lint, dependency existence, and the glossary rule, whose ground is house style the
repository's profile decides) are driven from the contract's own exemptions. This path is named by the
ops_recommendations contract as the acceptance_lint and glossary_lint exemption owner; it must not move.
"""

from __future__ import annotations

import copy
import functools
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

import yaml

from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.ops_portal._common import _REPO_ROOT
from scripts.ops_portal.writer_transport import _project_ops_record
from src.common.ducklake_scd2_schema import SchemaGateError, resolve_table_spec, schema_gate
from src.common.ducklake_write_rules import RowRuleViolationError, check_write
from src.row_rules.rules import RowRules

_PLACEHOLDER_ULID = "0" * 26
_GLOSSARY_PATH = _REPO_ROOT / "docs" / "contracts" / "glossary.yaml"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class RowRuleRejected(ValueError):
    """A contract-declared row rule refused the record; names table, column, rule and limit, never the value."""

    def __init__(self, table: str, column: str, rule: str, limit: str, exclude_before: str | None) -> None:
        self.table = table
        self.column = column
        self.rule = rule
        self.exclude_before = exclude_before
        since = f"; exclude_before {exclude_before}" if exclude_before else ""
        super().__init__(f"{table}.{column}: {limit} (row rule {rule}{since})")


def _limit_in_words(rule: str, spec: dict[str, Any]) -> str:
    if rule == "min_length":
        return f"must be at least {spec.get('min_length')} stripped characters"
    if rule == "accepted_values":
        return f"must be one of {list(spec.get('accepted_values', []))!r}"
    if rule == "pattern":
        return f"must match {spec.get('pattern')!r}"
    if rule == "array_element_format":
        return f"elements must match {spec.get('array_element_format')!r}"
    if rule == "not_null":
        return "is required"
    return "violates its declared rule"


_rec_exists_memo: dict[str, bool] = {}


def _rec_exists(rec_id: str, reader: object | None = None) -> bool:
    """True iff rec_id resolves against the full corpus via rec_by_id (open, closed, or
    superseded alike -- existence, not open-set membership; PLAN-dependency-referential-integrity).

    Memoises POSITIVE results only: ids are writer-allocated, so an id absent at T and filed
    at T+1 must not stay cached absent and reject a legitimate later dependency in the same
    process. `_rec_exists_memo` is module-level and directly clearable by tests.

    Reuses its own make_reader/rec_by_id shape (mirrors ops_data_portal._fetch_rec_from_reader's
    shape) rather than importing that facade function: scripts.ops_portal is not allowed to
    import scripts.ops_data_portal (.importlinter no-cycles-ops-data-portal-executor), so reusing
    the facade helper here would be an out-of-scope-boundary edit to add a new ignore_imports
    carve-out. `reader`, when supplied, is used as-is (Decision 88 invariant (i): the caller holds
    ONE reader across a whole write's element list rather than one per element) -- a fresh reader
    is constructed only when the caller has none to share. Loud-fails on an unreachable reader
    (Decision 84 I-3/I-4) -- no error handling here, propagates as-is.
    """
    if _rec_exists_memo.get(rec_id):
        return True
    if reader is None:
        from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

        reader = cast(DuckLakeReader, make_reader())
    found = bool(reader.named("rec_by_id", id=rec_id))  # type: ignore[attr-defined]
    if found:
        _rec_exists_memo[rec_id] = True
    return found


@functools.lru_cache(maxsize=None)
def _producer_params(table: str) -> dict[tuple[str, str], dict[str, Any]]:
    """(column, kind) -> the declared leg's params, from the contract the projection ships from."""
    from scripts.contract_rules import declared_rules  # noqa: PLC0415

    try:
        rules = declared_rules(table)
    except ValueError:
        return {}
    return {(r.column, r.kind): dict(r.params) for r in rules}


def _acceptance_lint(column: str, value: object, params: dict[str, Any]) -> None:
    ok, msg = lint_acceptance_command(
        str(value) if value else "", require_discrimination=bool(params.get("require_discrimination", False))
    )
    if not ok:
        raise ValueError(msg)


class _ElementReference:
    """array_element_reference for one write: one reader across the element list, built lazily (Decision 88 (i))."""

    def __init__(self) -> None:
        self._reader: object | None = None

    def __call__(self, column: str, value: object, params: dict[str, Any]) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            raise ValueError(f"{column} must be a list of strings, got {type(value).__name__}")
        elements = [str(x) for x in value]
        if self._reader is None and any(not _rec_exists_memo.get(e) for e in elements):
            from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

            self._reader = cast(DuckLakeReader, make_reader())
        absent = [e for e in elements if not _rec_exists(e, reader=self._reader)]
        if absent:
            raise ValueError(f"{column} elements must reference existing recs; absent: {absent!r}")


@functools.lru_cache(maxsize=None)
def _load_glossary(path: Path) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"glossary_lint: {path.name} is not loadable ({type(exc).__name__}); failing closed") from exc


class _GlossaryLint:
    """glossary_lint for one write: refuse a sentence the stored value does not already hold that uses either relative word."""

    def __init__(self, existing: dict[str, Any] | None) -> None:
        self._existing = existing

    def __call__(self, column: str, value: object, params: dict[str, Any]) -> None:
        if value is None:
            return
        from scripts.checks.contracts import _glossary_words  # noqa: PLC0415

        contract = _load_glossary(_GLOSSARY_PATH)
        known = _glossary_words.identifiers(contract)
        held = {s.strip() for s in _SENTENCE_END.split(str((self._existing or {}).get(column) or ""))}
        for sentence in _SENTENCE_END.split(str(value)):
            if sentence.strip() in held:
                continue
            for _, word in _glossary_words.unqualified_uses(sentence, yaml_keys=False, identifiers=known):
                derived = " (a short context is built from context_v2_json or dispute evidence)" if column == "context" else ""
                raise ValueError(
                    f'{column}: unqualified "{word}" -- say instead: {_glossary_words.say_instead(word, contract)}, '
                    f"or quote it if it is not the relative word; see docs/contracts/glossary.yaml{derived}"
                )


def _producer_handlers(existing: dict[str, Any] | None) -> dict[str, Callable[[str, object, dict[str, Any]], None]]:
    return {
        "acceptance_lint": _acceptance_lint,
        "array_element_reference": _ElementReference(),
        "glossary_lint": _GlossaryLint(existing),
    }


def _undated_projection(fields: dict[str, Any]) -> dict[str, Any]:
    """The projection with every dated leg (and its exclude_before entry) removed, so only undated rules remain."""
    out = copy.deepcopy(fields)
    for spec in out.values():
        for kind in list(spec.get("exclude_before", {})):
            spec.pop(kind, None)
        spec.pop("exclude_before", None)
    for name, spec in out.items():
        for kind in ("not_before", "at_most"):
            other = spec.get(kind)
            if other is not None and "derived" in (spec.get("role"), out.get(other, {}).get("role")):
                spec.pop(kind)
    return out


def _run_row_rules(table: str, record: dict[str, Any], *, insert: bool) -> None:
    spec = resolve_table_spec(table)
    columns = spec.fields if insert else _undated_projection(spec.fields)
    rules = RowRules.from_projection(columns)
    now = datetime.now(timezone.utc)
    try:
        check_write(spec, rules, record, now, now, _PLACEHOLDER_ULID, None)
    except RowRuleViolationError as exc:
        raise RowRuleRejected(
            table, exc.column, exc.rule, _limit_in_words(exc.rule, spec.fields.get(exc.column, {})), exc.exclude_before
        ) from exc


def _run_producer_rules(table: str, projected: dict[str, Any], *, insert: bool, existing: dict[str, Any] | None) -> None:
    exemptions = RowRules.from_projection(resolve_table_spec(table).fields).producer_exemptions
    if not exemptions:
        return
    params, handlers = _producer_params(table), _producer_handlers(existing)
    for column, kind in exemptions:
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"{table}.{column}: producer-side rule {kind!r} has no portal handler (fails closed)")
        value = projected.get(column)
        if insert or existing is None or value != existing.get(column):
            handler(column, value, params.get((column, kind), {}))


def gate_write(table: str, record: dict[str, Any], *, insert: bool, existing: dict[str, Any] | None = None) -> None:
    """Refuse *record* with the writer's own gate; never stricter than the writer.

    insert=True (file_ops only: the writer allocates the key) evaluates every rule with no prior. insert=False
    (update_ops and caller-keyed write_ops upserts) evaluates undated rules only: dated legs stay the writer's alone,
    which binds them against the stored row.
    """
    spec = resolve_table_spec(table)
    projected = _project_ops_record(table, record)
    keyed = {**projected, spec.merge_key: f"{spec.entity_id_prefix or 'x-'}0"} if insert else projected
    try:
        schema_gate(keyed, table=table)
    except SchemaGateError as exc:
        raise ValueError(str(exc)) from exc
    _run_row_rules(table, keyed, insert=insert)
    _run_producer_rules(table, projected, insert=insert, existing=existing)
