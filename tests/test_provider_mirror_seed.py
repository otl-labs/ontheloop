"""Provider mirror seed script: both roots, every locked provider asserted, one --delete sync (rec-4638)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "provider_mirror_seed.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "terraform-provider-mirror-seed.yml"

LOCK = 'provider "registry.terraform.io/{ns}" {{\n  version = "{v}"\n}}\n'

TERRAFORM_STUB = """#!/usr/bin/env bash
echo "terraform $*" >> "$STUB_LOG"
case "$*" in
  *"providers mirror"*)
    dir=${@: -1}
    root=${1#-chdir=}
    if [ "$root" = terraform/personal ]; then pkgs="hashicorp/aws:5.100.0 kislerdm/neon:0.13.0"
    else pkgs="integrations/github:${STUB_GITHUB_VERSION:-6.12.1}"; fi
    for p in $pkgs; do
      ns=${p%%:*}; v=${p##*:}; n=${ns##*/}
      case " $STUB_OMIT " in *" $n "*) continue;; esac
      mkdir -p "$dir/registry.terraform.io/$ns"
      : > "$dir/registry.terraform.io/$ns/terraform-provider-${n}_${v}_linux_amd64.zip"
    done;;
esac
"""
AWS_STUB = '#!/usr/bin/env bash\necho "aws $*" >> "$STUB_LOG"\n'


@pytest.fixture()
def scene(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for root, entries in {
        "personal": [("hashicorp/aws", "5.100.0"), ("kislerdm/neon", "0.13.0")],
        "github": [("integrations/github", "6.12.1")],
    }.items():
        d = repo / "terraform" / root
        d.mkdir(parents=True)
        (d / ".terraform.lock.hcl").write_text("".join(LOCK.format(ns=ns, v=v) for ns, v in entries), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("terraform", TERRAFORM_STUB), ("aws", AWS_STUB)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    return tmp_path


def run(scene: Path, *args: str, omit: str = "", github_version: str = "6.12.1") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{scene / 'bin'}:{os.environ['PATH']}",
        "STUB_LOG": str(scene / "calls.log"),
        "STUB_OMIT": omit,
        "STUB_GITHUB_VERSION": github_version,
    }
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=scene / "repo", env=env, capture_output=True, text=True)


def calls(scene: Path) -> list[str]:
    log = scene / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_script_is_valid_bash_with_strict_mode() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_both_roots_mirror_into_one_dir_before_the_single_delete_sync(scene: Path) -> None:
    out = run(scene, str(scene / "mirror"), "tf-provider-mirror")
    assert out.returncode == 0, out.stderr
    log = calls(scene)
    mirrors = [i for i, c in enumerate(log) if "providers mirror" in c]
    syncs = [i for i, c in enumerate(log) if c.startswith("aws s3 sync")]
    assert len(mirrors) == 2 and len(syncs) == 1 and max(mirrors) < syncs[0]
    assert all(str(scene / "mirror") in log[i] for i in mirrors)
    assert log[syncs[0]].endswith("s3://agent-platform-data-lake/tf-provider-mirror/ --delete")


def test_github_root_is_initialized_before_its_mirror_call(scene: Path) -> None:
    run(scene, str(scene / "mirror"), "tf-provider-mirror")
    log = calls(scene)
    init = next(i for i, c in enumerate(log) if "terraform/github init" in c)
    mirror = next(i for i, c in enumerate(log) if "terraform/github providers mirror" in c)
    assert init < mirror
    assert "-backend=false" in log[init] and "-lockfile=readonly" in log[init] and "-input=false" in log[init]


@pytest.mark.parametrize("omit", ["aws", "neon", "github"])
def test_a_missing_locked_provider_blocks_the_sync(scene: Path, omit: str) -> None:
    out = run(scene, str(scene / "mirror"), "tf-provider-mirror", omit=omit)
    assert out.returncode == 1
    assert "MIRROR_MISSING" in out.stdout and f"/{omit} " in out.stdout
    assert not any(c.startswith("aws ") for c in calls(scene))


def test_a_mirrored_github_version_other_than_the_locked_one_blocks_the_sync(scene: Path) -> None:
    out = run(scene, str(scene / "mirror"), "tf-provider-mirror", github_version="6.11.0")
    assert out.returncode == 1
    assert "MIRROR_MISSING integrations/github 6.12.1" in out.stdout
    assert not any(c.startswith("aws ") for c in calls(scene))


@pytest.mark.parametrize("prefix", ["tfstate", "tfplan", "", "tf-provider-mirror/../tfstate", "tf-provider-mirror/"])
def test_any_other_prefix_is_refused_before_any_call(scene: Path, prefix: str) -> None:
    out = run(scene, str(scene / "mirror"), prefix)
    assert out.returncode == 2 and "SEED_BAD_ARGS" in out.stderr
    assert calls(scene) == []


@pytest.mark.parametrize("args", [(), ("only-one",), ("a", "tf-provider-mirror", "extra")])
def test_missing_or_extra_arguments_are_refused_before_any_call(scene: Path, args: tuple[str, ...]) -> None:
    out = run(scene, *args)
    assert out.returncode == 2 and "SEED_BAD_ARGS" in out.stderr
    assert calls(scene) == []


def test_workflow_step_calls_the_script_with_the_two_env_values() -> None:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["jobs"]["seed-mirror"]["steps"]
    runs = [s["run"] for s in steps if "run" in s and "provider_mirror_seed.sh" in s["run"]]
    assert runs == ['bash scripts/ci/provider_mirror_seed.sh "$MIRROR_DIR" "$MIRROR_PREFIX"']
    env = wf["jobs"]["seed-mirror"]["env"]
    assert env["MIRROR_PREFIX"] == "tf-provider-mirror" and env["MIRROR_DIR"]
    assert not any("aws s3 sync" in s.get("run", "") for s in steps)


@pytest.mark.parametrize("which", ["terraform/personal", "terraform/github"])
def test_an_empty_or_missing_lockfile_blocks_the_sync(scene: Path, which: str) -> None:
    lock = scene / "repo" / which / ".terraform.lock.hcl"
    lock.write_text("", encoding="utf-8")
    assert run(scene, str(scene / "mirror"), "tf-provider-mirror").returncode == 1
    lock.unlink()
    out = run(scene, str(scene / "mirror"), "tf-provider-mirror")
    assert out.returncode == 1 and "lockfile-unreadable" in out.stdout
    assert not any(c.startswith("aws ") for c in calls(scene))
