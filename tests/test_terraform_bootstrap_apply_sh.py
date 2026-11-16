"""scripts/ci/terraform_bootstrap_apply.sh under stubs (PLAN-bootstrap-ci-gated-apply VP 8).

The real script and quiet_run.sh run from a tmp_path workspace; a stub terraform and a stub gate log their argv
(Decision 162 literal argv). Plan files must be gone after every exit path, and stdout holds only tokens.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / "scripts" / "ci"
TOKENS = (
    "CHECKED",
    "NARROWS_PRESENT",
    "APPLY_REFUSED",
    "DEPENDENT_RUN_PENDING",
    "APPLY_DONE",
    "REPLAN_CLEAN",
    "REPLAN_DIRTY",
    "SIMULATE_OWED",
    "PROVE_OK",
    "::warning::SIMULATE_OWED",
)

_TERRAFORM = """#!/usr/bin/env bash
echo "terraform $*" >> "$STUB_ARGV"
for a in "$@"; do
    case "$a" in
        -out=apply.bin) : > "$GITHUB_WORKSPACE/terraform/bootstrap/apply.bin" ;;
    esac
done
case " $* " in
    *" show "*) echo '{"resource_changes": []}' ;;
    *" -detailed-exitcode "*) exit "${REPLAN_RC:-0}" ;;
esac
exit 0
"""

_GATE = """#!/usr/bin/env python3
import os, sys
open(os.environ["STUB_ARGV"], "a").write("gate " + " ".join(sys.argv[1:]) + "\\n")
cmd = sys.argv[1]
if cmd == "check":
    print(os.environ.get("CHECK_OUT", "CHECKED rows=1"))
    raise SystemExit(int(os.environ.get("CHECK_RC", "0")))
if cmd == "prove":
    print("PROVE_OK")
    raise SystemExit(int(os.environ.get("PROVE_RC", "0")))
if cmd == "pending":
    raise SystemExit(int(os.environ.get("PENDING_RC", "0")))
raise SystemExit(99)
"""


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    for name in ("terraform_bootstrap_apply.sh", "quiet_run.sh"):
        shutil.copy(CI / name, tmp_path / "scripts" / "ci" / name)
    gate = tmp_path / "scripts" / "ci" / "terraform_bootstrap_apply_gate.py"
    gate.write_text(_GATE, encoding="utf-8")
    (tmp_path / "terraform" / "bootstrap").mkdir(parents=True)
    (tmp_path / "terraform" / "personal").mkdir(parents=True)
    (tmp_path / "rt").mkdir()
    (tmp_path / "bin").mkdir()
    tf = tmp_path / "bin" / "terraform"
    tf.write_text(_TERRAFORM, encoding="utf-8")
    tf.chmod(0o755)
    return tmp_path


def run(ws: Path, **env: str) -> tuple[int, str, list[str]]:
    argv_log = ws / "argv.log"
    merged = {
        **os.environ,
        "PATH": f"{ws / 'bin'}:{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(ws),
        "RUNNER_TEMP": str(ws / "rt"),
        "STUB_ARGV": str(argv_log),
        "REVIEWED": "[]",
        **env,
    }
    proc = subprocess.run(
        ["bash", "scripts/ci/terraform_bootstrap_apply.sh"], cwd=ws, env=merged, capture_output=True, text=True, check=False
    )
    argv = argv_log.read_text(encoding="utf-8").splitlines() if argv_log.exists() else []
    assert not (ws / "terraform" / "bootstrap" / "apply.bin").exists()
    assert not (ws / "rt" / "apply.json").exists()
    return proc.returncode, proc.stdout, argv


def tf_verbs(argv: list[str]) -> list[str]:
    return [a.split("terraform -chdir=terraform/bootstrap ", 1)[1].split()[0] for a in argv if a.startswith("terraform")]


def test_happy_path_runs_the_exact_argv_in_order(ws: Path) -> None:
    rc, out, argv = run(ws)
    tfvars = f"-var-file={ws}/terraform/personal/terraform.personal.tfvars"
    assert rc == 0
    assert argv[0] == "terraform -chdir=terraform/bootstrap init -input=false -lockfile=readonly"
    assert (
        argv[1]
        == f"terraform -chdir=terraform/bootstrap plan -input=false -no-color {tfvars} -var aws_profile= -out=apply.bin"
    )
    assert argv[2] == "terraform -chdir=terraform/bootstrap show -json apply.bin"
    assert argv[3].startswith("gate check --plan-json ") and argv[3].endswith("--reviewed []")
    assert argv[4] == "terraform -chdir=terraform/bootstrap apply -input=false -no-color apply.bin"
    assert (
        argv[5]
        == f"terraform -chdir=terraform/bootstrap plan -input=false -no-color -detailed-exitcode {tfvars} -var aws_profile="
    )
    assert out.splitlines() == ["CHECKED rows=1", "APPLY_DONE", "REPLAN_CLEAN"]


def test_prove_gate_stops_before_apply(ws: Path) -> None:
    rc, out, argv = run(ws, PROVE_GATE="true")
    assert rc == 0 and out.strip() == "PROVE_OK"
    assert tf_verbs(argv) == ["init", "plan", "show"] and not any(a.startswith("gate check") for a in argv)


def test_prove_gate_fails_on_a_non_empty_plan(ws: Path) -> None:
    rc, _out, argv = run(ws, PROVE_GATE="true", PROVE_RC="1")
    assert rc != 0 and "apply" not in tf_verbs(argv)


def test_refused_check_exits_before_any_apply(ws: Path) -> None:
    rc, out, argv = run(ws, CHECK_RC="1", CHECK_OUT="APPLY_REFUSED protected aws_iam_role.x boundary")
    assert rc != 0 and "APPLY_REFUSED check" in out and "apply" not in tf_verbs(argv)


def test_narrowing_plan_refuses_while_a_dependent_run_is_pending(ws: Path) -> None:
    narrows = "CHECKED rows=1\nNARROWS_PRESENT\nSIMULATE_OWED"
    rc, out, argv = run(ws, CHECK_OUT=narrows, PENDING_RC="1")
    assert rc != 0 and "DEPENDENT_RUN_PENDING" in out
    assert "apply" not in tf_verbs(argv) and any(a == "gate pending" for a in argv)
    rc, out, argv = run(ws, CHECK_OUT=narrows, PENDING_RC="0")
    assert rc == 0 and tf_verbs(argv).count("apply") == 1


def test_pending_is_not_consulted_without_narrows(ws: Path) -> None:
    _rc, _out, argv = run(ws, PENDING_RC="1")
    assert "gate pending" not in argv


def test_dirty_replan_exits_non_zero(ws: Path) -> None:
    rc, out, _argv = run(ws, REPLAN_RC="2")
    assert rc != 0 and "REPLAN_DIRTY" in out and "REPLAN_CLEAN" not in out


def test_simulate_owed_prints_only_after_the_clean_replan(ws: Path) -> None:
    rc, out, _argv = run(ws, CHECK_OUT="CHECKED rows=1\nNARROWS_PRESENT\nSIMULATE_OWED")
    lines = out.splitlines()
    assert rc == 0 and lines.index("REPLAN_CLEAN") < lines.index("SIMULATE_OWED")
    assert lines.count("SIMULATE_OWED") == 1 and any(ln.startswith("::warning::SIMULATE_OWED") for ln in lines)
    rc, out, _argv = run(ws, CHECK_OUT="CHECKED rows=1\nNARROWS_PRESENT\nSIMULATE_OWED", REPLAN_RC="2")
    assert rc != 0 and "SIMULATE_OWED" not in out


def test_output_holds_only_tokens(ws: Path) -> None:
    _rc, out, _argv = run(ws, CHECK_OUT="CHECKED rows=1\nNARROWS_PRESENT\nSIMULATE_OWED")
    assert all(ln.startswith(TOKENS) for ln in out.splitlines())


def test_plan_files_are_gone_when_init_or_plan_fails(ws: Path) -> None:
    (ws / "bin" / "terraform").write_text(
        _TERRAFORM.replace("exit 0\n", "exit 1\n", 1).replace('case " $* "', 'case " $* "'), encoding="utf-8"
    )
    rc, out, _argv = run(ws)
    assert rc != 0 and "APPLY_REFUSED" in out
