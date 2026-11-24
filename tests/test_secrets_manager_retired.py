"""Standing guard for PLAN-secrets-to-ssm-retire (Decision 237): the nine Secrets Manager copies are retired.

TestSecretsManagerRetired and TestRedCases are the graduated record; TestRemovedBlockOnce is the one-time claim that
the removed block for the DSN secret version is present, outside the graduated record (Decision 244 cl.4).
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PERSONAL = Path("terraform") / "personal"
BOOTSTRAP = Path("terraform") / "bootstrap"
_NINE = (
    r"neon-api-key|agent-platform-terraform-personal-tfvars|ducklake-neon-catalog-dsn|agent-platform-(?:deepseek|anthropic)-api-key"
    r"|agent-platform-ducklake-(?:telemetry-writer|telemetry-writer-prod|telemetry-reader|smoke-maintainer)-dsn"
    r"|ducklake_neon_catalog_dsn_secret_arn|TELEMETRY_DSN_SECRET_ID|DUCKLAKE_DSN_SECRET_ID"
)
_NINE_RE = re.compile(_NINE)
_READ_TOKEN_RE = re.compile(
    r"secretsmanager|secrets manager|secret_arn|get-secret-value|get_secret_value|SecretId|_SECRET_ID", re.IGNORECASE
)
_SWEEP_DIRS = ("src", "scripts", ".github", "terraform", "bin", "docs/runbooks")
_SWEEP_EXCLUDED = {"scripts/checks/iam_tf/_write_coverage.py", "scripts/checks/iam_tf/_read_coverage.py"}
_SKIP_PARTS = {"__pycache__", ".terraform", ".git", ".venv", "node_modules"}
_RESOURCE_RE = re.compile(r'^\s*resource\s+"aws_secretsmanager_secret(?:_version)?"', re.MULTILINE)
_LOOKUP_RE = re.compile(r'^\s*data\s+"aws_secretsmanager_secret(?:_version)?"', re.MULTILINE)
_ACTIONS_RE = re.compile(r"\b[Aa]ctions?\s*=\s*\[(.*?)\]", re.DOTALL)
_VALUE_VERBS = ("secretsmanager:getsecretvalue", "secretsmanager:batchgetsecretvalue")
_NAMED_LOOKUP_RE = re.compile(
    rf'data\s+"aws_secretsmanager_secret(?:_version)?"\s+"\w+"\s*\{{\s*(?:name|secret_id)\s*=\s*"(?:{_NINE})"'
)
_OUTPUT_RE = re.compile(r'^\s*output\s+"\w*secret_arn"', re.MULTILINE)
_ENV_RE = re.compile(r"^\s*(?:TELEMETRY_DSN_SECRET_ID|DUCKLAKE_DSN_SECRET_ID)\s*=", re.MULTILINE)
_RETIRED_SIDS = {
    "SecretsManagerValueReadEnumerated",
    "SecretsManagerDuckLakeNeonDSN",
    "SecretsManagerUpdateEnumerated",
    "InferenceCredentialsRead",
    "NeonCatalogDsnRead",
    "NeonDsnRead",
    "TelemetryDsnRead",
    "TelemetryReaderDsnRead",
    "DriftSessionSecretValueRead",
    "PlanSecretValueRead",
    "SecretsManagerTfvarsRead",
    "SecretsManagerDuckLakeNeonDSNRead",
}
_KEPT_BOOTSTRAP_SIDS = {
    "github_ci_apply_reads.tf": {"SecretsManagerMetadataRead", "SecretParameterValueRead"},
    "github_ci_apply_policy.tf": {"SecretsManagerMetadataWrite", "SecretParameterDsnWrite"},
    "platform_dev_policies.tf": {"InferenceCredentialsParameterRead"},
}
_KEPT_PERSONAL_SIDS = {
    "ducklake_lambdas.tf": {"NeonDsnParameterRead"},
    "ducklake_telemetry_writer.tf": {"TelemetryDsnParameterRead"},
    "ducklake_telemetry_reader.tf": {"TelemetryReaderDsnParameterRead"},
    "oidc_pipeline_roles.tf": {"DriftSessionSecretParameterRead"},
    "oidc_trusted_plan_role.tf": {"PlanSecretParameterRead"},
}


def _strip(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//")))


def _tf_texts(root: Path, rel: Path) -> dict[str, str]:
    return {p.name: _strip(p.read_text(encoding="utf-8")) for p in sorted((root / rel).glob("*.tf"))}


def _sids(text: str) -> set[str]:
    return set(re.findall(r'\b[Ss]id\s*=\s*"(\w+)"', text))


def _value_grants(text: str) -> list[str]:
    found: list[str] = []
    for body in _ACTIONS_RE.findall(text):
        for action in re.findall(r'"([^"]+)"', body):
            if any(fnmatch.fnmatchcase(verb, action.lower()) for verb in _VALUE_VERBS):
                found.append(action)
    return found


def personal_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, text in texts.items():
        if _RESOURCE_RE.search(text):
            problems.append(f"{name} declares an aws_secretsmanager_secret or _version resource")
        if _LOOKUP_RE.search(text) or _NAMED_LOOKUP_RE.search(text):
            problems.append(f"{name} reads an aws_secretsmanager_secret data source")
        if _value_grants(text):
            problems.append(f"{name} grants a Secrets Manager value verb {_value_grants(text)}")
        if _ENV_RE.search(text):
            problems.append(f"{name} sets a secret-id environment variable")
        if _OUTPUT_RE.search(text):
            problems.append(f"{name} outputs a Secrets Manager ARN")
        for sid in sorted(_sids(text) & _RETIRED_SIDS):
            problems.append(f"{name} still carries the retired Sid {sid}")
        for line in text.splitlines():
            if _NINE_RE.search(line) and re.search(r"secretsmanager|secret_arn|_SECRET_ID", line, re.IGNORECASE):
                problems.append(f"{name} names a retired secret in a Secrets Manager line: {line.strip()[:80]}")
    return problems


def kept_problems(texts: dict[str, str], kept: dict[str, set[str]]) -> list[str]:
    return [
        f"{name} lacks the kept Sid {sid}" for name, sids in kept.items() for sid in sorted(sids - _sids(texts.get(name, "")))
    ]


def sweep_hits(root: Path) -> list[str]:
    hits: list[str] = []
    for top in _SWEEP_DIRS:
        base = root / top
        if not base.exists():
            hits.append(f"MISSING {top}")
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
            for fname in filenames:
                path = Path(dirpath) / fname
                rel = path.relative_to(root).as_posix()
                if rel in _SWEEP_EXCLUDED:
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                for n, line in enumerate(lines, 1):
                    if _NINE_RE.search(line) and "/secret/" not in line and _READ_TOKEN_RE.search(line):
                        hits.append(f"{rel}:{n}")
    return hits


class TestSecretsManagerRetired:
    def test_personal_declares_no_secrets_manager_surface_for_the_nine(self) -> None:
        assert personal_problems(_tf_texts(REPO, PERSONAL)) == []

    def test_bootstrap_sids_and_lookups_are_gone_and_the_ssm_counterparts_remain(self) -> None:
        texts = _tf_texts(REPO, BOOTSTRAP)
        for name, text in texts.items():
            assert not (_sids(text) & _RETIRED_SIDS), (name, _sids(text) & _RETIRED_SIDS)
            assert not _NAMED_LOOKUP_RE.search(text), name
            for line in text.splitlines():
                assert not (_NINE_RE.search(line) and "secretsmanager" in line.lower()), (name, line)
        assert kept_problems(texts, _KEPT_BOOTSTRAP_SIDS) == []

    def test_personal_ssm_counterparts_remain(self) -> None:
        assert kept_problems(_tf_texts(REPO, PERSONAL), _KEPT_PERSONAL_SIDS) == []

    def test_the_dsn_parameter_remains_and_no_envelope_does(self) -> None:
        text = _tf_texts(REPO, PERSONAL)["neon_ducklake_catalog.tf"]
        assert 'resource "aws_ssm_parameter" "ducklake_neon_catalog_dsn"' in text
        assert not _RESOURCE_RE.search(text)

    def test_nothing_outside_history_reads_the_nine_from_secrets_manager(self) -> None:
        assert sweep_hits(REPO) == []


_ENVELOPE = 'resource "aws_secretsmanager_secret" "x" {\n  name = "agent-platform-deepseek-api-key"\n}\n'
_GRANT = 'Sid = "S"\nActions = ["%s"]\nResource = ["arn:aws:secretsmanager:r:a:secret:neon-api-key-*"]\n'


class TestRedCases:
    def test_envelope_and_version_resources_are_found(self) -> None:
        assert personal_problems({"a.tf": _ENVELOPE})
        assert personal_problems({"a.tf": 'resource "aws_secretsmanager_secret_version" "v" {\n}\n'})

    def test_value_grants_and_covering_wildcards_are_found(self) -> None:
        for action in (
            "secretsmanager:GetSecretValue",
            "secretsmanager:BatchGetSecretValue",
            "secretsmanager:Get*",
            "secretsmanager:*",
            "*",
        ):
            assert any("value verb" in p for p in personal_problems({"a.tf": _GRANT % action})), action
        assert not any("value verb" in p for p in personal_problems({"a.tf": _GRANT % "secretsmanager:DescribeSecret"}))

    def test_env_var_output_lookup_and_retired_sid_are_found(self) -> None:
        for text in (
            "      TELEMETRY_DSN_SECRET_ID = x.name\n",
            "      DUCKLAKE_DSN_SECRET_ID = x.name\n",
            'output "deepseek_api_key_secret_arn" {\n}\n',
            'data "aws_secretsmanager_secret" "d" {\n  name = "x"\n}\n',
            'Sid = "NeonDsnRead"\n',
        ):
            assert personal_problems({"a.tf": text}), text

    def test_a_bootstrap_lookup_of_a_retired_secret_is_found_and_the_app_key_lookup_is_not(self) -> None:
        retired = 'data "aws_secretsmanager_secret" "d" {\n  name = "agent-platform-deepseek-api-key"\n}\n'
        assert _NAMED_LOOKUP_RE.search(retired)
        assert not _NAMED_LOOKUP_RE.search('data "aws_secretsmanager_secret" "k" {\n  name = "app-key"\n}\n')

    def test_comments_do_not_count(self) -> None:
        assert personal_problems({"a.tf": _strip('# resource "aws_secretsmanager_secret" "x" {\n')}) == []

    def test_a_missing_kept_sid_is_found(self) -> None:
        assert kept_problems({"f.tf": 'Sid = "A"'}, {"f.tf": {"A", "B"}, "g.tf": {"C"}}) == [
            "f.tf lacks the kept Sid B",
            "g.tf lacks the kept Sid C",
        ]

    def test_the_sweep_flags_reads_and_exempts_parameters_and_the_coverage_maps(self, tmp_path: Path) -> None:
        for top in _SWEEP_DIRS:
            (tmp_path / top).mkdir(parents=True)
        read = 'boto3.get_secret_value(SecretId="neon-api-key")\n'  # pragma: allowlist secret
        (tmp_path / "src" / "a.py").write_text(read, encoding="utf-8")
        (tmp_path / "scripts" / "b.py").write_text(
            'ssm.get_parameter(Name="/secret/neon-api-key")  # SecretId\n', encoding="utf-8"
        )
        (tmp_path / "bin" / "c.sh").write_text("Read the ducklake-neon-catalog-dsn from Secrets Manager\n", encoding="utf-8")
        (tmp_path / ".github" / "d.yml").write_text("neon-api-key is a plain mention\n", encoding="utf-8")
        cov = tmp_path / "scripts" / "checks" / "iam_tf"
        cov.mkdir(parents=True)
        (cov / "_read_coverage.py").write_text('"secretsmanager" neon-api-key\n', encoding="utf-8")
        assert sorted(sweep_hits(tmp_path)) == ["bin/c.sh:1", "src/a.py:1"]

    def test_the_sweep_reports_a_missing_directory(self, tmp_path: Path) -> None:
        assert "MISSING src" in sweep_hits(tmp_path)


class TestRemovedBlockOnce:
    def test_the_dsn_secret_version_is_forgotten_without_a_destroy_call(self) -> None:
        text = _tf_texts(REPO, PERSONAL)["neon_ducklake_catalog.tf"]
        assert re.search(
            r"removed\s*\{\s*from\s*=\s*aws_secretsmanager_secret_version\.ducklake_neon_catalog_dsn\s*"
            r"lifecycle\s*\{\s*destroy\s*=\s*false\s*\}\s*\}",
            text,
        )
