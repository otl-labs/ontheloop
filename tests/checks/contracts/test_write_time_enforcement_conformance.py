# complexity-waiver: decision-43
"""rec-4158's acceptance node and the writer-adapter proof (Decision 210; Decisions 187, 191 derived, never enumerated).

test_every_write_decidable_rule_has_a_writer_validator runs the registered conformance check on the real tree. The
adapter test then derives the writer's handlers from every Lambda manifest and, for each row-writing action and each
table it writes, breaks a rule-clean record in exactly each rule (kind, column) the table's RowRules enforce and asserts
the adapter refuses it naming that rule and column. An action, handler or rule it cannot classify fails.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_write_time_enforcement_conformance as conformance
from src.common import ducklake_control_tables as control
from src.common import ducklake_runtime as rt
from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_scd2_schema import SchemaGateError, load_field_semantics
from src.common.ducklake_write_rules import RowRuleViolationError, rules_for
from src.row_rules.rules import RowRules
from src.telemetry.append import prepare_batch
from src.telemetry.gate import GateError, RowRuleGateError
from tests.fixtures.ducklake_fakes import FakeCon, ops_rec_record
from tests.fixtures.turn_capture_ducklake import load_specs

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(seconds=30, microseconds=999_999)
TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
_SHA = "a" * 64  # pragma: allowlist secret


def _event(ref: str, **over: Any) -> dict[str, Any]:
    row = {
        "event_kind": "point",
        "event_timestamp": T0 + timedelta(seconds=5),
        "session_started_at": T0,
        "external_ref": ref,
        "entity_ref": ref,
        "session_ref": "sess",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 3,
    }
    return {**row, **over}


def _session(ref: str, **over: Any) -> dict[str, Any]:
    return {k: v for k, v in _event(ref, **over).items() if k != "session_ref"}


def _observation(**over: Any) -> dict[str, Any]:
    return _event("o#0", **over)


# Scenarios: complete rule-clean rows, one per combination of conditional rules the table declares (the first is the base).
SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "ops_recommendations": [ops_rec_record()],
    "ops_decisions": [{"id": "dec-1", "title": "A decision", "status": "active"}],
    "ops_tenants": [
        {
            "tenant_id": TENANT,
            "principal_kind": "aws_account",
            "principal_ref": "placeholder",
            "status": "active",
            "canonical_tenant_id": None,
        }  # fmt: skip
    ],
    "ops_projects": [
        {
            "project_id": PROJECT,
            "tenant_id": TENANT,
            "project_ref": "proj-1",
            "registration": "registered",
            "status": "active",
            "purpose": "real",
        }  # fmt: skip
    ],
    "telemetry_sessions": [
        _session(
            "s#0",
            event_kind="open",
            entity_ref="sess",
            workflow="implement",
            execution_attempt=1,
            harness_session_key="session_01AbC",
            checkout_sha="a" * 40,
            checkout_base_sha="b" * 40,
            checkout_dirty=False,
        ),  # fmt: skip
        _session("s#1", event_kind="close", entity_ref="sess", outcome="success"),
        _session(
            "s#2",
            event_kind="annotate",
            entity_ref="sess",
            pr_url="https://example.invalid/pr/1",
            ci_outcome="green",
            coverage_after=90.5,
        ),  # fmt: skip
    ],
    "telemetry_observations": [
        _observation(observation_type="process_event", name="hook:probe", severity="info"),
        _observation(
            observation_type="tool_call",
            event_kind="close",
            outcome="success",
            output_capture="captured",
            output_bytes=5,
            output_sha256=_SHA,
            output_lines=1,
        ),  # fmt: skip
        _observation(
            observation_type="model_call",
            tokens_input=1,
            tokens_output=2,
            tokens_cache_read=0,
            tokens_cache_creation=5,
            cache_creation_1h_tokens=3,
            cache_creation_5m_tokens=2,
            usage_final=True,
            usage_source="record",
            requested_model="claude-opus-5-5",
            reasoning_effort="xhigh",
            billing_shape="metered_marginal",
            reasoning_visibility="full",
            reasoning_tokens=1,
        ),  # fmt: skip
        _observation(
            observation_type="model_call",
            tokens_input=1,
            tokens_output=2,
            tokens_cache_read=0,
            tokens_cache_creation=0,
            billing_shape="metered_marginal",
            reasoning_visibility="none",
            reasoning_tokens=0,
        ),  # fmt: skip
    ],
    "telemetry_transcripts": [
        _event(
            "t#0",
            observation_ref="turn-1",
            purpose="prompt",
            origin="human",
            content="hello",
            content_sha256=hashlib.sha256(b"hello").hexdigest(),
            content_bytes=5,
            content_truncated=False,
        ),  # fmt: skip
        _event(
            "t#1",
            observation_ref="turn-1",
            purpose="prompt",
            origin="human",
            content_omitted_reason="withheld_by_policy",
            content_sha256=hashlib.sha256(b"").hexdigest(),
            content_bytes=0,
            content_truncated=False,
        ),  # fmt: skip
    ],
    "telemetry_agents": [
        _event("a#0", event_kind="open", agent_type="manual"),
        _event("a#1", event_kind="close", agent_type="manual", outcome="success"),
        _event(
            "a#2",
            event_kind="open",
            agent_type="manual",
            trigger="workflow_run",
            entity_ref="a#2/run",
            host_run_ref="gha:1/1/rca/1",
        ),  # fmt: skip
        _event("a#3", event_kind="open", agent_type="manual", trigger="subagent_sync"),
    ],
}


def _ignore(*_a: Any, **_k: Any) -> None:
    return None


class _ReachedCatalog(Exception):
    """The adapter passed its row-rule gate and touched the catalog: the record was accepted."""


class _NoCatalogCon:
    def execute(self, sql: str, params: Any = None) -> _NoCatalogCon:
        raise _ReachedCatalog(sql)


@functools.lru_cache(maxsize=1)
def specs() -> dict[str, Any]:
    return load_specs()


def rules_of(table: str) -> RowRules:
    """The RowRules the adapter for *table* evaluates (the event spec's own, or the SCD2 spec's rules_for)."""
    if table in specs():
        return specs()[table].rules
    rules = rules_for(rt.resolve_table_spec(table))
    assert rules is not None, table
    return rules


@functools.lru_cache(maxsize=1)
def semantics() -> dict[str, Any]:
    return load_field_semantics()


def columns_of(table: str) -> dict[str, dict[str, Any]]:
    return semantics()["ops_tables"][table]["columns"]


def write_ops(table: str, record: dict[str, Any]) -> None:
    rt.write_scd2(FakeCon(created_lookup=None), dict(record), table=table, sleep=_ignore)


def update_ops(table: str, record: dict[str, Any]) -> None:
    prior = (datetime.now(timezone.utc),)
    rt.write_scd2(FakeCon(created_lookup=[prior]), dict(record), table=table, require_exists=True, sleep=_ignore)


def file_ops(table: str, record: dict[str, Any]) -> None:
    merge_key = rt.resolve_table_spec(table).merge_key
    rt.file_scd2(_NoCatalogCon(), {k: v for k, v in record.items() if k != merge_key}, table=table)


def register(table: str, record: dict[str, Any]) -> None:
    spec = rt.resolve_table_spec(table)
    inputs = {n for n, _ in spec.ordered_columns if spec.fields[n].get("role") == "input"}
    reg._write_dimension(
        _NoCatalogCon(),
        table,
        {k: v for k, v in record.items() if k in inputs},
        derived={k: v for k, v in record.items() if k not in inputs},
        identity=rt.mint_write_identity(),
        prior=None,
        semantics=semantics(),
    )


def append_telemetry(table: str, record: dict[str, Any]) -> None:
    present = {k: v for k, v in record.items() if v is not None}  # a null is expressed by omitting the key
    prepare_batch(specs()[table], [present], tenant_id=TENANT, project_id=PROJECT, now=NOW)


def outcome(adapter: Any, table: str, record: dict[str, Any]) -> tuple[str, str | None] | None:
    """(rule, column) of the refusal, or None when the adapter accepted the record."""
    try:
        adapter(table, record)
    except RowRuleViolationError as exc:
        return exc.rule, exc.column
    except RowRuleGateError as exc:
        return exc.rule, exc.column
    except GateError as exc:
        return str(exc.reason), exc.column
    except SchemaGateError as exc:
        return "schema_gate", str(exc)
    except _ReachedCatalog:
        return None
    return None


def _bad_text(pattern_ok: Any, also_ok: Any = None) -> str:
    """A string *pattern_ok* refuses; with *also_ok* it must still satisfy that pattern (a conditional rule's mutation)."""
    for candidate in ("~", "~ NOT A MATCH ~", "!!", "/abs/path", "a\\b", "x" * 300, "\u00e9\u00fc", _SHA):
        if pattern_ok(candidate) is None and (also_ok is None or also_ok(candidate) is not None):
            return candidate
    raise AssertionError("no candidate violates the pattern")


_NONZERO = {"host_run_ref": "gha:1/1/rca/1"}  # a null_or_zero_when string column's breaking value must still match its pattern


def matches(row: dict[str, Any], condition: Any) -> bool:
    return all(row.get(column) in allowed for column, allowed in condition.items())


def kind_columns(rules: RowRules) -> set[tuple[str, str]]:
    """Every (kind, column) the engine enforces for this table, derived from the RowRules fields."""
    out = {("not_null", c) for c in rules.not_null}
    for kind, mapping in (
        ("accepted_values", rules.accepted_values),
        ("min_length", rules.min_length),
        ("pattern", rules.patterns),
        ("pattern_when", rules.pattern_when),
        ("array_element_format", rules.element_formats),
        ("required_when", rules.required_when),
        ("not_before", rules.not_before),
        ("at_most", rules.at_most),
        ("null_or_zero_when", rules.null_or_zero_when),
        ("max_after_write_seconds", rules.max_after_write_seconds),
    ):
        out |= {(kind, column) for column in mapping}
    out |= {("exactly_one_of", group[0]) for group in rules.exactly_one_of}
    content = rules.content
    if content is not None:
        for leg, column in (
            ("integrity", content.inline),
            ("uri_above_threshold", content.uri),
            ("within_cap", content.uri),
        ):
            if leg not in rules.exemptions and (leg != "within_cap" or content.cap is not None):
                if leg != "integrity" or content.integrity:
                    out.add((leg, column))
    return out


def mutation(rules: RowRules, row: dict[str, Any], kind: str, column: str) -> dict[str, Any] | None:
    """*row* broken in exactly the rule (kind, column), or None when this scenario cannot exercise it."""
    out = dict(row)
    if kind == "not_null":
        out[column] = None
    elif kind == "accepted_values":
        out[column] = "zz-not-an-accepted-value"
    elif kind == "min_length":
        floor, pattern = rules.min_length[column], rules.patterns.get(column)
        shortest = ("x" * (floor - 1), " " * floor, "")
        out[column] = next(c for c in shortest if len(c.strip()) < floor and (pattern is None or pattern.fullmatch(c)))
    elif kind == "pattern":
        out[column] = _bad_text(rules.patterns[column].fullmatch)
    elif kind == "array_element_format":
        out[column] = ["~ bad element ~"]
    elif kind == "pattern_when":
        for condition, pattern in rules.pattern_when[column]:
            if matches(row, condition) and row.get(column) is not None:
                base = rules.patterns.get(column)
                out[column] = _bad_text(pattern.fullmatch, base.fullmatch if base else None)
                return out
        return None
    elif kind == "required_when":
        if row.get(column) is None or not matches(row, rules.required_when[column]):
            return None
        out[column] = None
    elif kind == "null_or_zero_when":
        if not matches(row, rules.null_or_zero_when[column]):
            return None
        out[column] = _NONZERO.get(column, 1)
    elif kind == "not_before":
        other = rules.not_before[column]
        if row.get(column) is None or row.get(other) is None:
            return None
        out[column] = row[other] - timedelta(days=1)
    elif kind == "at_most":
        other = rules.at_most[column]
        if row.get(column) is None or row.get(other) is None:
            return None
        out[column] = row[other] + 1
    elif kind == "max_after_write_seconds":
        out[column] = NOW + timedelta(seconds=rules.max_after_write_seconds[column] + 60)
    elif kind == "exactly_one_of":
        group = next(g for g in rules.exactly_one_of if g[0] == column)
        if all(row.get(c) is None for c in group):
            return None
        for member in group:
            out[member] = None
    elif rules.content is not None and kind == "integrity":
        if row.get(rules.content.inline) is None:
            return None
        out[rules.content.sha] = "f" * 64
    elif rules.content is not None and kind in ("uri_above_threshold", "within_cap"):
        if row.get(rules.content.inline) is None:  # an omission row has no inline payload to move to a uri
            return None
        threshold, cap = rules.content.threshold, rules.content.cap
        out.update({rules.content.inline: None, rules.content.uri: "s3://placeholder/object"})
        out[rules.content.size] = threshold if kind == "uri_above_threshold" else (cap or threshold) + 1
        out[rules.content.sha] = _SHA
    else:
        raise AssertionError(f"no mutation for {kind} on {column}")
    return out


_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _ROOT / "docs" / "contracts"
_FS = yaml.safe_load((_ROOT / "config/lambda/ducklake/field_semantics.yaml").read_text(encoding="utf-8"))

# handler module -> {action: (adapter, table filter)} for every row-writing action; the test exercises exactly these
_ROW_WRITING = {
    "src/lambdas/ducklake_writer/handler.py": {"write_ops": write_ops, "update_ops": update_ops, "file_ops": file_ops},
    "src/lambdas/ducklake_telemetry_writer/handler.py": {
        "register_tenant": register,
        "register_project": register,
        "append_telemetry": append_telemetry,
    },
}
_NON_ROW_WRITING: dict[str, dict[str, str]] = {
    "src/lambdas/ducklake_writer/handler.py": {
        "attach_check": "probe",
        "create_tables": "provisioning: the smoke pair",
        "write": "the smoke pair's write (ops_smoke_events and the history/current pair carry no row rules)",
        "create_ops_tables": "provisioning, refused for dormant-free tables only; writes no row",
        "idempotency_probe": "probe",
        "partition_probe": "probe",
        "inlining_probe": "probe",
        "inlining": "probe",
        "loudfail_probe": "probe",
        "churn": "load probe on the smoke pair",
        "churn_single": "load probe on the smoke pair",
        "connect_probe": "probe",
        "reset_warm_connection": "maintenance",
        "describe": "read",
    },
    "src/lambdas/ducklake_telemetry_writer/handler.py": {
        "attach_check": "probe",
        "describe": "read",
        "provision_telemetry": "provisioning: tables and counter seeds, no caller row",
        "telemetry_probe": "probe",
        "begin_blobs": "issues claim-check upload grants; writes no row (the row arrives through append_telemetry)",
    },
    "src/lambdas/ducklake_telemetry_reader/handler.py": dict.fromkeys(
        ("attach_check", "describe", "named_read"), "read or probe: the telemetry reader holds no write grant"
    ),
    "src/lambdas/ducklake_telemetry_content_reader/handler.py": dict.fromkeys(
        ("describe", "named_read"), "read: the telemetry content reader holds no write grant"
    ),
    "src/lambdas/ducklake_reader/handler.py": dict.fromkeys(
        (
            "attach_check read_current partition_prune_check write_probe read_ops_current "
            "read_ops_history named_read dq_check connect_probe reset_warm_connection describe"
        ).split(),
        "read or probe: the reader holds no write grant",
    ),
    "src/lambdas/ducklake_maintenance/handler.py": {
        **dict.fromkeys(
            (
                "catalog_reinit restore_drill merge_ops gc_ops catalog_stats reconcile_columns "
                "clone_catalog control_health reconcile_partitions rewrite_partition_layout "
                "provision_telemetry_login ensure_catalog_indexes"
            ).split(),
            "maintenance: moves or removes files, writes no caller row",
        ),
        "provision_catalog_owner": "provisioning: owner role and ownership, no row",
        "provision_telemetry_catalog": "provisioning: tables and counter seeds, no caller row",
    },
    "src/lambdas/ducklake_maintenance_smoke/handler.py": dict.fromkeys(
        "merge gc hot_merge breaker_probe create_telemetry_tables".split(),
        "disposable ducklake_smoke catalog maintenance or provisioning, no caller row",
    ),
}
_REGISTRY_LESS = {
    "src/lambdas/ducklake_catalog_dr/handler.py": "catalog disaster-recovery job: dump and restore, no row write",
    "src/data/handlers/scheduled_agent_handler.py": "dispatches scheduled agents, writes no warehouse row through the writer",
    "src/data/handlers/findings_processor_handler.py": "findings processor, writes through the portal, not a writer verb",
}
_REGISTRATION_VERBS = {"register_tenant": "ops_tenants", "register_project": "ops_projects"}


def _manifest_handlers() -> list[str]:
    handlers: list[str] = []
    for manifest in sorted((_ROOT / "src" / "lambdas").glob("*/manifest.yaml")):
        handlers.extend(yaml.safe_load(manifest.read_text(encoding="utf-8")).get("handlers") or [])
    return handlers


def _has_registry(handler: str) -> bool:
    tree = ast.parse((_ROOT / handler).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_all_actions":
            return True
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "_ACTIONS" for t in targets):
            return True
    return False


def _actions(handler: str) -> list[str]:
    module = importlib.import_module(handler.removesuffix(".py").replace("/", "."))
    return list(module._all_actions()) if hasattr(module, "_all_actions") else sorted(module._ACTIONS)


def test_every_write_decidable_rule_has_a_writer_validator(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    registry.pop_declaration()
    conformance.validate_write_time_enforcement_conformance(failed)
    declaration = registry.pop_declaration()
    assert failed == []
    inventory = set(_FS["ops_tables"]) | set(control.control_table_names())
    assert f"{len(_FS['ops_tables'])} writer tables each match one arm" in capsys.readouterr().out
    assert declaration is not None and declaration.kind == "examined" and declaration.count > 100
    assert inventory >= {"ops_recommendations", "ops_entity_counters", "ops_priority_queue", "ops_smoke_events"}


def test_every_action_and_handler_is_classified() -> None:
    handlers = _manifest_handlers()
    assert len(handlers) >= 8 and set(_REGISTRY_LESS) <= set(handlers)
    for handler in handlers:
        if handler in _REGISTRY_LESS:
            assert not _has_registry(handler), f"{handler} gained an action registry: classify its actions"
            continue
        assert _has_registry(handler), f"{handler} has no action registry and is not in the declared registry-less map"
        actions = _actions(handler)
        row_writing, other = _ROW_WRITING.get(handler, {}), _NON_ROW_WRITING.get(handler, {})
        unclassified = sorted(set(actions) - set(row_writing) - set(other))
        assert not unclassified, f"{handler}: classify {unclassified} as row-writing (with a case) or not (with a reason)"
        stale = sorted((set(row_writing) | set(other)) - set(actions))
        assert not stale and not (set(row_writing) & set(other)), (handler, stale)
    assert set(_ROW_WRITING) | set(_NON_ROW_WRITING) <= set(handlers)


def _tables(action: str) -> list[str]:
    ops = _FS["ops_tables"]
    generic = [
        t
        for t, entry in ops.items()
        if (_CONTRACTS / f"{t}.yaml").is_file()
        and entry.get("write_mode") != "control"
        and entry.get("table_class") != "event"
        and entry.get("write_boundary") is None
    ]
    if action in ("write_ops", "update_ops"):
        return generic
    if action == "file_ops":
        return [t for t in generic if rt.resolve_table_spec(t).id_keyspace == "writer"]
    if action in _REGISTRATION_VERBS:
        return [_REGISTRATION_VERBS[action]]
    return [t for t, entry in ops.items() if entry.get("write_boundary") == "telemetry_append"]


def test_registration_and_append_verbs_cover_their_boundaries() -> None:
    ops = _FS["ops_tables"]
    assert set(_REGISTRATION_VERBS.values()) == {t for t, e in ops.items() if e.get("write_boundary") == "registration"}
    assert {t for t, e in ops.items() if e.get("write_boundary") == "telemetry_append"} == {
        t for t, e in ops.items() if e.get("table_class") == "event"
    }
    assert set(_tables("write_ops")) == set(SCENARIOS) & set(_tables("write_ops")) and len(_tables("write_ops")) >= 2


_REFUSED_AS_NOT_NULL = {"not_null", "schema_gate", "type", "required_ref", "grain_key_null"}


def _accepted_refusal(kind: str, column: str, refusal: tuple[str, str | None] | None) -> bool:
    if refusal is None:
        return False
    rule, named = refusal
    if kind == "not_null":
        return rule in _REFUSED_AS_NOT_NULL and column in str(named)
    return rule == kind and named == column


def _cases() -> list[tuple[str, str, str]]:
    return [
        (action, table, handler) for handler, verbs in _ROW_WRITING.items() for action in verbs for table in _tables(action)
    ]


@pytest.mark.parametrize(("action", "table", "handler"), _cases(), ids=lambda v: str(v).rsplit("/", 2)[-2:][0])
def test_each_writer_adapter_rejects_every_declared_kind(action: str, table: str, handler: str) -> None:
    adapter = _ROW_WRITING[handler][action]
    scenarios = SCENARIOS[table]
    for scenario in scenarios:
        assert outcome(adapter, table, scenario) is None, f"{table}: a rule-clean scenario was refused by {action}"
    rules, columns = rules_of(table), columns_of(table)
    merge_key = rt.resolve_table_spec(table).merge_key if table in _FS["ops_tables"] and table not in specs() else None
    expected = {
        (kind, column)
        for kind, column in kind_columns(rules)
        if (columns.get(column) or {}).get("role") != "derived" and not (action == "file_ops" and column == merge_key)
    }
    exercised: set[tuple[str, str]] = set()
    for kind, column in sorted(expected):
        for scenario in scenarios:
            broken = mutation(rules, scenario, kind, column)
            if broken is None:
                continue
            refusal = outcome(adapter, table, broken)
            assert _accepted_refusal(kind, column, refusal), f"{action} on {table}: breaking {kind} on {column} -> {refusal}"
            exercised.add((kind, column))
    assert exercised == expected, f"{action} on {table}: no scenario exercises {sorted(expected - exercised)}"
    assert expected, f"{table} declares no enforceable rule at all"
