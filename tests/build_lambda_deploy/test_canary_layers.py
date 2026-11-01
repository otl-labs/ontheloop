"""Tests for scripts/build_lambda_deploy.py -- canary-layer publishing + ducklake-profile
passthrough concern (VERBATIM split from tests/test_build_lambda_deploy.py, rec-2709 Wave 12).
"""

import types
from unittest.mock import patch

import pytest

import scripts.build_lambda as bl
import scripts.build_lambda_deploy as bd

pytestmark = pytest.mark.unit


class _FakePath:
    """Minimal Path double exposing .name + .stat().st_size for size-assert tests."""

    def __init__(self, size=100, name="x.zip"):
        self._size = size
        self.name = name

    def stat(self):
        return types.SimpleNamespace(st_size=self._size)


def _args(**kw):
    import argparse

    ns = argparse.Namespace(
        skip_upload=True,
        bucket="",
        profile="agent_platform",
        region="eu-west-2",
        deploy=False,
        ducklake_only=False,
        list_bundle=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestDucklakeBuildProfile:
    def test_ducklake_build_passes_profile_through(self):
        """_run_ducklake_build (the orchestrator; still defined in scripts.build_lambda) passes the
        profile to the DuckLake builders unchanged -- exercised via the facade since the orchestrator
        itself does not move."""
        captured = {}
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch(
                "scripts.build_lambda.build_ducklake_function_package",
                side_effect=[
                    _FakePath(name="w.zip"),
                    _FakePath(name="r.zip"),
                    _FakePath(name="m.zip"),
                    _FakePath(name="ms.zip"),
                    _FakePath(name="dr.zip"),
                    _FakePath(name="tw.zip"),
                    _FakePath(name="trd.zip"),
                ],
            ),
            patch("scripts.build_lambda.build_ducklake_deps_layer", return_value=_FakePath(name="deps.zip")),
            patch(
                "scripts.build_lambda.build_ducklake_extensions_layer",
                side_effect=lambda td, **kw: captured.update(kw) or _FakePath(name="ext.zip"),
            ),
            patch("scripts.build_lambda.build_pgclient_layer", return_value=_FakePath(name="pgclient.zip")),
            patch("scripts.build_lambda.assert_within_size_limit"),
        ):
            bl._run_ducklake_build(_args(skip_upload=True, profile="agent_platform"))
        assert captured["profile"] == "agent_platform"


class TestPublishCanaryLayers:
    """Unit tests for publish_canary_layers() -- mocked aws lambda publish-layer-version."""

    def _arn_response(self, layer_name: str) -> str:
        import json

        return json.dumps(
            {
                "LayerVersionArn": f"arn:aws:lambda:eu-west-2:ACCOUNT_ID_PLACEHOLDER:layer:{layer_name}:99",
                "Version": 99,
            }
        )

    def test_publishes_all_three_layers_returns_arns(self):
        call_args_list = []

        def fake_run(cmd, **kw):
            layer_name = cmd[cmd.index("--layer-name") + 1]
            call_args_list.append(layer_name)
            return types.SimpleNamespace(returncode=0, stdout=self._arn_response(layer_name), stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            arns = bd.publish_canary_layers(bucket="my-bucket", profile="agent_platform", region="eu-west-2")

        assert set(arns.keys()) == {"ducklake-deps-layer", "ducklake-extensions-layer", "ducklake-pgclient-layer"}
        for name, arn in arns.items():
            assert arn == f"arn:aws:lambda:eu-west-2:ACCOUNT_ID_PLACEHOLDER:layer:{name}:99"
        assert set(call_args_list) == {"ducklake-deps-layer", "ducklake-extensions-layer", "ducklake-pgclient-layer"}

    def test_prints_json_arns_to_stdout(self, capsys):
        def fake_run(cmd, **kw):
            layer_name = cmd[cmd.index("--layer-name") + 1]
            return types.SimpleNamespace(returncode=0, stdout=self._arn_response(layer_name), stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd.publish_canary_layers(bucket="b", profile="p", region="r")

        captured = capsys.readouterr().out
        import json

        json_line = [ln for ln in captured.splitlines() if ln.startswith("{")]
        assert json_line, "Expected a JSON line in stdout"
        data = json.loads(json_line[-1])
        assert "ducklake-deps-layer" in data

    def test_exits_on_publish_failure(self):
        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="AccessDenied")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit):
                bd.publish_canary_layers(bucket="b", profile="p", region="r")

    def test_uses_correct_s3_key_per_layer(self):
        seen_keys = []

        def fake_run(cmd, **kw):
            content_arg = cmd[cmd.index("--content") + 1]
            seen_keys.append(content_arg)
            layer_name = cmd[cmd.index("--layer-name") + 1]
            return types.SimpleNamespace(returncode=0, stdout=self._arn_response(layer_name), stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd.publish_canary_layers(bucket="my-bucket", profile="p", region="r")

        for key in seen_keys:
            assert "my-bucket" in key
            assert key.startswith("S3Bucket=my-bucket,S3Key=lambda-packages/")

    def test_canary_layers_sourced_from_accessor(self):
        """publish_canary_layers iterates _build_ducklake_layer_names(), not DUCKLAKE_LAYER_NAMES directly.

        Relocated from TestBuildLambdaContract (tests/test_build_lambda.py:902, critique finding B):
        publish_canary_layers now resolves _build_ducklake_layer_names in ITS OWN (deploy) module
        namespace, so the patch target is the string-form scripts.build_lambda_deploy accessor --
        an attribute-object-style patch against the old facade alias would silently no-op post-split.
        """
        import json

        fake_layers = ["layer-a", "layer-b"]
        call_log: list[str] = []

        def fake_run(cmd, **kw):
            layer_name = cmd[cmd.index("--layer-name") + 1]
            call_log.append(layer_name)
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"LayerVersionArn": f"arn:aws:lambda:::layer:{layer_name}:1", "Version": 1}),
                stderr="",
            )

        with (
            patch("scripts.build_lambda_deploy._build_ducklake_layer_names", return_value=fake_layers),
            patch("scripts.build_lambda.subprocess.run", side_effect=fake_run),
        ):
            bd.publish_canary_layers(bucket="b", profile="p", region="r")
        assert call_log == fake_layers
