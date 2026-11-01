"""Standing guard for both DuckLake writers' errors alarms (PLAN-ducklake-typed-unmapped-errors).

A handler that answers a typed 5xx returns normally, so AWS/Lambda Errors stays zero; Url5xxCount is the metric that
moves. Each alarm therefore sums FILL(Errors, 0) + FILL(Url5xxCount, 0) (Url5xxCount emits no datapoint at zero). Every
predicate is a pure function of HCL text with its own red case on synthetic HCL.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR, find_resource_block

_ALARM_TYPE = "aws_cloudwatch_metric_alarm"
_TELEMETRY = ("ducklake_telemetry_writer.tf", "ducklake_telemetry_writer_errors", "ducklake_telemetry_writer")
_TWIN = (
    "ducklake_telemetry_writer_smoke.tf",
    "ducklake_telemetry_writer_smoke_errors",
    "ducklake_telemetry_writer_smoke",
)
_READER = ("ducklake_telemetry_reader.tf", "ducklake_telemetry_reader_errors", "ducklake_telemetry_reader")
_OPS = ("ducklake_lambdas.tf", "ducklake_writer_errors", "ducklake_writer")
_EXPRESSION = "FILL(m1, 0) + FILL(m2, 0)"


def _query_block(block: str, query_id: str) -> str:
    match = re.search(rf'metric_query\s*\{{\s*id\s*=\s*"{query_id}"', block)
    assert match is not None, f"metric_query {query_id!r} missing"
    depth, start = 0, block.index("{", match.start())
    for i in range(start, len(block)):
        depth += block[i] == "{"
        depth -= block[i] == "}"
        if depth == 0:
            return block[match.start() : i + 1]
    raise AssertionError("unbalanced metric_query")


def alarm_defects(block: str, function: str) -> list[str]:
    defects: list[str] = []
    fn = rf"FunctionName\s*=\s*aws_lambda_function\.{function}\.function_name"
    for qid, metric in (("m1", "Errors"), ("m2", "Url5xxCount")):
        try:
            q = _query_block(block, qid)
        except AssertionError:
            defects.append(f"{qid} missing")
            continue
        if not re.search(rf'metric_name\s*=\s*"{metric}"', q) or not re.search(r'namespace\s*=\s*"AWS/Lambda"', q):
            defects.append(f"{qid} is not AWS/Lambda {metric}")
        if not re.search(r'stat\s*=\s*"Sum"', q) or not re.search(r"period\s*=\s*900", q):
            defects.append(f"{qid} is not Sum over 900")
        if not re.search(fn, q):
            defects.append(f"{qid} not on {function}")
    try:
        e1 = _query_block(block, "e1")
        if not re.search(rf'expression\s*=\s*"{re.escape(_EXPRESSION)}"', e1) or not re.search(r"return_data\s*=\s*true", e1):
            defects.append("e1 is not the FILLed sum with return_data")
    except AssertionError:
        defects.append("e1 missing")
    if not re.search(r"threshold\s*=\s*0\b", block) or "GreaterThanThreshold" not in block:
        defects.append("threshold is not > 0")
    if not re.search(r"evaluation_periods\s*=\s*1\b", block):
        defects.append("evaluation_periods is not 1")
    for action in ("alarm_actions", "ok_actions"):
        if not re.search(rf"{action}\s*=\s*\[aws_sns_topic\.alerts\.arn\]", block):
            defects.append(f"{action} alerts SNS action missing")
    if not re.search(r'treat_missing_data\s*=\s*"notBreaching"', block):
        defects.append("treat_missing_data is not notBreaching")
    if re.search(r"^\s*metric_name\s*=", re.sub(r"metric_query\s*\{.*", "", block, flags=re.S), re.M):
        defects.append("top-level metric_name still present")
    return defects


def _real_block(spec: tuple[str, str, str]) -> str:
    tf_file, name, _ = spec
    return find_resource_block((_TERRAFORM_PERSONAL_DIR / tf_file).read_text(encoding="utf-8"), _ALARM_TYPE, name)


def test_telemetry_writer_alarm_sums_errors_and_url5xx() -> None:
    assert alarm_defects(_real_block(_TELEMETRY), _TELEMETRY[2]) == []


def test_twin_and_telemetry_reader_alarms_sum_errors_and_url5xx() -> None:
    for spec in (_TWIN, _READER):
        assert alarm_defects(_real_block(spec), spec[2]) == []


def test_twin_alarm_is_red_when_pointed_at_the_writer() -> None:
    block = _real_block(_TWIN).replace(
        "ducklake_telemetry_writer_smoke.function_name", "ducklake_telemetry_writer.function_name"
    )
    assert "m1 not on ducklake_telemetry_writer_smoke" in alarm_defects(block, _TWIN[2])


def test_ops_writer_alarm_sums_errors_and_url5xx() -> None:
    assert alarm_defects(_real_block(_OPS), _OPS[2]) == []


def test_alarm_expression_fills_both_operands() -> None:
    for spec in (_TELEMETRY, _TWIN, _READER, _OPS):
        e1 = _query_block(_real_block(spec), "e1")
        assert "FILL(m1, 0)" in e1 and "FILL(m2, 0)" in e1


_RED_CASES = [
    (lambda b: b.replace('"Url5xxCount"', '"Invocations"'), "m2 is not AWS/Lambda Url5xxCount"),
    (lambda b: b.replace("FILL(m2, 0)", "m2"), "e1 is not the FILLed sum with return_data"),
    (lambda b: b.replace("FILL(m1, 0)", "m1"), "e1 is not the FILLed sum with return_data"),
    (lambda b: b.replace("return_data = true", "return_data = false"), "e1 is not the FILLed sum with return_data"),
    (lambda b: b.replace("threshold           = 0", "threshold           = 1"), "threshold is not > 0"),
    (lambda b: b.replace("evaluation_periods  = 1", "evaluation_periods  = 2"), "evaluation_periods is not 1"),
    (
        lambda b: b.replace("ok_actions    = [aws_sns_topic.alerts.arn]", "ok_actions    = []"),
        "ok_actions alerts SNS action missing",
    ),
    (
        lambda b: b.replace("alarm_actions = [aws_sns_topic.alerts.arn]", "alarm_actions = []"),
        "alarm_actions alerts SNS action missing",
    ),
    (lambda b: b.replace("notBreaching", "missing"), "treat_missing_data is not notBreaching"),
    (lambda b: b.replace("period      = 900", "period      = 60"), "m1 is not Sum over 900"),
    (
        lambda b: b.replace(
            "aws_lambda_function.ducklake_telemetry_writer.function_name", "aws_lambda_function.other.function_name"
        ),
        "m1 not on ducklake_telemetry_writer",
    ),
    (lambda b: 'metric_name = "Errors"\n' + b, "top-level metric_name still present"),
]


@pytest.mark.parametrize(("mutate", "expected"), _RED_CASES)
def test_pin_rejects_a_dropped_operand_or_fill(mutate: Callable[[str], str], expected: str) -> None:
    block = _real_block(_TELEMETRY)
    assert alarm_defects(block, _TELEMETRY[2]) == []
    assert expected in alarm_defects(mutate(block), _TELEMETRY[2])
