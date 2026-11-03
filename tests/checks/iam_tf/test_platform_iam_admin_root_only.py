"""Standing guard for the one-state-per-applier rule (Decision 223, rec-3838,
PLAN-platform-iam-personal-release): terraform/personal declares no PlatformDev/PlatformAdmin
IAM.

terraform/personal must never declare an aws_iam_role named PlatformDev or PlatformAdmin, nor an
aws_iam_role_policy, aws_iam_role_policy_attachment, aws_iam_policy_attachment (its `roles = [...]`
list), aws_iam_role_policies_exclusive or aws_iam_role_policy_attachments_exclusive targeting
either -- by resource reference, data-source indirection or a local/variable whose literal value
names the role. The literal check is keyed ONLY on those resources' `role` / `roles` / `name` /
`name_prefix` / `role_name` arguments, so an unrelated mention elsewhere (a comment, such as
oidc.tf's note that the two roles are bootstrap-owned, or a log/event pattern such as a
Decision 202 metric filter) cannot false-positive. Each of the seven relocated addresses must carry exactly one
`removed` block with `lifecycle { destroy = false }` in terraform/personal.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONAL = _REPO_ROOT / "terraform" / "personal"

_BANNED_NAMES = ("PlatformDev", "PlatformAdmin")

_SEVEN_ADDRESSES = [
    "aws_iam_role.platform_dev",
    "aws_iam_role.platform_admin",
    "aws_iam_role_policy.platform_dev_runtime",
    "aws_iam_role_policy.platform_admin_ops",
    "aws_iam_role_policy.platform_admin_datalake",
    "aws_iam_role_policy.platform_admin_bootstrap_state",
    "aws_iam_role_policy.platform_admin_ducklake_breakglass",
]

_ROLE_REF_RE = re.compile(r"\baws_iam_role\.(platform_dev|platform_admin)\.(?:name|id|arn)\b")
_LOCAL_REF_RE = re.compile(r"\blocal\.(\w+)\b")
_VAR_REF_RE = re.compile(r"\bvar\.(\w+)\b")
_DATA_ROLE_REF_RE = re.compile(r"\bdata\.aws_iam_role\.(\w+)\.(?:name|id|arn)\b")


def _personal_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_PERSONAL.glob("*.tf"))}


def _joined(texts: dict[str, str]) -> str:
    return "\n".join(texts.values())


def _all_blocks(text: str) -> list[tuple[str, str, str, str]]:
    """Every (kind, type, name, body) of a resource/data block, comments ignored."""
    out: list[tuple[str, str, str, str]] = []
    for m in re.finditer(r'\b(resource|data)\s+"([\w-]+)"\s+"([\w-]+)"\s*\{', _strip_comments(text)):
        out.append((m.group(1), m.group(2), m.group(3), _block_body(text, m.end() - 1)))
    return out


def _collect_local_literals(joined: str) -> dict[str, str]:
    """{local name: literal string value} for every `locals { k = "v" ... }` string assignment."""
    out: dict[str, str] = {}
    for m in re.finditer(r"\blocals\s*\{", _mask(joined)):
        body = _block_body(joined, m.end() - 1)
        for em in re.finditer(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', _strip_comments(body), re.M):
            out[em.group(1)] = em.group(2)
    return out


def _collect_var_default_literals(joined: str) -> dict[str, str]:
    """{var name: literal default} for every `variable "x" { default = "v" }`."""
    out: dict[str, str] = {}
    for m in re.finditer(r'\bvariable\s+"(\w+)"\s*\{', _strip_comments(joined)):
        body = _block_body(joined, m.end() - 1)
        dm = re.search(r'^\s*default\s*=\s*"([^"]*)"\s*$', _strip_comments(body), re.M)
        if dm is not None:
            out[m.group(1)] = dm.group(1)
    return out


def _collect_data_role_names(joined: str) -> dict[str, str]:
    """{data.aws_iam_role name: literal `name = "..."`} for every aws_iam_role data block."""
    out: dict[str, str] = {}
    for kind, rtype, rname, body in _all_blocks(joined):
        if kind == "data" and rtype == "aws_iam_role":
            nm = re.search(r'^\s*name\s*=\s*"([^"]*)"\s*$', _strip_comments(body), re.M)
            if nm is not None:
                out[rname] = nm.group(1)
    return out


def _resolve(
    expr: str,
    local_literals: dict[str, str],
    var_literals: dict[str, str],
    data_role_names: dict[str, str],
    _depth: int = 0,
) -> str | None:
    """Resolve `expr` (a raw attribute RHS) to a literal role name, or None if it cannot be
    resolved to one of the banned literal names by any of the recognised indirections."""
    if _depth > 4:
        return None
    expr = expr.strip()
    interp = re.fullmatch(r'"\$\{\s*([^{}]+?)\s*\}"', expr)
    if interp is not None:
        return _resolve(interp.group(1), local_literals, var_literals, data_role_names, _depth + 1)
    lit = re.fullmatch(r'"([^"]*)"', expr)
    if lit is not None:
        return lit.group(1)
    rm = _ROLE_REF_RE.fullmatch(expr)
    if rm is not None:
        return "PlatformDev" if rm.group(1) == "platform_dev" else "PlatformAdmin"
    dm = _DATA_ROLE_REF_RE.fullmatch(expr)
    if dm is not None:
        return data_role_names.get(dm.group(1))
    lm = _LOCAL_REF_RE.fullmatch(expr)
    if lm is not None and lm.group(1) in local_literals:
        return _resolve(f'"{local_literals[lm.group(1)]}"', local_literals, var_literals, data_role_names, _depth + 1)
    vm = _VAR_REF_RE.fullmatch(expr)
    if vm is not None and vm.group(1) in var_literals:
        return _resolve(f'"{var_literals[vm.group(1)]}"', local_literals, var_literals, data_role_names, _depth + 1)
    return None


def _attr(body: str, name: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", _strip_comments(body), re.M)
    return None if m is None else m.group(1)


def _list_elements(raw: str) -> list[str]:
    """Top-level comma-separated elements of a `[...]` list literal; [] if not a bracketed list."""
    m = re.fullmatch(r"\[(.*)\]", raw, re.S)
    if m is None:
        return []
    inner = m.group(1)
    depth = 0
    elems: list[str] = []
    cur = ""
    for ch in inner:
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        if ch == "," and depth == 0:
            elems.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        elems.append(cur)
    return [e.strip() for e in elems]


def _removed_blocks(joined: str) -> dict[str, list[bool | None]]:
    """{address: [destroy_flag_or_None, ...]} for every `removed { from = <addr> ... }` block."""
    out: dict[str, list[bool | None]] = {}
    for m in re.finditer(r"\bremoved\s*\{", _mask(joined)):
        body = _block_body(joined, m.end() - 1)
        fm = re.search(r"^\s*from\s*=\s*([\w.]+)\s*$", _strip_comments(body), re.M)
        if fm is None:
            continue
        destroy: bool | None = None
        lcm = re.search(r"\blifecycle\s*\{", _mask(body))
        if lcm is not None:
            lc_body = _block_body(body, lcm.end() - 1)
            dm = re.search(r"\bdestroy\s*=\s*(true|false)\b", _strip_comments(lc_body))
            if dm is not None:
                destroy = dm.group(1) == "true"
        out.setdefault(fm.group(1), []).append(destroy)
    return out


def _removed_block_problems(joined: str) -> list[str]:
    out: list[str] = []
    blocks = _removed_blocks(joined)
    for addr in _SEVEN_ADDRESSES:
        flags = blocks.get(addr)
        if not flags:
            out.append(f"{addr} has no removed block in terraform/personal")
            continue
        if len(flags) > 1:
            out.append(f"{addr} has {len(flags)} removed blocks, expected exactly 1")
        if any(f is not False for f in flags):
            out.append(f"{addr}'s removed block does not declare lifecycle destroy = false")
    return out


def _declaration_problems(
    joined: str,
    local_literals: dict[str, str],
    var_literals: dict[str, str],
    data_role_names: dict[str, str],
) -> list[str]:
    out: list[str] = []
    resolve = lambda e: _resolve(e, local_literals, var_literals, data_role_names)  # noqa: E731

    for kind, rtype, rname, body in _all_blocks(joined):
        if kind != "resource":
            continue
        full_addr = f"{rtype}.{rname}"

        if rtype == "aws_iam_role":
            for attr_name in ("name", "name_prefix"):
                raw = _attr(body, attr_name)
                if raw is None:
                    continue
                resolved = resolve(raw)
                if resolved in _BANNED_NAMES:
                    out.append(f"{full_addr} declares {attr_name} resolving to {resolved!r}")

        elif rtype in ("aws_iam_role_policy", "aws_iam_role_policy_attachment"):
            raw = _attr(body, "role")
            if raw is None:
                continue
            resolved = resolve(raw)
            if resolved in _BANNED_NAMES:
                out.append(f"{full_addr}'s role argument resolves to {resolved!r}")

        elif rtype == "aws_iam_policy_attachment":
            raw = _attr(body, "roles")
            if raw is None:
                continue
            for elem in _list_elements(raw):
                resolved = resolve(elem)
                if resolved in _BANNED_NAMES:
                    out.append(f"{full_addr}'s roles list names {resolved!r}")

        elif rtype in ("aws_iam_role_policies_exclusive", "aws_iam_role_policy_attachments_exclusive"):
            raw = _attr(body, "role_name")
            resolved = resolve(raw) if raw is not None else None
            # Any use of these `*_exclusive` resource types against either identity is banned;
            # when role_name cannot be resolved to a literal, still flag it as unauditable scope
            # against a managed-boundary-carrying root only if it plainly names a banned literal
            # somewhere in its own body (never a scan of the whole file).
            if resolved in _BANNED_NAMES or any(name in body for name in _BANNED_NAMES):
                out.append(f"{full_addr} ({rtype}) targets a banned identity")

    return out


def problems(texts: dict[str, str]) -> list[str]:
    joined = _joined(texts)
    local_literals = _collect_local_literals(joined)
    var_literals = _collect_var_default_literals(joined)
    data_role_names = _collect_data_role_names(joined)
    return [
        *_declaration_problems(joined, local_literals, var_literals, data_role_names),
        *_removed_block_problems(joined),
    ]


class TestPlatformIamAdminRootOnly:
    def test_real_tree_is_clean(self) -> None:
        assert problems(_personal_texts()) == []

    def test_readded_role_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += '\nresource "aws_iam_role" "sneaky" {\n  name = "PlatformDev"\n}\n'
        assert any("PlatformDev" in p for p in problems(texts))

    def test_inline_policy_attached_by_literal_role_name_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_role_policy" "sneaky" {\n  name   = "Sneaky"\n'
            '  role   = "PlatformAdmin"\n  policy = "{}"\n}\n'
        )
        assert any("PlatformAdmin" in p for p in problems(texts))

    def test_managed_policy_attachment_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_role_policy_attachment" "sneaky" {\n'
            '  role       = "PlatformDev"\n  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"\n}\n'
        )
        assert any("PlatformDev" in p for p in problems(texts))

    def test_policy_attachment_roles_list_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_policy_attachment" "sneaky" {\n  name       = "sneaky"\n'
            '  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"\n'
            '  roles      = ["SomeOtherRole", "PlatformAdmin"]\n}\n'
        )
        assert any("PlatformAdmin" in p for p in problems(texts))

    def test_exclusive_resource_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_role_policies_exclusive" "sneaky" {\n  role_name    = "PlatformDev"\n  policy_names = []\n}\n'
        )
        assert any("aws_iam_role_policies_exclusive" in p for p in problems(texts))

    def test_attachments_exclusive_resource_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_role_policy_attachments_exclusive" "sneaky" {\n'
            '  role_name   = "PlatformAdmin"\n  policy_arns = []\n}\n'
        )
        assert any("aws_iam_role_policy_attachments_exclusive" in p for p in problems(texts))

    def test_removed_block_missing_destroy_false_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            "removed {\n  from = aws_iam_role.platform_dev\n\n  lifecycle {\n    destroy = false\n  }\n}",
            "removed {\n  from = aws_iam_role.platform_dev\n}",
            1,
        )
        assert texts["platform_roles.tf"] != _personal_texts()["platform_roles.tf"]
        assert any(
            "aws_iam_role.platform_dev's removed block does not declare lifecycle destroy = false" in p
            for p in problems(texts)
        )

    def test_missing_removed_block_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] = texts["platform_roles.tf"].replace(
            "removed {\n  from = aws_iam_role.platform_admin\n\n  lifecycle {\n    destroy = false\n  }\n}\n\n",
            "",
            1,
        )
        assert texts["platform_roles.tf"] != _personal_texts()["platform_roles.tf"]
        assert any("aws_iam_role.platform_admin has no removed block" in p for p in problems(texts))

    def test_string_interpolated_self_reference_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_iam_role_policy" "sneaky6" {\n  name   = "Sneaky"\n'
            '  role   = "${aws_iam_role.platform_admin.name}"\n  policy = "{}"\n}\n'
        )
        assert any("PlatformAdmin" in p for p in problems(texts))

    def test_data_source_indirection_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\ndata "aws_iam_role" "sneaky" {\n  name = "PlatformDev"\n}\n'
            '\nresource "aws_iam_role_policy" "sneaky2" {\n  name   = "Sneaky"\n'
            '  role   = data.aws_iam_role.sneaky.name\n  policy = "{}"\n}\n'
        )
        assert any("PlatformDev" in p for p in problems(texts))

    def test_local_indirection_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nlocals {\n  sneaky_role = "PlatformAdmin"\n}\n'
            '\nresource "aws_iam_role_policy" "sneaky3" {\n  name   = "Sneaky"\n'
            '  role   = local.sneaky_role\n  policy = "{}"\n}\n'
        )
        assert any("PlatformAdmin" in p for p in problems(texts))

    def test_variable_indirection_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nvariable "sneaky_role" {\n  type    = string\n  default = "PlatformDev"\n}\n'
            '\nresource "aws_iam_role_policy" "sneaky4" {\n  name   = "Sneaky"\n'
            '  role   = var.sneaky_role\n  policy = "{}"\n}\n'
        )
        assert any("PlatformDev" in p for p in problems(texts))

    def test_role_name_via_local_is_detected(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nlocals {\n  sneaky_name = "PlatformAdmin"\n}\n'
            '\nresource "aws_iam_role" "sneaky5" {\n  name = local.sneaky_name\n}\n'
        )
        assert any("PlatformAdmin" in p for p in problems(texts))

    def test_metric_filter_pattern_naming_platformdev_is_not_flagged(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_cloudwatch_log_metric_filter" "sneaky_but_fine" {\n'
            '  name           = "example"\n'
            '  log_group_name = "example"\n'
            '  pattern        = "{ $.userIdentity.sessionContext.sessionIssuer.userName = \\"PlatformDev\\" }"\n'
            "  metric_transformation {\n"
            '    name      = "Example"\n'
            '    namespace = "Example"\n'
            '    value     = "1"\n'
            "  }\n}\n"
        )
        assert problems(texts) == []

    def test_event_pattern_naming_platformadmin_is_not_flagged(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += (
            '\nresource "aws_cloudwatch_event_rule" "sneaky_but_fine" {\n'
            '  name          = "example"\n'
            '  event_pattern = "{ \\"detail\\": { \\"userIdentity\\": { \\"userName\\": [\\"PlatformAdmin\\"] } } }"\n'
            "}\n"
        )
        assert problems(texts) == []

    def test_comment_naming_platformdev_is_not_flagged(self) -> None:
        texts = dict(_personal_texts())
        texts["platform_roles.tf"] += "\n# a stray comment mentioning PlatformDev changes nothing\n"
        assert problems(texts) == []
