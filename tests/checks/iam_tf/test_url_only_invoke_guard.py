"""Decision 213 standing guard: an identity-trusting DuckLake Lambda is reachable only through its Function URL.

Each predicate returns a problem list so its red case drives the real predicate on synthetic input.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

from scripts.checks.iam_tf._read_coverage import _extract_bracket_block, _split_top_level_objects
from src.lambdas.ducklake_maintenance.catalog_logins import LOGINS, render_login_sql
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _resource_body, _strip_comments
from tests.fixtures.terraform_hcl_blocks import tf_dir_file_texts

_ROOT = Path(__file__).resolve().parents[3]
_PERSONAL = _ROOT / "terraform" / "personal"
_BOOTSTRAP = _ROOT / "terraform" / "bootstrap"
_SCHEMAS = frozenset({"ducklake_ops", "public", "ducklake_smoke", "ducklake_telemetry"})
_ARN = "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-"
_FIVE = ("writer", "reader", "telemetry-writer", "telemetry-writer-smoke", "telemetry-reader")
_BASES = {f"{_ARN}{fn}" for fn in _FIVE}
_DEV_BASES = {f"{_ARN}{fn}" for fn in ("writer", "reader", "telemetry-writer", "telemetry-reader")}
_DEV_FORMS = _DEV_BASES | {f"{b}:*" for b in _DEV_BASES}
_EXACT = {f"{_ARN}{fn}{suffix}" for fn in ("writer", "reader") for suffix in ("", ":*")}
_FAMILY = f"{_ARN}telemetry-*"
_BOUNDARY_FORMS = _EXACT | {_FAMILY}
_VIA_URL = "lambda:InvokedViaFunctionUrl"
_NAMES = "writer|reader|telemetry-writer-smoke|telemetry-writer|telemetry-reader"
_FORMS = (
    re.compile(rf"aws_lambda_function\.ducklake_({_NAMES.replace('-', '_')})\.arn\}}?(?::\*)?$"),
    re.compile(rf"function:agent-platform-ducklake-({_NAMES})(?::\*)?$"),
)


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


def _function_locals(texts: dict[str, str]) -> dict[str, str]:
    pattern = re.compile(rf'^\s*(\w+)\s*=\s*"agent-platform-ducklake-({_NAMES})"\s*$', re.M)
    return {m.group(1): m.group(2) for text in texts.values() for m in pattern.finditer(_strip_comments(text))}


def _named_forms(raw: str, fn_locals: dict[str, str]) -> set[tuple[str, bool]]:
    forms: set[tuple[str, bool]] = set()
    for token in re.findall(r'"(?:[^"\\]|\\.)*"|[^\s,\[\]]+', raw):
        token = token.strip('"')
        forms |= {(m.group(1).replace("_", "-"), token.endswith(":*")) for m in (p.search(token) for p in _FORMS) if m}
        forms |= {(fn, token.endswith(":*")) for name, fn in fn_locals.items() if re.search(rf"\blocal\.{name}\b", token)}
    return forms


def _covers(actions: list[str], verb: str) -> bool:
    return any(fnmatch.fnmatch(verb.lower(), a.lower()) for a in actions)


def grant_problems(stmts: list[dict], fn_locals: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for s in stmts:
        if s["effect"] != "Allow" or not _named_forms(s["resources"], fn_locals):
            continue
        label = s["sid"] or "<anonymous>"
        if _covers(s["actions"], "lambda:InvokeFunction") and ("Bool", _VIA_URL, ["true"]) not in s["conditions"]:
            problems.append(f"{label}: lambda:InvokeFunction lacks Bool {_VIA_URL} = true")
        if _covers(s["actions"], "lambda:InvokeFunctionUrl") and s["conditions"]:
            problems.append(f"{label}: lambda:InvokeFunctionUrl must sit in an unconditioned statement")
    return problems


def boundary_problems(text: str) -> list[str]:
    by_sid = {s["sid"]: s for s in _json_statements(text)}
    deny = {
        "DenyDuckLakeDirectInvoke": (
            {"lambda:InvokeFunction", "lambda:InvokeAsync"},
            _BOUNDARY_FORMS,
            [("BoolIfExists", _VIA_URL, ["false"])],
        ),
        "DenyDuckLakeResourcePolicyEdit": (
            {"lambda:AddPermission", "lambda:RemovePermission", "lambda:PutResourcePolicy", "lambda:DeleteResourcePolicy"},
            _BOUNDARY_FORMS,
            [],
        ),
        "DenyDuckLakeEventSourceMapping": (
            {"lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping"},
            {"*"},
            [("ArnLike", "lambda:FunctionArn", sorted(_BOUNDARY_FORMS))],
        ),
    }
    problems: list[str] = []
    for sid, expected in deny.items():
        s = by_sid.get(sid)
        if s is None or s["effect"] != "Deny":
            problems.append(f"{sid}: Deny statement missing")
        elif (set(s["actions"]), set(_list(s["resources"])), [(o, k, sorted(v)) for o, k, v in s["conditions"]]) != expected:
            problems.append(f"{sid}: actions, resources or condition differ from Decision 213 cl.1")
    return problems


def _sid_patterns(stmt: dict) -> list[str]:
    if stmt["sid"] != "DenyDuckLakeEventSourceMapping":
        return _list(stmt["resources"])
    return [v for _, key, values in stmt["conditions"] if key == "lambda:FunctionArn" for v in values]


def coverage_problems(text: str) -> list[str]:
    by_sid = {s["sid"]: s for s in _json_statements(text)}
    problems: list[str] = []
    for sid in ("DenyDuckLakeDirectInvoke", "DenyDuckLakeResourcePolicyEdit", "DenyDuckLakeEventSourceMapping"):
        patterns = _sid_patterns(by_sid[sid]) if sid in by_sid else []
        for fn in _FIVE:
            for suffix in ("", ":*"):
                if not any(fnmatch.fnmatchcase(f"{_ARN}{fn}{suffix}", pattern) for pattern in patterns):
                    problems.append(f"{sid}: {fn}{suffix} matches no resource")
    return problems


def trail_selector_problems(trail_text: str) -> list[str]:
    body = _resource_body(trail_text, "aws_cloudtrail", "platform_security") or ""
    blocks = [_block_body(body, m.end() - 1) for m in re.finditer(r"\bevent_selector\s*\{", _mask(body))]
    all_events = [b for b in blocks if re.search(r'read_write_type\s*=\s*"All"', b)]
    data = [b for b in all_events if "data_resource" in _mask(b) and re.search(r"include_management_events\s*=\s*false", b)]
    mgmt = [b for b in all_events if "data_resource" not in _mask(b) and re.search(r"include_management_events\s*=\s*true", b)]
    data_ok = any(
        re.search(r'\btype\s*=\s*"AWS::Lambda::Function"', b) and set(_list(_field(b, "values"))) == _BASES for b in data
    )
    return (["no Lambda data-event selector for exactly the five functions"] if not data_ok else []) + (
        ["the All management-event selector is missing"] if not mgmt else []
    )


def sql_problems(sql: str, schema: str, owner_role: str | None = None) -> list[str]:
    body = "\n".join(line.split("--")[0] for line in sql.splitlines())
    foreign = "|".join(sorted(_SCHEMAS - {schema}))
    problems = [
        f"role SQL matches {pattern!r}"
        for pattern in (
            r"(?i)GRANT\s[^;]*CREATE",
            r"(?i)GRANT\s+ALL\b",
            rf"(?i)\b(?:ON|IN)\s+SCHEMA\s+(?:{foreign})\b",
            r"(?i)\bPASSWORD\b",
        )
        if re.search(pattern, body)
    ]
    if owner_role is None and not re.search(rf"(?i)\bON\s+SCHEMA\s+{schema}\b", body):
        problems.append(f"role SQL grants nothing on {schema}")
    if owner_role is not None:
        grants = re.findall(r"(?im)^GRANT\s+(?!CONNECT\b)(\w+)\s+TO\s+\w+", body)
        if grants != [owner_role]:
            problems.append(f"role SQL grants {grants}, expected exactly the owner role {owner_role}")
        if re.search(r"(?i)\bON\s+SCHEMA\b", body):
            problems.append("role SQL grants on a schema directly instead of through the owner role")
    if not re.search(r"(?i)CONNECTION\s+LIMIT\s+\d+", body):
        problems.append("role SQL sets no CONNECTION LIMIT")
    return problems


def _sid_resources(text: str, sid: str) -> str:
    return next((s["resources"] for s in _json_statements(text) if s["sid"] == sid), "")


def _dev_problems(text: str, fn_locals: dict[str, str]) -> list[str]:
    return [
        f"{sid} does not name all eight function ARN forms"
        for sid in ("DuckLakeInvokeUrl", "DuckLakeInvokeViaUrlOnly")
        if len(_named_forms(_sid_resources(text, sid), fn_locals)) != 8
    ]


def test_real_boundary_carries_the_three_denies() -> None:
    assert boundary_problems(_bootstrap("github_ci_apply_boundary.tf")) == []


def test_real_boundary_covers_every_telemetry_function() -> None:
    assert coverage_problems(_bootstrap("github_ci_apply_boundary.tf")) == []


def test_real_identity_grants_are_url_only() -> None:
    # Concatenate per-root statement lists -- tf_dir_file_texts keys by bare filename, so a dict
    # merge of the two roots' texts would let the bootstrap platform_dev_policies.tf silently hide
    # the personal one (or vice versa). The guarded-Sid superset assertion holds over the union, so
    # this stays green after the sibling plan releases the personal copy.
    personal_texts, bootstrap_texts = tf_dir_file_texts(_PERSONAL), tf_dir_file_texts(_BOOTSTRAP)
    stmts = [
        s
        for texts in (personal_texts, bootstrap_texts)
        for text in texts.values()
        for s in _json_statements(text) + _block_statements(text)
    ]
    fn_locals = {**_function_locals(personal_texts), **_function_locals(bootstrap_texts)}
    assert grant_problems(stmts, fn_locals) == []
    guarded = {
        s["sid"] for s in stmts if _named_forms(s["resources"], fn_locals) and ("Bool", _VIA_URL, ["true"]) in s["conditions"]
    }
    assert {"DuckLakeInvokeViaUrlOnly", "DuckLakeInvokeCIViaUrlOnly", "DuckLakeWriterInvokeViaUrlOnly"} <= guarded


def test_real_trail_logs_lambda_data_events() -> None:
    assert trail_selector_problems(_bootstrap("platform_security_trail.tf")) == []


def test_real_role_sql_is_scoped() -> None:
    assert {login.kind for login in LOGINS.values()} == {"writer", "reader", "maintainer"}
    for name, login in LOGINS.items():
        owner = login.owner_role if login.kind == "maintainer" else None
        assert sql_problems(render_login_sql(name), login.schema, owner) == [], name


def test_real_bootstrap_reads_list_the_telemetry_writer_role() -> None:
    reads = _sid_resources(_bootstrap("github_ci_apply_reads.tf"), "IAMRolesRead")
    assert "role/agent-platform-ducklake-telemetry-writer" in reads


def test_real_platform_dev_names_all_six_forms() -> None:
    # terraform/bootstrap is the durable owner (PLAN-platform-iam-bootstrap-adopt); the parity
    # module covers the personal copy while both roots declare it.
    texts = tf_dir_file_texts(_BOOTSTRAP)
    assert _dev_problems(texts["platform_dev_policies.tf"], _function_locals(texts)) == []


def test_bootstrap_dailyops_without_condition_is_reported() -> None:
    bootstrap_texts = tf_dir_file_texts(_BOOTSTRAP)
    mutated = dict(bootstrap_texts)
    mutated["platform_dev_policies.tf"] = mutated["platform_dev_policies.tf"].replace(
        """        Condition = {
          Bool = {
            "lambda:InvokedViaFunctionUrl" = "true"
          }
        }
""",
        "",
        1,
    )
    assert mutated["platform_dev_policies.tf"] != bootstrap_texts["platform_dev_policies.tf"]
    stmts = [s for text in mutated.values() for s in _json_statements(text) + _block_statements(text)]
    assert grant_problems(stmts, _function_locals(mutated)) != []


def test_real_maintenance_role_reads_the_scoped_secret() -> None:
    raw = _sid_resources(tf_dir_file_texts(_PERSONAL)["ducklake_maintenance.tf"], "NeonDsnRead")
    assert "aws_secretsmanager_secret.ducklake_telemetry_writer_dsn.arn" in raw


_BOUNDARY_TMPL = """
locals { p = jsonencode({ Statement = [
  { Sid = "DenyDuckLakeDirectInvoke", Effect = "Deny", Action = ["lambda:InvokeFunction", "lambda:InvokeAsync"],
    Resource = [%(six)s], Condition = { BoolIfExists = { "lambda:InvokedViaFunctionUrl" = "false" } } },
  { Sid = "DenyDuckLakeResourcePolicyEdit", Effect = "Deny", Action = ["lambda:AddPermission", "lambda:RemovePermission",
    "lambda:PutResourcePolicy", "lambda:DeleteResourcePolicy"],
    Resource = [%(six)s] },
  { Sid = "DenyDuckLakeEventSourceMapping", Effect = "Deny",
    Action = ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping"], Resource = "*",
    Condition = { ArnLike = { "lambda:FunctionArn" = [%(six)s] } } },
] }) }
"""


def _boundary(forms: set[str]) -> str:
    return _BOUNDARY_TMPL % {"six": ", ".join(f'"{a}"' for a in sorted(forms))}


_GOOD_BOUNDARY = _boundary(_BOUNDARY_FORMS)


def _forms_without(name: str) -> set[str]:
    if name in ("writer", "reader"):
        return {f for f in _BOUNDARY_FORMS if not f.startswith(f"{_ARN}{name}")}
    others = [fn for fn in _FIVE if fn.startswith("telemetry-") and fn != name]
    return (_BOUNDARY_FORMS - {_FAMILY}) | {f"{_ARN}{fn}{s}" for fn in others for s in ("", ":*")}


def test_coverage_predicate_is_green_on_the_synthetic_boundary_and_red_on_none() -> None:
    assert coverage_problems(_GOOD_BOUNDARY) == []
    assert len(coverage_problems("locals { p = jsonencode({ Statement = [] }) }")) == 30


@pytest.mark.parametrize("name", _FIVE)
def test_coverage_predicate_is_red_for_each_dropped_function(name: str) -> None:
    problems = coverage_problems(_boundary(_forms_without(name)))
    assert len(problems) == 6, problems
    assert all(f": {name}" in p for p in problems), problems


def test_boundary_predicate_is_green_on_a_complete_synthetic_boundary_and_red_on_none() -> None:
    assert boundary_problems(_GOOD_BOUNDARY) == []
    assert len(boundary_problems("locals { p = jsonencode({ Statement = [] }) }")) == 3


@pytest.mark.parametrize(
    "old,new",
    [
        ('"lambda:InvokedViaFunctionUrl" = "false"', '"lambda:InvokedViaFunctionUrl" = "true"'),
        ("BoolIfExists", "Bool"),
        ('"lambda:InvokeAsync"', '"lambda:GetFunction"'),
        ('"lambda:RemovePermission"', '"lambda:GetPolicy"'),
        ('"lambda:PutResourcePolicy"', '"lambda:GetResourcePolicy"'),
        ('"lambda:DeleteResourcePolicy"', '"lambda:GetResourcePolicy"'),
        ('"lambda:UpdateEventSourceMapping"', '"lambda:ListTags"'),
        ('Resource = "*"', 'Resource = "x"'),
        (f'"{_FAMILY}"', '"x"'),
        (f'"{_ARN}reader:*"', '"x"'),
        (f'"{_ARN}writer"', f'"{_ARN}telemetry-writer"'),
        ('"lambda:FunctionArn" = [', '"lambda:EventSourceArn" = ['),
        ('Effect = "Deny"', 'Effect = "Allow"'),
    ],
)
def test_boundary_predicate_is_red_on_each_mutation(old: str, new: str) -> None:
    assert old in _GOOD_BOUNDARY
    assert boundary_problems(_GOOD_BOUNDARY.replace(old, new, 1))


_BLOCK = 'statement {\n sid = "S"\n effect = "Allow"\n actions = [%s]\n resources = [%s]\n%s}\n'
_COND = ' condition {\n test = "Bool"\n variable = "lambda:InvokedViaFunctionUrl"\n values = ["true"]\n }\n'
_FN = _function_locals({"a.tf": 'locals {\n tel = "agent-platform-ducklake-telemetry-writer"\n}'})


def _block_problems(actions: str, resource: str, cond: str = "", fn_locals: dict[str, str] | None = None) -> list[str]:
    return grant_problems(_block_statements(_BLOCK % (actions, resource, cond)), fn_locals or {})


@pytest.mark.parametrize(
    "resource",
    [
        "aws_lambda_function.ducklake_writer.arn",
        '"${aws_lambda_function.ducklake_reader.arn}:*"',
        f'"{_ARN}telemetry-writer"',
        f'"{_ARN}writer:*"',
        '"arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.tel}"',
    ],
)
def test_grant_predicate_covers_each_reference_form(resource: str) -> None:
    assert _block_problems('"lambda:InvokeFunction"', resource, fn_locals=_FN), resource
    assert _block_problems('"lambda:InvokeFunction"', resource, _COND, _FN) == []


def test_grant_predicate_flags_wildcard_actions_a_conditioned_url_grant_and_json_statements() -> None:
    res = "aws_lambda_function.ducklake_writer.arn"
    assert _block_problems('"lambda:*"', res) and _block_problems('"lambda:Invoke*"', res)
    assert _block_problems('"lambda:InvokeFunctionUrl"', res, _COND)
    assert _block_problems('"lambda:InvokeFunctionUrl"', res) == []
    assert _block_problems('"lambda:InvokeFunction"', '"*"') == []
    plain = (
        'x = jsonencode({ Statement = [{ Sid = "J", Effect = "Allow", Action = ["lambda:InvokeFunction"], '
        "Resource = [%s] }] })"
    )
    cond = 'Condition = { Bool = { "lambda:InvokedViaFunctionUrl" = "true" } }'
    assert grant_problems(_json_statements(plain % res), {})
    assert grant_problems(_json_statements((plain % res).replace("] }] })", f"], {cond} }}] }})")), {}) == []


@pytest.mark.parametrize("name", ["telemetry-writer", "telemetry-reader"])
def test_platform_dev_predicate_is_red_when_a_telemetry_writer_form_is_omitted(name: str) -> None:
    def stmt(sid: str, forms: list[str]) -> str:
        return f'{{ Sid = "{sid}", Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [{", ".join(forms)}] }}'

    full = [f'"{a}"' for a in sorted(_DEV_FORMS)]
    ok = f"Statement = [{stmt('DuckLakeInvokeUrl', full)}, {stmt('DuckLakeInvokeViaUrlOnly', full)}]"
    assert _dev_problems(ok, {}) == []
    short = [f for f in full if not f.endswith(f'{name}:*"')]
    assert len(short) == 7
    red = ok.replace(", ".join(full), ", ".join(short), 1)
    assert _dev_problems(red, {}) == ["DuckLakeInvokeUrl does not name all eight function ARN forms"]
    assert len(_dev_problems("Statement = []", {})) == 2


_TRAIL = """
resource "aws_cloudtrail" "platform_security" {
  event_selector {
    read_write_type = "All"
    include_management_events = true
  }
  event_selector {
    read_write_type = "All"
    include_management_events = false
    data_resource {
      type = "AWS::Lambda::Function"
      values = [%s]
    }
  }
}
""" % ", ".join(f'"{b}"' for b in sorted(_BASES))


@pytest.mark.parametrize(
    "old,new",
    [
        ('type = "AWS::Lambda::Function"', 'type = "AWS::S3::Object"'),
        (f'"{_ARN}telemetry-writer"', f'"{_ARN}telemetry-writer:*"'),
        (f'"{_ARN}telemetry-writer-smoke"', '"x"'),
        (f'"{_ARN}telemetry-reader"', '"x"'),
        ("include_management_events = false", "include_management_events = true"),
        ("include_management_events = true", "include_management_events = false"),
    ],
)
def test_trail_predicate_is_red_on_each_mutation(old: str, new: str) -> None:
    assert trail_selector_problems(_TRAIL) == []
    assert old in _TRAIL
    assert trail_selector_problems(_TRAIL.replace(old, new, 1))


_SMOKE = "GRANT USAGE ON SCHEMA ducklake_smoke TO r;"
_SQL_OK = f"""-- header mentions CREATE and GRANT ALL in a comment only
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'r') THEN CREATE ROLE r; END IF; END $$;
ALTER ROLE r LOGIN NOINHERIT NOCREATEDB NOCREATEROLE CONNECTION LIMIT 10;
{_SMOKE}
ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA ducklake_smoke GRANT SELECT ON TABLES TO r;
"""


@pytest.mark.parametrize(
    "old,new",
    [
        (_SMOKE, "GRANT USAGE, CREATE ON SCHEMA ducklake_smoke TO r;"),
        (_SMOKE, f"{_SMOKE}\nGRANT USAGE ON SCHEMA ducklake_ops TO r;"),
        (_SMOKE, f"{_SMOKE}\nGRANT SELECT ON ALL TABLES IN SCHEMA public TO r;"),
        (_SMOKE, "GRANT ALL ON DATABASE d TO r;"),
        (_SMOKE, "GRANT USAGE ON SCHEMA other TO r;"),
        ("NOCREATEROLE CONNECTION LIMIT 10", "NOCREATEROLE"),
        ("NOCREATEROLE CONNECTION LIMIT 10", "NOCREATEROLE CONNECTION LIMIT 10 PASSWORD 'x'"),
    ],
)
def test_sql_predicate_is_red_on_each_mutation(old: str, new: str) -> None:
    assert sql_problems(_SQL_OK, "ducklake_smoke") == [], (
        "CREATE ROLE, NOCREATEDB, FOR ROLE ducklake_ops and comments must not false-match"
    )
    assert old in _SQL_OK
    assert sql_problems(_SQL_OK.replace(old, new, 1), "ducklake_smoke")


def test_sql_predicate_checks_each_login_against_its_own_schema() -> None:
    telemetry = _SQL_OK.replace("ducklake_smoke", "ducklake_telemetry")
    assert sql_problems(telemetry, "ducklake_telemetry") == []
    assert sql_problems(telemetry, "ducklake_smoke")
    assert sql_problems(f"{telemetry}\nGRANT USAGE ON SCHEMA ducklake_smoke TO r;", "ducklake_telemetry")


_MAINTAINER = (
    "GRANT CONNECT ON DATABASE d TO r;\n"
    "GRANT ducklake_smoke_owner TO r WITH INHERIT TRUE;\n"
    "ALTER ROLE r LOGIN CONNECTION LIMIT 8;"
)


@pytest.mark.parametrize(
    "old,new",
    [
        ("GRANT ducklake_smoke_owner TO r", "GRANT ducklake_ops TO r"),
        ("GRANT ducklake_smoke_owner TO r WITH INHERIT TRUE;", ""),
        (
            "GRANT ducklake_smoke_owner TO r WITH INHERIT TRUE;",
            "GRANT ducklake_smoke_owner TO r;\nGRANT ducklake_telemetry_owner TO r;",
        ),
        (
            "GRANT ducklake_smoke_owner TO r WITH INHERIT TRUE;",
            "GRANT ducklake_smoke_owner TO r;\nGRANT USAGE ON SCHEMA ducklake_smoke TO r;",
        ),
        ("LIMIT 8", "PASSWORD 'x'"),
    ],
)
def test_maintainer_sql_predicate_is_red_unless_exactly_one_owner_role_is_granted(old: str, new: str) -> None:
    assert sql_problems(_MAINTAINER, "ducklake_smoke", "ducklake_smoke_owner") == []
    assert old in _MAINTAINER
    assert sql_problems(_MAINTAINER.replace(old, new, 1), "ducklake_smoke", "ducklake_smoke_owner")
