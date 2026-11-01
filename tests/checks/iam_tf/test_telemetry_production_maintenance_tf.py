"""PLAN-telemetry-production-maintenance PR 2: the telemetry catalog's maintenance schedules, alarms and thresholds.

Each test asserts its synthetic-HCL red case inside the same function, so no extra node is collected. The class groups the
three nodes that one registry test_selector graduates.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from src.lambdas.ducklake_maintenance._shared import CATALOG_METRICS
from tests.fixtures.terraform_hcl_blocks import find_resource_block, tf_dir_file_texts

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_TF_DIR = _ROOT / "terraform" / "personal"
_MAINT_TF = _TF_DIR / "ducklake_maintenance_telemetry.tf"
_WRITER_TF = _TF_DIR / "ducklake_telemetry_writer.tf"
_FIXTURE = _ROOT / "tests" / "fixtures" / "gc_ops_dryrun_baseline_telemetry.json"

_MAINT_ALARMS = {
    "ducklake_telemetry_breaker": "ducklake-telemetry-maintenance-breaker",
    "ducklake_telemetry_gc_referenced_missing": "ducklake-telemetry-gc-referenced-missing",
    "ducklake_telemetry_gc_liveness": "ducklake-telemetry-gc-liveness",
    "ducklake_telemetry_merge_failed": "ducklake-telemetry-merge-failed",
    "ducklake_telemetry_merge_liveness": "ducklake-telemetry-merge-liveness",
    "ducklake_telemetry_merge_duration": "ducklake-telemetry-merge-duration",
    "ducklake_telemetry_files_after_merge": "ducklake-telemetry-files-after-merge",
    "ducklake_telemetry_catalog_stats_rows": "ducklake-telemetry-catalog-stats-rows",
}
_WRITER_ALARMS = {
    "ducklake_telemetry_writer_occ": "ducklake-telemetry-writer-occ",
    "ducklake_telemetry_writer_commit_latency": "ducklake-telemetry-writer-commit-latency",
}
_LIVENESS = ("ducklake_telemetry_gc_liveness", "ducklake_telemetry_merge_liveness")
_CRON_RE = re.compile(r'schedule_expression\s*=\s*"cron\(([^)]*)\)"')
_METRIC_RE = re.compile(r'metric_name\s*=\s*"(\w+)"')
_DIM_RE = re.compile(r"MetaSchema\s*=\s*local\.ducklake_telemetry_prod_meta_schema")
_THRESHOLD_SHAPE_RE = re.compile(r">\s*\d+\s*%|\d+\s*(?:GiB|GB|MB|bytes)", re.IGNORECASE)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _alarm(text: str, name: str) -> str:
    return find_resource_block(text, "aws_cloudwatch_metric_alarm", name)


def _field(block: str, key: str) -> str:
    match = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", block, re.MULTILINE)
    assert match is not None, f"{key} not found in {block[:120]!r}"
    return match.group(1)


def _local(text: str, key: str) -> str:
    match = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    assert match is not None, f"local {key} not found"
    return match.group(1)


def _alarm_texts() -> dict[str, str]:
    maint, writer = _text(_MAINT_TF), _text(_WRITER_TF)
    return {n: _alarm(maint, n) for n in _MAINT_ALARMS} | {n: _alarm(writer, n) for n in _WRITER_ALARMS}


class TestTelemetryMaintenanceSchedulePins:
    def test_rules_target_the_production_telemetry_catalog(self) -> None:
        text = _text(_MAINT_TF)
        for rule, action, cron in (("merge", "merge_ops", "50 */6 * * ? *"), ("gc", "gc_ops", "5 4 ? * SUN *")):
            rule_block = find_resource_block(text, "aws_cloudwatch_event_rule", f"ducklake_maintenance_telemetry_{rule}")
            assert _CRON_RE.search(rule_block).group(1) == cron
            assert _field(rule_block, "state") == '"ENABLED"'
            target = find_resource_block(text, "aws_cloudwatch_event_target", f"ducklake_maintenance_telemetry_{rule}")
            assert f'action      = "{action}"' in target
            assert "data_path   = local.ducklake_telemetry_data_path" in target
            assert "meta_schema = local.ducklake_telemetry_prod_meta_schema" in target
            assert "aws_lambda_function.ducklake_maintenance.arn" in target
            perm = find_resource_block(text, "aws_lambda_permission", f"ducklake_maintenance_telemetry_{rule}")
            assert "aws_lambda_function.ducklake_maintenance.function_name" in perm
        disabled = text.replace('state               = "ENABLED"', 'state               = "DISABLED"', 1)
        assert (
            _field(find_resource_block(disabled, "aws_cloudwatch_event_rule", "ducklake_maintenance_telemetry_merge"), "state")
            != '"ENABLED"'
        )
        mispointed = find_resource_block(text, "aws_cloudwatch_event_target", "ducklake_maintenance_telemetry_gc").replace(
            "local.ducklake_telemetry_prod_meta_schema", '"ducklake_ops"'
        )
        assert "meta_schema = local.ducklake_telemetry_prod_meta_schema" not in mispointed

    def test_every_telemetry_alarm_is_dimensioned_by_meta_schema(self) -> None:
        for name, block in _alarm_texts().items():
            assert _DIM_RE.search(block), name
            assert "aws_sns_topic.alerts.arn" in _field(block, "alarm_actions"), name
            metric = _METRIC_RE.search(block).group(1)
            if name in _MAINT_ALARMS:
                assert _field(block, "namespace") == '"DuckLakeMaintenance"' and metric in CATALOG_METRICS, (name, metric)
            else:
                assert _field(block, "namespace") == '"DuckLakeTelemetryWriter"', name
                assert metric in {"OccRetryCount", "CommitLatencyMs"}, (name, metric)
            assert _field(block, "treat_missing_data") == ('"breaching"' if name in _LIVENESS else '"notBreaching"'), name
        undimensioned = _alarm_texts()["ducklake_telemetry_merge_failed"].replace("MetaSchema", "Other")
        assert not _DIM_RE.search(undimensioned)
        assert _field(_alarm_texts()["ducklake_telemetry_writer_occ"], "threshold") == "3"

    def test_thresholds_follow_the_measurement_formulas(self) -> None:
        from src.common.ducklake_maintenance import SNAPSHOT_RETAIN_DAYS
        from src.common.ducklake_maintenance_ops import G4_MAX_DELETE_FILES

        maint, writer = _text(_MAINT_TF), _text(_WRITER_TF)
        for key in ("telemetry_data_files_per_day", "telemetry_stats_rows_per_day", "telemetry_commit_latency_p95_ms_7d"):
            assert re.fullmatch(r"[1-9]\d*", _local(maint, key)), key
        assert int(_local(maint, "telemetry_stats_retention_days")) == SNAPSHOT_RETAIN_DAYS + 7
        assert _local(maint, "telemetry_files_after_merge_threshold") == "7 * local.telemetry_data_files_per_day"
        assert _local(maint, "telemetry_stats_rows_threshold") == (
            "2 * local.telemetry_stats_retention_days * local.telemetry_stats_rows_per_day"
        )
        assert (
            _local(maint, "telemetry_merge_duration_threshold_ms")
            == "aws_lambda_function.ducklake_maintenance.timeout * 1000 / 2"
        )
        assert (
            _field(_alarm(maint, "ducklake_telemetry_files_after_merge"), "threshold")
            == "local.telemetry_files_after_merge_threshold"
        )
        assert (
            _field(_alarm(maint, "ducklake_telemetry_catalog_stats_rows"), "threshold")
            == "local.telemetry_stats_rows_threshold"
        )
        assert (
            _field(_alarm(maint, "ducklake_telemetry_merge_duration"), "threshold")
            == "local.telemetry_merge_duration_threshold_ms"
        )
        assert _field(_alarm(writer, "ducklake_telemetry_writer_commit_latency"), "threshold") == (
            "ceil(2 * local.telemetry_commit_latency_p95_ms_7d / 1000) * 1000"
        )
        assert math.ceil(2 * int(_local(maint, "telemetry_commit_latency_p95_ms_7d")) / 1000) * 1000 > 0
        assert 7 * int(_local(maint, "telemetry_data_files_per_day")) < G4_MAX_DELETE_FILES
        dropped = _local(maint, "telemetry_stats_rows_threshold").replace(" * local.telemetry_stats_retention_days", "")
        assert dropped != _local(maint, "telemetry_stats_rows_threshold")


def test_admin_cadence_minutes_are_distinct() -> None:
    minutes: dict[str, str] = {}
    for name, text in tf_dir_file_texts(_TF_DIR).items():
        if name not in {"ducklake_maintenance.tf", "ducklake_maintenance_telemetry.tf"}:
            continue
        for block in re.findall(r'resource\s+"aws_cloudwatch_event_rule"\s+"(\w+)"\s*\{', text):
            rule = find_resource_block(text, "aws_cloudwatch_event_rule", block)
            cron = _CRON_RE.search(rule).group(1).split()
            minutes[block] = cron[0]
    assert len(minutes) == 5, minutes
    assert len(set(minutes.values())) == 5, minutes


def test_dr_dump_precedes_the_telemetry_gc() -> None:
    dr = (
        _CRON_RE.search(
            find_resource_block(_text(_TF_DIR / "ducklake_catalog_dr.tf"), "aws_cloudwatch_event_rule", "ducklake_catalog_dr")
        )
        .group(1)
        .split()
    )
    gc = (
        _CRON_RE.search(
            find_resource_block(_text(_MAINT_TF), "aws_cloudwatch_event_rule", "ducklake_maintenance_telemetry_gc")
        )
        .group(1)
        .split()
    )
    ops_gc = (
        _CRON_RE.search(
            find_resource_block(
                _text(_TF_DIR / "ducklake_maintenance.tf"), "aws_cloudwatch_event_rule", "ducklake_maintenance_gc_ops"
            )
        )
        .group(1)
        .split()
    )
    assert dr[2:] == gc[2:] == ops_gc[2:]
    assert (int(dr[1]), int(dr[0])) < (int(ops_gc[1]), int(ops_gc[0])) < (int(gc[1]), int(gc[0]))


def test_liveness_alarms_treat_missing_data_as_breaching_within_the_period_cap() -> None:
    blocks = _alarm_texts()
    for name in _LIVENESS:
        block = blocks[name]
        assert _field(block, "treat_missing_data") == '"breaching"'
        assert _field(block, "statistic") == '"SampleCount"' and _field(block, "comparison_operator") == '"LessThanThreshold"'
        period, periods = int(_field(block, "period")), int(_field(block, "evaluation_periods"))
        assert _field(block, "datapoints_to_alarm") == str(periods)
        assert period >= 3600 and period * periods <= 604800, (name, period, periods)
    assert _field(blocks["ducklake_telemetry_merge_liveness"], "period") == "21600"
    assert _field(blocks["ducklake_telemetry_gc_liveness"], "period") == "86400"


def test_alarm_descriptions_pin_no_threshold() -> None:
    for name, block in _alarm_texts().items():
        description = _field(block, "alarm_description")
        assert not _THRESHOLD_SHAPE_RE.search(description), (name, description)
    assert _THRESHOLD_SHAPE_RE.search('alarm_description = "fires above 10 GiB"')


def test_ops_alarms_stay_undimensioned() -> None:
    text = _text(_TF_DIR / "ducklake_maintenance.tf")
    for name in (
        "ducklake_maintenance_breaker",
        "ducklake_maintenance_gc_ops_referenced_missing",
        "ducklake_maintenance_gc_ops_liveness",
    ):
        block = _alarm(text, name)
        assert "dimensions" not in block and "MetaSchema" not in block, name


def test_committed_telemetry_dry_run_licenses_the_gc_enablement() -> None:
    assert _FIXTURE.is_file(), "tests/fixtures/gc_ops_dryrun_baseline_telemetry.json is missing: take the dry run first"
    reading = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert reading.get("ok") is True and reading.get("action") == "gc_ops" and reading.get("dry_run") is True
    assert reading.get("referenced_missing") == 0, "a catalog-live path is missing from storage: STOP and RCA"
    assert reading.get("unsized_candidates") == 0, "an unsized candidate would 500 the first firing: STOP and RCA"
    assert (reading.get("live_bytes") or 0) > 0, "no live bytes: the reading was not taken on the seeded catalog"
    tables = set(reading.get("tables") or [])
    canonical = {"telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"}
    assert canonical <= tables and len(tables) >= 9, sorted(tables)
    assert not any(t.startswith("ducklake_smoke") for t in tables), "the reading names smoke tables: wrong catalog"
    provenance = reading.get("provenance") or {}
    assert provenance.get("merge_sha") and provenance.get("date"), provenance
    for banned in ("arn:", "s3://", ".neon.", "amazonaws"):
        assert banned not in json.dumps(reading)
