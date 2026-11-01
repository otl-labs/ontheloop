"""MIRROR for src/lambdas/ducklake_maintenance/_shared.py (Decision 204,
PLAN-ducklake-partition-layout-remediation).

Mirror test for _shared.py -- identifier and data_path guards, 100% line coverage.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.lambdas.ducklake_maintenance import _shared

pytestmark = pytest.mark.unit


def test_extension_directory_is_a_string_constant():
    assert isinstance(_shared.EXTENSION_DIRECTORY, str)


def test_require_identifier_accepts_bare_identifier():
    assert _shared._require_identifier("ducklake_ops") == "ducklake_ops"
    assert _shared._require_identifier("_leading_underscore") == "_leading_underscore"


@pytest.mark.parametrize("bad", ["ducklake ops", "ducklake-ops", "1leading_digit", "", None, 42, "ducklake;drop table x"])
def test_require_identifier_rejects_non_identifier(bad):
    with pytest.raises(DuckLakeRuntimeError, match="invalid SQL identifier"):
        _shared._require_identifier(bad)


def test_require_data_path_accepts_s3_uri():
    assert _shared._require_data_path("s3://bucket/prefix/") == "s3://bucket/prefix/"


@pytest.mark.parametrize("bad", ["http://bucket/prefix/", "bucket/prefix/", "", None, 42])
def test_require_data_path_rejects_non_s3_uri(bad):
    with pytest.raises(DuckLakeRuntimeError, match="invalid data_path"):
        _shared._require_data_path(bad)


def test_emit_maintenance_metric_forwards_to_rt_emit_metric():
    with patch.object(_shared.rt, "emit_metric") as mock_emit:
        _shared._emit_maintenance_metric("SomeMetric", 3.0, profile="agent_platform_admin")
    mock_emit.assert_called_once_with(
        "SomeMetric", 3.0, namespace=_shared.maint.MAINTENANCE_CLOUDWATCH_NAMESPACE, profile="agent_platform_admin"
    )


def test_emit_maintenance_metric_passes_dimensions_through():
    with patch.object(_shared.rt, "emit_metric") as mock_emit:
        _shared._emit_maintenance_metric("StatsIndexValid", 1.0, dimensions={"MetaSchema": "ducklake_ops"})
    assert mock_emit.call_args.kwargs["dimensions"] == {"MetaSchema": "ducklake_ops"}


def test_data_path_is_the_env_pinned_default():
    assert _shared.DATA_PATH is None or _shared.DATA_PATH.startswith("s3://")


def test_catalog_dimensions_keep_the_ops_series_undimensioned():
    assert _shared.catalog_dimensions("ducklake_ops") is None
    assert _shared.catalog_dimensions("bad-name;") is None
    assert _shared.catalog_dimensions(None) is None
    assert _shared.catalog_dimensions("ducklake_telemetry") == {"MetaSchema": "ducklake_telemetry"}


def test_catalog_metrics_is_the_closed_alarm_input_set():
    assert _shared.CATALOG_METRICS == frozenset(
        {
            "MaintenanceBreakerTrip",
            "GcReferencedMissing",
            "GcDeletedSnapshots",
            "GcDeferredFiles",
            "MergeOpsPassFailed",
            "MergeOpsTablesCount",
            "MergeOpsFilesAfterTotal",
            "MergeOpsDurationMs",
            "CatalogFileColumnStatsRows",
        }
    )
