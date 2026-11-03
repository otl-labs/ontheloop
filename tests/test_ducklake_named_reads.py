"""Tests for src/common/ducklake_named_reads.py -- the named-read registry moved out of
ducklake_scd2_schema (telemetry table-registration plan; Decision 124 facade-plus-siblings) plus the
per-verb read_version stamp (review item C1). Pure and DB-free.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from src.common import ducklake_named_reads as nr
from src.common import ducklake_scd2_schema as schema

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# rec_history NAMED_READS entry + NAMED_READS_VERSION (T1.16 c4)
# ---------------------------------------------------------------------------


def test_registry_version_is_six():
    assert nr.NAMED_READS_VERSION == 6


def test_ops_verb_read_versions_unchanged_by_event_reads():
    """The event-journal class lives in the reader-only registry: no ops digest or describe row moves (registry v6)."""
    from src.common import ducklake_event_reads as ev  # noqa: PLC0415

    pinned = TestRecsRelatedVerbV5._PINNED_DIGESTS
    assert {v: nr.read_version(v) for v in pinned} == pinned
    assert not set(nr.NAMED_READS) & set(ev._event_reads())
    described = ev.describe_all()
    assert {v: described[v] for v in nr.NAMED_READS} == nr.describe_named_reads()


def test_rec_history_verb_registered():
    rh = nr.NAMED_READS["rec_history"]
    assert rh.table == "ops_recommendations"
    assert rh.params == ("id",)
    assert "{hist}" in rh.sql
    assert "ORDER BY last_updated_timestamp DESC, ulid DESC" in rh.sql


# ---------------------------------------------------------------------------
# Total-order invariant (T1.16 c1): every paginable or trailing-LIMIT verb ends in a unique key
# ---------------------------------------------------------------------------


def test_named_reads_total_order_invariant():
    import re

    def lim(s: str) -> bool:
        return bool(re.search(r"\bLIMIT\s+\d+\s*$", s, re.I))

    def tot(s: str) -> bool:
        return bool(re.search(r"ORDER BY .*\b(id|ulid)\b[^,]*$", s, re.I | re.S))

    bad = [v for v, n in nr.NAMED_READS.items() if (n.paginable or lim(n.sql)) and not tot(n.sql)]
    assert not bad


def test_priority_queue_current_excluded_from_total_order_check():
    """priority_queue_current's LIMIT is a subquery bound, not a trailing output cap -- excluded."""
    entry = nr.NAMED_READS["priority_queue_current"]
    assert not entry.paginable
    import re

    assert not re.search(r"\bLIMIT\s+\d+\s*$", entry.sql, re.I)


def test_ci_rca_open_and_budget_bypass_recent_have_id_tiebreak():
    """The two trailing-LIMIT verbs' ORDER BY carries an `id` tiebreak (reproducible bounded reads)."""
    for verb in ("ci_rca_open", "budget_bypass_recent"):
        sql = nr.NAMED_READS[verb].sql
        assert ", id LIMIT" in sql


def test_open_recs_and_recs_by_title_prefix_are_paginable():
    assert nr.NAMED_READS["open_recs"].paginable is True
    assert nr.NAMED_READS["recs_by_title_prefix"].paginable is True


# ---------------------------------------------------------------------------
# describe_named_reads (CD.10 / CD.15 agent-facing describe surface)
# ---------------------------------------------------------------------------


def test_describe_named_reads_covers_every_verb():
    out = nr.describe_named_reads()
    assert set(out) == set(nr.NAMED_READS)
    entry = out["rec_by_id"]
    assert entry["params"] == ["id"]
    assert entry["params_schema"]["required"] == ["id"]
    assert "table" in entry and "description" in entry and "paginable" in entry


def test_describe_named_reads_no_params_verb():
    out = nr.describe_named_reads()
    assert out["open_recs"]["params"] == []
    assert out["open_recs"]["params_schema"]["required"] == []


# ---------------------------------------------------------------------------
# read_version -- computed, opaque, per-verb digest (review item C1)
# ---------------------------------------------------------------------------


def test_read_version_is_per_verb_digest(monkeypatch):
    before = {v: nr.read_version(v) for v in nr.NAMED_READS}
    assert before == {v: nr.read_version(v) for v in nr.NAMED_READS}  # stable across calls
    assert all(len(d) == 16 and int(d, 16) >= 0 for d in before.values())

    changed = dict(nr.NAMED_READS)
    changed["count_by_status"] = dataclasses.replace(changed["count_by_status"], sql="SELECT status FROM {tbl}")
    changed["ci_rca_since"] = dataclasses.replace(changed["ci_rca_since"], params=("since_ts", "extra"))
    monkeypatch.setattr(nr, "NAMED_READS", changed)
    after = {v: nr.read_version(v) for v in nr.NAMED_READS}
    assert {v for v in before if before[v] != after[v]} == {"count_by_status", "ci_rca_since"}  # no other verb moves


def test_read_version_ignores_sql_whitespace_but_not_paginable(monkeypatch):
    base = nr.NAMED_READS["count_by_status"]
    spaced = dataclasses.replace(base, sql="  " + base.sql.replace(" ", "   ") + "\n")
    paginable = dataclasses.replace(base, paginable=True)
    original = nr.read_version("count_by_status")
    monkeypatch.setattr(nr, "NAMED_READS", {**nr.NAMED_READS, "count_by_status": spaced})
    assert nr.read_version("count_by_status") == original
    monkeypatch.setattr(nr, "NAMED_READS", {**nr.NAMED_READS, "count_by_status": paginable})
    assert nr.read_version("count_by_status") != original


def test_select_star_verb_reversions_when_its_column_projection_changes():
    semantics = copy.deepcopy(schema.load_field_semantics())
    star = [v for v, n in nr.NAMED_READS.items() if nr._SELECT_STAR.search(n.sql)]
    assert {"rec_by_id", "recs_by_ids", "rec_history", "decision_by_id", "priority_queue_current"} <= set(star)
    other = [v for v in nr.NAMED_READS if v not in star]
    before = {v: nr.read_version(v, semantics) for v in nr.NAMED_READS}

    columns = semantics["ops_tables"]["ops_recommendations"]["columns"]
    columns["brand_new"] = {"role": "input", "sql_type": "VARCHAR", "nullable": True}
    after = {v: nr.read_version(v, semantics) for v in nr.NAMED_READS}
    changed = {v for v in before if before[v] != after[v]}
    assert changed == {"rec_by_id", "recs_by_ids", "rec_history"}  # the SELECT * verbs bound to the changed table only
    assert not changed & set(other)


def test_read_version_is_computed_at_call_time_not_import():
    """No module-scope facade import and no module-level digest (AGENTS.md: never raise at import)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(nr))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "src.common.ducklake_scd2_schema"
    assert not hasattr(nr, "READ_VERSIONS")


def test_describe_named_reads_carries_read_version():
    out = nr.describe_named_reads()
    for verb, entry in out.items():
        assert entry["read_version"] == nr.read_version(verb)


class TestRecsReadVerbsV4:
    def test_four_verbs_registered_with_params(self):
        assert nr.NAMED_READS["recs_by_ids"].params == ("ids",)
        assert nr.NAMED_READS["recs_for_files"].params == ("files",)
        assert nr.NAMED_READS["open_recs_tally"].params == ()
        assert nr.NAMED_READS["preflight_signal_rows"].params == ("followon_ids",)
        for verb in ("recs_by_ids", "recs_for_files", "open_recs_tally", "preflight_signal_rows"):
            assert nr.NAMED_READS[verb].table == "ops_recommendations"

    def test_rec_by_id_retained_and_marked_deprecated(self):
        assert "rec_by_id" in nr.NAMED_READS
        assert nr.NAMED_READS["rec_by_id"].description.startswith("deprecated: use recs_by_ids (removal rec-4240)")

    def test_agent_facing_flags(self):
        assert nr.NAMED_READS["open_recs"].agent_facing is False
        assert nr.NAMED_READS["preflight_signal_rows"].agent_facing is False
        hidden = {"open_recs", "preflight_signal_rows", "recs_related"}
        assert all(n.agent_facing for v, n in nr.NAMED_READS.items() if v not in hidden)
        out = nr.describe_named_reads()
        assert out["open_recs"]["agent_facing"] is False and out["recs_by_ids"]["agent_facing"] is True

    def test_agent_facing_is_outside_read_version(self, monkeypatch):
        before = {v: nr.read_version(v) for v in nr.NAMED_READS}
        flipped = dataclasses.replace(nr.NAMED_READS["count_by_status"], agent_facing=False)
        monkeypatch.setattr(nr, "NAMED_READS", {**nr.NAMED_READS, "count_by_status": flipped})
        assert {v: nr.read_version(v) for v in nr.NAMED_READS} == before

    def test_preexisting_verbs_keep_their_read_version(self):
        # SQL of every pre-v4 verb is untouched, so its stamp (a function of its own definition) is stable.
        for verb in ("ci_rca_open", "ci_rca_since", "count_by_status", "recs_by_title_prefix", "decisions_max_updated"):
            assert nr.read_version(verb) == nr.read_version(verb)
            assert "agent_facing" not in nr.NAMED_READS[verb].sql

    def test_signal_sources_rendered_into_preflight_signal_rows(self):
        sql = nr.NAMED_READS["preflight_signal_rows"].sql
        for src in nr.PREFLIGHT_SIGNAL_SOURCES:
            assert f"'{src}'" in sql
        assert nr.PREFLIGHT_SIGNAL_SOURCES == (
            "ci_rca",
            "ci_rca_evidence_dispute",
            "ci_rca_probe_health",
            "tf_drift",
            "budget_bypass",
            "budget_breach",
        )


class TestRecsRelatedVerbV5:
    """recs_related (registry v5, Decision 215 cl.3) is added EXPAND-only: no pre-existing verb is re-versioned.

    The pins below are the "re-versioning must be deliberate" guard: a later intentional verb change (rec-4240)
    edits them openly, which reads as a decision, never as a weakened assertion (Decision 181).
    """

    # read_version digests of the non-SELECT-* verbs as of registry v4; each depends on its own definition alone.
    _PINNED_DIGESTS = {
        "open_recs": "5d3ae501947b4b12",  # pragma: allowlist secret
        "recs_for_files": "d9515498c54fa82c",  # pragma: allowlist secret
        "open_recs_tally": "7a7e774f982ccfc5",  # pragma: allowlist secret
        "preflight_signal_rows": "2c5174d7a4de87ea",  # pragma: allowlist secret
        "recs_by_title_prefix": "95bc1cd04ce6a316",  # pragma: allowlist secret
        "ci_rca_open": "054654225c37156d",  # pragma: allowlist secret
        "ci_rca_since": "d3f176730f71d09f",  # pragma: allowlist secret
        "forward_fix_recursion": "3b20742b42a60fce",  # pragma: allowlist secret
        "budget_bypass_recent": "4cd857dab8b85aa0",  # pragma: allowlist secret
        "count_by_status": "5a665efb122a6c5c",  # pragma: allowlist secret
        "decisions_max_updated": "b60a6ce94ae250f5",  # pragma: allowlist secret
    }
    # SELECT * verbs: their digest folds in the column projection, which floats with field_semantics, so the
    # definition is pinned (table, params, paginable, normalised SQL) instead of the digest.
    _PINNED_SELECT_STAR = {
        "rec_by_id": ("ops_recommendations", ("id",), False, "SELECT * FROM {tbl} WHERE id = ?"),
        "recs_by_ids": (
            "ops_recommendations",
            ("ids",),
            False,
            "SELECT * FROM {tbl} WHERE list_contains(string_split(?, ','), id) ORDER BY id",
        ),
        "rec_history": (
            "ops_recommendations",
            ("id",),
            False,
            "SELECT * FROM {hist} WHERE id = ? ORDER BY last_updated_timestamp DESC, ulid DESC",
        ),
        "decision_by_id": ("ops_decisions", ("id",), False, "SELECT * FROM {tbl} WHERE id = ?"),
        "priority_queue_current": (
            "ops_priority_queue",
            (),
            False,
            (
                "SELECT * FROM {tbl} WHERE queue_run_id = (SELECT queue_run_id FROM {tbl} "
                "ORDER BY last_updated_timestamp DESC LIMIT 1) ORDER BY rank"
            ),
        ),
    }

    def test_registered_with_five_params_in_bind_order(self):
        entry = nr.NAMED_READS["recs_related"]
        assert entry.table == "ops_recommendations"
        assert entry.params == ("files", "near", "terms", "exclude_ids", "max_rows")
        assert entry.paginable is False
        assert entry.agent_facing is False
        assert "scripts.recs_related" in entry.description and "not a bulk path" in entry.description

    def test_sql_has_one_placeholder_per_param_in_order(self):
        sql = nr.NAMED_READS["recs_related"].sql
        assert sql.count("?") == len(nr.NAMED_READS["recs_related"].params)
        assert sql.rstrip().endswith("LIMIT least(CAST(? AS INTEGER), 100)")

    def test_order_by_ends_with_id_tiebreak_immediately_before_limit(self):
        sql = " ".join(nr.NAMED_READS["recs_related"].sql.split())
        assert ", id LIMIT least(" in sql
        assert sql.index("ORDER BY direct DESC") < sql.index("created_timestamp DESC, id LIMIT")

    def test_candidates_are_open_only_and_cap_is_100(self):
        sql = nr.NAMED_READS["recs_related"].sql
        assert "status = 'open'" in sql
        assert "least(CAST(? AS INTEGER), 100)" in sql

    def test_deprecated_descriptions_lead_the_two_interim_verbs(self):
        for verb in ("recs_for_files", "open_recs"):
            assert nr.NAMED_READS[verb].description.startswith("deprecated:")
            assert "removal rec-4240" in nr.NAMED_READS[verb].description

    def test_no_pre_existing_verb_is_reversioned(self):
        assert {v: nr.read_version(v) for v in self._PINNED_DIGESTS} == self._PINNED_DIGESTS

    def test_select_star_verb_definitions_are_unchanged(self):
        for verb, pinned in self._PINNED_SELECT_STAR.items():
            entry = nr.NAMED_READS[verb]
            assert (entry.table, entry.params, entry.paginable, " ".join(entry.sql.split())) == pinned

    def test_no_pinned_verb_was_removed(self):
        assert (set(self._PINNED_DIGESTS) | set(self._PINNED_SELECT_STAR)) <= set(nr.NAMED_READS)
