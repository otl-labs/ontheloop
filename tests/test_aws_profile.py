"""Tests for scripts.aws_profile.resolve_aws_profile.

The helper decides between a named ~/.aws/config profile (local / web dev) and
the boto3 default credential chain (Lambda execution role; GitHub-hosted OIDC
runner, which exports AWS_ACCESS_KEY_ID into the environment).
"""

from __future__ import annotations

import os
from unittest import mock

from scripts.aws_profile import resolve_aws_profile

_CREDENTIAL_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME", "AWS_PROFILE", "AWS_CONFIG_FILE")


def _env(**overrides: str) -> dict[str, str]:
    """A copy of os.environ with the credential-signal keys cleared, plus overrides."""
    base = dict(os.environ)
    for key in _CREDENTIAL_ENV_KEYS:
        base.pop(key, None)
    base.update(overrides)
    return base


def test_returns_none_when_oidc_env_credentials_present():
    with mock.patch.dict(os.environ, _env(AWS_ACCESS_KEY_ID="AKIAEXAMPLE"), clear=True):
        assert resolve_aws_profile(default="agent_platform") is None


def test_returns_none_in_lambda():
    with mock.patch.dict(os.environ, _env(AWS_LAMBDA_FUNCTION_NAME="some-fn"), clear=True):
        assert resolve_aws_profile(default="agent_platform") is None


def test_returns_named_default_when_no_env_credentials():
    with mock.patch.dict(os.environ, _env(), clear=True):
        assert resolve_aws_profile(default="agent_platform") == "agent_platform"


def test_aws_profile_env_takes_precedence_locally():
    with mock.patch.dict(os.environ, _env(AWS_PROFILE="custom-profile"), clear=True):
        assert resolve_aws_profile(default="agent_platform") == "custom-profile"


def test_explicit_override_used_locally():
    with mock.patch.dict(os.environ, _env(), clear=True):
        assert resolve_aws_profile("explicit-profile", default="agent_platform") == "explicit-profile"


def test_env_credentials_short_circuit_explicit_override():
    # On a hosted runner a named profile does not exist; ambient env creds must win
    # even if a caller passes an explicit profile.
    with mock.patch.dict(os.environ, _env(AWS_ACCESS_KEY_ID="AKIAEXAMPLE"), clear=True):
        assert resolve_aws_profile("explicit-profile", default="agent_platform") is None


_SELF_SUFFICIENT = "[profile agent_platform]\nregion = eu-west-2\ncredential_process = /x/placeholder\n"


def _with_config(tmp_path, text: str | None, **overrides: str) -> dict[str, str]:
    path = tmp_path / "config"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return _env(AWS_CONFIG_FILE=str(path), **overrides)


def test_proxy_placeholder_env_credentials_keep_a_self_sufficient_profile(tmp_path):
    for akid in ("proxy-injected", "any-other-placeholder"):
        with mock.patch.dict(os.environ, _with_config(tmp_path, _SELF_SUFFICIENT, AWS_ACCESS_KEY_ID=akid), clear=True):
            assert resolve_aws_profile() == "agent_platform"
            assert resolve_aws_profile("agent_platform") == "agent_platform"


def test_env_credentials_with_a_profile_lacking_credential_process_use_the_default_chain(tmp_path):
    for text in (
        None,
        "[profile other]\ncredential_process = /x\n",
        "[profile agent_platform]\nregion = eu-west-2\n",
        "not ini [",
    ):
        with mock.patch.dict(os.environ, _with_config(tmp_path, text, AWS_ACCESS_KEY_ID="AKIAEXAMPLE"), clear=True):
            assert resolve_aws_profile() is None


def test_lambda_never_takes_a_profile_even_with_a_config(tmp_path):
    with mock.patch.dict(os.environ, _with_config(tmp_path, _SELF_SUFFICIENT, AWS_LAMBDA_FUNCTION_NAME="fn"), clear=True):
        assert resolve_aws_profile() is None


def test_name_precedence_is_checked_against_the_config_under_env_credentials(tmp_path):
    text = _SELF_SUFFICIENT + "[profile custom]\ncredential_process = /y\n[default]\ncredential_process = /z\n"
    env = _with_config(tmp_path, text, AWS_ACCESS_KEY_ID="proxy-injected", AWS_PROFILE="custom")
    with mock.patch.dict(os.environ, env, clear=True):
        assert resolve_aws_profile() == "custom"
        assert resolve_aws_profile("agent_platform") == "agent_platform"
        assert resolve_aws_profile("absent") is None
    with mock.patch.dict(os.environ, _with_config(tmp_path, text, AWS_ACCESS_KEY_ID="proxy-injected"), clear=True):
        assert resolve_aws_profile(default="default") == "default"


def test_config_defaults_to_the_home_file_and_an_unreadable_path_counts_as_absent(tmp_path):
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "config").write_text(_SELF_SUFFICIENT, encoding="utf-8")
    with mock.patch.dict(os.environ, _env(HOME=str(tmp_path), AWS_ACCESS_KEY_ID="proxy-injected"), clear=True):
        assert resolve_aws_profile() == "agent_platform"
    with mock.patch.dict(os.environ, _env(AWS_CONFIG_FILE=str(tmp_path), AWS_ACCESS_KEY_ID="proxy-injected"), clear=True):
        assert resolve_aws_profile() is None
