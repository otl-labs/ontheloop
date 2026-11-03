"""Contract-derived, table-agnostic row rules (Decision 210 cl.1/cl.3), shared by every closed write boundary.

Stdlib only and plane-neutral: no first-party import, so the telemetry kernel and the DuckLake ops writer evaluate the
same engine (rec-4158). Every rule is built from the generator projection (RowRules.from_projection) -- nothing in this
module names a table, a column or a column value of any contract. check_row raises RowRuleError, which names the
table, the rule and the column and never a row value.

A None value passes every value rule (min_length, pattern, pattern_when, array_element_format, accepted_values): NULL is
not_null's concern alone. pattern_when is a list of {when, pattern} entries: a non-None value must fully match the pattern
of every entry whose condition (the required_when grammar) the row meets, and a non-str value under a met entry violates;
it is not dateable and always binds. A dated rule (exclude_before) binds an insert always, and an older row only when the
write changes the rule's column (the recorded older-rows rule).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping

_COLUMN_KEYS = frozenset(
    {
        "role",
        "sql_type",
        "nullable",
        "description",
        "semantics",
        "required_when",
        "accepted_values",
        "not_before",
        "max_after_write_seconds",
        "at_most",
        "null_or_zero_when",
        "representation_of",
        "pattern",
        "pattern_when",
        "not_null",
        "min_length",
        "array_element_format",
        "exclude_before",
        "write_time_exemptions",
    }
)
_DATEABLE_KINDS = frozenset(
    {"not_null", "accepted_values", "min_length", "pattern", "array_element_format", "not_before", "at_most"}
)
_TWO_COLUMN_KINDS = frozenset({"not_before", "at_most"})
PRODUCER_SIDE_KINDS = frozenset({"acceptance_lint", "array_element_reference", "glossary_lint"})
_TABLE_KEYS = frozenset({"exactly_one_of", "payload"})
_CONTENT_KEYS = frozenset({"inline", "uri", "sha", "size", "threshold", "cap", "integrity"})
EXEMPTABLE_LEGS = frozenset({"inline_within_threshold", "uri_above_threshold", "within_cap", "integrity"})
WAIVABLE_KINDS = frozenset(
    {
        "not_null",
        "accepted_values",
        "min_length",
        "pattern",
        "pattern_when",
        "array_element_format",
        "required_when",
        "not_before",
        "at_most",
        "null_or_zero_when",
        "max_after_write_seconds",
    }
)


class RowRuleError(ValueError):
    """A row breaks a contract-declared rule; carries table, rule and column, never a value."""

    def __init__(self, table: str, rule: str, column: str, detail: str = "") -> None:
        self.table = table
        self.rule = rule
        self.column = column
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"{table}.{column}: row rule {rule} violated{suffix}")


class RuleProjectionError(ValueError):
    """The projection handed to RowRules.from_projection is malformed or declares an unknown rule key."""


@dataclass(frozen=True)
class ContentRule:
    inline: str
    uri: str
    sha: str
    size: str
    threshold: int
    cap: int | None = None
    integrity: bool = False


@dataclass(frozen=True)
class RowRules:
    not_null: frozenset[str] = frozenset()
    accepted_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    required_when: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)
    exactly_one_of: tuple[tuple[str, ...], ...] = ()
    patterns: Mapping[str, re.Pattern[str]] = field(default_factory=dict)
    pattern_when: Mapping[str, tuple[tuple[Mapping[str, frozenset[str]], re.Pattern[str]], ...]] = field(default_factory=dict)
    min_length: Mapping[str, int] = field(default_factory=dict)
    element_formats: Mapping[str, re.Pattern[str]] = field(default_factory=dict)
    exclude_before: Mapping[str, Mapping[str, datetime]] = field(default_factory=dict)
    producer_exemptions: Mapping[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    not_before: Mapping[str, str] = field(default_factory=dict)
    at_most: Mapping[str, str] = field(default_factory=dict)
    null_or_zero_when: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)
    max_after_write_seconds: Mapping[str, int] = field(default_factory=dict)
    content: ContentRule | None = None
    exemptions: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    representation_only: frozenset[str] = frozenset()
    waived: Mapping[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def from_projection(
        cls, columns: Mapping[str, Mapping[str, Any]], table_rules: Mapping[str, Any] | None = None
    ) -> RowRules:
        table_rules = table_rules or {}
        unknown_table = set(table_rules) - _TABLE_KEYS
        if unknown_table:
            raise RuleProjectionError(f"unknown table rule key(s) {sorted(unknown_table)}")
        not_null: set[str] = set()
        accepted: dict[str, frozenset[str]] = {}
        required: dict[str, dict[str, frozenset[str]]] = {}
        patterns: dict[str, re.Pattern[str]] = {}
        conditional: dict[str, tuple[tuple[Mapping[str, frozenset[str]], re.Pattern[str]], ...]] = {}
        min_length: dict[str, int] = {}
        element_formats: dict[str, re.Pattern[str]] = {}
        dated: dict[str, dict[str, datetime]] = {}
        producer: dict[tuple[str, str], tuple[str, str]] = {}
        not_before: dict[str, str] = {}
        at_most: dict[str, str] = {}
        zero: dict[str, dict[str, frozenset[str]]] = {}
        skew: dict[str, int] = {}
        exemptions: dict[str, tuple[str, str]] = {}
        representation_only: set[str] = set()
        waived: dict[tuple[str, str], tuple[str, str]] = {}
        for name, spec in columns.items():
            unknown = set(spec) - _COLUMN_KEYS
            if unknown:
                raise RuleProjectionError(f"{name}: unknown rule key(s) {sorted(unknown)}")
            if spec.get("nullable") is False:
                not_null.add(name)
            if "not_null" in spec:
                if spec["not_null"] is not True:
                    raise RuleProjectionError(f"{name}: not_null must be true")
                not_null.add(name)
            if "min_length" in spec:
                min_length[name] = _min_length(name, spec["min_length"])
            if "accepted_values" in spec:
                accepted[name] = frozenset(spec["accepted_values"])
            if "required_when" in spec:
                required[name] = _condition(name, "required_when", spec["required_when"], columns)
            if "null_or_zero_when" in spec:
                zero[name] = _condition(name, "null_or_zero_when", spec["null_or_zero_when"], columns)
            _collect_formats(name, spec, columns, patterns, conditional, element_formats)
            if "not_before" in spec:
                not_before[name] = _column_ref(name, "not_before", spec["not_before"], columns)
            if "at_most" in spec:
                at_most[name] = _column_ref(name, "at_most", spec["at_most"], columns)
            if "max_after_write_seconds" in spec:
                skew[name] = _seconds(name, spec["max_after_write_seconds"])
            if "representation_of" in spec:
                representation_only.add(name)
            _collect_exemptions(name, spec, exemptions, producer, waived)
            if "exclude_before" in spec:
                dated[name] = _exclude_before(name, spec)
        groups = tuple(tuple(group) for group in table_rules.get("exactly_one_of", ()))
        for group in groups:
            missing = [c for c in group if c not in columns]
            if missing:
                raise RuleProjectionError(f"exactly_one_of names unknown column(s) {missing}")
        content = _content(table_rules.get("payload"), columns)
        return cls(
            not_null=frozenset(not_null),
            accepted_values=accepted,
            required_when=required,
            exactly_one_of=groups,
            patterns=patterns,
            pattern_when=conditional,
            min_length=min_length,
            element_formats=element_formats,
            exclude_before=dated,
            producer_exemptions=producer,
            not_before=not_before,
            at_most=at_most,
            null_or_zero_when=zero,
            max_after_write_seconds=skew,
            content=content,
            exemptions=exemptions,
            representation_only=frozenset(representation_only),
            waived=waived,
        )


def _seconds(column: str, seconds: Any) -> int:
    if type(seconds) is not int or seconds < 0:
        raise RuleProjectionError(f"{column}: max_after_write_seconds must be a non-negative integer")
    return seconds


def _min_length(column: str, raw: Any) -> int:
    if type(raw) is not int or raw < 0:
        raise RuleProjectionError(f"{column}: min_length must be a non-negative integer")
    return raw


def _exclude_before(column: str, spec: Mapping[str, Any]) -> dict[str, datetime]:
    raw = spec["exclude_before"]
    if not isinstance(raw, Mapping) or not raw:
        raise RuleProjectionError(f"{column}: exclude_before must be a non-empty mapping of rule key to ISO date")
    out: dict[str, datetime] = {}
    for kind, day in raw.items():
        if kind not in _DATEABLE_KINDS or kind not in spec:
            raise RuleProjectionError(f"{column}: exclude_before names {kind!r}, which is not a dateable rule of this column")
        if kind == "not_null" and spec.get("nullable") is False:
            raise RuleProjectionError(f"{column}: a dated not_null on a nullable:false column would silently drop its date")
        out[kind] = _midnight_utc(column, kind, day)
    return out


def _midnight_utc(column: str, kind: str, day: Any) -> datetime:
    try:
        parsed = date.fromisoformat(day) if isinstance(day, str) else day
        if type(parsed) is not date:
            raise ValueError(day)
    except (TypeError, ValueError) as exc:
        raise RuleProjectionError(f"{column}: exclude_before[{kind}] is not an ISO date") from exc
    return datetime.combine(parsed, time.min, tzinfo=timezone.utc)


def _collect_exemptions(
    column: str,
    spec: Mapping[str, Any],
    exemptions: dict[str, tuple[str, str]],
    producer: dict[tuple[str, str], tuple[str, str]],
    waived: dict[tuple[str, str], tuple[str, str]],
) -> None:
    for leg, why in (spec.get("write_time_exemptions") or {}).items():
        producer_side = leg in PRODUCER_SIDE_KINDS
        waivable = leg in WAIVABLE_KINDS
        if not producer_side and not waivable and (leg not in EXEMPTABLE_LEGS or leg in exemptions):
            raise RuleProjectionError(f"{column}: write_time_exemptions names unknown or duplicate leg {leg!r}")
        if not (isinstance(why, Mapping) and why.get("reason") and why.get("owner")):
            raise RuleProjectionError(f"{column}: exemption {leg!r} needs a reason and an owner")
        recorded = (str(why["reason"]), str(why["owner"]))
        if waivable:
            if leg in spec:
                raise RuleProjectionError(f"{column}: {leg} is exempt and must not also ship as a rule")
            if leg == "not_null" and spec.get("nullable") is False:
                raise RuleProjectionError(f"{column}: not_null cannot be exempted on a nullable:false column")
            waived[(column, leg)] = recorded
        elif producer_side:
            producer[(column, leg)] = recorded
        else:
            exemptions[leg] = recorded


def _collect_formats(
    column: str,
    spec: Mapping[str, Any],
    columns: Mapping[str, Mapping[str, Any]],
    patterns: dict[str, re.Pattern[str]],
    conditional: dict[str, tuple[tuple[Mapping[str, frozenset[str]], re.Pattern[str]], ...]],
    element_formats: dict[str, re.Pattern[str]],
) -> None:
    if "array_element_format" in spec:
        element_formats[column] = _pattern(column, spec["array_element_format"])
    if "pattern" in spec:
        patterns[column] = _pattern(column, spec["pattern"])
    if "pattern_when" in spec:
        conditional[column] = _pattern_when(column, spec["pattern_when"], columns)


def _condition(column: str, rule: str, raw: Any, columns: Mapping[str, Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    if not isinstance(raw, Mapping) or not raw:
        raise RuleProjectionError(f"{column}: {rule} must be a non-empty mapping")
    out: dict[str, frozenset[str]] = {}
    for key, values in raw.items():
        if not isinstance(values, (list, tuple)) or not values:
            raise RuleProjectionError(f"{column}: {rule}.{key} must be a non-empty list")
        if key not in columns:
            raise RuleProjectionError(f"{column}: {rule} names unknown column {key!r}")
        declared = columns[key].get("accepted_values")
        try:
            allowed = frozenset(values)
            outside = declared is not None and not allowed <= set(declared)
        except TypeError as exc:
            raise RuleProjectionError(f"{column}: {rule}.{key} holds an unhashable value") from exc
        if outside:
            raise RuleProjectionError(f"{column}: {rule}.{key} names value(s) outside the column's accepted_values")
        out[key] = allowed
    return out


def _pattern_when(
    column: str, raw: Any, columns: Mapping[str, Mapping[str, Any]]
) -> tuple[tuple[Mapping[str, frozenset[str]], re.Pattern[str]], ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise RuleProjectionError(f"{column}: pattern_when must be a non-empty list")
    entries = []
    for entry in raw:
        if not isinstance(entry, Mapping) or set(entry) != {"when", "pattern"}:
            raise RuleProjectionError(f"{column}: each pattern_when entry must be a mapping of exactly when and pattern")
        entries.append(
            (
                _condition(column, "pattern_when", entry["when"], columns),
                _pattern(column, entry["pattern"], rule="pattern_when"),
            )
        )
    return tuple(entries)


def _pattern(column: str, raw: Any, *, rule: str = "pattern") -> re.Pattern[str]:
    try:
        return re.compile(raw)
    except (re.error, TypeError) as exc:
        raise RuleProjectionError(f"{column}: {rule} is not a valid regex") from exc


def _column_ref(column: str, rule: str, other: Any, columns: Mapping[str, Any]) -> str:
    if not isinstance(other, str) or other not in columns:
        raise RuleProjectionError(f"{column}: {rule} names an unknown column")
    return other


def _content(raw: Any, columns: Mapping[str, Any]) -> ContentRule | None:
    if raw is None:
        return None
    unknown = set(raw) - _CONTENT_KEYS
    if unknown:
        raise RuleProjectionError(f"unknown content rule key(s) {sorted(unknown)}")
    for key in ("inline", "uri", "sha", "size"):
        if raw.get(key) not in columns:
            raise RuleProjectionError(f"content rule {key} names an unknown column")
    threshold, cap = raw.get("threshold"), raw.get("cap")
    if type(threshold) is not int or (cap is not None and type(cap) is not int):
        raise RuleProjectionError("content rule threshold and cap must be integers")
    if cap is not None and cap < threshold:
        raise RuleProjectionError("content rule cap must not be below the threshold")
    return ContentRule(raw["inline"], raw["uri"], raw["sha"], raw["size"], threshold, cap, bool(raw.get("integrity")))


def _matches(row: Mapping[str, Any], condition: Mapping[str, frozenset[str]]) -> bool:
    return all(row.get(key) in values for key, values in condition.items())


def check_row(
    table: str,
    row: Mapping[str, Any],
    rules: RowRules,
    created_timestamp: datetime,
    *,
    prior: Mapping[str, Any] | None = None,
) -> None:
    """Raise RowRuleError on the first rule *row* breaks; a passing row passes again against any later created_timestamp.

    *prior* is the existing row an update replaces (None for an insert). A dated rule binds iff prior is None, or the
    row was created at or after the rule's date, or the write changes the rule's column (a two-column rule: either).
    """
    binds = _binder(rules, row, created_timestamp, prior)
    _check_presence(table, row, rules, binds)
    if rules.content is not None:
        _check_content(table, row, rules)
    _check_relations(table, row, rules, created_timestamp, binds)


def _binder(rules: RowRules, row: Mapping[str, Any], created: datetime, prior: Mapping[str, Any] | None) -> Any:
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    def binds(column: str, kind: str, *others: str) -> bool:
        since = rules.exclude_before.get(column, {}).get(kind)
        if since is None or prior is None or created >= since:
            return True
        return any(row.get(c) != prior.get(c) for c in (column, *others))

    return binds


def _check_presence(table: str, row: Mapping[str, Any], rules: RowRules, binds: Any) -> None:
    for column in sorted(rules.not_null):
        if row.get(column) is None and binds(column, "not_null"):
            raise RowRuleError(table, "not_null", column)
    for column, condition in rules.required_when.items():
        if row.get(column) is None and _matches(row, condition):
            raise RowRuleError(table, "required_when", column)
    for column, allowed in rules.accepted_values.items():
        value = row.get(column)
        if value is not None and value not in allowed and binds(column, "accepted_values"):
            raise RowRuleError(table, "accepted_values", column)
    for group in rules.exactly_one_of:
        if sum(row.get(column) is not None for column in group) != 1:
            raise RowRuleError(table, "exactly_one_of", group[0], f"group of {len(group)}")
    _check_formats(table, row, rules, binds)


def _check_formats(table: str, row: Mapping[str, Any], rules: RowRules, binds: Any) -> None:
    for column, pattern in rules.patterns.items():
        value = row.get(column)
        if value is not None and pattern.fullmatch(value) is None and binds(column, "pattern"):
            raise RowRuleError(table, "pattern", column)
    for column, entries in rules.pattern_when.items():
        value = row.get(column)
        if value is None:
            continue
        for condition, pattern in entries:
            if _matches(row, condition) and (not isinstance(value, str) or pattern.fullmatch(value) is None):
                raise RowRuleError(table, "pattern_when", column)
    for column, floor in rules.min_length.items():
        value = row.get(column)
        if value is not None and (not isinstance(value, str) or len(value.strip()) < floor) and binds(column, "min_length"):
            raise RowRuleError(table, "min_length", column)
    for column, pattern in rules.element_formats.items():
        value = row.get(column)
        if value is not None and not _elements_match(value, pattern) and binds(column, "array_element_format"):
            raise RowRuleError(table, "array_element_format", column)


def _elements_match(value: Any, pattern: re.Pattern[str]) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) and pattern.fullmatch(item) is not None for item in value)


def _check_relations(table: str, row: Mapping[str, Any], rules: RowRules, created_timestamp: datetime, binds: Any) -> None:
    for column, other in rules.not_before.items():
        value, bound = row.get(column), row.get(other)
        if value is not None and bound is not None and value < bound and binds(column, "not_before", other):
            raise RowRuleError(table, "not_before", column, f"earlier than {other}")
    for column, other in rules.at_most.items():
        value, bound = row.get(column), row.get(other)
        if value is not None and bound is not None and value > bound and binds(column, "at_most", other):
            raise RowRuleError(table, "at_most", column, f"above {other}")
    for column, condition in rules.null_or_zero_when.items():
        value = row.get(column)
        if value not in (None, 0) and _matches(row, condition):
            raise RowRuleError(table, "null_or_zero_when", column)
    for column, seconds in rules.max_after_write_seconds.items():
        value = row.get(column)
        if value is not None and value - created_timestamp > timedelta(seconds=seconds):
            raise RowRuleError(table, "max_after_write_seconds", column, f"more than {seconds} s after write")


def _check_content(table: str, row: Mapping[str, Any], rules: RowRules) -> None:
    rule = rules.content
    assert rule is not None
    inline, uri, size = row.get(rule.inline), row.get(rule.uri), row.get(rule.size)
    if inline is not None and rule.integrity and "integrity" not in rules.exemptions:
        data = inline.encode("utf-8")
        if row.get(rule.sha) != hashlib.sha256(data).hexdigest() or size != len(data):
            raise RowRuleError(table, "integrity", rule.inline, f"{rule.sha}/{rule.size} do not describe the payload")
    if size is None:
        return
    if inline is not None and size > rule.threshold and "inline_within_threshold" not in rules.exemptions:
        raise RowRuleError(table, "inline_within_threshold", rule.inline, f"{rule.size} above {rule.threshold}")
    if uri is not None and size <= rule.threshold and "uri_above_threshold" not in rules.exemptions:
        raise RowRuleError(table, "uri_above_threshold", rule.uri, f"{rule.size} not above {rule.threshold}")
    if rule.cap is not None and (inline is not None or uri is not None) and size > rule.cap:
        if "within_cap" not in rules.exemptions:
            raise RowRuleError(table, "within_cap", rule.inline if inline is not None else rule.uri, f"above {rule.cap}")


__all__ = [
    "ContentRule",
    "EXEMPTABLE_LEGS",
    "PRODUCER_SIDE_KINDS",
    "RowRuleError",
    "RowRules",
    "RuleProjectionError",
    "check_row",
]
