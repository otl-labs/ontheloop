"""restore_rehearsal_aws (rec-5023): secret transport, create_function shape, invoke client, timeouts, scratch guards."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.ducklake_smoke import restore_rehearsal_aws as aws
from scripts.ducklake_smoke import restore_rehearsal_evidence as ev

pytestmark = pytest.mark.unit

RUN = "20261109t193000_a1b2c3"


class _ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def _sess(**clients: Any) -> MagicMock:
    s = MagicMock()
    s.client.side_effect = lambda name, **kw: clients[name]
    return s


def _pager(pages: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = pages
    return client


# ------------------------------------------------------------------ keys and scratch guards


def test_scratch_key_stays_inside_the_run_prefix_and_refuses_traversal():
    assert aws.scratch_key(RUN, "ducklake/a") == f"ducklake-neon-smoke/restore-rehearsal/{RUN}/ducklake/a"
    assert aws.scratch_key(RUN, "/x") == f"ducklake-neon-smoke/restore-rehearsal/{RUN}/x"
    with pytest.raises(aws.RehearsalError):
        aws.scratch_key(RUN, "../ducklake/a")


def test_function_names_never_start_with_agent_platform():
    assert not aws.RESTORE_FN.startswith("agent-platform-") and not aws.CHECKSUM_FN.startswith("agent-platform-")
    assert aws.RESTORE_FN.endswith("-restore-rehearsal-ephemeral") and aws.CHECKSUM_FN.endswith("-restore-rehearsal-ephemeral")


def test_session_passes_profile_and_region():
    boto3 = MagicMock()
    with patch.dict("sys.modules", {"boto3": boto3}):
        aws.session("prof", "eu-west-2")
    boto3.Session.assert_called_once_with(profile_name="prof", region_name="eu-west-2")


# ------------------------------------------------------------------ reads


def test_read_deploy_record_requires_both_shas():
    body = json.dumps({"source_git_sha": "abc", "code_sha256": "x="}).encode()
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(body)}
    assert aws.read_deploy_record(_sess(s3=s3))["source_git_sha"] == "abc"
    s3.get_object.return_value = {"Body": io.BytesIO(json.dumps({"source_git_sha": None, "code_sha256": "x"}).encode())}
    with pytest.raises(aws.RehearsalError):
        aws.read_deploy_record(_sess(s3=s3))


def test_function_config_reads_only_the_needed_fields():
    lam = MagicMock()
    lam.get_function_configuration.return_value = {
        "CodeSha256": "sha",
        "Role": "role-arn",
        "MemorySize": 1536,
        "Layers": [{"Arn": "l1"}, {"Arn": "l2"}],
        "Environment": {"Variables": {"SECRET": "x"}},
    }
    cfg = aws.function_config(_sess(lambda_=None, **{"lambda": lam}), "fn")
    assert cfg == {"CodeSha256": "sha", "Role": "role-arn", "MemorySize": 1536, "Layers": ["l1", "l2"]}
    lam.get_function_configuration.return_value = {"CodeSha256": "s", "Role": "r", "MemorySize": 1}
    assert aws.function_config(_sess(**{"lambda": lam}), "fn")["Layers"] == []


def test_function_exists_and_url():
    lam = MagicMock()
    assert aws.function_exists(_sess(**{"lambda": lam}), "fn") is True
    lam.get_function.side_effect = _ClientError("ResourceNotFoundException")
    assert aws.function_exists(_sess(**{"lambda": lam}), "fn") is False
    lam.get_function.side_effect = _ClientError("AccessDenied")
    with pytest.raises(_ClientError):
        aws.function_exists(_sess(**{"lambda": lam}), "fn")
    lam.get_function_url_config.return_value = {"FunctionUrl": "https://u.lambda-url.example/"}
    assert aws.function_url(_sess(**{"lambda": lam}), "fn") == "https://u.lambda-url.example"


def test_leftover_branches_matches_the_rehearsal_prefix_only():
    with patch.object(
        aws.neon_api, "list_branches", return_value=[{"id": "1", "name": "main"}, {"id": "2", "name": "restore_rehearsal_x"}]
    ):
        assert aws.leftover_branches("k", "p") == ["restore_rehearsal_x"]


def test_scratch_objects_and_prefix_size():
    s3 = _pager([{"Contents": [{"Key": "a", "Size": 3}, {"Key": "b", "Size": 4}]}, {}])
    dev = _sess(s3=s3)
    assert aws.scratch_objects(dev) == ["a", "b"]
    assert s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == "ducklake-neon-smoke/restore-rehearsal/"
    aws.scratch_objects(dev, RUN)
    assert s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == aws.scratch_prefix(RUN)
    assert aws.prefix_size(dev, "ducklake/") == {"objects": 2, "bytes": 7}


def test_copy_prefix_copies_only_into_the_scratch_prefix_and_counts():
    s3 = _pager([{"Contents": [{"Key": "ducklake/a", "Size": 3}, {"Key": "ducklake/b", "Size": 4}]}])
    out = aws.copy_prefix(_sess(s3=s3), RUN)
    assert out["objects"] == 2 and out["bytes"] == 7 and out["ms"] >= 0
    dests = {c.args[2] for c in s3.copy.call_args_list}
    assert dests == {aws.scratch_key(RUN, "ducklake/a"), aws.scratch_key(RUN, "ducklake/b")}
    assert all(c.args[0]["Bucket"] == aws.BUCKET and c.args[1] == aws.BUCKET for c in s3.copy.call_args_list)


def test_head_exists():
    s3 = MagicMock()
    assert aws.head_exists(_sess(s3=s3), "k") is True
    for code in ("404", "NoSuchKey", "NotFound"):
        s3.head_object.side_effect = _ClientError(code)
        assert aws.head_exists(_sess(s3=s3), "k") is False
    s3.head_object.side_effect = _ClientError("AccessDenied")
    with pytest.raises(_ClientError):
        aws.head_exists(_sess(s3=s3), "k")


def test_delete_scratch_deletes_only_through_the_key_builder():
    prefix = aws.scratch_prefix(RUN)
    s3 = _pager([{"Contents": [{"Key": prefix + "ducklake/a", "Size": 1}]}])
    assert aws.delete_scratch(_sess(s3=s3), RUN) == 1
    s3.delete_object.assert_called_once_with(Bucket=aws.BUCKET, Key=prefix + "ducklake/a")


# ------------------------------------------------------------------ dump and presign


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock(status_code=status)
    r.json.return_value = body or {}
    return r


def _admin_with_creds() -> MagicMock:
    s = MagicMock()
    s.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock(
        access_key="AK",
        secret_key="SK",  # pragma: allowlist secret
        token=None,
    )
    return s


def test_dump_posts_signed_with_timeout_330_and_registers_the_bucket():
    body = {"ok": True, "s3_key": "catalog-dr/x.dump", "bucket": "dr-bucket-xyz", "dump_bytes": 5}
    with patch("requests.post", return_value=_resp(200, body)) as post:
        out = aws.dump_catalog(_admin_with_creds(), "https://u.lambda-url.example")
    assert post.call_args.kwargs["timeout"] == 330 and "Authorization" in post.call_args.kwargs["headers"]
    assert out["dump_bytes"] == 5 and "dr-bucket-xyz" in ev.BUCKET_LITERALS
    ev.BUCKET_LITERALS.remove("dr-bucket-xyz")


def test_dump_failures_are_loud():
    with patch("requests.post", return_value=_resp(500)):
        with pytest.raises(aws.RehearsalError, match="status 500"):
            aws.dump_catalog(_admin_with_creds(), "https://u")
    with patch("requests.post", return_value=_resp(200, {"ok": True})):
        with pytest.raises(aws.RehearsalError, match="lacked"):
            aws.dump_catalog(_admin_with_creds(), "https://u")


def test_presign_is_fifteen_minutes_and_registered_as_a_secret():
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://b.s3.amazonaws.com/k?X-Amz-Signature=zzz"
    url = aws.presign_dump(_sess(s3=s3), "dr-bucket", "catalog-dr/x.dump")
    kwargs = s3.generate_presigned_url.call_args
    assert kwargs.kwargs["ExpiresIn"] == 900 and kwargs.args[0] == "get_object"
    assert ev.redact(f"leak {url}") == "leak [redacted]"
    ev._secrets.discard(url)


# ------------------------------------------------------------------ Neon target


def test_create_target_returns_dsn_only_in_memory_and_registers_secrets():
    seen: list[str] = []
    with (
        patch.object(aws.neon_api, "create_branch", return_value={"branch_id": "br-1", "host": "ep-x.neon.example"}) as cb,
        patch.object(aws.neon_api, "create_role", return_value="pw-zzz-fake") as cr,  # pragma: allowlist secret
        patch.object(aws.neon_api, "create_database") as cd,
    ):
        out = aws.create_target("key", "proj", RUN, on_branch=seen.append)
    name = f"restore_rehearsal_{RUN}"
    cb.assert_called_once_with("key", "proj", name=name)
    cr.assert_called_once_with("key", "proj", "br-1", name)
    cd.assert_called_once_with("key", "proj", "br-1", name, name)
    assert seen == ["br-1"] and out["branch_id"] == "br-1"
    assert out["dsn_uri"].startswith(f"postgresql://{name}:") and "pw-zzz-fake" in out["dsn_uri"]
    assert "pw-zzz-fake" not in ev.redact(out["dsn_uri"])
    for s in ("pw-zzz-fake", out["dsn_uri"], "ep-x.neon.example"):
        ev._secrets.discard(s)


def test_create_target_without_callback():
    with (
        patch.object(aws.neon_api, "create_branch", return_value={"branch_id": "b", "host": "h.neon.example"}),
        patch.object(aws.neon_api, "create_role", return_value="p"),
        patch.object(aws.neon_api, "create_database"),
    ):
        assert aws.create_target("k", "p", RUN)["branch_id"] == "b"
    for s in ("p", "h.neon.example"):
        ev._secrets.discard(s)
    ev._secrets.clear()


# ------------------------------------------------------------------ functions and invoke


def test_create_function_settings_environment_and_concurrency():
    lam = MagicMock()
    lam.create_function.return_value = {"CodeSha256": "sha", "Environment": {"Variables": {"X": "leak"}}}
    with patch.object(aws.canary, "_wait_function_active") as wait:
        sha = aws.create_function(
            _sess(**{"lambda": lam}),
            aws.RESTORE_FN,
            zip_key="lambda-packages/s/ducklake-maintenance.zip",
            role="restore-role-arn",
            layers=["l1"],
            memory=1536,
            env={"DUCKLAKE_RESTORE_TARGET_DSN": "uri"},
            profile="admin",
        )
    kw = lam.create_function.call_args.kwargs
    assert sha == "sha"
    assert kw["EphemeralStorage"] == {"Size": 10240} and kw["Timeout"] == 900 and kw["MemorySize"] == 1536
    assert kw["Runtime"] == "python3.12" and kw["Handler"] == "src.lambdas.ducklake_maintenance.handler.handler"
    assert kw["Architectures"] == ["x86_64"] and kw["Role"] == "restore-role-arn" and kw["Layers"] == ["l1"]
    assert kw["Code"] == {"S3Bucket": aws.BUCKET, "S3Key": "lambda-packages/s/ducklake-maintenance.zip"}
    assert kw["Environment"] == {"Variables": {"DUCKLAKE_RESTORE_TARGET_DSN": "uri"}}
    lam.put_function_concurrency.assert_called_once_with(FunctionName=aws.RESTORE_FN, ReservedConcurrentExecutions=1)
    wait.assert_called_once_with(aws.RESTORE_FN, profile="admin", region="eu-west-2")


def test_invoker_has_960_read_timeout_and_one_attempt():
    admin = MagicMock()
    aws.invoker(admin)
    cfg = admin.client.call_args.kwargs["config"]
    assert cfg.read_timeout == 960 and cfg.retries["total_max_attempts"] == 1


def _invoke_client(payload: Any = None, *, function_error: str | None = None, raises: Exception | None = None) -> MagicMock:
    client = MagicMock()
    if raises:
        client.invoke.side_effect = raises
    else:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        resp: dict[str, Any] = {"Payload": io.BytesIO(raw)}
        if function_error:
            resp["FunctionError"] = function_error
        client.invoke.return_value = resp
    return client


def test_invoke_step_unwraps_the_function_url_envelope():
    envelope = {"statusCode": 200, "body": json.dumps({"ok": True, "step": "verify"})}
    assert aws.invoke_step(_invoke_client(envelope), "fn", {"step": "verify"}) == {"ok": True, "step": "verify"}
    assert aws.invoke_step(_invoke_client({"statusCode": 200, "body": {"ok": 1}}), "fn", {"step": "s"}) == {"ok": 1}
    assert aws.invoke_step(_invoke_client({"ok": True}), "fn", {"step": "s"}) == {"ok": True}


def test_invoke_step_status_other_than_200_is_an_error_without_the_body():
    envelope = {"statusCode": 400, "body": json.dumps({"error": "postgresql://u:p@h/d"})}
    with pytest.raises(aws.RehearsalError) as info:
        aws.invoke_step(_invoke_client(envelope), "fn", {"step": "restore"})
    assert "postgresql" not in str(info.value) and "400" in str(info.value)


def test_a_timeout_function_error_is_a_limit_not_a_step_error():
    with pytest.raises(aws.LimitHit) as info:
        aws.invoke_step(
            _invoke_client(b'{"errorMessage": "Task timed out after 900.00 seconds"}', function_error="Unhandled"),
            "fn",
            {"step": "restore"},
        )
    assert info.value.step == "restore" and info.value.limit == "lambda_timeout_900s"


def test_a_client_read_timeout_is_a_limit_and_other_errors_are_redacted():
    class ReadTimeoutError(Exception):
        pass

    with pytest.raises(aws.LimitHit):
        aws.invoke_step(_invoke_client(raises=ReadTimeoutError("host-secret")), "fn", {"step": "restore"})
    with pytest.raises(aws.RehearsalError) as info:
        aws.invoke_step(_invoke_client(raises=RuntimeError("host-secret.example")), "fn", {"step": "rewrite"})
    assert "host-secret" not in str(info.value)


def test_other_function_error_carries_no_payload():
    with pytest.raises(aws.RehearsalError) as info:
        aws.invoke_step(
            _invoke_client(b'{"errorMessage": "boom host-secret"}', function_error="Unhandled"), "fn", {"step": "s"}
        )
    assert "host-secret" not in str(info.value)


def test_delete_function_confirmed_counts_resource_not_found_as_torn_down():
    lam = MagicMock()
    with patch.object(aws.canary, "_canary_delete_function", return_value=True):
        assert aws.delete_function_confirmed(_sess(**{"lambda": lam}), "fn", profile="p") is True
    lam.get_function.side_effect = _ClientError("ResourceNotFoundException")
    with patch.object(aws.canary, "_canary_delete_function", return_value=False):
        assert aws.delete_function_confirmed(_sess(**{"lambda": lam}), "fn", profile="p") is True
    lam.get_function.side_effect = None
    with patch.object(aws.canary, "_canary_delete_function", return_value=False):
        assert aws.delete_function_confirmed(_sess(**{"lambda": lam}), "fn", profile="p") is False
