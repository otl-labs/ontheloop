"""Behaviour tests for scripts/ci/provider_mirror_gate.sh (terraform-validate's provider-mirror gate chain).

The real script is run exactly as the workflow step runs it (`bash -e -c 'bash scripts/ci/provider_mirror_gate.sh'`)
from a temp repo copy holding the real tfrc and both real lockfiles, with a fake `aws` on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REAL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_REL = "scripts/ci/provider_mirror_gate.sh"
TFRC_REL = "config/terraform/cc-web.tfrc"
PERSONAL_LOCK = "terraform/personal/.terraform.lock.hcl"
GITHUB_LOCK = "terraform/github/.terraform.lock.hcl"

AWS_STUB = r"""#!/usr/bin/env bash
echo "$@" >> "$STUB_DIR/aws.argv"
dest="$4"
case "$STUB_MODE" in
  fail) echo "boom" >&2; exit 1 ;;
  timeout) exit 124 ;;
  *) mkdir -p "$dest"
     IFS=':' read -ra files <<< "${STUB_FILES:-}"
     for f in "${files[@]}"; do
       if [ -n "$f" ]; then mkdir -p "$dest/$(dirname "$f")"; echo bin > "$dest/$f"; fi
     done
     exit 0 ;;
esac
"""


def _locked_version(lock_rel: str, address: str) -> str:
    text = (REAL_ROOT / lock_rel).read_text(encoding="utf-8")
    block = re.search(rf'provider "{re.escape(address)}" \{{(.*?)\n\}}', text, re.DOTALL)
    assert block, f"{address} not locked in {lock_rel}"
    return re.search(r'version\s*=\s*"([^"]+)"', block.group(1)).group(1)  # type: ignore[union-attr]


NEON_V = _locked_version(PERSONAL_LOCK, "registry.terraform.io/kislerdm/neon")
GITHUB_V = _locked_version(GITHUB_LOCK, "registry.terraform.io/integrations/github")
NEON_ZIP = f"registry.terraform.io/kislerdm/neon/terraform-provider-neon_{NEON_V}_linux_amd64.zip"
GITHUB_ZIP = f"registry.terraform.io/integrations/github/terraform-provider-github_{GITHUB_V}_linux_amd64.zip"
OTHER_GITHUB_ZIP = "registry.terraform.io/integrations/github/terraform-provider-github_0.0.1_linux_amd64.zip"
BOTH = [NEON_ZIP, GITHUB_ZIP]
UNSET = object()


class Gate:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.stubs = tmp_path / "stubs"
        self.runner_temp = tmp_path / "runner"
        self.env_file = tmp_path / "github_env"
        self.out_file = tmp_path / "github_output"
        self.summary = tmp_path / "summary"
        for d in (self.stubs, self.runner_temp):
            d.mkdir()
        for rel in (SCRIPT_REL, TFRC_REL, PERSONAL_LOCK, GITHUB_LOCK):
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REAL_ROOT / rel, dst)
        fake = tmp_path / "fakebin"
        fake.mkdir()
        (fake / "aws").write_text(AWS_STUB, encoding="utf-8")
        (fake / "aws").chmod((fake / "aws").stat().st_mode | stat.S_IEXEC)
        self.fake = fake

    def run(self, files: list[str], mode: str = "ok", **overrides: object) -> subprocess.CompletedProcess[str]:
        env: dict[str, str] = {
            "PATH": f"{self.fake}:{os.environ['PATH']}",
            "STUB_DIR": str(self.stubs),
            "STUB_MODE": mode,
            "STUB_FILES": ":".join(files),
            "OIDC_FAILED": "false",
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_ENV": str(self.env_file),
            "GITHUB_OUTPUT": str(self.out_file),
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }
        for key, val in overrides.items():
            if val is UNSET:
                env.pop(key, None)
            else:
                env[key] = str(val)
        return subprocess.run(
            ["bash", "-e", "-c", f"bash {SCRIPT_REL}"],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
            check=False,
        )

    def env_lines(self) -> list[str]:
        return self.env_file.read_text(encoding="utf-8").splitlines() if self.env_file.exists() else []

    def active_lines(self) -> list[str]:
        if not self.out_file.exists():
            return []
        return [ln for ln in self.out_file.read_text(encoding="utf-8").splitlines() if ln.startswith("active=")]

    def aws_argv(self) -> list[str]:
        f = self.stubs / "aws.argv"
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


@pytest.fixture
def gate(tmp_path: Path) -> Gate:
    return Gate(tmp_path)


def _tfrc_includes(text: str) -> list[str]:
    line = re.search(r"^\s*include\s*=\s*\[(.*)\]\s*$", text, re.MULTILINE)
    assert line, "tfrc include must be a single-line list"
    return re.findall(r'"([^"]+)"', line.group(1))


def test_active_when_every_locked_provider_is_mirrored(gate: Gate) -> None:
    result = gate.run(BOTH)
    assert result.returncode == 0, result.stderr
    env = gate.env_lines()
    assert len(env) == 1 and env[0].startswith("TF_CLI_CONFIG_FILE=")
    rendered = Path(env[0].split("=", 1)[1]).read_text(encoding="utf-8")
    assert f'path    = "{gate.runner_temp}/tf-provider-mirror"' in rendered
    assert "registry.terraform.io/kislerdm/*" in rendered and "registry.terraform.io/integrations/*" in rendered
    assert gate.active_lines() == ["active=true"]
    expected = f"provider-mirror: ACTIVE (kislerdm/neon {NEON_V}, integrations/github {GITHUB_V})"
    assert expected in result.stdout
    assert expected in gate.summary.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ([NEON_ZIP], "registry.terraform.io/integrations/github"),
        ([NEON_ZIP, OTHER_GITHUB_ZIP], "registry.terraform.io/integrations/github"),
        ([GITHUB_ZIP], "registry.terraform.io/kislerdm/neon"),
    ],
    ids=["github-absent", "github-wrong-version", "neon-absent"],
)
def test_a_missing_or_wrong_version_provider_degrades(gate: Gate, files: list[str], needle: str) -> None:
    result = gate.run(files)
    assert result.returncode == 0, result.stderr
    assert gate.env_lines() == []
    assert gate.active_lines() == ["active=false"]
    assert "provider-mirror: DEGRADED" in result.stdout
    degraded = [ln for ln in result.stdout.splitlines() if "DEGRADED" in ln][0]
    assert "gate 3/3" in degraded or "gate 2/3" in degraded
    assert needle in degraded or "synced namespace" in degraded


@pytest.mark.parametrize("oidc", ["true", "", UNSET], ids=["true", "empty", "unset"])
def test_oidc_failure_degrades_without_calling_aws(gate: Gate, oidc: object) -> None:
    result = gate.run(BOTH, OIDC_FAILED=oidc)
    assert result.returncode == 0
    assert gate.aws_argv() == []
    assert gate.env_lines() == []
    assert gate.active_lines() == ["active=false"]
    assert "gate 1/3" in result.stdout


def test_sync_failure_and_empty_namespace_degrade(gate: Gate) -> None:
    failed = gate.run(BOTH, mode="fail")
    assert failed.returncode == 0 and "gate 2/3" in failed.stdout and gate.env_lines() == []
    timed = gate.run(BOTH, mode="timeout")
    assert timed.returncode == 0 and "gate 2/3" in timed.stdout and gate.env_lines() == []
    partial = gate.run([NEON_ZIP])
    assert partial.returncode == 0 and "gate 2/3" in partial.stdout
    assert "integrations" in partial.stdout and gate.env_lines() == []


def test_sync_downloads_only_included_namespaces(gate: Gate) -> None:
    gate.run(BOTH)
    argv = gate.aws_argv()[0].split()
    includes = [argv[i + 1] for i, a in enumerate(argv) if a == "--include"]
    expected = _tfrc_includes((REAL_ROOT / TFRC_REL).read_text(encoding="utf-8"))
    assert includes == expected
    assert "--exclude" in argv and argv[argv.index("--exclude") + 1] == "*"
    assert not any("hashicorp" in i for i in includes)


def test_gate_providers_match_tfrc_includes_and_lockfiles() -> None:
    includes = _tfrc_includes((REAL_ROOT / TFRC_REL).read_text(encoding="utf-8"))
    script = (REAL_ROOT / SCRIPT_REL).read_text(encoding="utf-8")
    checked = [p for p in ("kislerdm/neon", "integrations/github") if f"registry.terraform.io/{p}" in script or p in script]
    assert checked == ["kislerdm/neon", "integrations/github"]

    def covered(provider: str, pattern: str) -> bool:
        return f"registry.terraform.io/{provider}".startswith(pattern.removesuffix("*"))

    for p in checked:
        assert any(covered(p, i) for i in includes), f"{p} not under a tfrc include"
    for i in includes:
        assert any(covered(p, i) for p in checked), f"include {i} covers no checked provider"
    for lock in REAL_ROOT.glob("terraform/*/.terraform.lock.hcl"):
        for addr in re.findall(r'^provider "([^"]+)"', lock.read_text(encoding="utf-8"), re.MULTILINE):
            if any(addr.startswith(i.removesuffix("*")) for i in includes):
                short = addr.removeprefix("registry.terraform.io/")
                assert short in checked, f"{lock} locks {addr}, under an included namespace the gate does not check"


@pytest.mark.parametrize(
    ("name", "files", "mode", "overrides", "expect_active", "one_active_line"),
    [
        ("all-present", BOTH, "ok", {}, True, True),
        ("oidc", BOTH, "ok", {"OIDC_FAILED": "true"}, False, True),
        ("sync-fail", BOTH, "fail", {}, False, True),
        ("sync-timeout", BOTH, "timeout", {}, False, True),
        ("empty-sync", [], "ok", {}, False, True),
        ("github-missing", [NEON_ZIP], "ok", {}, False, True),
        ("github-wrong", [NEON_ZIP, OTHER_GITHUB_ZIP], "ok", {}, False, True),
        ("neon-missing", [GITHUB_ZIP], "ok", {}, False, True),
        ("runner-temp-unset", BOTH, "ok", {"RUNNER_TEMP": UNSET}, False, True),
        ("github-env-unset", BOTH, "ok", {"GITHUB_ENV": UNSET}, False, True),
        ("summary-unset", BOTH, "ok", {"GITHUB_STEP_SUMMARY": UNSET}, True, True),
        ("output-unset", BOTH, "ok", {"GITHUB_OUTPUT": UNSET}, False, False),
        ("runner-temp-missing-dir", BOTH, "ok", {"RUNNER_TEMP": "@missing"}, True, True),
    ],
)
def test_every_branch_writes_active_and_exits_zero(
    gate: Gate,
    name: str,
    files: list[str],
    mode: str,
    overrides: dict[str, object],
    expect_active: bool,
    one_active_line: bool,
) -> None:
    overrides = dict(overrides)
    if overrides.get("RUNNER_TEMP") == "@missing":
        overrides["RUNNER_TEMP"] = gate.tmp / "does" / "not" / "exist"
    result = gate.run(files, mode=mode, **overrides)
    assert result.returncode == 0, f"{name}: {result.stderr}"
    if one_active_line:
        assert gate.active_lines() == [f"active={'true' if expect_active else 'false'}"], name
    assert ("provider-mirror: ACTIVE" in result.stdout) == expect_active, name
    assert ("TF_CLI_CONFIG_FILE=" in "".join(gate.env_lines())) == expect_active, name
    if not expect_active:
        assert "provider-mirror: DEGRADED" in result.stdout, name


def test_unparseable_lockfile_degrades(gate: Gate) -> None:
    (gate.root / GITHUB_LOCK).write_text("# no providers\n", encoding="utf-8")
    result = gate.run(BOTH)
    assert result.returncode == 0
    assert "could not parse integrations/github" in result.stdout
    assert gate.active_lines() == ["active=false"] and gate.env_lines() == []
