"""Tests for bin/aws-proxy-placeholder-credentials (credential_process placeholder)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import botocore.session
import pytest

SCRIPT = Path("bin/aws-proxy-placeholder-credentials")
PLACEHOLDER_FIELDS = (("AccessKeyId", "proxy-injected-key-id"), ("SecretAccessKey", "proxy-injected-secret"))


def _run() -> dict:
    out = subprocess.run([str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def test_prints_exact_credential_process_json() -> None:
    payload = _run()
    assert payload["Version"] == 1
    for field, expected in PLACEHOLDER_FIELDS:
        assert payload[field] == expected
    assert set(payload) == {"Version", *(field for field, _ in PLACEHOLDER_FIELDS)}


def test_values_are_not_key_shaped() -> None:
    raw = SCRIPT.read_text(encoding="utf-8") + json.dumps(_run())
    assert not re.search(r"(AKIA|ASIA|AIDA)[A-Z0-9]{8,}", raw)


def test_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111


def test_botocore_resolves_profile_through_credential_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.write_text(
        f"[profile agent_static]\nregion = eu-west-2\ncredential_process = {SCRIPT.resolve()}\n", encoding="utf-8"
    )
    empty = tmp_path / "credentials"
    empty.write_text("", encoding="utf-8")
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty))
    creds = botocore.session.Session(profile="agent_static").get_credentials()
    assert creds is not None
    assert creds.access_key == dict(PLACEHOLDER_FIELDS)["AccessKeyId"]
