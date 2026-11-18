"""scripts/ci/telemetry_content_record.py: the record job's entry point (Decision 272 cl.2)."""

from __future__ import annotations

import base64
import json
import types
from unittest.mock import patch

import pytest

from scripts.ci import telemetry_content_record as rec

GOOD = base64.b64encode(b"d" * 32).decode()
SHA = "a" * 40


def _env(monkeypatch: pytest.MonkeyPatch, code: str, sha: str) -> None:
    monkeypatch.setenv("CODE_SHA256", code)
    monkeypatch.setenv("LAMBDA_ARTIFACT_SHA", sha)
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.delenv("GITHUB_SHA", raising=False)


@pytest.mark.parametrize("code", ["", "abc", "!!!!", base64.b64encode(b"short").decode()])
def test_bad_digest_is_refused_before_any_write(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    _env(monkeypatch, code, SHA)
    with patch("scripts.build_lambda_deploy.subprocess.run") as run:
        assert rec.main() == 2
    run.assert_not_called()


@pytest.mark.parametrize("sha", ["", "abc", "g" * 40, "A" * 40])
def test_bad_sha_is_refused_before_any_write(monkeypatch: pytest.MonkeyPatch, sha: str) -> None:
    _env(monkeypatch, GOOD, sha)
    with patch("scripts.build_lambda_deploy.subprocess.run") as run:
        assert rec.main() == 2
    run.assert_not_called()


def test_valid_pair_writes_one_telemetry_content_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, GOOD, SHA)
    with patch(
        "scripts.build_lambda_deploy.subprocess.run", return_value=types.SimpleNamespace(returncode=0, stdout="", stderr="")
    ) as run:
        assert rec.main() == 0
    assert run.call_count == 1
    argv = run.call_args.args[0]
    assert argv[:4] == ["aws", "s3", "cp", "-"]
    assert (
        argv[4]
        == "s3://agent-platform-data-lake/deploy-records/telemetry-content/agent-platform-ducklake-telemetry-content.json"
    )
    assert "--profile" not in argv
    record = json.loads(run.call_args.kwargs["input"])
    assert record["code_sha256"] == GOOD and record["source_git_sha"] == SHA
