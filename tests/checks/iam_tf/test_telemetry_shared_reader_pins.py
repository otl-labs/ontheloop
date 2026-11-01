"""Standing guard: the shared ducklake_reader carries no telemetry state (PLAN-telemetry-production-flip).

The telemetry writer's catalog pin moved to the production telemetry catalog (Decision 230) and event reads are the
separate telemetry reader's (Decision 236); the flip's narrow PR retired the smoke binding the shared reader kept through
the expand window. Pins, over the real terraform/personal tree: no shared-reader TELEMETRY_* env key and no statement that
is named for, names, covers or lists telemetry-blobs/, while the ops data grants stay on the ducklake/ and smoke prefixes;
and the flip's looser production fence, which any reintroduced binding must still pass: every TELEMETRY_* env value is the
smoke local for its key and resolves to no production value, at most one statement reaches telemetry-blobs/ (s3:GetObject
only on telemetry-blobs/ducklake_smoke/*), and no S3 statement or list prefix covers ducklake-telemetry/ or
telemetry-blobs/ducklake_telemetry/. Each predicate has red cases on synthetic HCL.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase

import pytest

from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR, find_resource_block, tf_dir_file_texts

_BUCKET = "probe-bucket"
_ARN = f"arn:aws:s3:::{_BUCKET}"
_READER = "ducklake_reader"
_SMOKE_ENV = {
    "TELEMETRY_META_SCHEMA": "local.ducklake_smoke_meta_schema",
    "TELEMETRY_DATA_PATH": "local.ducklake_smoke_data_path",
    "TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_smoke_blob_root",
}
_SMOKE_BLOB = f"{_ARN}/telemetry-blobs/ducklake_smoke/*"
_PRODUCTION_KEYS = ("ducklake-telemetry/probe-key", "telemetry-blobs/ducklake_telemetry/probe-key")
_PRODUCTION_VALUE = re.compile(r"\bducklake_telemetry\b|ducklake-telemetry/")
_HINT = "the flip's narrow PR retired the shared reader's telemetry binding; event reads are the telemetry reader's"
_BLOB_KEYS = ("telemetry-blobs/ducklake_smoke/probe-key", "telemetry-blobs/ducklake_telemetry/probe-key")
_OPS_DATA_PREFIXES = ["ducklake/*", "ducklake-neon-smoke/*"]


def _brace_slice(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_idx : i + 1]
    raise AssertionError("unbalanced braces")


def _locals(text: str) -> dict[str, str]:
    m = re.search(r"\blocals\s*\{", text)
    assert m is not None, "no locals block"
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"', _brace_slice(text, m.end() - 1), re.MULTILINE))


def _resolve(token: str, locs: dict[str, str]) -> str:
    token = token.strip().strip('"')
    if token == "aws_s3_bucket.data_lake.arn":
        return _ARN
    token = token.replace("${aws_s3_bucket.data_lake.arn}", _ARN).replace("${aws_s3_bucket.data_lake.bucket}", _BUCKET)
    return re.sub(r"\$\{local\.(\w+)\}", lambda m: _resolve(_local(m.group(1), locs), locs), token)


def _local(name: str, locs: dict[str, str]) -> str:
    assert name in locs, f"local.{name} is not a quoted literal in the first locals block"
    return locs[name]


def _items(raw: str | None, locs: dict[str, str]) -> list[str]:
    if raw is None:
        return []
    raw = raw.strip()
    parts = raw[1:-1].split(",") if raw.startswith("[") else [raw]
    return [_resolve(p, locs) for p in parts if p.strip()]


def _uncommented(block: str) -> str:
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))


def _reader_env(text: str) -> dict[str, str]:
    block = _uncommented(find_resource_block(text, "aws_lambda_function", _READER))
    m = re.search(r"\bvariables\s*=\s*\{", block)
    assert m is not None, f"{_READER} has no environment variables block"
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


def _reader_statements(text: str) -> dict[str, dict]:
    block, locs = _uncommented(find_resource_block(text, "aws_iam_role_policy", _READER)), _locals(text)
    out: dict[str, dict] = {}
    for n, stmt in enumerate(_statement_objects(block)):
        sid = re.search(r'\bSid\s*=\s*"(\w+)"', stmt)
        action = re.search(r"\b(Action|NotAction)\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", stmt)
        resource = re.search(r"\bResource\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[\w.]+)", stmt)
        prefix = re.search(r'"s3:prefix"\s*=\s*(\[[^\]]*\])', stmt)
        effect = re.search(r'\bEffect\s*=\s*"(\w+)"', stmt)
        out[sid.group(1) if sid else f"statement[{n}]"] = {
            "effect": effect.group(1) if effect else "",
            "not_action": bool(action and action.group(1) == "NotAction"),
            "actions": _items(action.group(2) if action else None, locs),
            "resources": _items(resource.group(1) if resource else None, locs),
            "prefixes": _items(prefix.group(1) if prefix else None, locs),
        }
    return out


def _s3_capable(s: dict) -> bool:
    return s["not_action"] or any(fnmatchcase("s3:getobject", a.lower()) or a.lower().startswith("s3:") for a in s["actions"])


def assert_shared_reader_never_reaches_production_telemetry(text: str) -> None:
    locs = _locals(text)
    for key, value in _reader_env(text).items():
        if not key.startswith("TELEMETRY_"):
            continue
        assert value == _SMOKE_ENV.get(key), f"{_READER} {key} is {value!r}, want {_SMOKE_ENV.get(key)}: {_HINT}"
        resolved = _resolve(f'"${{{value}}}"', locs)
        assert not _PRODUCTION_VALUE.search(resolved), f"{_READER} {key} resolves to production {resolved!r}: {_HINT}"
    blob = []
    for sid, s in _reader_statements(text).items():
        if s["effect"] != "Allow" or not _s3_capable(s):
            continue
        reach = [f"{_ARN}/{k}" for k in _PRODUCTION_KEYS]
        assert not any(fnmatchcase(k, r) for k in reach for r in s["resources"]), f"{_READER} {sid} covers production: {_HINT}"
        lists = s["not_action"] or any(fnmatchcase("s3:listbucket", a.lower()) for a in s["actions"])
        if lists and any(fnmatchcase(_ARN, r) for r in s["resources"]):
            covering = not s["prefixes"] or any(fnmatchcase(k, p) for k in _PRODUCTION_KEYS for p in s["prefixes"])
            assert not covering, f"{_READER} {sid} can list production telemetry: {_HINT}"
        smoke_probe = f"{_ARN}/telemetry-blobs/ducklake_smoke/probe-key"
        names_blobs = any("telemetry-blobs" in v for v in s["resources"] + s["prefixes"])
        if names_blobs or any(fnmatchcase(smoke_probe, r) for r in s["resources"]):
            blob.append((sid, s))
    assert len(blob) <= 1, f"{_READER} has {len(blob)} telemetry-blobs statements {[sid for sid, _ in blob]}; at most one"
    for sid, s in blob:
        assert not s["not_action"] and s["actions"] == ["s3:GetObject"], f"{_READER} {sid} must be s3:GetObject only"
        assert s["resources"] == [_SMOKE_BLOB] and not s["prefixes"], f"{_READER} {sid} must be on {_SMOKE_BLOB} only"


def assert_shared_reader_carries_no_telemetry_state(text: str) -> None:
    leaked = sorted(k for k in _reader_env(text) if k.startswith("TELEMETRY_"))
    assert not leaked, f"{_READER} env carries telemetry state {leaked}: {_HINT}"
    statements = _reader_statements(text)
    for sid, s in statements.items():
        named = [v for v in s["resources"] + s["prefixes"] if "telemetry" in v]
        assert not sid.startswith("Telemetry") and not named, f"{_READER} {sid} is a telemetry grant {named}: {_HINT}"
        if s["effect"] != "Allow" or not _s3_capable(s):
            continue
        keys = [f"{_ARN}/{k}" for k in _BLOB_KEYS]
        assert not any(fnmatchcase(k, r) for k in keys for r in s["resources"]), f"{_READER} {sid} covers telemetry-blobs/"
        lists = s["not_action"] or any(fnmatchcase("s3:listbucket", a.lower()) for a in s["actions"])
        if lists and any(fnmatchcase(_ARN, r) for r in s["resources"]):
            covering = not s["prefixes"] or any(fnmatchcase(k, p) for k in _BLOB_KEYS for p in s["prefixes"])
            assert not covering, f"{_READER} {sid} can list telemetry-blobs/: {_HINT}"
    ro, lst = statements.get("S3DataReadOnly"), statements.get("S3ListDataPrefix")
    want = [f"{_ARN}/{p}" for p in _OPS_DATA_PREFIXES]
    assert ro is not None and ro["resources"] == want, f"{_READER} S3DataReadOnly must stay on {want}"
    assert lst is not None and lst["prefixes"] == _OPS_DATA_PREFIXES, f"{_READER} S3ListDataPrefix must stay on ops prefixes"


_ARN_REF = "${aws_s3_bucket.data_lake.arn}"
_OPS_RO = {
    "Sid": "S3DataReadOnly",
    "Action": ["s3:GetObject"],
    "Resource": [f"{_ARN_REF}/${{local.ducklake_prod_data_prefix}}/*", f"{_ARN_REF}/${{local.ducklake_smoke_data_prefix}}/*"],
}
_LIST = {
    "Sid": "S3ListDataPrefix",
    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
    "Resource": [_ARN_REF],
    "prefix": ["${local.ducklake_prod_data_prefix}/*", "${local.ducklake_smoke_data_prefix}/*"],
}
_BLOB = {
    "Sid": "TelemetryBlobRead",
    "Action": ["s3:GetObject"],
    "Resource": [f"{_ARN_REF}/${{local.telemetry_blob_data_prefix}}/${{local.ducklake_smoke_meta_schema}}/*"],
}
_METRICS = {"Sid": "Metrics", "Action": ["cloudwatch:PutMetricData"], "Resource": ["*"]}
_STMTS = [_OPS_RO, _BLOB, _LIST, _METRICS]


def _stmt(s: dict) -> str:
    verb = "NotAction" if "NotAction" in s else "Action"
    sid = [f"  Sid = {json.dumps(s['Sid'])}"] if "Sid" in s else []
    lines = ["{", *sid, '  Effect = "Allow"', f"  {verb} = {json.dumps(s[verb])}"]
    lines.append(f"  Resource = {json.dumps(s['Resource'])}")
    if "prefix" in s:
        lines.append(f'  Condition = {{ StringLike = {{ "s3:prefix" = {json.dumps(s["prefix"])} }} }}')
    return "\n".join([*lines, "},"])


def _doc(
    env: dict[str, str | None] | None = None, stmts: list[dict] | None = None, smoke_schema: str = "ducklake_smoke"
) -> str:
    merged = {"DUCKLAKE_DATA_PATH": "local.ducklake_prod_data_path", **_SMOKE_ENV, **(env or {})}
    body = "\n".join(f"      {k} = {v}" for k, v in merged.items() if v is not None)
    policy = "\n".join(_stmt(s) for s in (_STMTS if stmts is None else stmts))
    locs = {
        "ducklake_telemetry_meta_schema": "ducklake_telemetry",
        "ducklake_smoke_meta_schema": smoke_schema,
        "telemetry_blob_data_prefix": "telemetry-blobs",
        "ducklake_prod_data_prefix": "ducklake",
        "ducklake_prod_data_path": "s3://${aws_s3_bucket.data_lake.bucket}/ducklake/",
        "ducklake_smoke_data_prefix": "ducklake-neon-smoke",
        "ducklake_smoke_data_path": "s3://${aws_s3_bucket.data_lake.bucket}/ducklake-neon-smoke/",
        "ducklake_telemetry_data_prefix": "ducklake-telemetry",
        "ducklake_telemetry_data_path": "s3://${aws_s3_bucket.data_lake.bucket}/ducklake-telemetry/",
        "ducklake_telemetry_blob_root": "s3://${aws_s3_bucket.data_lake.bucket}/telemetry-blobs/${local.ducklake_telemetry_meta_schema}/",
        "ducklake_telemetry_smoke_blob_root": "s3://${aws_s3_bucket.data_lake.bucket}/telemetry-blobs/${local.ducklake_smoke_meta_schema}/",
    }
    return "\n".join(
        [
            "locals {",
            *(f'  {k} = "{v}"' for k, v in locs.items()),
            "}",
            f'resource "aws_lambda_function" "{_READER}" {{\n  environment {{\n    variables = {{\n{body}\n    }}\n  }}\n}}',
            f'resource "aws_iam_role_policy" "{_READER}" {{\n  policy = jsonencode({{ Statement = [\n{policy}\n] }})\n}}',
        ]
    )


def test_shared_reader_never_reaches_production_telemetry() -> None:
    assert_shared_reader_never_reaches_production_telemetry(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)["ducklake_lambdas.tf"])


def test_shared_reader_guard_rejects_a_production_reach() -> None:
    assert_shared_reader_never_reaches_production_telemetry(_doc())
    assert_shared_reader_never_reaches_production_telemetry(_doc(stmts=[_OPS_RO, _LIST]))
    assert_shared_reader_never_reaches_production_telemetry(_doc(env=dict.fromkeys(_SMOKE_ENV)))
    blobs = "${local.telemetry_blob_data_prefix}"
    bad_envs = [
        {"TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema"},
        {"TELEMETRY_DATA_PATH": "local.ducklake_telemetry_data_path"},
        {"TELEMETRY_BLOB_ROOT": "local.ducklake_telemetry_blob_root"},
        {"TELEMETRY_BLOB_ROOT": "local.ducklake_prod_data_path"},
        {"TELEMETRY_DSN_SECRET_ID": "aws_secretsmanager_secret.ducklake_telemetry_writer_prod_dsn.name"},
    ]
    bad_stmts = [
        {**_BLOB, "Action": ["s3:GetObject", "s3:PutObject"]},
        {**_BLOB, "Action": ["s3:DeleteObject"]},
        {**_BLOB, "Action": ["s3:*"]},
        {"Sid": "TelemetryBlobRead", "NotAction": ["s3:PutObject"], "Resource": _BLOB["Resource"]},
        {**_BLOB, "Resource": [f"{_ARN_REF}/{blobs}/${{local.ducklake_telemetry_meta_schema}}/*"]},
        {**_BLOB, "Resource": [f"{_ARN_REF}/{blobs}/*"]},
        {**_BLOB, "Resource": [f"{_ARN_REF}/{blobs}/another_catalog/*"]},
        {**_BLOB, "Resource": [f"{_ARN_REF}/*"]},
        {**_LIST, "Sid": "TelemetryBlobRead", "Action": ["s3:ListBucket"], "prefix": [f"{blobs}/ducklake_smoke/*"]},
    ]
    production_prefix = "${local.ducklake_telemetry_data_prefix}"
    bad_docs = [
        *(_doc(env=env) for env in bad_envs),
        *(_doc(stmts=[_OPS_RO, stmt, _LIST]) for stmt in bad_stmts),
        _doc(stmts=[{**_OPS_RO, "Resource": [*_OPS_RO["Resource"], f"{_ARN_REF}/{production_prefix}/*"]}, _BLOB, _LIST]),
        _doc(stmts=[_OPS_RO, _BLOB, {**_LIST, "prefix": [*_LIST["prefix"], f"{production_prefix}/*"]}]),
        _doc(stmts=[_OPS_RO, _BLOB, {**_LIST, "prefix": []}]),
        _doc(stmts=[_OPS_RO, _BLOB, {**_BLOB, "Sid": "Second"}, _LIST]),
        _doc(smoke_schema="ducklake_telemetry"),
        _doc(
            env={
                "DUCKLAKE_DATA_PATH": '"${local.ducklake_prod_data_path}"',
                "TELEMETRY_META_SCHEMA": "local.ducklake_telemetry_meta_schema",
            }
        ),
        _doc(stmts=[*_STMTS, {"Action": ["s3:GetObject"], "Resource": [f"{_ARN_REF}/{production_prefix}/*"]}]),
        _doc(stmts=[_OPS_RO, {**_BLOB, "Resource": [f"{_ARN_REF}/{blobs}/${{local.nowhere}}/*"]}, _LIST]),
    ]
    for doc in bad_docs:
        with pytest.raises(AssertionError):
            assert_shared_reader_never_reaches_production_telemetry(doc)


def test_shared_reader_carries_no_telemetry_state() -> None:
    assert_shared_reader_carries_no_telemetry_state(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)["ducklake_lambdas.tf"])


def test_no_telemetry_state_guard_rejects_a_leaky_shared_reader() -> None:
    cleared = dict.fromkeys(_SMOKE_ENV)
    narrowed = [_OPS_RO, _LIST, _METRICS]
    assert_shared_reader_carries_no_telemetry_state(_doc(env=cleared, stmts=narrowed))
    blobs = "${local.telemetry_blob_data_prefix}"
    bad_docs = [
        _doc(),
        _doc(stmts=narrowed),
        *(_doc(env={**cleared, key: value}, stmts=narrowed) for key, value in _SMOKE_ENV.items()),
        _doc(env={**cleared, "TELEMETRY_DSN_SECRET_ID": '"unused"'}, stmts=narrowed),
        _doc(env=cleared),
        _doc(env=cleared, stmts=[*narrowed, {**_BLOB, "Sid": "BlobRead"}]),
        _doc(env=cleared, stmts=[*narrowed, {"Action": ["s3:GetObject"], "Resource": [f"{_ARN_REF}/*"]}]),
        _doc(env=cleared, stmts=[*narrowed, {"Sid": "Wide", "NotAction": ["s3:PutObject"], "Resource": ["*"]}]),
        _doc(env=cleared, stmts=[*narrowed, {**_LIST, "Sid": "ListBlobs", "prefix": [f"{blobs}/*"]}]),
        _doc(env=cleared, stmts=[*narrowed, {**_LIST, "Sid": "ListAll", "prefix": ["*"]}]),
        _doc(env=cleared, stmts=[*narrowed, {"Sid": "ListNoCond", "Action": ["s3:ListBucket"], "Resource": [_ARN_REF]}]),
        _doc(
            env=cleared,
            stmts=[*narrowed, {"Sid": "TelemetryMetrics", "Action": ["cloudwatch:PutMetricData"], "Resource": ["*"]}],
        ),
        _doc(env=cleared, stmts=[{**_OPS_RO, "Resource": _OPS_RO["Resource"][:1]}, _LIST, _METRICS]),
        _doc(env=cleared, stmts=[_OPS_RO, {**_LIST, "prefix": _LIST["prefix"][:1]}, _METRICS]),
        _doc(env=cleared, stmts=[_LIST, _METRICS]),
        _doc(env=cleared, stmts=[_OPS_RO, _METRICS]),
    ]
    for doc in bad_docs:
        with pytest.raises(AssertionError):
            assert_shared_reader_carries_no_telemetry_state(doc)
