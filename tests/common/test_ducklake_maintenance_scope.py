"""Tests for src/common/ducklake_maintenance_scope.py -- pure catalog-scope resolution
(compaction-scope-policy-matrix, T2.18).

Every function under test is pure (no connection, no I/O), so all coverage here is unmocked direct
assertion over fixture dicts shaped like field_semantics.yaml's ops_tables / maintenance_policy.
"""

from __future__ import annotations

import pytest

from src.common import ducklake_maintenance_scope as scope

pytestmark = pytest.mark.unit


def _semantics(**extra_ops_tables) -> dict:
    """A minimal field_semantics-shaped fixture: one table of each class, plus a dormant one."""
    ops_tables = {
        "ops_recommendations": {
            "status": "live",
            "history_table": "ops_recommendations_history",
            "current_table": "ops_recommendations_current",
        },
        "ops_smoke_events": {
            "status": "smoke",
            "write_mode": "append_only",
            "history_table": "ops_smoke_events_history",
        },
        "ops_entity_counters": {
            "status": "live",
            "write_mode": "control",
        },
        "ops_priority_queue": {
            "status": "dormant",
            "history_table": "ops_priority_queue_history",
            "current_table": "ops_priority_queue_current",
        },
    }
    ops_tables.update(extra_ops_tables)
    return {"ops_tables": ops_tables}


def _tables_block(*, history_name: str = "ducklake_smoke_history", current_name: str = "ducklake_smoke_current") -> dict:
    """A field_semantics-shaped `tables:` block, matching the smoke-harness pair's declared shape."""
    return {
        "history": {
            "name": history_name,
            "partition": "year(created_timestamp), month(created_timestamp), day(created_timestamp)",
        },
        "current": {"name": current_name, "partition": "bucket(8, rec_id)"},
    }


def _semantics_with_smoke_tables(**extra_ops_tables) -> dict:
    semantics = _semantics(**extra_ops_tables)
    semantics["tables"] = _tables_block()
    return semantics


def _policy(**overrides) -> dict:
    base = {
        "scd2": {"merge_ops": {"apply": True, "reason": "standard"}},
        "append_only": {"merge_ops": {"apply": True, "reason": "standard"}},
        "control": {"merge_ops": {"apply": True, "reason": "standard"}},
    }
    for cls, cell in overrides.items():
        base[cls] = cell
    return base


# ---------------------------------------------------------------------------
# build_registry / classify_table
# ---------------------------------------------------------------------------
class TestBuildRegistryAndClassify:
    def test_scd2_table_maps_history_and_current(self):
        registry = scope.build_registry(_semantics())
        hist = registry["ops_recommendations_history"]
        cur = registry["ops_recommendations_current"]
        assert hist.table_id == "ops_recommendations" and hist.table_class == "scd2" and hist.side == "history"
        assert cur.table_id == "ops_recommendations" and cur.table_class == "scd2" and cur.side == "current"

    def test_append_only_table_has_no_current_entry(self):
        registry = scope.build_registry(_semantics())
        assert "ops_smoke_events_history" in registry
        assert "ops_smoke_events_current" not in registry
        assert registry["ops_smoke_events_history"].table_class == "append_only"

    def test_control_table_maps_to_itself(self):
        registry = scope.build_registry(_semantics())
        entry = registry["ops_entity_counters"]
        assert entry.table_id == "ops_entity_counters"
        assert entry.table_class == "control"
        assert entry.side == "table"

    def test_classify_unknown_physical_name_is_none(self):
        registry = scope.build_registry(_semantics())
        assert scope.classify_table("some_stray_table", registry) is None

    def test_default_write_mode_is_scd2(self):
        """A table with no write_mode key defaults to scd2 -- No class inherits a default silently
        elsewhere: this is the ONE declared default, mirrored from ducklake_scd2_schema."""
        registry = scope.build_registry(_semantics())
        assert registry["ops_recommendations_history"].table_class == "scd2"


# ---------------------------------------------------------------------------
# class_universe -- derived from write_mode, never from maintenance_policy's own keys
# ---------------------------------------------------------------------------
class TestClassUniverse:
    def test_covers_all_three_live_classes(self):
        assert scope.class_universe(_semantics()) == frozenset({"scd2", "append_only", "control"})

    def test_class_universe_is_independent_of_policy_matrix(self):
        """A class present in ops_tables but ABSENT from any maintenance_policy row must still
        appear in the universe -- class_universe never reads maintenance_policy, so it cannot
        become a self-referential oracle that only ever sees classes the matrix already declares."""
        semantics = _semantics(ops_widget={"status": "live", "write_mode": "widget"})
        universe = scope.class_universe(semantics)
        assert "widget" in universe
        # No maintenance_policy key for "widget" anywhere in this fixture -- proves the universe
        # was NOT derived from (or filtered by) the policy matrix's own keys.
        policy = _policy()
        assert "widget" not in policy


# ---------------------------------------------------------------------------
# load_policy
# ---------------------------------------------------------------------------
class TestLoadPolicy:
    def test_returns_matrix_when_present(self):
        semantics = {"maintenance_policy": _policy()}
        assert scope.load_policy(semantics) == _policy()

    def test_raises_when_absent(self):
        with pytest.raises(scope.DuckLakeMaintenanceScopeError, match="maintenance_policy is absent"):
            scope.load_policy({})

    def test_raises_when_empty(self):
        with pytest.raises(scope.DuckLakeMaintenanceScopeError, match="maintenance_policy is absent"):
            scope.load_policy({"maintenance_policy": {}})


# ---------------------------------------------------------------------------
# resolve_scope
# ---------------------------------------------------------------------------
class TestResolveScope:
    def _registry(self):
        return scope.build_registry(_semantics())

    def test_classified_apply_true_goes_to_merge(self):
        result = scope.resolve_scope(
            ["ops_recommendations_history"], verb="merge_ops", policy=_policy(), registry=self._registry()
        )
        assert result.to_merge == ("ops_recommendations_history",)
        assert result.skipped == ()
        assert result.unclassified == ()

    def test_classified_apply_false_with_reason_is_skipped(self):
        policy = _policy(scd2={"merge_ops": {"apply": False, "reason": "quarantined for maintenance"}})
        result = scope.resolve_scope(
            ["ops_recommendations_history"], verb="merge_ops", policy=policy, registry=self._registry()
        )
        assert result.to_merge == ()
        assert result.skipped == (
            {"table": "ops_recommendations_history", "table_class": "scd2", "reason": "quarantined for maintenance"},
        )

    def test_classified_apply_false_without_reason_raises(self):
        policy = _policy(scd2={"merge_ops": {"apply": False}})
        with pytest.raises(scope.DuckLakeMaintenanceScopeError, match="no reason"):
            scope.resolve_scope(["ops_recommendations_history"], verb="merge_ops", policy=policy, registry=self._registry())

    def test_missing_policy_cell_for_classified_table_raises(self):
        """A table classifies fine, but its class has no cell for the requested verb -- fail
        closed rather than silently skipping (validate_maintenance_policy_matrix should have
        caught this pre-deploy; resolve_scope is the runtime backstop)."""
        policy = {"scd2": {}, "append_only": _policy()["append_only"], "control": _policy()["control"]}
        with pytest.raises(scope.DuckLakeMaintenanceScopeError, match="no cell for class"):
            scope.resolve_scope(["ops_recommendations_history"], verb="merge_ops", policy=policy, registry=self._registry())

    def test_unclassifiable_table_is_collected_not_raised(self):
        """Unclassifiable tables are COLLECTED, never raised on individually here -- the caller
        (action_merge_ops) decides when/whether to raise after merging everything classified."""
        result = scope.resolve_scope(["a_stray_table"], verb="merge_ops", policy=_policy(), registry=self._registry())
        assert result.to_merge == ()
        assert result.unclassified == ("a_stray_table",)

    def test_mixed_batch_merges_classified_and_collects_unclassified(self):
        result = scope.resolve_scope(
            ["ops_recommendations_history", "ops_entity_counters", "a_stray_table"],
            verb="merge_ops",
            policy=_policy(),
            registry=self._registry(),
        )
        assert set(result.to_merge) == {"ops_recommendations_history", "ops_entity_counters"}
        assert result.unclassified == ("a_stray_table",)

    def test_control_class_table_is_merge_eligible(self):
        """rec-3762: ops_entity_counters (control class) must be reachable through resolve_scope
        when its policy cell is apply=true -- included by declared cell, not naming accident."""
        result = scope.resolve_scope(["ops_entity_counters"], verb="merge_ops", policy=_policy(), registry=self._registry())
        assert result.to_merge == ("ops_entity_counters",)


# ---------------------------------------------------------------------------
# smoke_harness -- declared `tables:` membership (Decision 191 amendment,
# PLAN-ducklake-smoke-harness-classification), never a name pattern
# ---------------------------------------------------------------------------
class TestSmokeHarnessRegistry:
    def test_build_registry_classifies_declared_smoke_harness_pair(self):
        registry = scope.build_registry(_semantics_with_smoke_tables())
        hist = registry["ducklake_smoke_history"]
        cur = registry["ducklake_smoke_current"]
        assert hist.table_id == "ducklake_smoke_history"
        assert hist.table_class == scope.SMOKE_HARNESS_CLASS
        assert hist.side == "history"
        assert cur.table_id == "ducklake_smoke_current"
        assert cur.table_class == scope.SMOKE_HARNESS_CLASS
        assert cur.side == "current"

    def test_build_registry_no_smoke_harness_entries_without_tables_block(self):
        registry = scope.build_registry(_semantics())
        assert "ducklake_smoke_history" not in registry
        assert "ducklake_smoke_current" not in registry

    def test_build_registry_tolerates_a_missing_side_in_the_tables_block(self):
        """A `tables:` block declaring only one side (e.g. mid-migration) registers that side
        and skips the absent one -- never a KeyError on the missing side."""
        semantics = _semantics_with_smoke_tables()
        del semantics["tables"]["current"]
        registry = scope.build_registry(semantics)
        assert "ducklake_smoke_history" in registry
        assert "ducklake_smoke_current" not in registry

    def test_build_registry_raises_on_smoke_harness_name_collision(self):
        semantics = _semantics_with_smoke_tables()
        semantics["tables"]["history"]["name"] = "ops_recommendations_history"
        with pytest.raises(scope.DuckLakeMaintenanceScopeError, match="collides"):
            scope.build_registry(semantics)

    def test_class_universe_includes_smoke_harness_only_when_tables_declared(self):
        assert scope.SMOKE_HARNESS_CLASS not in scope.class_universe(_semantics())
        assert scope.SMOKE_HARNESS_CLASS in scope.class_universe(_semantics_with_smoke_tables())

    def test_tables_block_agrees_with_smoke_spec(self):
        """The `tables:` block (MEMBERSHIP source) and partition_transforms via resolve_table_spec(None)
        (declared-SPEC source, consulted by _declared_spec_text) must never diverge -- the live,
        generated field_semantics.yaml pins both names and both normalized partition specs."""
        from src.common.ducklake_partition_spec import normalize_partition_spec
        from src.common.ducklake_scd2_schema import (
            SMOKE_CURRENT_TABLE,
            SMOKE_HISTORY_TABLE,
            load_field_semantics,
            resolve_table_spec,
        )

        semantics = load_field_semantics()
        tables = semantics["tables"]
        assert tables["history"]["name"] == SMOKE_HISTORY_TABLE
        assert tables["current"]["name"] == SMOKE_CURRENT_TABLE

        smoke_spec = resolve_table_spec(None, semantics)
        assert normalize_partition_spec(tables["history"]["partition"]) == normalize_partition_spec(
            smoke_spec.partition_history
        )
        assert normalize_partition_spec(tables["current"]["partition"]) == normalize_partition_spec(
            smoke_spec.partition_current
        )


# ---------------------------------------------------------------------------
# reconcile_catalog -- catalog vs. registry, BOTH divergence directions
# ---------------------------------------------------------------------------
class TestReconcileCatalog:
    def test_catalog_table_absent_from_registry_is_catalog_unregistered(self):
        result = scope.reconcile_catalog(["a_stray_table"], semantics=_semantics())
        assert result.catalog_unregistered == ("a_stray_table",)

    def test_registered_dormant_table_absent_from_catalog_is_registered_absent(self):
        """A dormant table's physical absence from the (production) catalog enumeration is
        EXPECTED, not a defect -- dispositioned by the registry's own `status` field."""
        result = scope.reconcile_catalog(
            ["ops_recommendations_history", "ops_recommendations_current"], semantics=_semantics()
        )
        absent_ids = {e["table_id"]: e["status"] for e in result.registered_absent}
        assert absent_ids.get("ops_priority_queue") == "dormant"

    def test_registered_live_table_present_in_catalog_is_not_registered_absent(self):
        result = scope.reconcile_catalog(
            ["ops_recommendations_history", "ops_recommendations_current", "ops_entity_counters", "ops_smoke_events_history"],
            semantics=_semantics(),
        )
        absent_ids = {e["table_id"] for e in result.registered_absent}
        assert "ops_recommendations" not in absent_ids
        assert "ops_entity_counters" not in absent_ids
        assert "ops_smoke_events" not in absent_ids

    def test_both_divergence_directions_in_one_pass(self):
        """A catalog with one unregistered stray table AND missing the dormant registered table --
        both directions of divergence must be reported simultaneously, not one masking the other."""
        result = scope.reconcile_catalog(
            ["ops_recommendations_history", "ops_recommendations_current", "a_stray_table"],
            semantics=_semantics(),
        )
        assert result.catalog_unregistered == ("a_stray_table",)
        absent_ids = {e["table_id"] for e in result.registered_absent}
        assert "ops_priority_queue" in absent_ids  # registered (dormant), absent from this catalog
        assert "ops_entity_counters" in absent_ids  # registered (live control), absent from this catalog


# ---------------------------------------------------------------------------
# VERB_UNIVERSE -- Decision 204's two new per-table verbs
# ---------------------------------------------------------------------------


def test_partition_verbs_in_universe_with_control_rewrite_excluded():
    assert scope.VERB_UNIVERSE == ("merge_ops", "reconcile_partitions", "rewrite_partition_layout")

    policy = {
        "scd2": {
            "merge_ops": {"apply": True, "reason": "standard"},
            "reconcile_partitions": {"apply": True, "reason": "standard"},
            "rewrite_partition_layout": {"apply": True, "reason": "standard"},
        },
        "append_only": {
            "merge_ops": {"apply": True, "reason": "standard"},
            "reconcile_partitions": {"apply": True, "reason": "standard"},
            "rewrite_partition_layout": {"apply": True, "reason": "standard"},
        },
        "control": {
            "merge_ops": {"apply": True, "reason": "standard"},
            "reconcile_partitions": {"apply": True, "reason": "standard"},
            "rewrite_partition_layout": {"apply": False, "reason": "entity-id counter, never re-laid"},
        },
    }
    registry = scope.build_registry(_semantics())

    for verb in ("reconcile_partitions", "merge_ops"):
        result = scope.resolve_scope(["ops_recommendations_history"], verb=verb, policy=policy, registry=registry)
        assert result.to_merge == ("ops_recommendations_history",)

    rewrite_result = scope.resolve_scope(
        ["ops_entity_counters"], verb="rewrite_partition_layout", policy=policy, registry=registry
    )
    assert rewrite_result.to_merge == ()
    assert rewrite_result.skipped == (
        {"table": "ops_entity_counters", "table_class": "control", "reason": "entity-id counter, never re-laid"},
    )


def test_event_entries_classify_as_append_only_history():
    """Registering the telemetry event tables adds no class and no policy cell (Decision 191: write_mode
    is the sole class source); each event entry is one append_only history table."""
    semantics = scope.load_field_semantics()
    registry = scope.build_registry(semantics)
    events = [t for t, e in semantics["ops_tables"].items() if e.get("table_class") == "event"]
    assert len(events) >= 4 and {"telemetry_sessions", "telemetry_agents"} <= set(events)
    for table in events:
        classified = registry[table]
        assert (classified.table_class, classified.side, classified.table_id) == ("append_only", "history", table)
        assert f"{table}_current" not in registry
    assert scope.class_universe(semantics) == {"scd2", "append_only", "control", "smoke_harness"}


def test_registered_absent_preproduction_status_is_expected():
    """The pre-production tables, absent from a production enumeration, report status pre_production
    (registered-and-absent is expected for them; only a live table's absence is notable)."""
    semantics = scope.load_field_semantics()
    result = scope.reconcile_catalog(["ops_recommendations_history", "ops_recommendations_current"], semantics=semantics)
    absent = {e["table_id"]: e["status"] for e in result.registered_absent}
    for table in (
        "telemetry_sessions",
        "telemetry_observations",
        "telemetry_transcripts",
        "telemetry_agents",
        "ops_tenants",
        "ops_projects",
    ):
        assert absent[table] == "pre_production"
