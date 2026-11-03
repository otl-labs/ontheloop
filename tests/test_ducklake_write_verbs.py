"""Tests for src/common/ducklake_write_verbs.py -- the write-verb registry moved out of
ducklake_scd2_schema (telemetry table-registration plan; Decision 124 facade-plus-siblings). Pure.
"""

from __future__ import annotations

import pytest

from src.common import ducklake_write_verbs as wv

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# describe_write_verbs (CD.10 / CD.15 agent-facing describe surface)
# ---------------------------------------------------------------------------


def test_describe_write_verbs_covers_registry():
    out = wv.describe_write_verbs()
    assert set(out) == set(wv.VERB_REGISTRY)
    assert set(out) >= {"write_ops", "update_ops", "file_ops", "create_ops_tables"}
    for verb, entry in out.items():
        assert entry["description"], verb
        assert entry["params_schema"]["type"] == "object"


def test_verb_registry_entries_are_writeverb_instances():
    for verb, entry in wv.VERB_REGISTRY.items():
        assert isinstance(entry, wv.WriteVerb)
        assert entry.verb == verb
