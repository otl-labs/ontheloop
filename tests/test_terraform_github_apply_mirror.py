"""Tests for scripts/ci/terraform_github_apply_mirror.sh (rec-5005, Decision 248 cl.4 Update)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "terraform_github_apply_mirror.sh"
LOCK = ROOT / "terraform" / "github" / ".terraform.lock.hcl"
PREFIX = "s3://agent-platform-data-lake/tf-provider-mirror/"
ADDRESS = "registry.terraform.io/integrations/github"
ACCOUNT = "123456789012"
ARN = f"arn:aws:iam::{ACCOUNT}:role/synthetic"

_AWS = """#!/usr/bin/env bash
echo "$*" >> "$STUB_ARGV"
case "${STUB_MODE:-ok}" in
    ok) printf 'PK-package' > "$4" ;;
    fail) exit 1 ;;
    timeout) exit 124 ;;
    empty) : > "$4" ;;
    denied) echo "An error occurred (AccessDenied): not authorized to perform s3:GetObject on $STUB_ARN" >&2; exit 1 ;;
esac
exit 0
"""
_UNAME = """#!/usr/bin/env bash
echo "${STUB_UNAME:-Linux x86_64}"
"""
_SHA = """#!/usr/bin/env bash
if [ "${SHA_MODE:-lock}" = lock ]; then echo "$STUB_LOCK_HASH  $1"; else echo "$(printf 'f%.0s' {1..64})  $1"; fi
"""


def _lock_hashes() -> list[str]:
    text = LOCK.read_text(encoding="utf-8")
    block = text.split('provider "registry.terraform.io/integrations/github"', 1)[1].split("\n}", 1)[0]
    return re.findall(r'"zh:([0-9a-f]{64})"', block)


def _version() -> str:
    m = re.search(r'provider "registry.terraform.io/integrations/github" \{\s+version\s+=\s+"([^"]+)"', LOCK.read_text())
    assert m
    return m.group(1)


def _stub(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    (repo / "scripts" / "ci").mkdir(parents=True)
    (repo / "terraform" / "github").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "ci" / SCRIPT.name)
    shutil.copy2(LOCK, repo / "terraform" / "github" / LOCK.name)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, text in (("aws", _AWS), ("uname", _UNAME), ("sha256sum", _SHA)):
        _stub(stubs / name, text)
    runner = tmp_path / "runner"
    runner.mkdir()
    (tmp_path / "github_env").write_text("", encoding="utf-8")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("MIRROR_", "GITHUB_", "RUNNER_", "STUB_", "SHA_"))}
    e.update(
        PATH=f"{stubs}:{os.environ['PATH']}",
        MIRROR_PREFIX=PREFIX,
        RUNNER_TEMP=str(runner),
        GITHUB_ENV=str(tmp_path / "github_env"),
        STUB_ARGV=str(tmp_path / "argv"),
        STUB_ARN=ARN,
        STUB_LOCK_HASH=_lock_hashes()[0],
        REPO=str(repo),
    )
    return e


def _fetch(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-e", "-c", 'bash "$REPO/scripts/ci/terraform_github_apply_mirror.sh" fetch'],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=env["REPO"],
        check=False,
    )


def test_fetch_active_writes_a_mirror_only_tfrc(env: dict[str, str]) -> None:
    r = _fetch(env)
    version = _version()
    assert r.returncode == 0
    key = f"{ADDRESS}/terraform-provider-github_{version}_linux_amd64.zip"
    dest = f"{env['RUNNER_TEMP']}/tf-github-mirror/{key}"
    assert Path(env["STUB_ARGV"]).read_text().strip() == f"s3 cp {PREFIX}{key} {dest} --only-show-errors"
    tfrc = Path(env["RUNNER_TEMP"]) / "tf-github-mirror.tfrc"
    assert Path(env["GITHUB_ENV"]).read_text().splitlines() == [f"TF_CLI_CONFIG_FILE={tfrc}"]
    text = tfrc.read_text()
    assert re.findall(r'include\s+=\s+\["([^"]+)"\]', text) == [ADDRESS]
    assert re.findall(r'exclude\s+=\s+\["([^"]+)"\]', text) == [ADDRESS]
    assert f"provider-mirror: ACTIVE (integrations/github {version})" in r.stdout
    assert "DEGRADED" not in r.stdout


def _unparseable(env: dict[str, str]) -> None:
    (Path(env["REPO"]) / "terraform" / "github" / LOCK.name).write_text("# empty\n", encoding="utf-8")


def _tfrc_dir(env: dict[str, str]) -> None:
    (Path(env["RUNNER_TEMP"]) / "tf-github-mirror.tfrc").mkdir()


def _env_dir(env: dict[str, str]) -> None:
    Path(env["GITHUB_ENV"]).unlink()
    Path(env["GITHUB_ENV"]).mkdir()


@pytest.mark.parametrize(
    "case",
    [
        ("copy error", {"STUB_MODE": "fail"}, None),
        ("timeout", {"STUB_MODE": "timeout"}, None),
        ("empty file", {"STUB_MODE": "empty"}, None),
        ("hash mismatch", {"SHA_MODE": "other"}, None),
        ("tfrc write failed", {}, _tfrc_dir),
        ("GITHUB_ENV write failed", {}, _env_dir),
        ("unparseable lockfile", {}, _unparseable),
        ("MIRROR_PREFIX unset", {"MIRROR_PREFIX": None}, None),
        ("RUNNER_TEMP unset", {"RUNNER_TEMP": None}, None),
        ("GITHUB_ENV unset", {"GITHUB_ENV": None}, None),
        ("uname aarch64", {"STUB_UNAME": "Linux aarch64"}, None),
    ],
    ids=lambda c: c[0],
)
def test_fetch_degrades_and_exits_zero(env: dict[str, str], case: tuple) -> None:
    _, overrides, mutate = case
    if mutate is not None:
        mutate(env)
    for k, v in overrides.items():
        if v is None:
            env.pop(k)
        else:
            env[k] = v
    github_env = env.get("GITHUB_ENV")
    r = _fetch(env)
    assert r.returncode == 0
    assert "ACTIVE" not in r.stdout
    assert len([ln for ln in r.stdout.splitlines() if ln.startswith("provider-mirror: DEGRADED")]) == 1
    assert len([ln for ln in r.stdout.splitlines() if ln.startswith("::warning::provider-mirror: DEGRADED")]) == 1
    if github_env and Path(github_env).is_file():
        assert "TF_CLI_CONFIG_FILE" not in Path(github_env).read_text()
    if overrides.get("SHA_MODE"):
        assert not list(Path(env["RUNNER_TEMP"]).rglob("*.zip"))


def test_fetch_never_prints_aws_error_text(env: dict[str, str]) -> None:
    env["STUB_MODE"] = "denied"
    r = _fetch(env)
    assert r.returncode == 0 and "DEGRADED" in r.stdout
    assert ACCOUNT not in r.stdout + r.stderr
    assert "arn:aws" not in r.stdout + r.stderr and "AccessDenied" not in r.stdout + r.stderr


@pytest.mark.parametrize(
    "log, expected",
    [
        ("Error: Failed to install provider\nerror: 504 Gateway Timeout\n", "provider_install"),
        ("Error: Error acquiring the state lock\n", "backend"),
        ("Error: Failed to install provider\nError loading state: 502 Bad Gateway\n", "backend"),
        ("something unrelated entirely\n", "other"),
        (None, "other"),
    ],
)
def test_classify_names_a_class_and_never_the_log(env: dict[str, str], tmp_path: Path, log: str | None, expected: str) -> None:
    path = tmp_path / "tf.log"
    if log is not None:
        path.write_text(log, encoding="utf-8")
    r = subprocess.run(
        ["bash", str(SCRIPT), "classify", str(path)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert r.returncode == 0
    assert r.stdout.splitlines() == [f"init failure class: {expected}"]
    assert r.stderr == ""
