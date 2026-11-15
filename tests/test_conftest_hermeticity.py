"""Tests proving the global hermetic-AWS guard fixtures in tests/conftest.py (rec-2484).

L1 (_hermetic_aws_profile) and L2 (_block_unmocked_aws_client) are two independent,
defense-in-depth layers:

- L1 makes boto3.Session(profile_name=...) raise ProfileNotFound deterministically -- no real
  named profile or ambient credential env var can satisfy it, on dev or CI alike.
- L2 raises loudly on any un-mocked AWS client construction (boto3.client/resource/
  Session().client) that reaches past L1 -- e.g. the boto3 default credential chain, which
  does not require a named profile at all.

Each layer has its own opt-out marker: @pytest.mark.integration bypasses L1 only;
@pytest.mark.aws bypasses L2 only. A genuinely live-AWS integration test needs BOTH markers
to fully bypass both layers -- this is deliberate (rec-2484 acceptance criteria), not an
oversight.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import boto3
import botocore.exceptions
import pytest

from scripts.aws_profile import resolve_aws_profile


class TestL1ProfileHermeticity:
    """L1: boto3.Session(profile_name=...) raises ProfileNotFound deterministically."""

    def test_named_profile_raises_profile_not_found(self) -> None:
        with pytest.raises(botocore.exceptions.ProfileNotFound):
            boto3.Session(profile_name="agent_platform")

    def test_config_and_credentials_files_redirected_to_nonexistent_path(self) -> None:
        from pathlib import Path

        assert not Path(os.environ["AWS_CONFIG_FILE"]).exists()
        assert not Path(os.environ["AWS_SHARED_CREDENTIALS_FILE"]).exists()
        assert "nonexistent-aws-config-dir" in os.environ["AWS_CONFIG_FILE"]

    def test_credential_signal_env_vars_deleted(self) -> None:
        for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
            assert var not in os.environ

    def test_ec2_instance_metadata_disabled(self) -> None:
        assert os.environ.get("AWS_EC2_METADATA_DISABLED") == "true"

    def test_no_fake_access_key_set_named_profile_resolution_still_works(self) -> None:
        """The delete-credential-signals design never sets a fake AWS_ACCESS_KEY_ID -- that
        would flip scripts.aws_profile.resolve_aws_profile to None and silently break
        named-profile resolution (tests/test_aws_profile.py). This is the rec-2484 constraint
        that distinguishes L1 from a naive "set fake creds everywhere" approach."""
        assert "AWS_ACCESS_KEY_ID" not in os.environ
        assert resolve_aws_profile(default="agent_platform") == "agent_platform"

    @pytest.mark.integration
    def test_integration_marker_bypasses_l1(self) -> None:
        """Under @pytest.mark.integration, L1 must not touch AWS_CONFIG_FILE /
        AWS_SHARED_CREDENTIALS_FILE -- integration tests need real AWS access. The fixture's
        redirect always contains this literal directory segment; its absence proves the
        fixture's setenv call did not run for this node."""
        assert "nonexistent-aws-config-dir" not in os.environ.get("AWS_CONFIG_FILE", "")


class TestL2CreateClientTripwire:
    """L2: an un-mocked botocore create_client raises the tripwire loudly."""

    def test_unmocked_boto3_client_raises_tripwire(self) -> None:
        with pytest.raises(RuntimeError, match="without mocking it"):
            boto3.client("s3", region_name="eu-west-2")

    def test_unmocked_boto3_resource_raises_tripwire(self) -> None:
        with pytest.raises(RuntimeError, match="without mocking it"):
            boto3.resource("s3", region_name="eu-west-2")

    def test_unmocked_session_client_raises_tripwire(self) -> None:
        with pytest.raises(RuntimeError, match="without mocking it"):
            boto3.Session().client("s3", region_name="eu-west-2")

    @pytest.mark.aws
    def test_aws_marker_bypasses_l2_for_client_construction(self) -> None:
        """Under @pytest.mark.aws, client construction reaches real botocore instead of the
        tripwire -- a real client is built (construction alone makes no network call),
        proving the tripwire did not fire. A fresh boto3.Session() (not the boto3.client()
        module convenience, which lazily creates and reuses a single process-wide
        boto3.DEFAULT_SESSION) avoids any dependency on whichever earlier test in the full
        suite first realized that shared session's cached config, plus explicit dummy
        credentials avoid any real credential-chain resolution."""
        client = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing").client(
            "s3", region_name="eu-west-2"
        )
        assert client.meta.service_model.service_name == "s3"


@pytest.mark.integration
class TestClassLevelMarkerInheritance:
    """Both opt-out checks use request.node.get_closest_marker(...), not own_markers, so a
    class-level marker decorator (not just a method-level one) is honoured -- own_markers only
    sees markers applied directly to the test function and silently misses class/module-level
    ones (rec-575). A test suite that marks whole integration/aws classes (the common pattern,
    e.g. TestDuckLakeSpikeE2E in tests/test_ducklake_spike.py) depends on this."""

    def test_class_level_integration_marker_bypasses_l1(self) -> None:
        assert "nonexistent-aws-config-dir" not in os.environ.get("AWS_CONFIG_FILE", "")

    @pytest.mark.aws
    def test_class_level_integration_plus_method_level_aws_bypasses_both(self) -> None:
        """A live-AWS integration test needs both markers (L1 via the class, L2 via the
        method) to fully bypass both layers -- proven here by successfully constructing a
        client under a combined class+method marker. A fresh boto3.Session() plus explicit
        dummy credentials avoid any dependency on the process-wide cached default session's
        state (see test_aws_marker_bypasses_l2_for_client_construction above)."""
        client = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing").client(
            "s3", region_name="eu-west-2"
        )
        assert client.meta.service_model.service_name == "s3"


@pytest.mark.integration
class TestClassLevelMarkerInheritanceNetworkAndSubprocess:
    """VTS-22 (rec-575 closure): _allow_network_for_integration and _block_llm_cli_subprocess
    now also use request.node.get_closest_marker(...) instead of own_markers, so a class-level
    @pytest.mark.integration decorator is honoured for these two guards too -- completing the
    same fix already proven for the AWS pair above (TestClassLevelMarkerInheritance).

    A real connect() attempt is not a usable signal here: this repo's pyproject.toml sets
    --allow-hosts globally, so pytest_socket's OWN pytest_runtest_setup hook re-applies its
    host-based connect guard on every test unconditionally (regardless of any marker), which
    would make a connect-based assertion pass whether or not this fixture's own marker
    detection is correct -- a tautological check (verified empirically while designing this
    test: a bare socket connect to a disallowed host raises pytest_socket's
    SocketConnectBlockedError for a class-marked test exactly the same as for an unmarked one).
    The class-scoped spy fixture below instead observes the fixture's OWN branch directly:
    whether it calls pytest_socket.enable_socket() at all. Class scope is guaranteed by pytest
    to be instantiated before the root conftest's FUNCTION-scoped _allow_network_for_integration
    fixture (higher-scoped fixtures set up first), so the patch is reliably in place before that
    fixture's deferred `from pytest_socket import enable_socket` executes -- unlike same-scope
    autouse ordering, which pytest does not guarantee.
    """

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def _spy_enable_socket(cls):  # type: ignore[misc]
        """@classmethod (not a plain instance method) -- pytest instantiates a fresh test-class
        instance per test method, so a class-scoped fixture defined as an instance method is
        deprecated (PytestRemovedIn10Warning) since its `self` is only one of several
        short-lived per-test instances. This fixture doesn't need instance state at all (it only
        yields a plain list), so `cls` is unused beyond satisfying the classmethod signature."""
        import pytest_socket

        calls: list[bool] = []
        original = pytest_socket.enable_socket

        def _spy() -> None:
            calls.append(True)
            original()

        with patch("pytest_socket.enable_socket", side_effect=_spy):
            yield calls

    def test_class_level_integration_marker_calls_enable_socket(self, _spy_enable_socket: list[bool]) -> None:
        """A class- (not method-) level @pytest.mark.integration decorator must be honoured by
        _allow_network_for_integration's get_closest_marker check, so enable_socket() fires --
        own_markers would have silently missed the class decorator and never called it
        (rec-575; reproduced while designing this test: this assertion fails against the
        pre-fix own_markers check)."""
        assert _spy_enable_socket, "_allow_network_for_integration did not call enable_socket() under a class-level marker"

    def test_class_level_integration_marker_bypasses_llm_cli_guard(self) -> None:
        """A class-level @pytest.mark.integration decorator must exempt
        _block_llm_cli_subprocess's guard: subprocess.run for a stubbed LLM-CLI name ("gemini.CMD",
        chosen because it is guaranteed absent on PATH, unlike e.g. "claude" in this CLI's own dev
        environment) must reach the real subprocess.run (raising FileNotFoundError for a
        non-existent binary) rather than the guard's own RuntimeError."""
        with pytest.raises(FileNotFoundError):
            subprocess.run(["gemini.CMD"], capture_output=True)


class TestTransportVariableHermeticity:
    """PLAN-agent-proxy-signed-access: after cutover every agent environment exports
    OTL_OPS_VERBS_TRANSPORT, which would make the clients' Function URL nodes take the API path.
    The root conftest's _hermetic_aws_profile must delete it for every non-integration test.

    The class-scoped fixture is a @classmethod (as above) so it is set up before the root
    conftest's function-scoped fixtures; the class carries no integration marker, so the
    deletion applies and -m 'not integration' selects it."""

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def _set_transport_variable(cls):  # type: ignore[misc]
        with pytest.MonkeyPatch.context() as class_patch:
            class_patch.setenv("OTL_OPS_VERBS_TRANSPORT", "execute_api")
            yield

    def test_transport_selection_env_var_deleted(self) -> None:
        assert "OTL_OPS_VERBS_TRANSPORT" not in os.environ
