"""Behaviour tests for bin/sync-terraform-assets.sh with a stub aws and a stub tfvars delegate on a temp repo copy."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REAL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REAL_ROOT / "bin" / "sync-terraform-assets.sh"
ACCOUNT = str(10**11 + 7)
UNIQUE_ID = "AROA" + "B" * 17
ARN = f"arn:aws:sts::{ACCOUNT}:assumed-role/PlatformDev/session"
KEY_ID = "ASIA" + "C" * 16
LEAKY_STDERR = f"An error occurred (AccessDenied): User {ARN} id {UNIQUE_ID} acct {ACCOUNT} key {KEY_ID} is not allowed"

AWS_STUB = r"""#!/usr/bin/env bash
count_file="$STUB_DIR/aws.count"
n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
echo "$@" >> "$STUB_DIR/aws.argv"
echo "${AWS_PROFILE-unset}" >> "$STUB_DIR/aws.profile"
dest="$4"
ns="$dest/registry.terraform.io/kislerdm/neon"
populate() { mkdir -p "$ns"; echo bin > "$ns/p.zip"; }
case "$STUB_MODE" in
  ok) populate; exit 0 ;;
  empty) exit 0 ;;
  dirs_only) mkdir -p "$ns"; exit 0 ;;
  fail_then_ok) if [ "$n" -eq 1 ]; then echo "Could not connect to the endpoint URL" >&2; exit 1; fi; populate; exit 0 ;;
  creds) echo "Unable to locate credentials. You can configure credentials by running aws configure." >&2; exit 253 ;;
  region) echo "You must specify a region. You can also configure your region by running aws configure." >&2; exit 253 ;;
  hang) sleep 30 ;;
  leak) echo "$STUB_LEAK" >&2; exit 1 ;;
  network) echo "Could not connect to the endpoint URL" >&2; exit 1 ;;
esac
"""

DELEGATE_STUB = r"""#!/usr/bin/env bash
echo "${AWS_PROFILE-unset}" > "$STUB_DIR/delegate.profile"
echo "${GITHUB_ACTIONS-unset}" > "$STUB_DIR/delegate.github_actions"
echo ran >> "$STUB_DIR/delegate.ran"
case "$STUB_MODE" in
  tfvars_ok) exit 0 ;;
  tfvars_leak)
    echo "materialise-tfvars: FETCH_FAILED could not fetch the secret" >&2
    echo "$STUB_LEAK" >&2
    exit 1 ;;
esac
"""


def _write(path: Path, body: str, mode: int = stat.S_IEXEC) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | mode)


class Harness:
    def __init__(self, tmp_path: Path, mask_available: bool = True, tfrc: str | None = None) -> None:
        self.root = tmp_path / "repo"
        self.home = tmp_path / "home"
        self.stub_dir = tmp_path / "stubs"
        self.home.mkdir()
        self.stub_dir.mkdir()
        shutil.copy(SCRIPT, self.root_file("bin/sync-terraform-assets.sh"))
        tfrc_text = tfrc if tfrc is not None else (REAL_ROOT / "config/terraform/cc-web.tfrc").read_text(encoding="utf-8")
        _write(self.root / "config/terraform/cc-web.tfrc", tfrc_text, 0)
        _write(self.root / ".github/actions/materialise-tfvars/materialise_tfvars.sh", DELEGATE_STUB)
        if mask_available:
            _write(
                self.root / "bin/venv-python",
                f'#!/usr/bin/env bash\ncd "{REAL_ROOT}" && exec "{REAL_ROOT}/bin/venv-python" "$@"\n',
            )
        else:
            _write(self.root / "bin/venv-python", "#!/usr/bin/env bash\nexit 1\n")
        fake_bin = tmp_path / "fakebin"
        _write(fake_bin / "aws", AWS_STUB)
        self.fake_bin = fake_bin

    def root_file(self, rel: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def run(self, mode: str, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "STUB_DIR": str(self.stub_dir),
            "STUB_MODE": mode,
            "STUB_LEAK": LEAKY_STDERR,
            "SYNC_TF_ASSETS_BACKOFF_SECONDS": "0",
            "AWS_PROFILE": "agent_platform_admin",
        }
        env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.root / "bin/sync-terraform-assets.sh"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=90,
            check=False,
        )

    def aws_calls(self) -> int:
        f = self.stub_dir / "aws.count"
        return int(f.read_text(encoding="utf-8")) if f.exists() else 0

    @property
    def tfrc(self) -> Path:
        return self.home / ".terraform-mirror.tfrc"

    @property
    def log(self) -> Path:
        return self.home / ".terraform-mirror.log"


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_mirror_sync_success_writes_resolved_tfrc_and_log(h: Harness) -> None:
    result = h.run("ok")
    assert result.returncode == 0, result.stderr
    assert (h.home / ".terraform-mirror/registry.terraform.io/kislerdm/neon/p.zip").exists()
    assert f'path    = "{h.home / ".terraform-mirror"}"' in h.tfrc.read_text(encoding="utf-8")
    assert result.stdout.startswith("mirror-sync: OK (")
    assert h.log.read_text(encoding="utf-8") == result.stdout


def test_mirror_sync_runs_as_platform_dev_whatever_the_ambient_profile(h: Harness) -> None:
    for ambient in ("agent_platform_admin", "agent_platform", "unset-thing"):
        assert h.run("ok", AWS_PROFILE=ambient).returncode == 0
    argv = (h.stub_dir / "aws.argv").read_text(encoding="utf-8").splitlines()
    assert argv and all(line.rstrip().endswith("--delete --profile agent_platform") for line in argv)
    assert all("agent_platform_admin" not in line for line in argv)


def test_failed_attempt_is_retried_once(h: Harness) -> None:
    result = h.run("fail_then_ok")
    assert result.returncode == 0
    assert h.aws_calls() == 2
    assert result.stdout.startswith("mirror-sync: OK (")


@pytest.mark.parametrize(("mode", "klass"), [("creds", "NO_CREDENTIALS"), ("region", "NO_REGION")])
def test_credential_and_region_errors_are_not_retried(h: Harness, mode: str, klass: str) -> None:
    result = h.run(mode)
    assert result.returncode == 1
    assert h.aws_calls() == 1
    assert f"mirror-sync: WARNING {klass} (" in result.stdout


def test_hung_attempt_reports_timeout(h: Harness) -> None:
    result = h.run("hang", SYNC_TF_ASSETS_ATTEMPT_TIMEOUT="1")
    assert result.returncode == 1
    assert h.aws_calls() == 2
    assert "mirror-sync: WARNING TIMEOUT (" in result.stdout


def test_failed_sync_removes_resolved_tfrc_and_exits_one(h: Harness) -> None:
    h.tfrc.write_text("stale", encoding="utf-8")
    result = h.run("network")
    assert result.returncode == 1
    assert not h.tfrc.exists()
    assert "mirror-sync: WARNING NETWORK (" in result.stdout
    assert h.log.read_text(encoding="utf-8") == result.stdout


@pytest.mark.parametrize("mode", ["empty", "dirs_only"])
def test_empty_sync_removes_the_resolved_tfrc_and_exits_one(h: Harness, mode: str) -> None:
    h.tfrc.write_text("stale", encoding="utf-8")
    result = h.run(mode)
    assert result.returncode == 1
    assert not h.tfrc.exists()
    assert "mirror-sync: WARNING EMPTY_MIRROR (" in result.stdout
    assert h.aws_calls() == 1


def test_sync_fetches_only_the_namespaces_the_tfrc_routes_through_the_mirror(tmp_path: Path) -> None:
    includes = '"registry.terraform.io/kislerdm/*", "registry.terraform.io/acme/*"'
    tfrc = f'filesystem_mirror {{\n  path = "__TF_MIRROR_DIR__"\n  include = [{includes}]\n}}\n'
    harness = Harness(tmp_path, tfrc=tfrc)
    argv = harness.run("ok")
    line = (harness.stub_dir / "aws.argv").read_text(encoding="utf-8")
    assert "--exclude *" in line
    assert line.count("--include") == 2
    assert "--include registry.terraform.io/kislerdm/*" in line
    assert "--include registry.terraform.io/acme/*" in line
    assert argv.returncode == 1  # the acme namespace holds no file in the stub, so the sync is not non-empty


def test_default_mode_never_runs_the_delegate(h: Harness) -> None:
    h.run("ok")
    h.run("network")
    assert not (h.stub_dir / "delegate.ran").exists()


def test_tfvars_mode_pins_the_admin_profile(h: Harness) -> None:
    result = h.run("tfvars_ok", "--tfvars", AWS_PROFILE="agent_platform", GITHUB_ACTIONS="true")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("tfvars-fetch: OK (")
    assert (h.stub_dir / "delegate.profile").read_text(encoding="utf-8").strip() == "agent_platform_admin"
    assert (h.stub_dir / "delegate.github_actions").read_text(encoding="utf-8").strip() == "unset"
    assert h.aws_calls() == 0
    assert not h.log.exists()


@pytest.mark.parametrize("mode", ["default", "tfvars"])
def test_output_and_log_never_carry_account_id_arn_or_unique_id(h: Harness, mode: str) -> None:
    result = h.run("tfvars_leak", "--tfvars") if mode == "tfvars" else h.run("leak")
    assert result.returncode == 1
    out = result.stdout
    assert "WARNING ACCESS_DENIED" in out or "WARNING OTHER" in out
    assert "materialise-tfvars:" not in out
    for secret in (ACCOUNT, UNIQUE_ID, "arn:", KEY_ID):
        assert secret not in out + result.stderr
        if mode == "default":
            assert secret not in h.log.read_text(encoding="utf-8")
    assert "[ARN]" in out and "[ACCOUNT_ID]" in out and "[UNIQUE_ID]" in out


def test_scrub_runs_before_truncation(h: Harness) -> None:
    padding = "x" * 193
    straddle = f"{padding} {ACCOUNT} tail"
    result = h.run("leak", STUB_LEAK=straddle)
    assert ACCOUNT not in result.stdout
    assert ACCOUNT[:6] not in result.stdout.split("detail:", 1)[1]
    assert len(result.stdout.split("detail: ", 1)[1].rstrip("\n")) <= 200


def test_error_line_withheld_when_the_shared_mask_is_unavailable(tmp_path: Path) -> None:
    harness = Harness(tmp_path, mask_available=False)
    result = harness.run("leak")
    assert result.returncode == 1
    assert "detail withheld: mask unavailable" in result.stdout
    assert ACCOUNT not in result.stdout and "arn:" not in result.stdout
    assert harness.log.read_text(encoding="utf-8") == result.stdout
