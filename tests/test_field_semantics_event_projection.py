"""Mapped test for scripts/field_semantics_event_projection.py: fixture-contract projection
shape, fail-closed cases, generate() accepting an event contract without merge_key, and all four
real telemetry contracts projecting (unregistered).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import scripts.field_semantics_event_projection as proj_mod
import scripts.schema_to_field_semantics as schema_mod
from scripts.contracts import load_contract, resolve_refs
from scripts.schema_to_field_semantics import _map_iceberg_type, generate
from src.telemetry.identity import KeyPlan

_ROOT = Path(__file__).parent.parent

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "event_contracts"
_CONTRACTS_DIR = Path(__file__).parent.parent / "docs" / "contracts"

_FIXTURE_KEY_PLAN = {"fixture_events": {"entity_id": KeyPlan("fixture_events:entity_id", "entity_ref", True)}}


@pytest.fixture
def fixture_resolved():
    doc = load_contract(_FIXTURES_DIR / "fixture_events.yaml")
    return doc, resolve_refs(doc, _FIXTURES_DIR)


@pytest.fixture(autouse=True)
def _patch_key_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.telemetry.identity import KEY_PLANS as REAL_KEY_PLANS

    monkeypatch.setattr(proj_mod, "KEY_PLANS", {**REAL_KEY_PLANS, **_FIXTURE_KEY_PLAN})


def _project(table_id, resolved, ops_config=None, partition_by=None, **kwargs):
    directory = _FIXTURES_DIR if table_id == "fixture_events" else _CONTRACTS_DIR
    with patch.object(proj_mod, "_CONTRACTS_DIR", directory):
        return proj_mod.project_event_table(
            table_id, resolved, ops_config or {}, partition_by, map_iceberg_type=_map_iceberg_type, **kwargs
        )


class TestFixtureContractProjectionShape:
    def test_shape(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by, include_prose=True)

        assert entry["write_mode"] == "append_only"
        assert entry["history_table"] == "fixture_events"
        assert "merge_key" not in entry
        assert "current_table" not in entry
        assert "entity_id_prefix" not in entry
        assert "id_keyspace" not in entry
        assert entry["dedupe_key"] == ["producer", "event_id", "parser_version"]
        assert entry["entity_key"] == "entity_id"
        # The stored partition is the RESOLVED history spec, role-prefix stripped -- the raw
        # contract field carries "history=..." (parse_partition_by's role grammar).
        assert doc.governance.partition_by == f"history={entry['partition']['history']}"
        assert entry["partition_column"] == "session_started_at"

    def test_read_derived_field_has_no_column(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
        assert "duration_seconds" not in entry["columns"]

    def test_derived_role_set(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
        derived = {name for name, spec in entry["columns"].items() if spec["role"] == "derived"}
        assert derived == {"event_id", "created_timestamp", "tenant_id", "project_id", "entity_id"}

    def test_bigint_double_list_types(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
        assert entry["columns"]["retry_count"]["sql_type"] == "BIGINT"
        assert entry["columns"]["cost_estimate"]["sql_type"] == "DOUBLE"
        assert entry["columns"]["labels"]["sql_type"] == "VARCHAR[]"

    def test_required_when_takes_precedence_over_nullable_false(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
        outcome = entry["columns"]["outcome"]
        assert outcome["nullable"] is True
        assert outcome["required_when"] == {"event_kind": ["close"]}

    def test_plain_not_null_field_stays_not_null(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
        assert entry["columns"]["workflow"]["nullable"] is False
        assert "required_when" not in entry["columns"]["workflow"]

    def test_include_prose_false_omits_description_and_semantics(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by, include_prose=False)
        assert "description" not in entry["columns"]["workflow"]
        assert "semantics" not in entry["columns"]["workflow"]


class TestFailClosedCases:
    def test_missing_envelope_column_raises(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        truncated = dict(resolved)
        del truncated["event_id"]
        with pytest.raises(ValueError, match="missing required envelope column"):
            _project("fixture_events", truncated, partition_by=doc.governance.partition_by)

    def test_missing_role_prefix_partition_by_rejected(self, fixture_resolved) -> None:
        # No 'history=' role label -- rejected by the shared parse_partition_by grammar itself,
        # before this module's own day-grain check ever runs.
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="malformed"):
            _project("fixture_events", resolved, partition_by="day(session_started_at)")

    def test_current_role_partition_by_rejected(self, fixture_resolved) -> None:
        # An append_only event table declares only the history role -- a current= role (valid SCD2
        # grammar) is fail-closed here, not silently ignored.
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="current="):
            _project(
                "fixture_events",
                resolved,
                partition_by=(
                    "history=year(session_started_at), month(session_started_at), day(session_started_at); "
                    "current=bucket(8, entity_id)"
                ),
            )

    def test_missing_history_role_partition_by_rejected(self, fixture_resolved) -> None:
        # A parseable role-prefixed spec that declares neither 'history=' nor 'current=' -- distinct
        # from the malformed-grammar and the current-role-present cases above; the "must declare a
        # 'history=' role" branch is its own guard, reached only once the current= check clears.
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="must declare a 'history=' role"):
            _project(
                "fixture_events",
                resolved,
                partition_by="bogus=year(session_started_at), month(session_started_at), day(session_started_at)",
            )

    def test_partition_check_delegates_to_validate_partition_spec(self, fixture_resolved) -> None:
        """rec-4073 acceptance node: a spec the SHARED validate_partition_spec predicate accepts
        (year-only is a valid calendar prefix on its own) but that this module's own stricter
        day-grain-triple check must still reject -- proves the shared validator actually runs
        (a locally-reimplemented regex would never see this spec as anything but 'not a triple'
        for a different reason), and that this module still tightens beyond it for event tables.
        """
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="calendar-day triple"):
            _project("fixture_events", resolved, partition_by="history=year(session_started_at)")

    def test_shared_validator_rejection_surfaces(self, fixture_resolved) -> None:
        # A spec the shared validate_partition_spec predicate itself rejects (day() without its
        # coarser year()/month() prefix) -- surfaces as this module's own ValueError, not a raw
        # PartitionSpecError leaking past the projection boundary.
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="history spec is invalid"):
            _project("fixture_events", resolved, partition_by="history=day(session_started_at)")

    def test_missing_partition_by_rejected(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError):
            _project("fixture_events", resolved, partition_by=None)

    def test_partition_column_not_projected_as_timestamptz_rejected(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError):
            _project(
                "fixture_events",
                resolved,
                partition_by="history=year(event_id), month(event_id), day(event_id)",
            )

    def test_migration_columns_in_ops_config_rejected(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        with pytest.raises(ValueError, match="migration_columns"):
            _project(
                "fixture_events",
                resolved,
                ops_config={"migration_columns": {"history": {}}},
                partition_by=doc.governance.partition_by,
            )

    def test_derivation_read_with_realized_true_raises(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        mutated = dict(resolved)
        bad_field = mutated["session_started_at"].model_copy(update={"derivation": {"timing": "read", "realized": True}})
        mutated["session_started_at"] = bad_field
        with pytest.raises(ValueError, match="realized=true"):
            _project("fixture_events", mutated, partition_by=doc.governance.partition_by)

    def test_unregistered_table_raises(self, fixture_resolved) -> None:
        doc, resolved = fixture_resolved
        with pytest.raises(KeyError):
            _project("not_a_registered_table", resolved, partition_by=doc.governance.partition_by)

    def test_table_with_no_entity_ref_key_plan_raises(self, fixture_resolved, monkeypatch: pytest.MonkeyPatch) -> None:
        doc, resolved = fixture_resolved
        # A table registered in KEY_PLANS but with no plan whose ref_field == "entity_ref" (every
        # plan is an FK-only reference) -- _entity_key_column's own defensive fail-closed branch.
        monkeypatch.setattr(
            proj_mod,
            "KEY_PLANS",
            {"fixture_events": {"parent_id": KeyPlan("fixture_events:parent_id", "parent_ref", False)}},
        )
        with pytest.raises(ValueError, match="no entity-key column"):
            _project("fixture_events", resolved, partition_by=doc.governance.partition_by)


class TestGenerateAcceptsEventContractWithoutMergeKey:
    def test_dispatch_via_project_contract_table_needs_no_merge_key(self, fixture_resolved) -> None:
        # scripts.schema_to_field_semantics._project_contract_table is the single dispatch point
        # generate() calls per table; exercising it directly (table_class="event", merge_key=None)
        # is what "generate() accepts an event contract with no merge_key" means in practice, since
        # _CONTRACT_TABLE_IDS never names an event table (Nothing ships this slice).
        from scripts.schema_to_field_semantics import _project_contract_table

        doc, resolved = fixture_resolved
        assert doc.governance.merge_key is None
        with patch.object(proj_mod, "_CONTRACTS_DIR", _FIXTURES_DIR):
            entry = _project_contract_table(
                "fixture_events",
                resolved,
                None,
                {},
                table_class="event",
                partition_by=doc.governance.partition_by,
            )
        assert entry["write_mode"] == "append_only"
        assert "merge_key" not in entry


class TestGenerateMissingMergeKey:
    """Moved from tests/test_schema_to_field_semantics.py (Decision 128 decompose-by-default: that
    file was over its 500-SLOC budget). Not event-specific -- covers generate()'s existing
    non-event merge_key-required error path -- but carries no VP-step node_id reference, unlike
    that file's test_event_class_dispatches_to_event_projection, so it was the one free to move.
    """

    def test_missing_merge_key_raises(self) -> None:
        mock_doc = MagicMock()
        mock_doc.governance = MagicMock()
        mock_doc.governance.merge_key = None

        with patch("scripts.contracts.load_contract", return_value=mock_doc):
            with pytest.raises(ValueError, match="governance.merge_key is missing"):
                generate()


def test_maintenance_policy_absent_from_sidecar_raises(tmp_path: Path) -> None:
    """Moved from tests/test_schema_to_field_semantics.py (Decision 128 decompose-by-default, same
    reason as TestGenerateMissingMergeKey above). REQUIRED, not conditional: a sidecar missing
    maintenance_policy must raise (KeyError), never silently emit a projection without it."""
    sidecar_path = _ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
    sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    del sidecar["maintenance_policy"]
    tmp_sidecar = tmp_path / "field_semantics.static.yaml"
    tmp_sidecar.write_text(yaml.dump(sidecar), encoding="utf-8")

    with patch.object(schema_mod, "_SIDECAR_PATH", tmp_sidecar):
        with pytest.raises(KeyError, match="maintenance_policy"):
            generate()


def test_dedupe_key_is_telemetry_grain(fixture_resolved) -> None:
    """The projected dedupe_key is the telemetry grain (producer, event_id, parser_version) for
    the fixture and for all four real telemetry contracts (rec-4061/R2, grain-enforced-at-write).
    """
    doc, resolved = fixture_resolved
    entry = _project("fixture_events", resolved, partition_by=doc.governance.partition_by)
    assert entry["dedupe_key"] == ["producer", "event_id", "parser_version"]

    for table_id in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        real_doc = load_contract(_CONTRACTS_DIR / f"{table_id}.yaml")
        real_resolved = resolve_refs(real_doc, _CONTRACTS_DIR)
        real_entry = _project(table_id, real_resolved, partition_by=real_doc.governance.partition_by)
        assert real_entry["dedupe_key"] == ["producer", "event_id", "parser_version"], table_id


class TestRealTelemetryContractsProject:
    @pytest.mark.parametrize(
        "table_id",
        ["telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"],
    )
    def test_real_contract_projects_without_error(self, table_id: str) -> None:
        doc = load_contract(_CONTRACTS_DIR / f"{table_id}.yaml")
        resolved = resolve_refs(doc, _CONTRACTS_DIR)
        entry = _project(table_id, resolved, partition_by=doc.governance.partition_by)
        assert entry["write_mode"] == "append_only"
        assert "merge_key" not in entry

    def test_telemetry_sessions_has_no_duration_seconds_column(self) -> None:
        doc = load_contract(_CONTRACTS_DIR / "telemetry_sessions.yaml")
        resolved = resolve_refs(doc, _CONTRACTS_DIR)
        entry = _project("telemetry_sessions", resolved, partition_by=doc.governance.partition_by)
        assert "duration_seconds" not in entry["columns"]

    def test_telemetry_sessions_workflow_projects_nullable(self) -> None:
        doc = load_contract(_CONTRACTS_DIR / "telemetry_sessions.yaml")
        resolved = resolve_refs(doc, _CONTRACTS_DIR)
        entry = _project("telemetry_sessions", resolved, partition_by=doc.governance.partition_by)
        # workflow is nullable:false but carries required_when (open only) -> projects nullable.
        assert entry["columns"]["workflow"]["nullable"] is True
        assert entry["columns"]["workflow"]["required_when"] == {"event_kind": ["open"]}


# ---------------------------------------------------------------------------
# Row rules (Decision 210): projected from the raw contracts, $ref inheritance, fail closed.
# ---------------------------------------------------------------------------
_REAL_TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")


def _real_entry(table_id: str) -> dict:
    doc = load_contract(_CONTRACTS_DIR / f"{table_id}.yaml")
    return _project(table_id, resolve_refs(doc, _CONTRACTS_DIR), partition_by=doc.governance.partition_by)


def test_row_rules_projected_from_contracts() -> None:
    for table_id in _REAL_TABLES:
        event_timestamp = _real_entry(table_id)["columns"]["event_timestamp"]
        assert event_timestamp["not_before"] == "session_started_at", table_id
        assert event_timestamp["max_after_write_seconds"] == 300, table_id
    observations = _real_entry("telemetry_observations")
    columns = observations["columns"]
    assert columns["outcome"]["accepted_values"] == ["success", "error", "blocked", "interrupted"]
    assert columns["outcome"]["required_when"] == {"event_kind": ["close"], "observation_type": ["tool_call"]}
    assert columns["output_bytes"]["required_when"] == {"output_capture": ["not_persisted", "captured", "omitted_oversize"]}
    assert columns["output_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert columns["reasoning_tokens"]["at_most"] == "tokens_output"
    assert columns["reasoning_tokens"]["null_or_zero_when"] == {"reasoning_visibility": ["none"]}
    assert "table_rules" not in observations
    assert _real_entry("telemetry_sessions")["columns"]["workflow"]["nullable"] is True
    transcripts = _real_entry("telemetry_transcripts")
    assert transcripts["table_rules"] == {
        "exactly_one_of": [["content", "content_uri", "content_omitted_reason"]],
        "payload": {
            "inline": "content", "uri": "content_uri", "sha": "content_sha256", "size": "content_bytes",
            "threshold": 65536, "cap": 8388608, "integrity": True,
        },
    }  # fmt: skip
    assert transcripts["columns"]["content_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert transcripts["columns"]["content_omitted_reason"]["accepted_values"] == ["oversize", "withheld_by_policy"]


def test_amended_telemetry_rules_project() -> None:
    columns = _real_entry("telemetry_observations")["columns"]
    process_event_point = {"event_kind": ["point"], "observation_type": ["process_event"]}
    assert columns["severity"]["accepted_values"] == ["info", "warning", "error", "critical"]
    assert columns["severity"]["required_when"] == process_event_point
    assert columns["name"]["required_when"] == process_event_point
    assert "pattern" not in columns["name"]
    assert "state" not in _real_entry("telemetry_sessions")["columns"]


def test_agent_type_accepted_values_come_from_source_registry() -> None:
    registry = yaml.safe_load((_ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8"))
    expected = [entry["canonical_id"] for entry in registry["entries"]]
    assert _real_entry("telemetry_agents")["columns"]["agent_type"]["accepted_values"] == expected
    assert "claude-code-subagent" in expected


def test_representation_of_and_exemptions_projected() -> None:
    columns = _real_entry("telemetry_transcripts")["columns"]
    for name in ("content", "content_uri"):
        assert columns[name]["representation_of"] == ["content_sha256", "content_bytes"], name
    assert "write_time_exemptions" not in columns["content"]
    assert "write_time_exemptions" not in columns["content_uri"]


def _fixture_copy(tmp_path: Path, edit) -> Path:
    import shutil

    target = tmp_path / "contracts"
    shutil.copytree(_FIXTURES_DIR, target)
    edit(target)
    return target


def _edit_yaml(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _intents(directory: Path, table_id: str = "fixture_events") -> dict:
    resolved = resolve_refs(load_contract(_FIXTURES_DIR / "fixture_events.yaml"), _FIXTURES_DIR)
    with patch.object(proj_mod, "_CONTRACTS_DIR", directory):
        return proj_mod._effective_intents(table_id, resolved)


def test_unknown_rule_key_fails_closed(tmp_path: Path) -> None:
    directory = _fixture_copy(
        tmp_path,
        lambda d: _edit_yaml(d / "fixture_events.yaml", lambda c: c["fields"]["retry_count"]["dq_intent"].update(mystery=1)),
    )
    doc = load_contract(directory / "fixture_events.yaml")
    with patch.object(proj_mod, "_CONTRACTS_DIR", directory):
        with pytest.raises(ValueError, match=r"unknown dq_intent rule key\(s\) \['mystery'\]"):
            proj_mod.project_event_table(
                "fixture_events", resolve_refs(doc, directory), {}, doc.governance.partition_by,
                map_iceberg_type=_map_iceberg_type,
            )  # fmt: skip
    with pytest.raises(ValueError, match="accepted_values must carry a values list"):
        proj_mod._check_rule_keys("t", "c", {"accepted_values": ["a"]})


def test_pattern_when_projects_verbatim(tmp_path: Path) -> None:
    rule = [{"when": {"retry_count": [1]}, "pattern": "^x:[a-z]+$"}]
    directory = _fixture_copy(
        tmp_path,
        lambda d: _edit_yaml(
            d / "fixture_events.yaml", lambda c: c["fields"]["retry_count"]["dq_intent"].update(pattern_when=rule)
        ),
    )
    doc = load_contract(directory / "fixture_events.yaml")
    with patch.object(proj_mod, "_CONTRACTS_DIR", directory):
        entry = proj_mod.project_event_table(
            "fixture_events", resolve_refs(doc, directory), {}, doc.governance.partition_by, map_iceberg_type=_map_iceberg_type
        )
    assert entry["columns"]["retry_count"]["pattern_when"] == rule


def test_envelope_rules_layer_under_a_local_block(tmp_path: Path) -> None:
    def add_envelope_rule(directory: Path) -> None:
        _edit_yaml(
            directory / "fixture-event-envelope.yaml",
            lambda c: c["fields"]["event_timestamp"]["dq_intent"].update(
                not_before="session_started_at", max_after_write_seconds=300
            ),
        )

    directory = _fixture_copy(tmp_path, add_envelope_rule)
    intents = _intents(directory)
    assert intents["event_timestamp"] == {
        "not_null": True, "not_before": "session_started_at", "max_after_write_seconds": 300,
    }  # fmt: skip
    assert intents["created_timestamp"] == {"not_null": True}

    def local_adds_and_changes(directory: Path, key: str, value) -> None:
        _edit_yaml(
            directory / "fixture_events.yaml", lambda c: c["fields"]["event_timestamp"]["dq_intent_local"].update({key: value})
        )

    added = _fixture_copy(tmp_path / "a", add_envelope_rule)
    local_adds_and_changes(added, "required_when", {"event_id": ["x"]})
    assert _intents(added)["event_timestamp"]["required_when"] == {"event_id": ["x"]}
    same = _fixture_copy(tmp_path / "b", add_envelope_rule)
    local_adds_and_changes(same, "max_after_write_seconds", 300)
    assert _intents(same)["event_timestamp"]["max_after_write_seconds"] == 300
    changed = _fixture_copy(tmp_path / "c", add_envelope_rule)
    local_adds_and_changes(changed, "max_after_write_seconds", 60)
    with pytest.raises(ValueError, match="changes the inherited rule 'max_after_write_seconds'"):
        _intents(changed)


def test_not_null_by_presence_and_exempt_rule_not_projected() -> None:
    why = {"class": "temporary", "reason": "r", "owner": "rec-1", "review_by": "2027-01-31"}
    assert proj_mod._is_not_null({"not_null": True}, True)[0] is True
    assert proj_mod._is_not_null({"not_null": {"exclude_before": "2026-10-01"}}, True)[0] is True
    assert proj_mod._is_not_null({"not_null": {"enforced": False}}, True)[0] is False
    assert proj_mod._is_not_null({}, True)[0] is False and proj_mod._is_not_null({}, False)[0] is True
    assert proj_mod._is_not_null({"not_null": True, "write_time_exemptions": {"not_null": why}}, True)[0] is False
    intent = {"accepted_values": {"values": ["a"]}, "pattern": "^a$", "write_time_exemptions": {"accepted_values": why}}
    assert proj_mod._column_rules("t", "c", intent) == {"pattern": "^a$", "write_time_exemptions": {"accepted_values": why}}


def test_effective_intents_fail_closed_on_unreadable_or_dangling_input(tmp_path: Path) -> None:
    fallback = _intents(tmp_path)
    assert fallback["retry_count"] == {"not_null": {"enforced": False}} and fallback["event_id"] == {
        "not_null": {"enforced": True}
    }
    (tmp_path / "bad_events.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        _intents(tmp_path, "bad_events")
    (tmp_path / "locked_events.yaml").write_text("fields: {}\n", encoding="utf-8")
    resolved = resolve_refs(load_contract(_FIXTURES_DIR / "fixture_events.yaml"), _FIXTURES_DIR)
    with patch.object(proj_mod, "_CONTRACTS_DIR", tmp_path), patch.object(Path, "read_text", side_effect=OSError("denied")):
        with pytest.raises(ValueError, match="cannot read contract file"):
            proj_mod._effective_intents("locked_events", resolved)
    dangling = _fixture_copy(
        tmp_path / "d",
        lambda d: _edit_yaml(
            d / "fixture_events.yaml",
            lambda c: c["fields"]["event_id"].update({"$ref": "fixture-event-envelope.yaml#/contract/fields/nope"}),
        ),
    )
    with pytest.raises(ValueError, match="not found"):
        _intents(dangling)


def test_registry_sourced_values_fail_closed(tmp_path: Path) -> None:
    def make_registry(directory: Path, doc: dict) -> None:
        (directory / "lineage.yaml").write_text(
            yaml.safe_dump({"contract": {"id": "lineage"}, "fields": {"registry_key": {"dq_intent": {}}}, **doc}),
            encoding="utf-8",
        )

    def point_at_registry(directory: Path, doc: dict, own: dict | None = None) -> None:
        make_registry(directory, doc)
        (directory / "registry.yaml").write_text(
            yaml.safe_dump({"entries": [{"canonical_id": "a"}, {"canonical_id": "b"}]}), encoding="utf-8"
        )

        def change(c: dict) -> None:
            c["fields"]["retry_count"] = {"$ref": "lineage.yaml#/contract/fields/registry_key", "dq_intent_local": own or {}}

        _edit_yaml(directory / "fixture_events.yaml", change)

    good = _fixture_copy(
        tmp_path / "g",
        lambda d: point_at_registry(d, {"allowed_values": {"registry": "registry.yaml", "key": "canonical_id"}}),
    )
    with (
        patch.object(proj_mod, "_REGISTRY_KEY_REF", ("lineage.yaml", "registry_key")),
        patch.object(proj_mod, "_REPO_ROOT", good),
    ):
        assert _intents(good)["retry_count"]["accepted_values"] == {"values": ["a", "b"]}
    with (
        patch.object(proj_mod, "_REGISTRY_KEY_REF", ("lineage.yaml", "registry_key")),
        patch.object(proj_mod, "_REPO_ROOT", good),
    ):
        own = _fixture_copy(
            tmp_path / "o",
            lambda d: point_at_registry(
                d,
                {"allowed_values": {"registry": "registry.yaml", "key": "canonical_id"}},
                {"accepted_values": {"values": ["z"]}},
            ),
        )
        with pytest.raises(ValueError, match="declares no accepted_values of its own"):
            _intents(own)
        bare = _fixture_copy(tmp_path / "n", lambda d: point_at_registry(d, {}))
        with pytest.raises(ValueError, match="must name a registry and a key"):
            _intents(bare)
        empty = _fixture_copy(
            tmp_path / "e",
            lambda d: point_at_registry(d, {"allowed_values": {"registry": "empty.yaml", "key": "canonical_id"}}),
        )
        (empty / "empty.yaml").write_text("entries: []\n", encoding="utf-8")
        with patch.object(proj_mod, "_REPO_ROOT", empty), pytest.raises(ValueError, match="no values or duplicate"):
            _intents(empty)


def test_table_rule_projection_fails_closed() -> None:
    columns = {
        "body": {"sql_type": "VARCHAR"},
        "blob": {"sql_type": "VARCHAR"},
        "digest": {"sql_type": "VARCHAR"},
        "size": {"sql_type": "BIGINT"},
    }
    pair = ["digest", "size"]
    sized = {"content_inline_threshold_bytes": 10, "representation_of": pair, "full_output_cap_bytes": 100, "integrity": True}
    ok = {"body": sized, "blob": {"representation_of": pair}, "digest": {}, "size": {}}
    assert proj_mod._table_rules("t", ok, columns)["payload"] == {
        "inline": "body", "uri": "blob", "sha": "digest", "size": "size", "threshold": 10, "cap": 100, "integrity": True,
    }  # fmt: skip
    plain = {"body": {"content_inline_threshold_bytes": 5, "representation_of": pair}, "blob": {"representation_of": pair}}
    assert proj_mod._table_rules("t", plain, columns)["payload"]["cap"] is None
    assert proj_mod._table_rules("t", {"body": {}}, columns) == {}
    cases = [
        ({"body": {"exactly_one_of": {"fields": ["blob"]}}}, "exactly_one_of must list fields including itself"),
        ({"body": {"exactly_one_of": ["body"]}}, "exactly_one_of must list fields including itself"),
        ({"body": {"integrity": True}}, "without content_inline_threshold_bytes"),
        ({"body": sized, "blob": {**sized}}, "more than one content_inline_threshold_bytes"),
        ({"body": sized}, "exactly one sibling"),
        ({"body": sized, "blob": {"representation_of": pair}, "digest": {"representation_of": pair}}, "exactly one sibling"),
        (
            {"body": {**sized, "representation_of": ["digest"]}, "blob": {"representation_of": ["digest"]}},
            "one VARCHAR digest and one BIGINT",
        ),
        (
            {"body": {**sized, "representation_of": ["digest", "blob"]}, "blob": {"representation_of": ["digest", "blob"]}},
            "one VARCHAR digest and one BIGINT",
        ),
    ]
    for intents, message in cases:
        with pytest.raises(ValueError, match=message):
            proj_mod._table_rules("t", intents, columns)
    grouped = proj_mod._table_rules("t", {"body": {"exactly_one_of": {"fields": ["body", "blob"]}}}, columns)
    assert grouped == {"exactly_one_of": [["body", "blob"]]}
