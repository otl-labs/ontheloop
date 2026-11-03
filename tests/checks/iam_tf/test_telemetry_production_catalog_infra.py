"""Decision 230 standing guard for the production telemetry catalog's personal-root infrastructure.

Pins, over the real terraform tree: the production data prefix is a sibling of the ops prefix with its own noncurrent
lifecycle rule, the three scoped login secrets are containers with no secret version, the admin maintenance role reaches
exactly the new prefix and the five secrets it reads, and the planner and the bootstrap apply reads list the two roles the
next plan creates. Every predicate is a pure function of HCL text with its own red case on synthetic HCL.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks.iam_tf._read_coverage import _extract_bracket_block, _split_top_level_objects
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments
from tests.fixtures.terraform_hcl_blocks import (
    _TERRAFORM_PERSONAL_DIR,
    assert_every_rule_enabled,
    assert_every_rule_has_expired_object_delete_marker,
    assert_every_rule_noncurrent_days,
    find_resource_block,
    split_rule_blocks,
    tf_dir_file_texts,
)

_BOOTSTRAP_DIR = _TERRAFORM_PERSONAL_DIR.parent / "bootstrap"
_RULE_ID = "telemetry-noncurrent-reclaim"
_RULE = f"ducklake-{_RULE_ID}"
_SECRETS = {
    "ducklake_telemetry_writer_prod_dsn": "agent-platform-ducklake-telemetry-writer-prod-dsn",
    "ducklake_telemetry_reader_dsn": "agent-platform-ducklake-telemetry-reader-dsn",
    "ducklake_smoke_maintainer_dsn": "agent-platform-ducklake-smoke-maintainer-dsn",
}
_READ_SECRETS = {
    "ducklake_neon_catalog_dsn",
    "ducklake_telemetry_writer_dsn",
    *_SECRETS,
}
_NEW_ROLES = (
    "role/agent-platform-ducklake-telemetry-writer-smoke",
    "role/agent-platform-ducklake-telemetry-reader",
)
_VERSION = (
    'resource "aws_secretsmanager_secret_version" "x" {\n'
    "  secret_id = aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.id\n}\n"
)
_DATA_RW = "${aws_s3_bucket.data_lake.arn}/${local.ducklake_telemetry_data_prefix}/*"

_ROOT = Path(__file__).resolve().parents[3]


def _list(raw: str) -> list[str]:
    return re.findall(r'"([^"]*)"', raw)


def _field(body: str, name: str, anchor: str = "") -> str:
    match = re.search(rf"{anchor}\b{name}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[^\s,]+)", body, re.M)
    return match.group(1) if match else ""


def _json_conditions(body: str) -> list[tuple[str, str, list[str]]]:
    head = re.search(r"\bCondition\s*=\s*\{", _mask(body))
    if head is None:
        return []
    inner, out = _block_body(body, head.end() - 1), []
    for op in re.finditer(r"(\w+)\s*=\s*\{", _mask(inner)):
        for key in re.finditer(r'"([^"]+)"\s*=\s*(\[[^\]]*\]|"[^"]*")', _block_body(inner, op.end() - 1)):
            out.append((op.group(1), key.group(1), _list(key.group(2))))
    return out


def _json_statements(text: str) -> list[dict]:
    clean, out = _strip_comments(text), []
    for head in re.finditer(r"\bStatement\s*=\s*\[", clean):
        for body in _split_top_level_objects(_extract_bracket_block(clean, head.end() - 1)):
            out.append(
                {
                    "sid": (_list(_field(body, "Sid")) or [""])[0],
                    "effect": (_list(_field(body, "Effect")) or [""])[0],
                    "actions": _list(_field(body, "Action")),
                    "resources": _field(body, "Resource"),
                    "conditions": _json_conditions(body),
                }
            )
    return out


def _block_statements(text: str) -> list[dict]:
    clean, out = _strip_comments(text), []
    for head in re.finditer(r"\bstatement\s*\{", _mask(clean)):
        body, conds = _block_body(clean, head.end() - 1), []
        for cond in re.finditer(r"\bcondition\s*\{", _mask(body)):
            cbody = _block_body(body, cond.end() - 1)
            conds.append(
                (_list(_field(cbody, "test"))[0], _list(_field(cbody, "variable"))[0], _list(_field(cbody, "values")))
            )
        out.append(
            {
                "sid": (_list(_field(body, "sid", "^\\s*")) or [""])[0],
                "effect": (_list(_field(body, "effect", "^\\s*")) or ["Allow"])[0],
                "actions": _list(_field(body, "actions", "^\\s*")),
                "resources": _field(body, "resources", "^\\s*"),
                "conditions": conds,
            }
        )
    return out


def _bootstrap(name: str) -> str:
    return (_ROOT / "terraform" / "bootstrap" / name).read_text(encoding="utf-8")


def _locals(text: str) -> dict[str, str]:
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', _strip_comments(text), re.MULTILINE))


def prefix_problems(text: str) -> list[str]:
    loc = _locals(text)
    expected = {
        "ducklake_telemetry_data_prefix": "ducklake-telemetry",
        "ducklake_telemetry_data_path": "s3://${aws_s3_bucket.data_lake.bucket}/ducklake-telemetry/",
        "ducklake_telemetry_prod_meta_schema": "ducklake_telemetry",
        "ducklake_telemetry_reader_function": "agent-platform-ducklake-telemetry-reader",
        "ducklake_telemetry_meta_schema": "ducklake_telemetry",
        "ducklake_prod_data_prefix": "ducklake",
        "ducklake_smoke_data_prefix": "ducklake-neon-smoke",
    }
    problems = [f"local {k} is {loc.get(k)!r}, expected {v!r}" for k, v in expected.items() if loc.get(k) != v]
    prefixes = {k: v for k, v in loc.items() if k.endswith("_data_prefix")}
    for name, prefix in prefixes.items():
        for other_name, other in prefixes.items():
            if name != other_name and f"{prefix}/".startswith(f"{other}/"):
                problems.append(f"{name} {prefix!r} sits inside {other_name} {other!r}")
    return problems


def lifecycle_problems(main_text: str) -> list[str]:
    block = find_resource_block(main_text, "aws_s3_bucket_lifecycle_configuration", "data_lake")
    rules = [r for r in split_rule_blocks(block) if re.search(rf'\bid\s*=\s*"{_RULE}"', r)]
    if len(rules) != 1:
        return [f"expected exactly one {_RULE} rule, found {len(rules)}"]
    rule = rules[0]
    problems: list[str] = []
    for check in (
        assert_every_rule_enabled,
        assert_every_rule_has_expired_object_delete_marker,
        lambda r: assert_every_rule_noncurrent_days(r, 30),
    ):
        try:
            check(rules)
        except AssertionError as exc:
            problems.append(str(exc))
    if not re.search(r'prefix\s*=\s*"\$\{local\.ducklake_telemetry_data_prefix\}/"', rule):
        problems.append("rule prefix must derive from local.ducklake_telemetry_data_prefix with a trailing slash")
    if re.search(r"(?<!noncurrent_version_)expiration\s*\{[^}]*\bdays\b", rule):
        problems.append("rule expires current versions")
    if not re.search(r"abort_incomplete_multipart_upload\s*\{\s*days_after_initiation\s*=\s*1\b", rule):
        problems.append("rule does not abort incomplete multipart uploads after 1 day")
    return problems


def secret_problems(texts: dict[str, str]) -> list[str]:
    joined = "\n".join(_strip_comments(t) for t in texts.values())
    problems: list[str] = []
    for resource, name in _SECRETS.items():
        block = re.search(rf'resource\s+"aws_secretsmanager_secret"\s+"{resource}"\s*\{{[^}}]*name\s*=\s*"([^"]*)"', joined)
        if block is None or block.group(1) != name:
            problems.append(f"secret container {resource} missing or not named {name}")
        if re.search(rf"aws_secretsmanager_secret\.{resource}\.(?:id|arn|name)\b", _version_blocks(joined)):
            problems.append(f"{resource} has a secret version (the value is written out of band)")
    return problems


def _version_blocks(joined: str) -> str:
    return "\n".join(re.findall(r'resource\s+"aws_secretsmanager_secret_version"[^\n]*\{[^}]*\}', joined))


def maintenance_problems(policy_block: str) -> list[str]:
    stmts = {s["sid"]: s for s in _json_statements(policy_block)}
    problems: list[str] = []
    dsn = stmts.get("NeonDsnRead")
    read = set(re.findall(r"aws_secretsmanager_secret\.(\w+)\.arn", dsn["resources"])) if dsn else set()
    if read != _READ_SECRETS:
        problems.append(f"NeonDsnRead reads {sorted(read)}, expected {sorted(_READ_SECRETS)}")
    rw = stmts.get("TelemetryCatalogDataReadWriteDelete")
    if (
        rw is None
        or sorted(rw["actions"]) != ["s3:DeleteObject", "s3:GetObject", "s3:PutObject"]
        or _list(rw["resources"]) != [_DATA_RW]
    ):
        problems.append("TelemetryCatalogDataReadWriteDelete is not Get/Put/DeleteObject on the telemetry data prefix")
    listing = stmts.get("TelemetryCatalogDataList")
    cond = [("StringLike", "s3:prefix", ["${local.ducklake_telemetry_data_prefix}/*"])]
    if (
        listing is None
        or sorted(listing["actions"]) != ["s3:GetBucketLocation", "s3:ListBucket"]
        or listing["conditions"] != cond
    ):
        problems.append("TelemetryCatalogDataList is not ListBucket/GetBucketLocation conditioned on the telemetry prefix")
    body = _strip_comments(policy_block)
    problems += [
        f"role policy matches {p!r}"
        for p in (r"DeleteObjectVersion", r"telemetry_blob", r"telemetry-blobs")
        if re.search(p, body)
    ]
    prefixes = set(re.findall(r"local\.(\w+_data_prefix)", body))
    if prefixes != {"ducklake_prod_data_prefix", "ducklake_telemetry_data_prefix"}:
        problems.append(f"data prefixes reached are {sorted(prefixes)}")
    return problems


def reads_problems(oidc_text: str, apply_reads_text: str) -> list[str]:
    planner = next((s for s in _block_statements(oidc_text) if s["sid"] == "IAMPlatformRolesRead"), None)
    apply = next((s for s in _json_statements(apply_reads_text) if s["sid"] == "IAMRolesRead"), None)
    problems: list[str] = []
    for label, stmt in (("planner IAMPlatformRolesRead", planner), ("apply IAMRolesRead", apply)):
        resources = stmt["resources"] if stmt else ""
        problems += [f"{label} lacks {role}" for role in _NEW_ROLES if f'{role}"' not in resources]
    return problems


def _personal() -> dict[str, str]:
    return tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)


def _policy() -> str:
    return find_resource_block(_personal()["ducklake_maintenance.tf"], "aws_iam_role_policy", "ducklake_maintenance")


def _set_local(text: str, name: str, value: str) -> str:
    mutated = re.sub(rf'(\b{name}\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{value}"', text, count=1)
    assert mutated != text, name
    return mutated


def _rule_block(text: str) -> str:
    block = find_resource_block(text, "aws_s3_bucket_lifecycle_configuration", "data_lake")
    return next(r for r in split_rule_blocks(block) if _RULE in r)


def test_production_telemetry_prefix_is_a_sibling_of_the_ops_prefix() -> None:
    text = _personal()["ducklake_lambdas.tf"]
    assert prefix_problems(text) == []
    for name, value in (
        ("ducklake_telemetry_data_prefix", "ducklake/telemetry"),
        ("ducklake_telemetry_data_path", "s3://${aws_s3_bucket.data_lake.bucket}/ducklake/telemetry/"),
        ("ducklake_telemetry_prod_meta_schema", "ducklake_ops"),
        ("ducklake_telemetry_reader_function", "agent-platform-ducklake-reader"),
        ("ducklake_telemetry_meta_schema", "ducklake_smoke"),
    ):
        assert prefix_problems(_set_local(text, name, value)), name
    assert prefix_problems(_set_local(text, "ducklake_prod_data_prefix", "ducklake-telemetry"))


def test_production_telemetry_prefix_has_its_own_lifecycle_rule() -> None:
    text = (_TERRAFORM_PERSONAL_DIR / "main.tf").read_text(encoding="utf-8")
    assert lifecycle_problems(text) == []
    rule = _rule_block(text)
    for old, new in (
        (_RULE, "other-rule"),
        ('status = "Enabled"', 'status = "Disabled"'),
        ("${local.ducklake_telemetry_data_prefix}/", "${local.ducklake_telemetry_data_prefix}"),
        ("${local.ducklake_telemetry_data_prefix}/", "${local.ducklake_prod_data_prefix}/"),
        ("noncurrent_days = 30", "noncurrent_days = 90"),
        ("expired_object_delete_marker = true", "days = 365"),
        ("days_after_initiation = 1", "days_after_initiation = 7"),
    ):
        assert old in rule, old
        assert lifecycle_problems(text.replace(rule, rule.replace(old, new, 1), 1)), old


def test_login_secrets_are_containers_without_a_version() -> None:
    texts = _personal()
    assert secret_problems(texts) == []
    assert secret_problems({}) and len(secret_problems({})) == 3
    versioned = {
        **texts,
        "extra.tf": _VERSION,
    }
    assert secret_problems(versioned) == [
        "ducklake_smoke_maintainer_dsn has a secret version (the value is written out of band)"
    ]


def test_maintenance_role_reaches_the_production_telemetry_prefix_and_login_secrets_only() -> None:
    policy = _policy()
    assert maintenance_problems(policy) == []
    start = policy.index('"TelemetryCatalogDataReadWriteDelete"')
    widened = policy[:start] + policy[start:].replace('"s3:DeleteObject"]', '"s3:DeleteObject", "s3:DeleteObjectVersion"]', 1)
    assert maintenance_problems(widened)
    mutations = (
        (_DATA_RW, "${aws_s3_bucket.data_lake.arn}/${local.telemetry_blob_data_prefix}/*"),
        (_DATA_RW, "${aws_s3_bucket.data_lake.arn}/*"),
        ("aws_secretsmanager_secret.ducklake_telemetry_reader_dsn.arn,", ""),
        (
            "aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.arn,",
            "aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.arn,\n aws_secretsmanager_secret.other.arn,",
        ),
        ('"s3:prefix" = ["${local.ducklake_telemetry_data_prefix}/*"]', '"s3:prefix" = ["*"]'),
    )
    for old, new in mutations:
        assert old in policy, old
        assert maintenance_problems(policy.replace(old, new, 1)), old


def test_planner_and_apply_reads_list_the_new_roles() -> None:
    oidc = (_TERRAFORM_PERSONAL_DIR / "oidc.tf").read_text(encoding="utf-8")
    apply_reads = (_BOOTSTRAP_DIR / "github_ci_apply_reads.tf").read_text(encoding="utf-8")
    assert reads_problems(oidc, apply_reads) == []
    for role in _NEW_ROLES:
        arn = f'"arn:aws:iam::${{var.account_id}}:{role}",'
        assert arn in oidc and arn in apply_reads, role
        assert reads_problems(oidc.replace(arn, "", 1), apply_reads) == [f"planner IAMPlatformRolesRead lacks {role}"]
        assert reads_problems(oidc, apply_reads.replace(arn, "", 1)) == [f"apply IAMRolesRead lacks {role}"]


def test_maintenance_smoke_reads_only_the_smoke_maintainer_secret() -> None:
    text = (_TERRAFORM_PERSONAL_DIR / "ducklake_maintenance_smoke.tf").read_text(encoding="utf-8")
    assert "DUCKLAKE_DSN_SECRET_ID        = aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.name" in text
    read = re.search(r'Sid\s*=\s*"NeonDsnRead".*?Resource\s*=\s*\[([^\]]*)\]', text, re.S)
    assert read is not None
    assert [r.strip() for r in read.group(1).split(",") if r.strip()] == [
        "aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.arn"
    ]
    assert "ducklake_neon_catalog_dsn" not in text
    assert "ducklake_telemetry_writer_dsn" not in text
