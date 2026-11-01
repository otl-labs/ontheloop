"""Standing guard for the telemetry reader and the smoke twin (PLAN-telemetry-reader-and-smoke-twin, Decision 236).

Pins, over the real terraform/personal tree: the twin is bound to the fixed smoke catalog, prefixes and scoped login and
holds no Delete or production reference; the telemetry reader reads only the production telemetry data prefix with its
own login and no write, blob, shared-DSN or metrics statement, with an env pinned to production and no blob root; both
roles carry the apply boundary and both functions the ignore_changes marker; reserved concurrency fits the login limits.
Every predicate is a pure function of HCL text with its own red case on synthetic HCL.
"""

from __future__ import annotations

import re
from typing import Callable

import pytest

from src.lambdas.ducklake_maintenance.catalog_logins import LOGINS
from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR, find_resource_block, tf_dir_file_texts

_TWIN_FILE = "ducklake_telemetry_writer_smoke.tf"
_READER_FILE = "ducklake_telemetry_reader.tf"
_WRITER_FILE = "ducklake_telemetry_writer.tf"
_WRITER_SECRET = "aws_secretsmanager_secret.ducklake_telemetry_writer_dsn"  # pragma: allowlist secret
_READER_SECRET = "aws_secretsmanager_secret.ducklake_telemetry_reader_dsn"  # pragma: allowlist secret
_TWIN_FORBIDDEN = (
    "ducklake_telemetry_meta_schema",
    "ducklake_telemetry_data_prefix",
    "ducklake_telemetry_data_path",
    "ducklake_telemetry_prod_meta_schema",
    "ducklake_prod_data",
    "ducklake_telemetry_writer_prod",
    "ducklake_telemetry_reader_dsn",
    "ducklake_neon_catalog_dsn",
    "ducklake_telemetry_blob_root",
)
_READER_FORBIDDEN = (
    "telemetry_blob_data_prefix",
    "ducklake_smoke",
    "ducklake_prod_data",
    "ducklake_neon_catalog_dsn",
    "ducklake_telemetry_writer",
    "ducklake_telemetry_blob_root",
)
_READER_ACTIONS = {
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "secretsmanager:GetSecretValue",
    "ssm:GetParameter",
    "s3:GetObject",
    "s3:ListBucket",
    "s3:GetBucketLocation",
}
_BOUNDARY = "agent-platform-github-ci-apply-boundary"


def _strip(text: str) -> str:
    return re.sub(r"(?m)^\s*#.*$", "", text)


def _env_pairs(function_block: str) -> dict[str, str]:
    env = re.search(r"environment\s*\{\s*variables\s*=\s*\{(.*?)\n\s{4}\}", function_block, re.S)
    return dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", env.group(1), re.M)) if env else {}


def _statements(policy_block: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"\{\s*(?:#[^\n]*\n\s*)*Sid\s*=.*?\n      \},", policy_block, re.S)]


def _sid(stmt: str) -> str:
    return re.search(r'Sid\s*=\s*"(\w+)"', stmt).group(1)  # type: ignore[union-attr]


def _actions(stmt: str) -> list[str]:
    block = re.search(r"Action\s*=\s*\[(.*?)\]", stmt, re.S)
    return re.findall(r'"([^"]+)"', block.group(1)) if block else []


def _blocks(text: str, role: str, policy: str, function: str) -> tuple[str, str, str]:
    return (
        find_resource_block(text, "aws_iam_role", role),
        find_resource_block(text, "aws_iam_role_policy", policy),
        find_resource_block(text, "aws_lambda_function", function),
    )


def _common_problems(role: str, function: str) -> list[str]:
    problems: list[str] = []
    if _BOUNDARY not in role:
        problems.append("role lacks the apply permissions boundary")
    if not re.search(r"ignore_changes\s*=\s*\[source_code_hash\]", function):
        problems.append("function lacks lifecycle ignore_changes = [source_code_hash]")
    if "source_code_hash =" in re.sub(r"ignore_changes\s*=\s*\[source_code_hash\]", "", function):
        problems.append("function sets source_code_hash")
    return problems


def twin_problems(text: str, lambdas_text: str) -> list[str]:
    body = _strip(text)
    role, policy, function = _blocks(
        body, "ducklake_telemetry_writer_smoke", "ducklake_telemetry_writer_smoke", "ducklake_telemetry_writer_smoke"
    )
    problems = _common_problems(role, function)
    env = _env_pairs(function)
    want = {
        "TELEMETRY_META_SCHEMA": "local.ducklake_smoke_meta_schema",
        "TELEMETRY_DATA_PATH": "local.ducklake_smoke_data_path",
        "TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_smoke_blob_root",
        "TELEMETRY_DSN_SECRET_ID": f"{_WRITER_SECRET}.name",
    }
    problems += [f"env {k} is {env.get(k)!r}, want {v}" for k, v in want.items() if env.get(k) != v]
    problems += [f"twin references {name}" for name in _TWIN_FORBIDDEN if name in body]
    if re.search(r"s3:Delete", body, re.I):
        problems.append("twin grants a Delete action")
    secrets = re.findall(r"aws_secretsmanager_secret\.\w+", policy)
    if secrets != [_WRITER_SECRET]:
        problems.append(f"twin reads secrets {secrets}")
    s3_refs = set(re.findall(r"local\.(\w*prefix\w*|\w*meta_schema\w*)", policy))
    if s3_refs - {"ducklake_smoke_data_prefix", "telemetry_blob_data_prefix", "ducklake_smoke_meta_schema"}:
        problems.append(f"twin S3 statements reference {sorted(s3_refs)}")
    if not re.search(r'ducklake_smoke_meta_schema\s*=\s*"ducklake_smoke"', lambdas_text):
        problems.append("ducklake_smoke_meta_schema is not the literal ducklake_smoke")
    return problems


def reader_problems(text: str) -> list[str]:
    body = _strip(text)
    role, policy, function = _blocks(
        body, "ducklake_telemetry_reader", "ducklake_telemetry_reader", "ducklake_telemetry_reader"
    )
    problems = _common_problems(role, function)
    statements = _statements(policy)
    sids = {_sid(s) for s in statements}
    want = {
        "Logs",
        "TelemetryReaderDsnRead",
        "TelemetryReaderDsnParameterRead",
        "TelemetryCatalogDataRead",
        "TelemetryCatalogDataList",
    }
    if sids != want:
        problems.append(f"reader statements are {sorted(sids)}")
    for stmt in statements:
        extra = set(_actions(stmt)) - _READER_ACTIONS
        if extra:
            problems.append(f"{_sid(stmt)} grants {sorted(extra)}")
        if _sid(stmt) == "TelemetryCatalogDataRead" and _actions(stmt) != ["s3:GetObject"]:
            problems.append("data read is not GetObject only")
        if _sid(stmt).startswith("TelemetryCatalogData"):
            prefixes = set(re.findall(r"local\.(\w*prefix\w*)", stmt))
            if prefixes != {"ducklake_telemetry_data_prefix"}:
                problems.append(f"{_sid(stmt)} references prefixes {sorted(prefixes)}")
    problems += [f"reader references {name}" for name in _READER_FORBIDDEN if name in body]
    secrets = re.findall(r"aws_secretsmanager_secret\.\w+", policy)
    if secrets != [_READER_SECRET]:
        problems.append(f"reader reads secrets {secrets}")
    parameters = re.findall(r'"arn:aws:ssm:[^"]*:parameter/secret/[^"]*"', policy)
    if parameters != [
        '"arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-reader-dsn"'
    ]:
        problems.append(f"reader reads parameters {parameters}")
    return problems


def reader_env_problems(text: str) -> list[str]:
    function = find_resource_block(_strip(text), "aws_lambda_function", "ducklake_telemetry_reader")
    env = _env_pairs(function)
    want = {
        "TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_prod_meta_schema",
        "TELEMETRY_DATA_PATH": "local.ducklake_telemetry_data_path",
        "TELEMETRY_DSN_SECRET_ID": f"{_READER_SECRET}.name",
    }
    problems = [f"env {k} is {env.get(k)!r}, want {v}" for k, v in want.items() if env.get(k) != v]
    problems += [
        f"reader env carries {k}" for k in ("TELEMETRY_BLOB_ROOT", "DUCKLAKE_DATA_PATH", "DUCKLAKE_META_SCHEMA") if k in env
    ]
    return problems


def concurrency_problems(texts: dict[str, str]) -> list[str]:
    def literal(file: str, name: str) -> int:
        block = find_resource_block(_strip(texts[file]), "aws_lambda_function", name)
        match = re.search(r"reserved_concurrent_executions\s*=\s*(\d+)\b", block)
        assert match is not None, f"{name} has no literal reserved concurrency"
        return int(match.group(1))

    reader = literal(_READER_FILE, "ducklake_telemetry_reader")
    writer = literal(_WRITER_FILE, "ducklake_telemetry_writer")
    twin = literal(_TWIN_FILE, "ducklake_telemetry_writer_smoke")
    problems: list[str] = []
    if 2 * reader > LOGINS["ducklake_telemetry_reader"].connection_limit:
        problems.append("reader concurrency exceeds its login limit")
    if 2 * writer > LOGINS["ducklake_telemetry_writer_prod"].connection_limit:
        problems.append("writer concurrency exceeds the production writer login limit")
    if 2 * twin > LOGINS["ducklake_telemetry_writer"].connection_limit:
        problems.append("twin concurrency exceeds the smoke writer login limit")
    return problems


def _real() -> dict[str, str]:
    return tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)


def test_twin_is_pinned_to_the_smoke_catalog_and_login() -> None:
    texts = _real()
    assert twin_problems(texts[_TWIN_FILE], texts["ducklake_lambdas.tf"]) == []


def test_reader_reads_only_the_production_telemetry_data_prefix() -> None:
    assert reader_problems(_real()[_READER_FILE]) == []


def test_reader_env_is_pinned_to_production_with_no_blob_root() -> None:
    assert reader_env_problems(_real()[_READER_FILE]) == []


def test_new_functions_carry_the_boundary_and_ignore_changes() -> None:
    texts = _real()
    for file, role, function in (
        (_TWIN_FILE, "ducklake_telemetry_writer_smoke", "ducklake_telemetry_writer_smoke"),
        (_READER_FILE, "ducklake_telemetry_reader", "ducklake_telemetry_reader"),
    ):
        body = _strip(texts[file])
        assert (
            _common_problems(
                find_resource_block(body, "aws_iam_role", role), find_resource_block(body, "aws_lambda_function", function)
            )
            == []
        )


def test_reserved_concurrency_fits_the_login_limits() -> None:
    assert concurrency_problems(_real()) == []


def _twin(text: str) -> list[str]:
    texts = _real()
    return twin_problems(text, texts["ducklake_lambdas.tf"])


_TWIN_RED: list[Callable[[str], str]] = [
    lambda t: t.replace("local.ducklake_smoke_meta_schema\n", "local.ducklake_telemetry_meta_schema\n", 1),
    lambda t: t.replace("local.ducklake_smoke_data_path", "local.ducklake_telemetry_data_path", 1),
    lambda t: t.replace(_WRITER_SECRET + ".name", _READER_SECRET + ".name", 1),
    lambda t: t.replace(
        'Action   = ["s3:GetObject", "s3:PutObject"]', 'Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]', 1
    ),
    lambda t: t.replace("local.ducklake_smoke_data_prefix", "local.ducklake_telemetry_data_prefix", 1),
    lambda t: t.replace("permissions_boundary = ", "# removed = ", 1),
    lambda t: t.replace("ignore_changes = [source_code_hash]", "ignore_changes = []"),
]


@pytest.mark.parametrize("mutate", _TWIN_RED)
def test_twin_predicate_red_on_each_mutation(mutate: Callable[[str], str]) -> None:
    text = _real()[_TWIN_FILE]
    mutated = mutate(text)
    assert mutated != text
    assert _twin(mutated) != []


def test_twin_predicate_red_when_the_smoke_local_moves() -> None:
    texts = _real()
    moved = texts["ducklake_lambdas.tf"].replace(
        'ducklake_smoke_meta_schema               = "ducklake_smoke"', "ducklake_smoke_meta_schema = local.x"
    )
    assert moved != texts["ducklake_lambdas.tf"]
    assert twin_problems(texts[_TWIN_FILE], moved) != []


_READER_RED: list[Callable[[str], str]] = [
    lambda t: t.replace('Action   = ["s3:GetObject"]', 'Action   = ["s3:GetObject", "s3:PutObject"]', 1),
    lambda t: t.replace("local.ducklake_telemetry_data_prefix", "local.telemetry_blob_data_prefix", 1),
    lambda t: t.replace(_READER_SECRET + ".arn", "aws_secretsmanager_secret.ducklake_neon_catalog_dsn.arn", 1),
    lambda t: t.replace('Sid      = "TelemetryCatalogDataList"', 'Sid      = "TelemetryBlobRead"', 1),
    lambda t: t.replace(
        'Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]',
        'Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "cloudwatch:PutMetricData"]',
        1,
    ),
    lambda t: t.replace("permissions_boundary = ", "# removed = ", 1),
    lambda t: t.replace("ignore_changes = [source_code_hash]", "ignore_changes = []"),
]


@pytest.mark.parametrize("mutate", _READER_RED)
def test_reader_predicate_red_on_each_mutation(mutate: Callable[[str], str]) -> None:
    text = _real()[_READER_FILE]
    mutated = mutate(text)
    assert mutated != text
    assert reader_problems(mutated) != []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("local.ducklake_telemetry_prod_meta_schema", "local.ducklake_smoke_meta_schema", 1),
        lambda t: t.replace("local.ducklake_telemetry_data_path", "local.ducklake_smoke_data_path", 1),
        lambda t: t.replace(
            "TELEMETRY_DSN_SECRET_ID       = ", "TELEMETRY_BLOB_ROOT = local.x\n      TELEMETRY_DSN_SECRET_ID       = ", 1
        ),
        lambda t: t.replace(_READER_SECRET + ".name", _WRITER_SECRET + ".name", 1),
    ],
)
def test_reader_env_predicate_red_on_each_mutation(mutate: Callable[[str], str]) -> None:
    text = _real()[_READER_FILE]
    mutated = mutate(text)
    assert mutated != text
    assert reader_env_problems(mutated) != []


def test_concurrency_predicate_red_when_a_literal_outgrows_its_login() -> None:
    texts = _real()
    texts[_READER_FILE] = texts[_READER_FILE].replace(
        "reserved_concurrent_executions = 5", "reserved_concurrent_executions = 6"
    )
    assert "reader concurrency exceeds its login limit" in concurrency_problems(texts)
    texts = _real()
    texts[_WRITER_FILE] = texts[_WRITER_FILE].replace(
        "reserved_concurrent_executions = 5", "reserved_concurrent_executions = 6"
    )
    assert concurrency_problems(texts) == ["writer concurrency exceeds the production writer login limit"]
    texts = _real()
    texts[_TWIN_FILE] = texts[_TWIN_FILE].replace("reserved_concurrent_executions = 5", "reserved_concurrent_executions = 11")
    assert concurrency_problems(texts) == ["twin concurrency exceeds the smoke writer login limit"]
