"""Mirror test for the C6 line and its EMF block (telemetry_log.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.common.ducklake_telemetry_registration import CallerPrincipal
from src.lambdas.ducklake_telemetry_writer import telemetry_log as log

_ROOT = Path(__file__).resolve().parents[3]
_DATA_VERBS = (
    "append_telemetry",
    "begin_blobs",
    "register_tenant",
    "register_project",
    "provision_telemetry",
    "telemetry_probe",
)
_PRINCIPAL = CallerPrincipal(kind="aws_account", ref="acct", role_name="PlatformDev")


def line_for(action: str, status: int = 200, *, recorded: bool = False, **over: Any) -> dict[str, Any]:
    stats = log.RequestStats(
        tenant_id="T" * 26, project_id="P" * 26, tables={"telemetry_sessions": (3, 2)}, connect_reused=True
    )
    if recorded:
        stats.sink("OccRetryCount", 2.0)
        stats.sink("CommitLatencyMs", 1234.5678)
    args: dict[str, Any] = {
        "payload": {"component": "hook-stop"},
        "principal": _PRINCIPAL,
        "request_bytes": 10,
        "invoke_bytes": 12,
        "stats": stats,
    }
    return log.build_request_line(action, status, None, **{**args, **over})


def test_line_fields_and_emf_on_the_five_data_verbs() -> None:
    for verb in _DATA_VERBS:
        line = line_for(verb)
        assert (line["verb"], line["component"], line["status"], line["caller_role"]) == (
            verb,
            "hook-stop",
            200,
            "PlatformDev",
        )
        assert (line["request_bytes"], line["invoke_bytes"], line["connect_reused"]) == (10, 12, True)
        assert line["tables"] == {"telemetry_sessions": {"submitted": 3, "inserted": 2}}
        block = line["_aws"]["CloudWatchMetrics"][0]
        assert block == {
            "Namespace": "DuckLakeTelemetryWriter",
            "Dimensions": [["Verb"]],
            "Metrics": [{"Name": "RequestCount", "Unit": "Count"}],
        }
        assert (line["Verb"], line["RequestCount"]) == (verb, 1) and isinstance(line["_aws"]["Timestamp"], int)
    for verb in ("describe", "attach_check", "unknown"):
        assert "_aws" not in line_for(verb)
    assert line_for("append_telemetry", payload={"component": "Bad Name"})["component"] == "unknown"
    assert line_for("describe", payload=None)["component"] == "unknown"
    assert line_for("register_tenant", payload={"component": "ok"})["component"] == "ok"
    anonymous = line_for("unknown", 403, principal=None, request_bytes=None, invoke_bytes=None)
    assert (anonymous["caller_role"], anonymous["request_bytes"]) == (None, None)
    emitted = json.loads(json.dumps(line_for("append_telemetry")))
    assert emitted["verb"] == "append_telemetry"


def test_occ_directive_only_when_recorded() -> None:
    bare = line_for("append_telemetry")
    assert len(bare["_aws"]["CloudWatchMetrics"]) == 1 and "OccRetryCount" not in bare and "CommitLatencyMs" not in bare
    assert (bare["occ_retries"], bare["commit_ms"]) == (None, None)
    full = line_for("append_telemetry", recorded=True)
    directives = full["_aws"]["CloudWatchMetrics"]
    assert directives[1] == {
        "Namespace": "DuckLakeTelemetryWriter",
        "Dimensions": [[], ["MetaSchema"]],
        "Metrics": [{"Name": "OccRetryCount", "Unit": "Count"}, {"Name": "CommitLatencyMs", "Unit": "Milliseconds"}],
    }
    assert (full["OccRetryCount"], full["CommitLatencyMs"], full["occ_retries"], full["commit_ms"]) == (2, 1235, 2, 1235)
    for member in (full["OccRetryCount"], full["CommitLatencyMs"], full["commit_ms"]):
        assert isinstance(member, int)
    other = line_for("register_tenant", recorded=True)
    assert len(other["_aws"]["CloudWatchMetrics"]) == 1 and "OccRetryCount" not in other
    only_one = log.RequestStats()
    only_one.sink("OccRetryCount", 0.0)
    partial = log.build_request_line(
        "append_telemetry",
        503,
        "occ_exhausted",
        payload={},
        principal=None,
        request_bytes=None,
        invoke_bytes=None,
        stats=only_one,
    )
    assert [m["Name"] for m in partial["_aws"]["CloudWatchMetrics"][1]["Metrics"]] == [
        "OccRetryCount"
    ] and "CommitLatencyMs" not in partial


def test_emf_adds_a_meta_schema_series_beside_the_undimensioned_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", "ducklake_smoke")
    full = line_for("append_telemetry", recorded=True)
    first, second = full["_aws"]["CloudWatchMetrics"]
    assert first["Dimensions"] == [["Verb"]] and [m["Name"] for m in first["Metrics"]] == ["RequestCount"]
    assert second["Dimensions"] == [[], ["MetaSchema"]]
    assert full["MetaSchema"] == "ducklake_smoke"
    assert "MetaSchema" not in line_for("append_telemetry")
    assert "MetaSchema" not in line_for("register_tenant", recorded=True)


@pytest.mark.parametrize("value", [None, "", "bad name;", "1abc"])
def test_meta_schema_member_is_env_bound_and_checked(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("TELEMETRY_META_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("TELEMETRY_META_SCHEMA", value)
    full = line_for("append_telemetry", recorded=True, payload={"meta_schema": "ducklake_telemetry"})
    assert full["MetaSchema"] == "unknown"


def test_line_never_carries_account_id_or_arn(capsys: pytest.CaptureFixture[str]) -> None:
    account = "1234" + "56789012"
    arn = f"arn:aws:sts::{account}:assumed-role/PlatformDev/session"
    principal = CallerPrincipal(kind="aws_account", ref=account, role_name="PlatformDev")
    for verb in (*_DATA_VERBS, "describe", "unknown"):
        log.emit_request_line(
            verb,
            200,
            None,
            payload={"component": "hook-stop", "project_ref": account, "userArn": arn},
            principal=principal,
            request_bytes=5,
            invoke_bytes=7,
            stats=log.RequestStats(tenant_id="T" * 26, project_id="P" * 26),
        )
    out = capsys.readouterr().out
    assert account not in out and arn not in out and "arn:" not in out and out.count("\n") == 8
    for text in out.splitlines():
        parsed = json.loads(text)
        scrubbed = {k: v for k, v in parsed.items() if k != "_aws"}
        assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", json.dumps(scrubbed))
        assert "project_ref" not in scrubbed
    assert log._clean_id("not a ulid" + account) is None


def test_function_keeps_the_text_log_format() -> None:
    terraform = (_ROOT / "terraform" / "personal" / "ducklake_telemetry_writer.tf").read_text(encoding="utf-8")
    assert "logging_config" not in terraform and "log_format" not in terraform


def test_masked_commit_retries_is_a_line_field_never_an_emf_member() -> None:
    assert line_for("append_telemetry")["masked_commit_retries"] is None
    stats = log.RequestStats(masked_commit_retries=2)
    stats.sink("OccRetryCount", 2.0)
    stats.sink("CommitLatencyMs", 5.0)
    line = log.build_request_line(
        "append_telemetry", 200, None, payload={}, principal=None, request_bytes=1, invoke_bytes=1, stats=stats
    )
    assert line["masked_commit_retries"] == 2
    metrics = [m["Name"] for d in line["_aws"]["CloudWatchMetrics"] for m in d["Metrics"]]
    assert (
        "masked_commit_retries" not in metrics
        and "MaskedCommitRetries" not in line
        and "masked_commit_retries" not in line["_aws"]
    )
    zero = log.RequestStats(masked_commit_retries=0)
    assert (
        log.build_request_line(
            "register_tenant", 200, None, payload={}, principal=None, request_bytes=1, invoke_bytes=1, stats=zero
        )["masked_commit_retries"]
        == 0
    )


def test_begin_blobs_line_counts_with_emf() -> None:
    counts = dict(zip(log.BLOB_COUNT_FIELDS, range(1, len(log.BLOB_COUNT_FIELDS) + 1), strict=True))
    stats = log.RequestStats(tenant_id="T" * 26, project_id="P" * 26, blob_counts=counts)
    stats.sink("OccRetryCount", 1.0)
    stats.sink("CommitLatencyMs", 9.0)
    line = log.build_request_line(
        "begin_blobs", 200, None, payload={}, principal=None, request_bytes=1, invoke_bytes=1, stats=stats
    )
    assert {name: line[name] for name in log.BLOB_COUNT_FIELDS} == counts and "mismatched" not in line
    assert line["_aws"]["CloudWatchMetrics"] == [
        {
            "Namespace": "DuckLakeTelemetryWriter",
            "Dimensions": [["Verb"]],
            "Metrics": [{"Name": "RequestCount", "Unit": "Count"}],
        }
    ]
    assert (line["Verb"], line["RequestCount"]) == ("begin_blobs", 1) and "OccRetryCount" not in line
    append = line_for("append_telemetry")
    assert (append["blob_rows"], append["blob_head_ms"]) == (None, None) and "granted" not in append
    assert "granted" not in line_for("register_tenant")


def test_lines_never_carry_a_presigned_url() -> None:
    from tests.fixtures.telemetry_blob_fakes import presigned_url

    url = presigned_url("tenant/project/session/digest")
    payload = {"component": "hook-stop", "items": [{"upload": {"url": url}}]}
    stats = log.RequestStats(blob_counts={"items": 1, "granted": 1})
    text = json.dumps(
        log.build_request_line(
            "begin_blobs", 200, None, payload=payload, principal=None, request_bytes=1, invoke_bytes=1, stats=stats
        )
    )
    for banned in ("X-Amz-Signature", "X-Amz-Security-Token", "X-Amz-Credential", "ASIA", "https://", "digest"):
        assert banned not in text, banned
