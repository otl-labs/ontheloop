"""The telemetry-ci-capture composite action, its total delegate capture.sh and the ci-rca wiring.

capture.sh runs against a fake bin/venv-python in a copied tree, so the repo root derivation (three levels above the script),
the argument mapping, the credential isolation and the exit-0-on-every-path contract are asserted without a Python import.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION_DIR = ROOT / ".github" / "actions" / "telemetry-ci-capture"
WORKFLOW = ROOT / ".github" / "workflows" / "ci-rca.yml"
SHA = "a" * 40

FAKE_PYTHON = """\
#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_ARGV"
env > "$FAKE_ENV"
pwd > "$FAKE_PWD"
if [ -n "${FAKE_OUT:-}" ]; then printf '%b' "$FAKE_OUT"; fi
exit "${FAKE_EXIT:-0}"
"""


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def make_tree(tmp_path: Path, python: str | None = FAKE_PYTHON) -> Path:
    tree = tmp_path / "tree"
    target = tree / ".github" / "actions" / "telemetry-ci-capture"
    target.parent.mkdir(parents=True)
    shutil.copytree(ACTION_DIR, target)
    if python is not None:
        (tree / "bin").mkdir()
        fake = tree / "bin" / "venv-python"
        fake.write_text(python, encoding="utf-8")
        fake.chmod(0o755)
    return tree


def run(tmp_path: Path, tree: Path, *, extra: dict[str, str] | None = None, path: str | None = None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    env = {
        "PATH": path or os.environ["PATH"],
        "HOME": str(tmp_path),
        "GITHUB_WORKFLOW_REF": "example/repo/.github/workflows/ci-rca.yml@refs/heads/main",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "123456789",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_JOB": "rca",
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_SHA": SHA,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_ENV": str(tmp_path / "envfile"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "CAPTURE_MANIFESTS": "/tmp/a.sessions.jsonl\n\n/tmp/b.sessions.jsonl\n",
        "CAPTURE_AWS_ACCESS_KEY_ID": "",
        "CAPTURE_AWS_SECRET_ACCESS_KEY": "",
        "CAPTURE_AWS_SESSION_TOKEN": "",
        "FAKE_ARGV": str(tmp_path / "argv"),
        "FAKE_ENV": str(tmp_path / "seen_env"),
        "FAKE_PWD": str(tmp_path / "seen_pwd"),
        **(extra or {}),
    }
    done = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-o",
            "pipefail",
            str(tree / ".github/actions/telemetry-ci-capture/capture.sh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    for name in ("output", "envfile", "summary"):
        assert not (tmp_path / name).exists(), f"capture.sh wrote {name}"
    return done


def seen(tmp_path: Path) -> tuple[list[str], dict[str, str]]:
    argv = (tmp_path / "argv").read_text(encoding="utf-8").splitlines()
    pairs = (line.partition("=") for line in (tmp_path / "seen_env").read_text(encoding="utf-8").splitlines())
    return argv, {k: v for k, _, v in pairs}


def test_action_shape_has_no_outputs_and_a_delegate_only_body() -> None:
    action = load(ACTION_DIR / "action.yml")
    assert "outputs" not in action and action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {"manifests", "aws-access-key-id", "aws-secret-access-key", "aws-session-token"}
    assert action["inputs"]["manifests"]["required"] is True
    (step,) = action["runs"]["steps"]
    assert step["shell"] == "bash" and step["run"] == 'bash "${{ github.action_path }}/capture.sh"'
    assert step["env"] == {
        "CAPTURE_MANIFESTS": "${{ inputs.manifests }}",
        "CAPTURE_AWS_ACCESS_KEY_ID": "${{ inputs.aws-access-key-id }}",
        "CAPTURE_AWS_SECRET_ACCESS_KEY": "${{ inputs.aws-secret-access-key }}",
        "CAPTURE_AWS_SESSION_TOKEN": "${{ inputs.aws-session-token }}",
    }


def test_facts_map_to_ci_pass_arguments_and_the_root_is_the_action_path(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    done = run(tmp_path, tree, extra={"FAKE_OUT": "CI_CAPTURE done sessions=1 sent=1\\nnoise line\\n"})
    assert done.returncode == 0 and done.stdout == "CI_CAPTURE done sessions=1 sent=1\n"
    argv, _ = seen(tmp_path)
    assert argv[:3] == ["-m", "scripts.telemetry_capture", "ci-pass"]
    pairs = dict(zip(argv[3::2], argv[4::2], strict=False)) | {"--manifest": [a for a in argv if a.startswith("/tmp/")]}
    assert pairs["--workflow-path"] == ".github/workflows/ci-rca.yml" and pairs["--ref"] == "refs/heads/main"
    assert (pairs["--repository"], pairs["--run-id"], pairs["--run-attempt"], pairs["--job"]) == (
        "example/repo",
        "123456789",
        "2",
        "rca",
    )
    assert (pairs["--event-name"], pairs["--sha"]) == ("workflow_run", SHA)
    assert pairs["--manifest"] == ["/tmp/a.sessions.jsonl", "/tmp/b.sessions.jsonl"] and "--checkout-sha" not in argv
    assert Path((tmp_path / "seen_pwd").read_text().strip()).resolve() == tree.resolve()


def test_checkout_sha_comes_from_the_workspace_head(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    run(tmp_path, tree, extra={"FAKE_OUT": "CI_CAPTURE done sessions=0\\n"})
    argv, _ = seen(tmp_path)
    assert argv[argv.index("--checkout-sha") + 1] == head and head != SHA


def test_credential_inputs_replace_every_ambient_aws_variable(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    ambient = {
        "AWS_PROFILE": "admin",
        "AWS_ACCESS_KEY_ID": "ambient",
        "AWS_SECRET_ACCESS_KEY": "ambient",  # pragma: allowlist secret
        "AWS_SESSION_TOKEN": "ambient",
        "AWS_SECURITY_TOKEN": "ambient",
        "AWS_ROLE_ARN": "ambient",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/x",
        "AWS_REGION": "eu-west-2",
    }
    creds = {
        "CAPTURE_AWS_ACCESS_KEY_ID": "capture-id",
        "CAPTURE_AWS_SECRET_ACCESS_KEY": "capture-secret",  # pragma: allowlist secret
        "CAPTURE_AWS_SESSION_TOKEN": "capture-token",
    }
    run(tmp_path, tree, extra={**ambient, **creds, "FAKE_OUT": "CI_CAPTURE done sessions=0\\n"})
    _, env = seen(tmp_path)
    assert {k: v for k, v in env.items() if k.startswith("AWS_")} == {
        "AWS_ACCESS_KEY_ID": "capture-id",
        "AWS_SECRET_ACCESS_KEY": "capture-secret",  # pragma: allowlist secret
        "AWS_SESSION_TOKEN": "capture-token",
        "AWS_REGION": "eu-west-2",
    }


@pytest.mark.parametrize(
    "missing", ["CAPTURE_AWS_ACCESS_KEY_ID", "CAPTURE_AWS_SECRET_ACCESS_KEY", "CAPTURE_AWS_SESSION_TOKEN"]
)
def test_partial_credential_inputs_leave_the_ambient_environment(tmp_path: Path, missing: str) -> None:
    tree = make_tree(tmp_path)
    creds = {"CAPTURE_AWS_ACCESS_KEY_ID": "i", "CAPTURE_AWS_SECRET_ACCESS_KEY": "s", "CAPTURE_AWS_SESSION_TOKEN": "t"}
    creds[missing] = ""
    run(tmp_path, tree, extra={**creds, "AWS_PROFILE": "ambient", "FAKE_OUT": "CI_CAPTURE done sessions=0\\n"})
    _, env = seen(tmp_path)
    assert env["AWS_PROFILE"] == "ambient" and "AWS_ACCESS_KEY_ID" not in env


def test_no_python_is_a_fixed_token(tmp_path: Path) -> None:
    done = run(tmp_path, make_tree(tmp_path, python=None))
    assert (done.returncode, done.stdout) == (0, "CI_CAPTURE skipped reason=no_python\n")


def test_a_timeout_is_a_fixed_token(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "timeout").write_text("#!/usr/bin/env bash\nexit 124\n", encoding="utf-8")
    (shim / "timeout").chmod(0o755)
    done = run(tmp_path, tree, path=f"{shim}:{os.environ['PATH']}")
    assert (done.returncode, done.stdout) == (0, "CI_CAPTURE skipped reason=timeout\n")


@pytest.mark.parametrize(
    ("out", "code"), [("", "0"), ("traceback\\n", "0"), ("CI_CAPTURE done sessions=1\\n", "3"), ("", "1")]
)
def test_a_failed_or_silent_ci_pass_is_a_fixed_token_and_exit_zero(tmp_path: Path, out: str, code: str) -> None:
    done = run(tmp_path, make_tree(tmp_path), extra={"FAKE_OUT": out, "FAKE_EXIT": code})
    assert (done.returncode, done.stdout) == (0, "CI_CAPTURE skipped reason=ci_pass_failed\n")


def test_a_missing_environment_still_exits_zero(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", str(tree / ".github/actions/telemetry-ci-capture/capture.sh")],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0 and done.stdout.startswith("CI_CAPTURE ")


def test_ci_rca_captures_last_and_can_never_red_the_job() -> None:
    workflow = load(WORKFLOW)
    job = workflow["jobs"]["rca"]
    assert job["timeout-minutes"] == 25
    steps = job["steps"]
    agent = next(s for s in steps if s.get("id") == "rca_agent")
    assert agent["timeout-minutes"] == 17
    capture = steps[-1]
    assert capture["name"] == "Capture agent telemetry" and capture["uses"] == "./.github/actions/telemetry-ci-capture"
    assert capture["if"] == "always()" and capture["continue-on-error"] is True and capture["timeout-minutes"] == 5
    assert capture["with"] == {"manifests": "/tmp/ci-rca-output.json.sessions.jsonl"}
    assert not [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
    assert "telemetry-ci-capture" not in "".join(str(s.get("uses", "")) for s in steps[:-1])
