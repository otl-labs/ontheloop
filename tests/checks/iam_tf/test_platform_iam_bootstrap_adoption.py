"""terraform/bootstrap owns the seven PlatformDev/PlatformAdmin addresses (PLAN-platform-iam-
bootstrap-adopt, rec-3838 plan 1 of 2) -- durable, graduated.

Asserts the adoption is complete and protected: both roles and all five inline policies are
declared with correctly-shaped import blocks, prevent_destroy is present where required, the two
self-access preconditions parse the grant itself (an unconditioned Allow of the needed actions on
the needed resources, never a substring of the rendered policy), the trust documents carry their
own sts:ExternalId condition, the ExternalId variables are sensitive with no default, the secret
ARNs come from by-name data source lookups, PlatformAdmin's managed-policy attachment references
the adopted role, and every constant this root copies from terraform/personal equals its source
(compared as templates, never rendered ARNs -- Decision 101).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.checks.iam_tf._platform_security_hcl import _mask, _resource_body, _resources, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _REPO_ROOT / "terraform" / "bootstrap"
_PERSONAL = _REPO_ROOT / "terraform" / "personal"

_SEVEN = {
    ("aws_iam_role", "platform_dev"): "PlatformDev",
    ("aws_iam_role", "platform_admin"): "PlatformAdmin",
    ("aws_iam_role_policy", "platform_dev_runtime"): "PlatformDev:DailyOps",
    ("aws_iam_role_policy", "platform_admin_ops"): "PlatformAdmin:AdminOps",
    ("aws_iam_role_policy", "platform_admin_datalake"): "PlatformAdmin:PlatformDataLakeProvisioning",
    ("aws_iam_role_policy", "platform_admin_bootstrap_state"): "PlatformAdmin:BootstrapStateProvisioning",
    ("aws_iam_role_policy", "platform_admin_ducklake_breakglass"): "PlatformAdmin:DuckLakeBreakGlass",
}


def _bootstrap_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_BOOTSTRAP.glob("*.tf"))}


def _joined(texts: dict[str, str]) -> str:
    return "\n".join(texts.values())


def _import_ids(text: str) -> dict[str, str]:
    """{resource_addr: import_id} for every `import { id = "..." to = type.name }` block."""
    out: dict[str, str] = {}
    for m in re.finditer(r'import\s*\{\s*id\s*=\s*"([^"]+)"\s*\n\s*to\s*=\s*([\w.]+)\s*\}', _strip_comments(text)):
        out[m.group(2)] = m.group(1)
    return out


def _has_prevent_destroy(body: str) -> bool:
    lc_m = re.search(r"\blifecycle\s*\{", _mask(body))
    if lc_m is None:
        return False
    from tests.checks.iam_tf._platform_security_hcl import _block_body

    return bool(re.search(r"\bprevent_destroy\s*=\s*true", _block_body(body, lc_m.end() - 1)))


def _preconditions(body: str) -> list[str]:
    from tests.checks.iam_tf._platform_security_hcl import _block_body

    lc_m = re.search(r"\blifecycle\s*\{", _mask(body))
    if lc_m is None:
        return []
    lc_body = _block_body(body, lc_m.end() - 1)
    return [_block_body(lc_body, m.end() - 1) for m in re.finditer(r"\bprecondition\s*\{", _mask(lc_body))]


def _self_access_precondition(resource_body: str) -> str | None:
    """The resource's self-access precondition -- the one that is not the <= 10240 B size measure."""
    for p in _preconditions(resource_body):
        if "10240" not in p:
            return p
    return None


def _normalise_precondition(text: str) -> str:
    """Comments stripped, error_message attribute removed, whitespace runs collapsed to one space."""
    stripped = _strip_comments(text)
    stripped = re.sub(r'error_message\s*=\s*"(?:[^"\\]|\\.)*"', "", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


# Canonical condition HCL pinned by the plan's scope rows, normalised the same way the real tree's
# precondition text is normalised below -- an exact-match oracle (Decision 181): any token-preserving
# weakening (&& turned into ||, alltrue turned into anytrue, an inverted comparison) changes this
# string's text and fails here, never silently in the ADMIN gate.
_ADMIN_OPS_SELF_ACCESS_CONDITION = (
    "condition = anytrue([ for s in jsondecode(local.platform_admin_ops_policy_json).Statement : "
    'try(s.Sid, "") == "IAMFull" && s.Effect == "Allow" && !can(s.Condition) '
    '&& contains(flatten([try(s.Action, [])]), "iam:*") '
    '&& contains(flatten([try(s.Resource, [])]), "*") ])'
)

_BOOTSTRAP_STATE_SELF_ACCESS_CONDITION = (
    "condition = alltrue([ for arn, actions in { "
    '"arn:aws:s3:::agent-platform-bootstrap-tfstate" = ["s3:ListBucket"] '
    '"arn:aws:s3:::agent-platform-bootstrap-tfstate/*" = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"] '
    "} : anytrue([ for s in jsondecode(local.platform_admin_bootstrap_state_policy_json).Statement : "
    's.Effect == "Allow" && !can(s.Condition) '
    "&& contains(flatten([try(s.Resource, [])]), arn) "
    "&& alltrue([for a in actions : contains(flatten([try(s.Action, [])]), a)]) ]) ])"
)


def _self_access_guard_problems(resource_body: str | None, *, resource_name: str, canonical_condition: str) -> list[str]:
    out: list[str] = []
    if resource_body is None:
        return out
    precond = _self_access_precondition(resource_body)
    if precond is None:
        out.append(f"{resource_name} is missing its self-access precondition")
        return out
    if "strcontains(" in _strip_comments(precond):
        out.append(f"{resource_name}'s self-access precondition still uses strcontains( instead of parsing the grant")
    if _normalise_precondition(precond) != canonical_condition:
        out.append(f"{resource_name}'s self-access precondition no longer matches the pinned parsed-grant condition")
    error_message_m = re.search(r'error_message\s*=\s*"((?:[^"\\]|\\.)*)"', _strip_comments(precond))
    error_message = error_message_m.group(1) if error_message_m else ""
    if resource_name not in error_message or "self-access grant" not in error_message:
        out.append(
            f"{resource_name}'s self-access precondition error_message is missing its resource name "
            "or the phrase 'self-access grant'"
        )
    return out


def _collect_bodies_and_imports(texts: dict[str, str]) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    bodies: dict[tuple[str, str], str] = {}
    import_ids: dict[str, str] = {}
    for text in texts.values():
        for kind, rtype, rname in _resources(text):
            if kind == "resource":
                body = _resource_body(text, rtype, rname)
                if body is not None:
                    bodies[(rtype, rname)] = body
        import_ids.update(_import_ids(text))
    return bodies, import_ids


def _import_problems(bodies: dict[tuple[str, str], str], import_ids: dict[str, str]) -> list[str]:
    out: list[str] = []
    for addr, want_id in _SEVEN.items():
        rtype, rname = addr
        full_addr = f"{rtype}.{rname}"
        if bodies.get(addr) is None:
            out.append(f"{full_addr} is not declared in terraform/bootstrap")
            continue
        got_id = import_ids.get(full_addr)
        if got_id != want_id:
            out.append(f"{full_addr} import id is {got_id!r}, expected {want_id!r}")
    return out


def _protection_problems(bodies: dict[tuple[str, str], str]) -> list[str]:
    out: list[str] = []
    for addr in (("aws_iam_role", "platform_dev"), ("aws_iam_role", "platform_admin")):
        body = bodies.get(addr)
        if body is not None and not _has_prevent_destroy(body):
            out.append(f"{addr[0]}.{addr[1]} is missing lifecycle prevent_destroy")

    admin_ops_body = bodies.get(("aws_iam_role_policy", "platform_admin_ops"))
    if admin_ops_body is not None:
        if not _has_prevent_destroy(admin_ops_body):
            out.append("aws_iam_role_policy.platform_admin_ops (AdminOps) is missing lifecycle prevent_destroy")
        out.extend(
            _self_access_guard_problems(
                admin_ops_body,
                resource_name="PlatformAdmin AdminOps",
                canonical_condition=_ADMIN_OPS_SELF_ACCESS_CONDITION,
            )
        )

    state_body = bodies.get(("aws_iam_role_policy", "platform_admin_bootstrap_state"))
    if state_body is not None:
        if not _has_prevent_destroy(state_body):
            out.append(
                "aws_iam_role_policy.platform_admin_bootstrap_state (BootstrapStateProvisioning) is missing "
                "lifecycle prevent_destroy"
            )
        out.extend(
            _self_access_guard_problems(
                state_body,
                resource_name="PlatformAdmin BootstrapStateProvisioning",
                canonical_condition=_BOOTSTRAP_STATE_SELF_ACCESS_CONDITION,
            )
        )
    return out


def _size_precondition_problems(joined: str) -> list[str]:
    out: list[str] = []
    for sid, name in (
        ("platform_dev_runtime_policy_json", "DailyOps"),
        ("platform_admin_ops_policy_json", "AdminOps"),
        ("platform_admin_datalake_policy_json", "PlatformDataLakeProvisioning"),
        ("platform_admin_bootstrap_state_policy_json", "BootstrapStateProvisioning"),
        ("platform_admin_ducklake_breakglass_policy_json", "DuckLakeBreakGlass"),
    ):
        if not re.search(rf"length\(jsonencode\(jsondecode\(local\.{re.escape(sid)}\)\)\)\s*<=\s*10240", joined):
            out.append(f"{name} ({sid}) is missing its <= 10240 B size precondition")
    return out


def _external_id_variable_problems(joined: str) -> list[str]:
    from tests.checks.iam_tf._platform_security_hcl import _block_body

    out: list[str] = []
    for var in ("platform_dev_external_id", "platform_admin_external_id"):
        m = re.search(rf'variable\s+"{var}"\s*\{{', joined)
        if m is None:
            out.append(f"variable {var!r} is not declared")
            continue
        body = _block_body(joined, m.end() - 1)
        if not re.search(r"\bsensitive\s*=\s*true", body):
            out.append(f"variable {var!r} is not sensitive")
        if re.search(r"^\s*default\s*=", _strip_comments(body), re.M):
            out.append(f"variable {var!r} carries a default -- it must have none")
    return out


def _attachment_and_role_shape_problems(bodies: dict[tuple[str, str], str]) -> list[str]:
    out: list[str] = []
    attach_body = bodies.get(("aws_iam_role_policy_attachment", "platform_security_admin"))
    if attach_body is None:
        out.append("aws_iam_role_policy_attachment.platform_security_admin is not declared in terraform/bootstrap")
    else:
        role_m = re.search(r"^\s*role\s*=\s*(.+?)\s*$", _strip_comments(attach_body), re.M)
        role_val = role_m.group(1) if role_m else None
        if role_val != "aws_iam_role.platform_admin.name":
            out.append(f"platform_security_admin attachment role is {role_val!r}, expected aws_iam_role.platform_admin.name")

    dev_body = bodies.get(("aws_iam_role", "platform_dev"))
    if dev_body is not None:
        head = "\n".join(dev_body.splitlines()[:3])
        if "aws_iam_policy.github_ci_apply_boundary.arn" not in head:
            out.append("aws_iam_role.platform_dev does not reference the boundary within its block's first 3 lines")
    admin_body = bodies.get(("aws_iam_role", "platform_admin"))
    if admin_body is not None and "permissions_boundary" in admin_body:
        out.append("aws_iam_role.platform_admin declares a permissions_boundary -- it must declare none")

    for role_name, ext_var, body in (
        ("PlatformDev", "platform_dev_external_id", dev_body),
        ("PlatformAdmin", "platform_admin_external_id", admin_body),
    ):
        if body is None:
            continue
        if "sts:ExternalId" not in body or f"var.{ext_var}" not in body:
            out.append(f"{role_name}'s trust statement is missing its own sts:ExternalId condition on var.{ext_var}")
    return out


def _const(text: str, name: str) -> str | None:
    m = re.search(rf'{re.escape(name)}\s*=\s*"([^"]+)"', _strip_comments(text))
    return m.group(1) if m else None


def _var_default(text: str, name: str) -> str | None:
    from tests.checks.iam_tf._platform_security_hcl import _block_body

    m = re.search(rf'variable\s+"{name}"\s*\{{', _strip_comments(text))
    if m is None:
        return None
    body = _block_body(text, m.end() - 1)
    dm = re.search(r'default\s*=\s*"([^"]*)"', body)
    return dm.group(1) if dm else None


def _constant_parity_problems(joined: str, texts: dict[str, str]) -> list[str]:
    # Name parity (templates only, Decision 101): every bootstrap copy of a personal-root name
    # equals its source.
    personal_lambdas = (_PERSONAL / "ducklake_lambdas.tf").read_text(encoding="utf-8")
    personal_vars = (_PERSONAL / "variables.tf").read_text(encoding="utf-8")
    personal_inference = (_PERSONAL / "inference_credentials.tf").read_text(encoding="utf-8")
    personal_neon = (_PERSONAL / "neon_ducklake_catalog.tf").read_text(encoding="utf-8")

    checks = [
        ("agent-platform-data-lake" in joined, "bucket name agent-platform-data-lake"),
        ("agent-platform-counters" in joined, "counters table name agent-platform-counters"),
        (
            _const(joined, "ducklake_writer_function") == _const(personal_lambdas, "ducklake_writer_function"),
            "ducklake_writer_function local",
        ),
        (
            _const(joined, "ducklake_reader_function") == _const(personal_lambdas, "ducklake_reader_function"),
            "ducklake_reader_function local",
        ),
        (
            _const(joined, "ducklake_telemetry_writer_function")
            == _const(personal_lambdas, "ducklake_telemetry_writer_function"),
            "ducklake_telemetry_writer_function local",
        ),
        (
            _const(joined, "ducklake_telemetry_reader_function")
            == _const(personal_lambdas, "ducklake_telemetry_reader_function"),
            "ducklake_telemetry_reader_function local",
        ),
        (
            _const(joined, "ducklake_prod_data_prefix") == _const(personal_lambdas, "ducklake_prod_data_prefix"),
            "ducklake_prod_data_prefix local",
        ),
        (
            _const(joined, "ducklake_smoke_data_prefix") == _const(personal_lambdas, "ducklake_smoke_data_prefix"),
            "ducklake_smoke_data_prefix local",
        ),
        (
            "agent-platform-deepseek-api-key" in joined and "agent-platform-deepseek-api-key" in personal_inference,
            "deepseek secret name",
        ),
        (
            "agent-platform-anthropic-api-key" in joined and "agent-platform-anthropic-api-key" in personal_inference,
            "anthropic secret name",
        ),
        (
            "ducklake-neon-catalog-dsn" in joined and "ducklake-neon-catalog-dsn" in personal_neon,
            "neon catalog dsn secret name",
        ),
        (
            _var_default(joined, "agent_service_account_user_name")
            == _var_default(personal_vars, "agent_service_account_user_name"),
            "agent_service_account_user_name default",
        ),
    ]

    boundary_text = (_REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_boundary.tf").read_text(encoding="utf-8")
    checks.append(("agent-platform-github-ci-apply-boundary" in boundary_text, "boundary name"))

    adopted_secret_text = texts.get("platform_dev_policies.tf", "") + texts.get("platform_admin_data_policies.tf", "")
    secret_arn_lines = "\n".join(line for line in adopted_secret_text.splitlines() if "arn:aws:secretsmanager" in line)
    checks.append((re.search(r"-\*\"", secret_arn_lines) is None, "no wildcard-suffix (`-*`) secret ARN"))

    return [f"constant mismatch or drift: {label}" for ok, label in checks if not ok]


def problems(texts: dict[str, str]) -> list[str]:
    joined = _joined(texts)
    bodies, import_ids = _collect_bodies_and_imports(texts)
    return [
        *_import_problems(bodies, import_ids),
        *_protection_problems(bodies),
        *_size_precondition_problems(joined),
        *_external_id_variable_problems(joined),
        *_attachment_and_role_shape_problems(bodies),
        *_constant_parity_problems(joined, texts),
    ]


class TestBootstrapOwnsPlatformIam:
    def test_real_tree_is_clean(self) -> None:
        assert problems(_bootstrap_texts()) == []

    def test_dropped_import_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_roles.tf"] = re.sub(
            r'import\s*\{\s*id\s*=\s*"PlatformDev"\s*\n\s*to\s*=\s*aws_iam_role\.platform_dev\s*\}',
            "",
            texts["platform_roles.tf"],
            count=1,
        )
        assert texts["platform_roles.tf"] != _bootstrap_texts()["platform_roles.tf"]
        assert any("platform_dev" in p for p in problems(texts))

    def test_missing_prevent_destroy_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            "  lifecycle {\n    prevent_destroy = true\n  }\n}\n\n# PlatformAdmin",
            "}\n\n# PlatformAdmin",
            1,
        )
        assert texts["platform_roles.tf"] != _bootstrap_texts()["platform_roles.tf"]
        assert any("prevent_destroy" in p for p in problems(texts))

    def test_missing_iamfull_precondition_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        base = texts["platform_admin_policies.tf"]
        start = base.index("    precondition {\n      # Self-access guard")
        end = base.index("\n    }\n", start) + len("\n    }\n")
        texts["platform_admin_policies.tf"] = base[:start] + base[end:]
        assert texts["platform_admin_policies.tf"] != _bootstrap_texts()["platform_admin_policies.tf"]
        assert any("PlatformAdmin AdminOps is missing its self-access precondition" in p for p in problems(texts))

    def test_iamfull_allow_comparison_removed_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_admin_policies.tf"] = texts["platform_admin_policies.tf"].replace(
            'try(s.Sid, "") == "IAMFull" && s.Effect == "Allow" && !can(s.Condition)',
            'try(s.Sid, "") == "IAMFull" && !can(s.Condition)',
            1,
        )
        assert texts["platform_admin_policies.tf"] != _bootstrap_texts()["platform_admin_policies.tf"]
        assert any(
            "PlatformAdmin AdminOps" in p and "no longer matches the pinned parsed-grant condition" in p
            for p in problems(texts)
        )

    def test_iamfull_error_message_without_self_access_grant_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_admin_policies.tf"] = texts["platform_admin_policies.tf"].replace(
            'error_message = "PlatformAdmin AdminOps no longer has an unconditioned Allow of iam:* on * in the '
            'IAMFull statement -- this is the self-access grant that lets PlatformAdmin manage IAM at all."',
            'error_message = "PlatformAdmin AdminOps no longer has an unconditioned Allow of iam:* on * in the '
            'IAMFull statement."',
            1,
        )
        assert texts["platform_admin_policies.tf"] != _bootstrap_texts()["platform_admin_policies.tf"]
        assert any(
            "PlatformAdmin AdminOps" in p and "error_message is missing its resource name" in p for p in problems(texts)
        )

    def test_bootstrap_state_bucket_arn_corrupted_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_admin_data_policies.tf"] = texts["platform_admin_data_policies.tf"].replace(
            '"arn:aws:s3:::agent-platform-bootstrap-tfstate"   = ["s3:ListBucket"]',
            '"arn:aws:s3:::agent-platform-bootstrap-tfstate-x" = ["s3:ListBucket"]',
            1,
        )
        assert texts["platform_admin_data_policies.tf"] != _bootstrap_texts()["platform_admin_data_policies.tf"]
        assert any(
            "PlatformAdmin BootstrapStateProvisioning" in p and "no longer matches the pinned parsed-grant condition" in p
            for p in problems(texts)
        )

    def test_bootstrap_state_condition_clause_removed_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_admin_data_policies.tf"] = texts["platform_admin_data_policies.tf"].replace(
            's.Effect == "Allow" && !can(s.Condition)\n            && contains(flatten([try(s.Resource, [])]), arn)',
            's.Effect == "Allow"\n            && contains(flatten([try(s.Resource, [])]), arn)',
            1,
        )
        assert texts["platform_admin_data_policies.tf"] != _bootstrap_texts()["platform_admin_data_policies.tf"]
        assert any(
            "PlatformAdmin BootstrapStateProvisioning" in p and "no longer matches the pinned parsed-grant condition" in p
            for p in problems(texts)
        )

    def test_bootstrap_state_inner_alltrue_weakened_to_anytrue_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_admin_data_policies.tf"] = texts["platform_admin_data_policies.tf"].replace(
            "&& alltrue([for a in actions : contains(flatten([try(s.Action, [])]), a)])",
            "&& anytrue([for a in actions : contains(flatten([try(s.Action, [])]), a)])",
            1,
        )
        assert texts["platform_admin_data_policies.tf"] != _bootstrap_texts()["platform_admin_data_policies.tf"]
        assert any(
            "PlatformAdmin BootstrapStateProvisioning" in p and "no longer matches the pinned parsed-grant condition" in p
            for p in problems(texts)
        )

    def test_bootstrap_state_strcontains_form_restored_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        old_block = (
            "      condition = alltrue([\n"
            "        for arn, actions in {\n"
            '          "arn:aws:s3:::agent-platform-bootstrap-tfstate"   = ["s3:ListBucket"]\n'
            '          "arn:aws:s3:::agent-platform-bootstrap-tfstate/*" = ["s3:GetObject", "s3:PutObject", '
            '"s3:DeleteObject"]\n'
            "          } : anytrue([\n"
            "            for s in jsondecode(local.platform_admin_bootstrap_state_policy_json).Statement :\n"
            '            s.Effect == "Allow" && !can(s.Condition)\n'
            "            && contains(flatten([try(s.Resource, [])]), arn)\n"
            "            && alltrue([for a in actions : contains(flatten([try(s.Action, [])]), a)])\n"
            "        ])\n"
            "      ])"
        )
        strcontains_block = (
            "      condition = (\n"
            "        strcontains(local.platform_admin_bootstrap_state_policy_json, "
            '"arn:aws:s3:::agent-platform-bootstrap-tfstate\\"") &&\n'
            "        strcontains(local.platform_admin_bootstrap_state_policy_json, "
            '"arn:aws:s3:::agent-platform-bootstrap-tfstate/*\\"")\n'
            "      )"
        )
        base = texts["platform_admin_data_policies.tf"]
        assert old_block in base
        texts["platform_admin_data_policies.tf"] = base.replace(old_block, strcontains_block, 1)
        assert texts["platform_admin_data_policies.tf"] != _bootstrap_texts()["platform_admin_data_policies.tf"]
        assert any(
            "PlatformAdmin BootstrapStateProvisioning" in p
            and ("strcontains(" in p or "no longer matches the pinned parsed-grant condition" in p)
            for p in problems(texts)
        )

    def test_external_id_without_sensitive_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        base = texts["variables.tf"]
        m = re.search(r'variable\s+"platform_dev_external_id"\s*\{', base)
        from tests.checks.iam_tf._platform_security_hcl import _block_body

        body = _block_body(base, m.end() - 1)
        texts["variables.tf"] = base.replace(body, '\n  description = "x"\n  type        = string\n', 1)
        assert texts["variables.tf"] != _bootstrap_texts()["variables.tf"]
        assert any("is not sensitive" in p for p in problems(texts))

    def test_external_id_with_default_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        base = texts["variables.tf"]
        m = re.search(r'variable\s+"platform_admin_external_id"\s*\{', base)
        from tests.checks.iam_tf._platform_security_hcl import _block_body

        body = _block_body(base, m.end() - 1)
        new_body = '\n  description = "x"\n  type        = string\n  sensitive   = true\n  default     = "x"\n'
        texts["variables.tf"] = base.replace(body, new_body, 1)
        assert texts["variables.tf"] != _bootstrap_texts()["variables.tf"]
        assert any("carries a default" in p for p in problems(texts))

    def test_attachment_reverted_to_literal_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_security_admin_policy.tf"] = texts["platform_security_admin_policy.tf"].replace(
            "role       = aws_iam_role.platform_admin.name", 'role       = "PlatformAdmin"', 1
        )
        assert texts["platform_security_admin_policy.tf"] != _bootstrap_texts()["platform_security_admin_policy.tf"]
        assert any("attachment role is" in p for p in problems(texts))

    def test_literal_arn_boundary_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            "permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn",
            'permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"',
            1,
        )
        assert texts["platform_roles.tf"] != _bootstrap_texts()["platform_roles.tf"]
        assert any("first 3 lines" in p for p in problems(texts))

    def test_boundary_on_platformadmin_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            'resource "aws_iam_role" "platform_admin" {\n  name                 = "PlatformAdmin"',
            'resource "aws_iam_role" "platform_admin" {\n  name                 = "PlatformAdmin"\n'
            "  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn",
            1,
        )
        assert texts["platform_roles.tf"] != _bootstrap_texts()["platform_roles.tf"]
        assert any("declares a permissions_boundary" in p for p in problems(texts))

    def test_wildcard_suffix_secret_arn_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_dev_policies.tf"] = texts["platform_dev_policies.tf"].replace(
            "data.aws_secretsmanager_secret.deepseek_api_key.arn,",
            '"arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-deepseek-api-key-*",',
            1,
        )
        assert texts["platform_dev_policies.tf"] != _bootstrap_texts()["platform_dev_policies.tf"]
        assert any("wildcard-suffix" in p for p in problems(texts))

    def test_trust_statement_without_external_id_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            '"sts:ExternalId" = var.platform_dev_external_id', '"sts:ExternalId" = "hardcoded"', 1
        )
        assert texts["platform_roles.tf"] != _bootstrap_texts()["platform_roles.tf"]
        assert any("sts:ExternalId condition on var.platform_dev_external_id" in p for p in problems(texts))

    def test_drifted_constant_is_detected(self) -> None:
        texts = dict(_bootstrap_texts())
        texts["platform_dev_policies.tf"] = texts["platform_dev_policies.tf"].replace(
            'ducklake_prod_data_prefix  = "ducklake"', 'ducklake_prod_data_prefix  = "ducklake-drifted"', 1
        )
        assert texts["platform_dev_policies.tf"] != _bootstrap_texts()["platform_dev_policies.tf"]
        assert any("ducklake_prod_data_prefix" in p for p in problems(texts))
