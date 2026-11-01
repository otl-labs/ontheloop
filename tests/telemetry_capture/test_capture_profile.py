"""make_client pins the capture writer client to PlatformDev (agent_platform) whatever AWS_PROFILE names."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from scripts.telemetry_capture import transport
from scripts.telemetry_capture.transport import Budget

URL = "https://abc.lambda-url.eu-west-2.on.aws"


def _fake_boto(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class Session:
        def __init__(self, botocore_session: Any, profile_name: Any) -> None:
            seen["profile"] = profile_name

        def get_credentials(self) -> Any:
            return types.SimpleNamespace(get_frozen_credentials=lambda: object())

        def client(self, name: str, region_name: str) -> Any:
            return types.SimpleNamespace(get_parameter=lambda **kw: {"Parameter": {"Value": URL}})

    class Config:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    class Core:
        def set_default_client_config(self, config: Any) -> None:
            return None

    class SigV4Auth:
        def __init__(self, creds: Any, service: str, region: str) -> None:
            return None

    modules = {
        "boto3": types.SimpleNamespace(Session=Session),
        "botocore.session": types.SimpleNamespace(get_session=Core),
        "botocore.auth": types.SimpleNamespace(SigV4Auth=SigV4Auth),
        "botocore.awsrequest": types.SimpleNamespace(AWSRequest=object),
        "botocore.config": types.SimpleNamespace(Config=Config),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen


class TestCaptureSignsAsPlatformDev:
    def test_admin_ambient_profile_is_not_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
        seen = _fake_boto(monkeypatch)
        transport.make_client({}, None, Budget.start(10.0))
        assert seen["profile"] == "agent_platform"

    def test_environment_keys_keep_the_ambient_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "k")
        monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
        seen = _fake_boto(monkeypatch)
        transport.make_client({}, None, Budget.start(10.0))
        assert seen["profile"] is None
