"""secrets_to_ssm_copy: stubbed clients only -- never prints a value, never overwrites silently, verify is read-only."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "secrets_to_ssm_copy.py"
_spec = importlib.util.spec_from_file_location("secrets_to_ssm_copy", _PATH)
assert _spec is not None and _spec.loader is not None
copy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copy)

_VALUE = "value-for-{}-MARKER"  # pragma: allowlist secret


class _NotFound(Exception):
    response = {"Error": {"Code": "ParameterNotFound"}}


class _SM:
    def __init__(self, big: set[str] | None = None, broken: set[str] | None = None) -> None:
        self.big, self.broken = big or set(), broken or set()

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803
        if SecretId in self.broken:
            raise RuntimeError("boom " + _VALUE.format(SecretId))
        return {"SecretString": "x" * 4097 if SecretId in self.big else _VALUE.format(SecretId)}


class _SSM:
    exceptions = SimpleNamespace(ParameterNotFound=_NotFound)

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.store = dict(existing or {})
        self.puts: list[dict[str, Any]] = []

    def get_parameter(self, Name: str, WithDecryption: bool) -> dict[str, Any]:  # noqa: N803
        assert WithDecryption
        if Name not in self.store:
            raise _NotFound()
        return {"Parameter": {"Value": self.store[Name]}}

    def put_parameter(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)
        self.store[kwargs["Name"]] = kwargs["Value"]


def _run(args: list[str], sm: _SM, ssm: _SSM, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = copy.main(args, clients=(sm, ssm))
    return code, capsys.readouterr().out


def _all_present() -> dict[str, str]:
    return {f"/secret/{n}": _VALUE.format(n) for n in copy.SOURCE_NAMES}


def test_creates_all_eight_without_printing_a_value(capsys: pytest.CaptureFixture[str]) -> None:
    ssm = _SSM()
    code, out = _run([], _SM(), ssm, capsys)
    assert code == 0 and "SECRETS_TO_SSM_COPY_OK 8/8" in out and len(ssm.puts) == 8
    assert "MARKER" not in out
    first = ssm.puts[0]
    assert first["Type"] == "SecureString" and first["Overwrite"] is False and "KeyId" not in first and first["Tags"]


def test_unchanged_makes_no_write(capsys: pytest.CaptureFixture[str]) -> None:
    ssm = _SSM(_all_present())
    code, out = _run([], _SM(), ssm, capsys)
    assert code == 0 and ssm.puts == [] and out.count("unchanged") == 8


def test_mismatch_without_overwrite_fails_and_does_not_write(capsys: pytest.CaptureFixture[str]) -> None:
    existing = _all_present()
    existing["/secret/neon-api-key"] = "different"
    ssm = _SSM(existing)
    code, out = _run([], _SM(), ssm, capsys)
    assert code == 1 and ssm.puts == [] and "/secret/neon-api-key mismatch" in out and "SECRETS_TO_SSM_COPY_FAIL 1" in out


def test_overwrite_replaces_without_tags(capsys: pytest.CaptureFixture[str]) -> None:
    existing = _all_present()
    existing["/secret/neon-api-key"] = "different"
    ssm = _SSM(existing)
    code, out = _run(["--overwrite"], _SM(), ssm, capsys)
    assert code == 0 and "overwritten" in out
    assert len(ssm.puts) == 1 and ssm.puts[0]["Overwrite"] is True and "Tags" not in ssm.puts[0]
    assert "Tier" not in ssm.puts[0]


def test_tier_switches_above_4096_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    ssm = _SSM()
    _, out = _run([], _SM(big={"neon-api-key"}), ssm, capsys)
    assert "/secret/neon-api-key created tier=Advanced" in out
    assert "/secret/agent-platform-deepseek-api-key created tier=Standard" in out


def test_verify_never_writes(capsys: pytest.CaptureFixture[str]) -> None:
    ssm = _SSM()
    code, out = _run(["--verify"], _SM(), ssm, capsys)
    assert code == 1 and ssm.puts == [] and out.count("absent") == 8
    ssm = _SSM(_all_present())
    code, out = _run(["--verify"], _SM(), ssm, capsys)
    assert code == 0 and ssm.puts == []


def test_exception_prints_class_and_name_only(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run([], _SM(broken={"neon-api-key"}), _SSM(), capsys)
    assert code == 1 and "/secret/neon-api-key error=RuntimeError" in out and "MARKER" not in out and "boom" not in out


def test_no_sts_client_and_shebang() -> None:
    text = _PATH.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3\n")
    assert '"sts"' not in text and "'sts'" not in text
