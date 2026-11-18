"""scripts/ci/telemetry_content_deploy.sh: literal-argv tests with a fake aws on PATH (the Decision 162 R2 precedent)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "telemetry_content_deploy.sh"
CONFIG_REL = "src/lambdas/ducklake_telemetry_content_reader/deploy_config.json"
ZIP_BYTES = b"zip-bytes"
HEX = hashlib.sha256(ZIP_BYTES).hexdigest()
B64 = base64.b64encode(bytes.fromhex(HEX)).decode()
RECORD_B64 = base64.b64encode(b"r" * 32).decode()
CONFIG = {
    "handler": "h.handler",
    "layers": [
        {"name": "deps", "version": 3, "code_sha256": "PIN"},
        {"name": "ext", "version": 4, "code_sha256": "PIN"},
    ],
    "environment": {"A": "secret-looking-value", "B": "b"},
}
FAKE_AWS = r"""#!/usr/bin/env bash
echo "$*" >> "$AWS_LOG"
case "$1 $2" in
  "lambda get-function")
    if [ -f "$AWS_STATE" ]; then echo "$FAKE_LIVE_AFTER"; else echo "$FAKE_LIVE_BEFORE"; fi ;;
  "lambda get-layer-version")
    case "$*" in
      *LayerVersionArn*) echo "arn:fake:layer:$4:$6" ;;
      *) echo "$FAKE_LAYER_SHA" ;;
    esac ;;
  "lambda update-function-code") touch "$AWS_STATE" ;;
  "lambda get-function-configuration") cat "$FAKE_READBACK" ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(FAKE_AWS, encoding="utf-8")
    aws.chmod(aws.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / CONFIG_REL
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    (tmp_path / "z.zip").write_bytes(ZIP_BYTES)
    readback = tmp_path / "readback.json"
    readback.write_text(
        json.dumps(
            {
                "handler": "h.handler",
                "layers": ["arn:fake:layer:deps:3", "arn:fake:layer:ext:4"],
                "environment": CONFIG["environment"],
            }
        ),
        encoding="utf-8",
    )
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AWS_LOG": str(tmp_path / "aws.log"),
        "AWS_STATE": str(tmp_path / "updated"),
        "FAKE_LIVE_BEFORE": RECORD_B64,
        "FAKE_LIVE_AFTER": B64,
        "FAKE_LAYER_SHA": "PIN",
        "FAKE_READBACK": str(readback),
        "ZIP": "z.zip",
        "ACCOUNT_ID": "acctid",
        "CONTENT_SHA256": HEX,
        "RECORD_CODE_SHA256": RECORD_B64,
        "REBASELINE": "false",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
    }


def run(tmp_path: Path, env: dict[str, str], sub: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), sub], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def argv_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "aws.log").read_text(encoding="utf-8").splitlines()


def assert_failed_naming(result: subprocess.CompletedProcess[str], step: str) -> None:
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert f"step={step}" in combined
    for leaked in ("secret-looking-value", "acctid", "arn:fake"):
        assert leaked not in combined.replace("::add-mask::acctid", "")


def test_happy_path_configures_before_code_and_writes_the_output(tmp_path: Path, env: dict[str, str]) -> None:
    result = run(tmp_path, env, "deploy")
    assert result.returncode == 0, result.stderr
    log = argv_log(tmp_path)
    cfg = next(i for i, line in enumerate(log) if line.startswith("lambda update-function-configuration"))
    code = next(i for i, line in enumerate(log) if line.startswith("lambda update-function-code"))
    assert cfg < code
    assert "--layers arn:fake:layer:deps:3 arn:fake:layer:ext:4" in log[cfg]
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == f"code_sha256={B64}"
    assert "secret-looking-value" not in result.stdout + result.stderr
    assert "::add-mask::acctid" in result.stdout


def test_every_aws_call_carries_a_query(tmp_path: Path, env: dict[str, str]) -> None:
    run(tmp_path, env, "deploy")
    for line in argv_log(tmp_path):
        if line.startswith(("lambda wait",)):
            continue
        assert "--query" in line, line


def test_digest_mismatch_fails_naming_the_step(tmp_path: Path, env: dict[str, str]) -> None:
    env["CONTENT_SHA256"] = "0" * 64
    assert_failed_naming(run(tmp_path, env, "deploy"), "digest")
    assert not (tmp_path / "aws.log").exists()


def test_missing_account_id_fails_before_any_change(tmp_path: Path, env: dict[str, str]) -> None:
    del env["ACCOUNT_ID"]
    assert_failed_naming(run(tmp_path, env, "deploy"), "mask-account")
    assert not (tmp_path / "aws.log").exists()


def test_baseline_mismatch_fails_before_any_change(tmp_path: Path, env: dict[str, str]) -> None:
    env["FAKE_LIVE_BEFORE"] = "someone-else"
    assert_failed_naming(run(tmp_path, env, "deploy"), "baseline")
    assert not any(line.startswith("lambda update-function") for line in argv_log(tmp_path))


def test_rebaseline_skips_the_live_baseline_check(tmp_path: Path, env: dict[str, str]) -> None:
    env["FAKE_LIVE_BEFORE"] = "someone-else"
    env["REBASELINE"] = "true"
    assert run(tmp_path, env, "deploy").returncode == 0


def test_layer_pin_mismatch_fails_before_any_change(tmp_path: Path, env: dict[str, str]) -> None:
    env["FAKE_LAYER_SHA"] = "other"
    assert_failed_naming(run(tmp_path, env, "deploy"), "layer-pin")
    assert not any(line.startswith("lambda update-function") for line in argv_log(tmp_path))


@pytest.mark.parametrize("bad", [{"layers": []}, {"handler": None}, {"environment": []}])
def test_malformed_config_fails_before_any_change(tmp_path: Path, env: dict[str, str], bad: dict[str, object]) -> None:
    (tmp_path / CONFIG_REL).write_text(json.dumps({**CONFIG, **bad}), encoding="utf-8")
    assert_failed_naming(run(tmp_path, env, "deploy"), "config-shape")
    assert not any(line.startswith("lambda update-function") for line in argv_log(tmp_path))


def test_live_code_readback_mismatch_fails(tmp_path: Path, env: dict[str, str]) -> None:
    env["FAKE_LIVE_AFTER"] = RECORD_B64
    assert_failed_naming(run(tmp_path, env, "deploy"), "readback-code")


def test_configuration_readback_mismatch_fails_without_printing_values(tmp_path: Path, env: dict[str, str]) -> None:
    Path(env["FAKE_READBACK"]).write_text(
        json.dumps({"handler": "h.handler", "layers": ["arn:fake:layer:deps:3"], "environment": CONFIG["environment"]}),
        encoding="utf-8",
    )
    assert_failed_naming(run(tmp_path, env, "deploy"), "readback-configuration")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> dict[str, str]:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    shas = {}

    def commit(label: str, *files: str) -> None:
        for rel in files:
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{label}\n", encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", label)
        shas[label] = git(tmp_path, "rev-parse", "HEAD")

    commit("base", "README.md")
    commit("protective", "src/sensitive_values/engine.py")
    commit("bundled", "src/common/ducklake_runtime.py")
    commit("asset", "config/lambda/ducklake/field_semantics.yaml")
    commit("tooling", "scripts/build_lambda_packaging.py")
    commit("deploy-script", "scripts/ci/telemetry_content_deploy.sh")
    commit("shared-module-only", "src/common/ducklake_runtime.py")
    (tmp_path / "protective.txt").write_text(
        "src/sensitive_values\nsrc/common/ducklake_content_read_verbs.py\n", encoding="utf-8"
    )
    (tmp_path / "bundle.txt").write_text(
        "src/common/ducklake_runtime.py\nconfig/lambda/ducklake/field_semantics.yaml\nsrc/sensitive_values/engine.py\n",
        encoding="utf-8",
    )
    env.update(
        {
            "PROTECTIVE_PATHS_FILE": "protective.txt",
            "BUNDLE_PATHS_FILE": "bundle.txt",
            "GATE_SHA": "HEAD",
            "HEAD_SHA": shas["shared-module-only"],
            "RECORD_SHA": shas["base"],
            "RUN_URL": "https://example.invalid/run/1",
            "CONTENT_SHA256": HEX,
            "HELD": "false",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
    )
    return shas


def test_summary_leads_with_evidence_then_lists_every_going_out_commit(
    tmp_path: Path, env: dict[str, str], repo: dict[str, str]
) -> None:
    result = run(tmp_path, env, "summary")
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    evidence = text.index("### Why this asks you, and the evidence")
    going_out = text.index("### Every bundled commit going out")
    assert evidence < going_out and "Built digest" in text[evidence:going_out] and HEX in text
    bundled_section = text[going_out : text.index("### Build tooling going out")]
    assert "bundled" in bundled_section and "shared-module-only" in bundled_section and "asset" in bundled_section
    tooling = text[text.index("### Build tooling going out") : text.index("### Deploy path going out")]
    assert "tooling" in tooling and "bundled" not in tooling
    deploy_path = text[text.index("### Deploy path going out") : text.index("### deploy_config.json over the range")]
    assert "deploy-script" in deploy_path
    assert "CI publishes the layers: a pin proves which bytes run, not who built them (rec-4742)" in text
    assert "protective" in text[evidence:going_out]


def test_summary_of_a_first_deploy_needs_its_range_start(tmp_path: Path, env: dict[str, str], repo: dict[str, str]) -> None:
    env["RECORD_SHA"] = ""
    result = run(tmp_path, env, "summary")
    assert_failed_naming(result, "resolve-range-start")


def test_unknown_subcommand_names_a_step(tmp_path: Path, env: dict[str, str]) -> None:
    assert_failed_naming(run(tmp_path, env, "nope"), "usage")
