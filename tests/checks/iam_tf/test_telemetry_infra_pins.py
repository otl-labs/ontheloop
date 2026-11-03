"""Standing guard for the telemetry writer infrastructure (PLAN-telemetry-writer-infra, then PLAN-telemetry-production-flip).

Pins, over the real terraform/personal tree: the writer's catalog pin on the production telemetry catalog (Decision 230)
with the production data path, blob root and scoped login; the catalog-scoped blob root on the telemetry writer (never the
shared writer); the telemetry writer's production-only grants since the flip's narrow PR (Get/Put on the production data
prefix and fenced blob root plus prefix-conditioned Lists and the production scoped login, never a delete; the smoke
binding is the twin's); no delete under either blob root for any of the three roles; the untouched ops catalog env on
the shared writer and reader; the shared writer's lack of any telemetry state; the telemetry writer's distance from the
ops ducklake/ prefix; and the telemetry-blobs lifecycle rule. The shared reader's telemetry pins live in
test_telemetry_shared_reader_pins.py. Every predicate is a pure function of HCL text with its own red case on synthetic
HCL.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase

import pytest

from tests.fixtures.terraform_hcl_blocks import (
    _TERRAFORM_PERSONAL_DIR,
    assert_every_rule_enabled,
    find_resource_block,
    split_rule_blocks,
    tf_dir_file_texts,
)

_BUCKET = "probe-bucket"
_ARN = f"arn:aws:s3:::{_BUCKET}"
_PROBE_KEYS = (f"{_ARN}/telemetry-blobs/ducklake_smoke/probe-key", f"{_ARN}/telemetry-blobs/ducklake_telemetry/probe-key")
_PROD_KEY = f"{_ARN}/ducklake/probe-key"
_PROD_LIST_PROBE = "ducklake/probe-key"
_SCOPED_SECRET = "aws_secretsmanager_secret.ducklake_telemetry_writer_dsn"  # pragma: allowlist secret
_PROD_SECRET = "aws_secretsmanager_secret.ducklake_telemetry_writer_prod_dsn"  # pragma: allowlist secret
_DELETE_ACTIONS = ("s3:deleteobject", "s3:deleteobjectversion")
_OPS_FUNCTIONS = ("ducklake_writer", "ducklake_reader")
_TELEMETRY = "ducklake_telemetry_writer"
_DELETE_ROLES = (*_OPS_FUNCTIONS, _TELEMETRY)
_PIN_MOVE_HINT = (
    "the telemetry catalog pin is production since Decision 230; moving it is the flip plan's rollback, then edit this guard"
)
_PROD_ONLY = "production grants only since PLAN-telemetry-production-flip's narrow PR; the smoke binding is the twin's"
_FIELD_SEMANTICS = '"/var/task/config/lambda/ducklake/field_semantics.yaml"'
_RULE_ID = "telemetry-blobs-noncurrent-reclaim"


def _brace_slice(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]
    raise AssertionError("unbalanced braces")


def _locals(text: str) -> dict[str, str]:
    m = re.search(r"\blocals\s*\{", text)
    assert m is not None, "no locals block"
    body = _brace_slice(text, m.end() - 1)
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"', body, re.MULTILINE))


def _resolve(token: str, locs: dict[str, str]) -> str:
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    elif token == "aws_s3_bucket.data_lake.arn":
        return _ARN
    token = token.replace("${aws_s3_bucket.data_lake.arn}", _ARN).replace("${aws_s3_bucket.data_lake.bucket}", _BUCKET)
    return re.sub(r"\$\{local\.(\w+)\}", lambda m: _resolve(f'"{_local(m.group(1), locs)}"', locs), token)


def _local(name: str, locs: dict[str, str]) -> str:
    assert name in locs, f"local.{name} is not a quoted literal in the first locals block"
    return locs[name]


def _items(raw: str, locs: dict[str, str]) -> list[str]:
    raw = raw.strip()
    parts = raw[1:-1].split(",") if raw.startswith("[") else [raw]
    return [_resolve(i, locs) for i in parts if i.strip()]


def _uncommented(block: str) -> str:
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))


def _env(text: str, function: str) -> dict[str, str]:
    block = _uncommented(find_resource_block(text, "aws_lambda_function", function))
    m = re.search(r"\bvariables\s*=\s*\{", block)
    assert m is not None, f"{function} has no environment variables block"
    return dict(re.findall(r"^\s*([A-Z_]+)\s*=\s*(.+?)\s*$", _brace_slice(block, m.end() - 1), re.MULTILINE))


def _statement_objects(block: str) -> list[str]:
    m = re.search(r"\bStatement\s*=\s*\[", block)
    assert m is not None, "the policy has no Statement list"
    objects, i = [], m.end()
    while block[i] != "]":
        if block[i] == "{":
            objects.append(_brace_slice(block, i))
            i += len(objects[-1])
        else:
            i += 1
    return objects


def _statements(policy_block: str, locs: dict[str, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for n, stmt in enumerate(_statement_objects(_uncommented(policy_block))):
        sid = re.search(r'\bSid\s*=\s*"(\w+)"', stmt)
        action = re.search(r"\b(Action|NotAction)\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", stmt)
        resource = re.search(r"\bResource\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[\w.]+)", stmt)
        prefix = re.search(r'"s3:prefix"\s*=\s*(\[[^\]]*\])', stmt)
        effect = re.search(r'\bEffect\s*=\s*"(\w+)"', stmt)
        out[sid.group(1) if sid else f"statement[{n}]"] = {
            "effect": effect.group(1) if effect else "",
            "not_action": bool(action and action.group(1) == "NotAction"),
            "actions": _items(action.group(2), locs) if action else [],
            "resources": _items(resource.group(1), locs) if resource else [],
            "prefixes": _items(prefix.group(1), locs) if prefix else [],
        }
    return out


def _role_statements(text: str, role: str) -> dict[str, dict]:
    return _statements(find_resource_block(text, "aws_iam_role_policy", role), _locals(text))


def assert_pin_targets_production(text: str) -> None:
    locs = _locals(text)
    assert locs.get("ducklake_telemetry_meta_schema") == "ducklake_telemetry", f"meta schema local moved: {_PIN_MOVE_HINT}"
    env = _env(text, _TELEMETRY)
    want = {
        "TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema",
        "TELEMETRY_DATA_PATH": "local.ducklake_telemetry_data_path",
        "TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_blob_root",
        "TELEMETRY_DSN_SECRET_ID": f"{_PROD_SECRET}.name",
    }
    for key, value in want.items():
        assert env.get(key) == value, f"{_TELEMETRY} {key} is {env.get(key)!r}, want {value}: {_PIN_MOVE_HINT}"


def assert_blob_root_follows_the_writer_pin(text: str) -> None:
    locs = _locals(text)
    assert _env(text, _TELEMETRY).get("TELEMETRY_BLOB_ROOT") == "local.ducklake_telemetry_blob_root", (
        f"{_TELEMETRY} TELEMETRY_BLOB_ROOT must be local.ducklake_telemetry_blob_root"
    )
    root = _resolve('"${local.ducklake_telemetry_blob_root}"', locs)
    expected = f"s3://{_BUCKET}/telemetry-blobs/{locs.get('ducklake_telemetry_meta_schema')}/"
    assert root == expected, f"blob root {root!r} must be catalog-scoped ({expected!r}): {_PIN_MOVE_HINT}"
    assert "TELEMETRY_BLOB_ROOT" not in _env(text, "ducklake_writer"), "ducklake_writer must not carry a blob root"


def assert_no_delete_on_prefix(text: str) -> None:
    for role in _DELETE_ROLES:
        for sid, s in _role_statements(text, role).items():
            if s["effect"] != "Allow":
                continue
            deletes = s["not_action"] or any(fnmatchcase(d, a.lower()) for a in s["actions"] for d in _DELETE_ACTIONS)
            covers = any(fnmatchcase(key, r) for key in _PROBE_KEYS for r in s["resources"])
            assert not (deletes and covers), (
                f"{role} statement {sid} can delete telemetry-blobs/ objects (rec-4033 owns deletes)"
            )


def _plain_allow(st: dict[str, dict], sid: str, actions: list[str]) -> dict:
    s = st.get(sid)
    assert s is not None, f"telemetry writer policy lacks {sid}"
    assert s["effect"] == "Allow" and not s["not_action"], f"{sid} must be a plain Allow"
    assert sorted(s["actions"]) == actions, f"{sid} actions {s['actions']}"
    return s


def assert_writer_blob_grant_shape(text: str) -> None:
    st = _role_statements(text, _TELEMETRY)
    rw = _plain_allow(st, "TelemetryBlobReadWrite", ["s3:GetObject", "s3:PutObject"])
    lst = _plain_allow(st, "TelemetryBlobList", ["s3:ListBucket"])
    roots = ["telemetry-blobs/ducklake_telemetry/*"]
    assert rw["resources"] == [f"{_ARN}/{r}" for r in roots], (
        f"TelemetryBlobReadWrite resources {rw['resources']}: {_PROD_ONLY}"
    )
    assert lst["resources"] == [_ARN], f"TelemetryBlobList resources {lst['resources']}"
    assert lst["prefixes"] == roots, f"TelemetryBlobList must be fenced to {roots}, got {lst['prefixes']}: {_PROD_ONLY}"
    data = _plain_allow(st, "TelemetryCatalogDataReadWrite", ["s3:GetObject", "s3:PutObject"])
    data_list = _plain_allow(st, "TelemetryCatalogDataList", ["s3:GetBucketLocation", "s3:ListBucket"])
    prefixes = ["ducklake-telemetry/*"]
    assert data["resources"] == [f"{_ARN}/{p}" for p in prefixes], f"catalog data resources {data['resources']}: {_PROD_ONLY}"
    assert data_list["resources"] == [_ARN] and data_list["prefixes"] == prefixes, (
        f"catalog data list {data_list['resources']} {data_list['prefixes']}: {_PROD_ONLY}"
    )
    assert_no_delete_on_prefix(text)


def assert_blob_lifecycle_rule(main_text: str, lambdas_text: str) -> None:
    assert _locals(lambdas_text).get("telemetry_blob_data_prefix") == "telemetry-blobs", "telemetry_blob_data_prefix moved"
    block = find_resource_block(main_text, "aws_s3_bucket_lifecycle_configuration", "data_lake")
    rules = [r for r in split_rule_blocks(block) if re.search(rf'\bid\s*=\s*"{_RULE_ID}"', r)]
    assert len(rules) == 1, f"expected exactly one {_RULE_ID} rule, found {len(rules)}"
    assert_every_rule_enabled(rules)
    assert re.search(r'prefix\s*=\s*"\$\{local\.telemetry_blob_data_prefix\}/"', rules[0]), (
        "the telemetry rule prefix must derive from local.telemetry_blob_data_prefix with a trailing slash"
    )


def _covers_secrets(s: dict) -> bool:
    return s["not_action"] or any(a == "*" or a.lower().startswith("secretsmanager:") for a in s["actions"])


def assert_telemetry_writer_reads_only_its_scoped_login(text: str) -> None:
    st = _role_statements(text, _TELEMETRY)
    granting = {sid: s for sid, s in st.items() if s["effect"] == "Allow" and _covers_secrets(s)}
    assert list(granting) == ["TelemetryDsnRead"], (
        f"secretsmanager grants must be TelemetryDsnRead only, got {sorted(granting)}"
    )
    dsn = granting["TelemetryDsnRead"]
    assert not dsn["not_action"] and dsn["actions"] == ["secretsmanager:GetSecretValue"], f"DSN actions {dsn['actions']}"
    assert dsn["resources"] == [f"{_PROD_SECRET}.arn"], (
        f"DSN resources {dsn['resources']} must be the production scoped login secret only: {_PROD_ONLY}"
    )
    env = _env(text, _TELEMETRY)
    assert env.get("TELEMETRY_DSN_SECRET_ID") == f"{_PROD_SECRET}.name", (
        "TELEMETRY_DSN_SECRET_ID must name the production scoped login"
    )
    assert "ducklake_neon_catalog_dsn" not in find_resource_block(text, "aws_iam_role_policy", _TELEMETRY), (
        "the telemetry writer must never reference the shared catalog DSN"
    )


def assert_writer_carries_no_telemetry_state(text: str) -> None:
    env = _env(text, "ducklake_writer")
    leaked = sorted(k for k in env if k.startswith("TELEMETRY_"))
    assert not leaked, f"the shared writer env carries telemetry state {leaked}"
    for sid, s in _role_statements(text, "ducklake_writer").items():
        assert not sid.startswith("Telemetry"), f"the shared writer policy carries telemetry statement {sid}"
        assert not any(fnmatchcase(k, r) and "telemetry" in r for k in _PROBE_KEYS for r in s["resources"]), (
            f"the shared writer statement {sid} grants telemetry-blobs/"
        )


def assert_telemetry_writer_cannot_reach_the_production_prefix(text: str) -> None:
    for sid, s in _role_statements(text, _TELEMETRY).items():
        if s["effect"] != "Allow":
            continue
        s3_any = s["not_action"] or any(a == "*" or a.lower().startswith("s3:") for a in s["actions"])
        assert not (s3_any and any(fnmatchcase(_PROD_KEY, r) for r in s["resources"])), (
            f"telemetry writer statement {sid} covers the production ducklake/ prefix"
        )
        lists = s["not_action"] or any(fnmatchcase("s3:listbucket", a.lower()) for a in s["actions"])
        if lists and any(fnmatchcase(_ARN, r) for r in s["resources"]):
            covering = not s["prefixes"] or any(fnmatchcase(_PROD_LIST_PROBE, p) for p in s["prefixes"])
            assert not covering, f"telemetry writer statement {sid} can list the production ducklake/ prefix"
    env = _env(text, _TELEMETRY)
    for key in ("DUCKLAKE_DATA_PATH", "DUCKLAKE_META_SCHEMA"):
        assert key not in env, f"the telemetry writer must not set {key}"


def assert_ops_catalog_env_untouched(text: str) -> None:
    for fn in _OPS_FUNCTIONS:
        env = _env(text, fn)
        assert "DUCKLAKE_META_SCHEMA" not in env, f"{fn} sets DUCKLAKE_META_SCHEMA, which re-points every ops verb"
        assert env.get("DUCKLAKE_DATA_PATH") == "local.ducklake_prod_data_path", f"{fn} DUCKLAKE_DATA_PATH moved"
        assert env.get("DUCKLAKE_EXTENSION_DIRECTORY") == "local.ducklake_extension_dir", f"{fn} extension dir moved"
        assert env.get("DUCKLAKE_FIELD_SEMANTICS_PATH") == _FIELD_SEMANTICS, f"{fn} field semantics path moved"


_ARN_REF = "${aws_s3_bucket.data_lake.arn}"
_SMOKE_FENCE = "${local.telemetry_blob_data_prefix}/${local.ducklake_smoke_meta_schema}/*"
_FENCES = ["${local.telemetry_blob_data_prefix}/${local.ducklake_telemetry_prod_meta_schema}/*"]
_SMOKE_DATA = "${local.ducklake_smoke_data_prefix}/*"
_DATA = ["${local.ducklake_telemetry_data_prefix}/*"]
_RW = {
    "Sid": "TelemetryBlobReadWrite",
    "Action": ["s3:GetObject", "s3:PutObject"],
    "Resource": [f"{_ARN_REF}/{f}" for f in _FENCES],
}
_LIST = {
    "Sid": "TelemetryBlobList",
    "Action": ["s3:ListBucket"],
    "Resource": [_ARN_REF],
    "prefix": _FENCES,
}
_DSN = {
    "Sid": "TelemetryDsnRead",
    "Action": ["secretsmanager:GetSecretValue"],
    "Resource": [f"{_PROD_SECRET}.arn"],
}
_CATALOG_LIST = {
    "Sid": "TelemetryCatalogDataList",
    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
    "Resource": [_ARN_REF],
    "prefix": _DATA,
}
_CATALOG_RW = {
    "Sid": "TelemetryCatalogDataReadWrite",
    "Action": ["s3:GetObject", "s3:PutObject"],
    "Resource": [f"{_ARN_REF}/{p}" for p in _DATA],
}
_OPS_RW = {
    "Sid": "S3DataReadWrite",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
    "Resource": [f"{_ARN_REF}/${{local.ducklake_prod_data_prefix}}/*"],
}
_OPS_RO = {**_OPS_RW, "Sid": "S3DataReadOnly", "Action": ["s3:GetObject"]}
_READER_STMTS = [_OPS_RO]
_TELEMETRY_STMTS = [_DSN, _CATALOG_RW, _CATALOG_LIST, _RW, _LIST]


def _stmt(s: dict) -> str:
    lines = ["{", *([f"  Sid = {json.dumps(s['Sid'])}"] if "Sid" in s else []), '  Effect = "Allow"']
    lines.append(f"  {'NotAction' if 'NotAction' in s else 'Action'} = {json.dumps(s.get('NotAction', s.get('Action')))}")
    resource = ", ".join(s["Resource"])
    lines.append(
        f"  Resource = [{resource}]"
        if "." in resource and '"' not in resource and "$" not in resource
        else f"  Resource = {json.dumps(s['Resource'])}"
    )
    if "prefix" in s:
        lines.append(f'  Condition = {{ StringLike = {{ "s3:prefix" = {json.dumps(s["prefix"])} }} }}')
    return "\n".join([*lines, "},"])


def _doc(
    writer_env: dict[str, str] | None = None,
    reader_env: dict[str, str] | None = None,
    telemetry_env: dict[str, str] | None = None,
    writer_stmts: list[dict] | None = None,
    reader_stmts: list[dict] | None = None,
    telemetry_stmts: list[dict] | None = None,
    meta_schema: str = "ducklake_telemetry",
    blob_root: str = "s3://${aws_s3_bucket.data_lake.bucket}/${local.telemetry_blob_data_prefix}/${local.ducklake_telemetry_meta_schema}/",
) -> str:
    ops = {
        "DUCKLAKE_DATA_PATH": "local.ducklake_prod_data_path",
        "DUCKLAKE_EXTENSION_DIRECTORY": "local.ducklake_extension_dir",
        "DUCKLAKE_FIELD_SEMANTICS_PATH": _FIELD_SEMANTICS,
    }
    telemetry_base = {
        "TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema",
        "TELEMETRY_DATA_PATH": "local.ducklake_telemetry_data_path",
        "TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_blob_root",
        "TELEMETRY_DSN_SECRET_ID": f"{_PROD_SECRET}.name",
        "DUCKLAKE_EXTENSION_DIRECTORY": "local.ducklake_extension_dir",
        "DUCKLAKE_FIELD_SEMANTICS_PATH": _FIELD_SEMANTICS,
    }
    envs = {
        "ducklake_writer": {**ops, **(writer_env or {})},
        "ducklake_reader": {**ops, **(reader_env or {})},
        _TELEMETRY: {**telemetry_base, **(telemetry_env or {})},
    }
    stmts = {
        "ducklake_writer": writer_stmts if writer_stmts is not None else [_OPS_RW],
        "ducklake_reader": reader_stmts if reader_stmts is not None else _READER_STMTS,
        _TELEMETRY: telemetry_stmts if telemetry_stmts is not None else _TELEMETRY_STMTS,
    }
    parts = [
        "locals {",
        '  ducklake_telemetry_meta_schema = "' + meta_schema + '"',
        '  ducklake_smoke_meta_schema = "ducklake_smoke"',
        '  ducklake_telemetry_prod_meta_schema = "ducklake_telemetry"',
        '  telemetry_blob_data_prefix = "telemetry-blobs"',
        '  ducklake_prod_data_prefix = "ducklake"',
        '  ducklake_smoke_data_prefix = "ducklake-neon-smoke"',
        '  ducklake_telemetry_data_prefix = "ducklake-telemetry"',
        '  ducklake_telemetry_blob_root = "' + blob_root + '"',
        "}",
    ]
    for fn, env in envs.items():
        body = "\n".join(f"      {k} = {v}" for k, v in env.items())
        parts.append(
            f'resource "aws_lambda_function" "{fn}" {{\n  environment {{\n    variables = {{\n{body}\n    }}\n  }}\n}}'
        )
        policy = "\n".join(_stmt(s) for s in stmts[fn])
        parts.append(f'resource "aws_iam_role_policy" "{fn}" {{\n  policy = jsonencode({{ Statement = [\n{policy}\n] }})\n}}')
    return "\n".join(parts)


def _real() -> tuple[str, str]:
    texts = tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)
    return texts["ducklake_lambdas.tf"] + "\n" + texts["ducklake_telemetry_writer.tf"], texts["main.tf"]


def test_telemetry_pin_targets_the_production_catalog() -> None:
    assert_pin_targets_production(_real()[0])


def test_blob_root_follows_the_writer_pin() -> None:
    assert_blob_root_follows_the_writer_pin(_real()[0])


def test_writer_blob_grant_shape() -> None:
    text = _real()[0]
    assert_writer_blob_grant_shape(text)
    pinned_back = re.sub(r'(ducklake_telemetry_meta_schema\s*=\s*)"ducklake_telemetry"', r'\1"ducklake_smoke"', text, count=1)
    assert pinned_back != text
    assert_writer_blob_grant_shape(pinned_back)


def test_blob_lifecycle_rule_covers_the_prefix() -> None:
    lambdas, main = _real()
    assert_blob_lifecycle_rule(main, lambdas)


def test_ops_catalog_env_is_untouched() -> None:
    assert_ops_catalog_env_untouched(_real()[0])


def test_telemetry_writer_reads_only_its_scoped_login() -> None:
    assert_telemetry_writer_reads_only_its_scoped_login(_real()[0])


def test_writer_carries_no_telemetry_state() -> None:
    assert_writer_carries_no_telemetry_state(_real()[0])


def test_telemetry_writer_cannot_reach_the_production_prefix() -> None:
    assert_telemetry_writer_cannot_reach_the_production_prefix(_real()[0])


def test_pin_guard_rejects_a_moved_pin() -> None:
    assert_pin_targets_production(_doc())
    moved = [
        _doc(meta_schema="ducklake_smoke"),
        _doc(meta_schema="ducklake_ops"),
        _doc(telemetry_env={"TELEMETRY_META_SCHEMA": "local.ducklake_smoke_meta_schema"}),
        _doc(telemetry_env={"TELEMETRY_DATA_PATH": "local.ducklake_smoke_data_path"}),
        _doc(telemetry_env={"TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_smoke_blob_root"}),
        _doc(telemetry_env={"TELEMETRY_DSN_SECRET_ID": f"{_SCOPED_SECRET}.name"}),
    ]
    for doc in moved:
        with pytest.raises(AssertionError, match="Decision"):
            assert_pin_targets_production(doc)


def test_blob_root_guard_rejects_a_moved_root() -> None:
    assert_blob_root_follows_the_writer_pin(_doc())
    telemetry_moved = _doc(telemetry_env={"TELEMETRY_BLOB_ROOT": "local.elsewhere"})
    ops_writer_root = _doc(writer_env={"TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_blob_root"})
    unscoped = _doc(blob_root="s3://${aws_s3_bucket.data_lake.bucket}/${local.telemetry_blob_data_prefix}/")
    for doc in (telemetry_moved, ops_writer_root, unscoped):
        with pytest.raises(AssertionError):
            assert_blob_root_follows_the_writer_pin(doc)


def _grant_variants() -> list[str]:
    arn = _ARN_REF
    docs = []
    for action in ("s3:DeleteObject", "s3:DeleteObjectVersion", "s3:Delete*", "s3:*", "s3:*Object*"):
        extra = {**_RW, "Sid": "Extra", "Action": [action]}
        docs.append(_doc(telemetry_stmts=[*_TELEMETRY_STMTS, extra]))
    for resource in (f"{arn}/telemetry-*", f"{arn}/*", f"{arn}/*blobs*"):
        extra = {**_OPS_RW, "Sid": "Extra", "Resource": [resource]}
        docs.append(_doc(telemetry_stmts=[*_TELEMETRY_STMTS, extra]))
    not_action = {"Sid": "Open", "NotAction": ["s3:ListBucket"], "Resource": [f"{arn}/*"]}
    docs.append(_doc(telemetry_stmts=[*_TELEMETRY_STMTS, not_action]))
    for bad_list in ({**_LIST, "prefix": []}, {**_LIST, "Resource": [f"{arn}/*"]}, {**_LIST, "prefix": ["telemetry-blobs/*"]}):
        docs.append(_doc(telemetry_stmts=[_RW, bad_list]))
    docs.append(_doc(telemetry_stmts=[{**_RW, "Resource": [f"{arn}/telemetry-blobs/*"]}, _LIST]))
    docs.append(_doc(telemetry_stmts=[{**_RW, "Action": [*_RW["Action"], "s3:AbortMultipartUpload"]}, _LIST]))
    docs.append(_doc(telemetry_stmts=[_LIST]))
    smoke_root, smoke_data = f"{arn}/{_SMOKE_FENCE}", f"{arn}/{_SMOKE_DATA}"
    for roots in ([smoke_root], [smoke_root, *_RW["Resource"]], [*_RW["Resource"], smoke_root]):
        docs.append(_doc(telemetry_stmts=[_DSN, _CATALOG_RW, _CATALOG_LIST, {**_RW, "Resource": roots}, _LIST]))
    for fences in ([_SMOKE_FENCE], [_SMOKE_FENCE, *_FENCES], [*_FENCES, _SMOKE_FENCE]):
        docs.append(_doc(telemetry_stmts=[_DSN, _CATALOG_RW, _CATALOG_LIST, _RW, {**_LIST, "prefix": fences}]))
    for data in ([smoke_data], [smoke_data, *_CATALOG_RW["Resource"]], [*_CATALOG_RW["Resource"], smoke_data]):
        docs.append(_doc(telemetry_stmts=[_DSN, {**_CATALOG_RW, "Resource": data}, _CATALOG_LIST, _RW, _LIST]))
    for data in ([_SMOKE_DATA], [_SMOKE_DATA, *_DATA], [*_DATA, _SMOKE_DATA]):
        docs.append(_doc(telemetry_stmts=[_DSN, _CATALOG_RW, {**_CATALOG_LIST, "prefix": data}, _RW, _LIST]))
    return docs


def test_grant_guard_rejects_delete() -> None:
    assert_writer_blob_grant_shape(_doc())
    variants = _grant_variants()
    for doc in variants:
        with pytest.raises(AssertionError):
            assert_writer_blob_grant_shape(doc)
    for root in (f"{_ARN_REF}/{_SMOKE_FENCE}", *_RW["Resource"]):
        reader_delete = _doc(
            reader_stmts=[{**_RW, "Sid": "ReaderDelete", "Action": ["s3:DeleteObjectVersion"], "Resource": [root]}]
        )
        with pytest.raises(AssertionError, match="delete"):
            assert_no_delete_on_prefix(reader_delete)
        writer_delete = _doc(writer_stmts=[{**_RW, "Sid": "WriterDelete", "Action": ["s3:DeleteObject"], "Resource": [root]}])
        with pytest.raises(AssertionError, match="delete"):
            assert_no_delete_on_prefix(writer_delete)
        unnamed = _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {"Action": ["s3:DeleteObject"], "Resource": [root]}])
        with pytest.raises(AssertionError, match="delete"):
            assert_no_delete_on_prefix(unnamed)


def test_lifecycle_guard_rejects_a_missing_rule() -> None:
    lambdas, main = _real()
    block = find_resource_block(main, "aws_s3_bucket_lifecycle_configuration", "data_lake")
    for bad in (
        main.replace(block, block.replace(_RULE_ID, "other-rule")),
        main.replace(block, block.replace("${local.telemetry_blob_data_prefix}/", "${local.telemetry_blob_data_prefix}")),
    ):
        with pytest.raises(AssertionError):
            assert_blob_lifecycle_rule(bad, lambdas)


def test_ops_env_guard_rejects_a_moved_ops_catalog() -> None:
    assert_ops_catalog_env_untouched(_doc())
    bad_envs = [
        {"DUCKLAKE_META_SCHEMA": "local.ducklake_telemetry_meta_schema"},
        {"DUCKLAKE_DATA_PATH": "local.ducklake_smoke_data_path"},
        {"DUCKLAKE_EXTENSION_DIRECTORY": "local.elsewhere"},
        {"DUCKLAKE_FIELD_SEMANTICS_PATH": '"/tmp/other.yaml"'},
    ]
    for env in bad_envs:
        with pytest.raises(AssertionError):
            assert_ops_catalog_env_untouched(_doc(reader_env=env))


def test_scoped_login_guard_rejects_the_shared_login() -> None:
    assert_telemetry_writer_reads_only_its_scoped_login(_doc())
    shared = "aws_secretsmanager_secret.ducklake_neon_catalog_dsn"
    rest = _TELEMETRY_STMTS[1:]
    bad = [
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_DSN, "Sid": "SharedDsn", "Resource": [f"{shared}.arn"]}]),
        _doc(telemetry_stmts=[{**_DSN, "Resource": [f"{shared}.arn"]}, *rest]),
        _doc(telemetry_stmts=[{**_DSN, "Action": ["secretsmanager:*"]}, *rest]),
        _doc(telemetry_stmts=[{**_DSN, "Resource": [*_DSN["Resource"], f"{shared}.arn"]}, *rest]),
        _doc(telemetry_stmts=[{**_DSN, "Resource": [f"{_SCOPED_SECRET}.arn"]}, *rest]),
        _doc(telemetry_stmts=[{**_DSN, "Resource": [f"{_SCOPED_SECRET}.arn", f"{_PROD_SECRET}.arn"]}, *rest]),
        _doc(telemetry_stmts=[{**_DSN, "Resource": [f"{_PROD_SECRET}.arn", f"{_SCOPED_SECRET}.arn"]}, *rest]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {"Sid": "Any", "NotAction": ["s3:GetObject"], "Resource": ["*"]}]),
        _doc(telemetry_stmts=rest),
        _doc(telemetry_env={"TELEMETRY_DSN_SECRET_ID": f"{shared}.name"}),
        _doc(telemetry_env={"TELEMETRY_DSN_SECRET_ID": f"{_SCOPED_SECRET}.name"}),
    ]
    for doc in bad:
        with pytest.raises(AssertionError):
            assert_telemetry_writer_reads_only_its_scoped_login(doc)


def test_no_telemetry_state_guard_rejects_a_leaky_writer() -> None:
    assert_writer_carries_no_telemetry_state(_doc())
    bad = [
        _doc(writer_env={"TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema"}),
        _doc(writer_env={"TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_blob_root"}),
        _doc(writer_stmts=[_OPS_RW, _RW]),
        _doc(writer_stmts=[_OPS_RW, {**_RW, "Sid": "Blob"}]),
        _doc(
            writer_env={
                "DUCKLAKE_FIELD_SEMANTICS_PATH": '"${local.ducklake_extension_dir}/x.yaml"',
                "TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema",
            }
        ),
        _doc(writer_stmts=[_OPS_RW, {"Action": ["s3:GetObject"], "Resource": [_RW["Resource"][0]]}]),
    ]
    for doc in bad:
        with pytest.raises(AssertionError):
            assert_writer_carries_no_telemetry_state(doc)


def test_production_prefix_guard_rejects_reach() -> None:
    assert_telemetry_writer_cannot_reach_the_production_prefix(_doc())
    arn = _ARN_REF
    prod = "${local.ducklake_prod_data_prefix}"
    bad = [
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_OPS_RW, "Sid": "ProdRW"}]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_OPS_RW, "Sid": "ProdGet", "Action": ["s3:GetObject"]}]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_OPS_RW, "Sid": "Wild", "Action": ["s3:*"], "Resource": [f"{arn}/*"]}]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_OPS_RW, "Sid": "Star", "Action": ["*"], "Resource": ["*"]}]),
        _doc(
            telemetry_stmts=[
                *_TELEMETRY_STMTS,
                {"Sid": "Not", "NotAction": ["s3:DeleteObject"], "Resource": [f"{arn}/{prod}/*"]},
            ]
        ),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_LIST, "Sid": "ListAll", "prefix": ["*"]}]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {**_LIST, "Sid": "ListProd", "prefix": [f"{prod}/*"]}]),
        _doc(telemetry_stmts=[*_TELEMETRY_STMTS, {"Sid": "ListNoCond", "Action": ["s3:ListBucket"], "Resource": [arn]}]),
        _doc(telemetry_env={"DUCKLAKE_DATA_PATH": "local.ducklake_prod_data_path"}),
        _doc(telemetry_env={"DUCKLAKE_META_SCHEMA": "ducklake"}),
    ]
    for doc in bad:
        with pytest.raises(AssertionError):
            assert_telemetry_writer_cannot_reach_the_production_prefix(doc)
