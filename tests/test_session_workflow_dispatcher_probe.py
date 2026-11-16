"""Mirror test for scripts/session/workflow_dispatcher_probe.py (PLAN-scheduled-workflow-dispatch PR 2, VP 21)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.session import workflow_dispatcher_probe as probe
from src.data.handlers import github_app_token as gat

KEY = "/secret/agent-platform-workflow-dispatcher-app-key"
PEM = "FAKEPEMSECRET"
TOKEN = "ghs_FAKETOKENSECRET"
_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"runs": [], "revoked": [], "child_line": json.dumps({"verdict": "NOT_REVIEWER"})}

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        state["runs"].append((cmd, kwargs))
        if cmd[0] == "aws":
            return SimpleNamespace(returncode=state.get("aws_rc", 0), stdout=PEM + "\n")
        if state.get("child_raises"):
            raise RuntimeError("child failed")
        return SimpleNamespace(returncode=state.get("child_rc", 0), stdout="noise\n" + state["child_line"] + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Popen")))
    monkeypatch.setattr(gat, "app_jwt", lambda client_id, pem, now: "JWTSECRET")
    monkeypatch.setattr(gat, "installation_token", lambda api, jwt, repo, perms: state.setdefault("perms", perms) and TOKEN)
    monkeypatch.setattr(gat, "revoke", lambda api, token: state["revoked"].append(token))
    return state


def _main(*extra: str) -> int:
    return probe.main(["--run-id", "5", "--key-parameter", KEY, *extra], version=(3, 12))


def test_old_python_exits_before_any_read(wired: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert probe.main(["--run-id", "5", "--key-parameter", KEY], version=(3, 9)) == 2
    assert wired["runs"] == [] and "dnf install" in capsys.readouterr().err


def test_default_version_is_the_interpreter(wired: dict[str, Any]) -> None:
    assert probe.main(["--run-id", "5", "--key-parameter", KEY]) == 0


def test_other_key_parameter_refuses_before_read(wired: dict[str, Any]) -> None:
    assert probe.main(["--run-id", "5", "--key-parameter", "/secret/other"], version=(3, 12)) == 2
    assert wired["runs"] == []


def test_token_reaches_child_only_through_env(wired: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert _main() == 0
    aws_cmd, aws_kwargs = wired["runs"][0]
    assert aws_cmd[:4] == ["aws", "ssm", "get-parameter", "--name"] and "--with-decryption" in aws_cmd
    assert aws_cmd[aws_cmd.index("--region") + 1] == (_REPO / "config/aws-region").read_text().strip()
    assert "--profile" not in aws_cmd and aws_kwargs["capture_output"] is True
    child_cmd, child_kwargs = wired["runs"][1]
    assert child_cmd[0] == sys.executable and "env:GH_TOKEN" in child_cmd
    assert TOKEN not in " ".join(child_cmd) and child_kwargs["env"]["GH_TOKEN"] == TOKEN
    assert wired["perms"] == {"actions": "write", "metadata": "read"}
    out = capsys.readouterr().out
    assert TOKEN not in out and PEM not in out and "JWTSECRET" not in out
    assert "child_exit=0" in out and wired["revoked"] == [TOKEN]


def test_profile_is_passed(wired: dict[str, Any]) -> None:
    _main("--profile", "admin")
    aws_cmd = wired["runs"][0][0]
    assert aws_cmd[aws_cmd.index("--profile") + 1] == "admin"


def test_aws_failure_raises(wired: dict[str, Any]) -> None:
    wired["aws_rc"] = 255
    with pytest.raises(RuntimeError, match="exited 255"):
        _main()


def test_revoke_runs_after_child_failure(wired: dict[str, Any]) -> None:
    wired["child_raises"] = True
    with pytest.raises(RuntimeError):
        _main()
    assert wired["revoked"] == [TOKEN]


def test_token_echo_is_redacted(wired: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    wired["child_line"] = json.dumps({"verdict": "NOT_REVIEWER", "x": TOKEN})
    _main()
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("verdict", "code"),
    [
        ("TOKEN_LACKS_PERMISSION", 0),
        ("NOT_REVIEWER", 0),
        ("APPROVED", 1),
        ("REFUSED", 2),
        ("PROXY_BLOCKED", 2),
        ("UNAVAILABLE", 2),
        ("INCONCLUSIVE", 2),
        ("PROBE_INVALID", 2),
    ],
)
def test_exit_codes(wired: dict[str, Any], verdict: str, code: int) -> None:
    wired["child_line"] = json.dumps({"verdict": verdict})
    assert _main() == code


@pytest.mark.parametrize("line", ["not json", json.dumps(["x"])])
def test_unparseable_child_line_proves_nothing(wired: dict[str, Any], line: str) -> None:
    wired["child_line"] = line
    assert _main() == 2


def test_empty_child_output(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "_run_child", lambda repo, run_id, token: (3, ""))
    assert _main() == 2


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


_ALLOWED_THIRD_PARTY = {
    "yaml",
    "_yaml",
    "jwt",
    "cryptography",
    "_cffi_backend",
    "cffi",
    "pycparser",
    "cython_runtime",
    "scripts",
    "src",
}


def _closure() -> set[str]:
    code = (
        "import sys; import scripts.session.workflow_dispatcher_probe; "
        "print(' '.join(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO, check=True)
    return set(done.stdout.split())


def test_import_closure_is_stdlib_yaml_jwt_cryptography_and_repo() -> None:
    allowed = set(sys.stdlib_module_names) | _ALLOWED_THIRD_PARTY | {"__future__", "__main__", "builtins", "sitecustomize"}
    tops = _closure()
    assert "boto3" not in tops and "botocore" not in tops
    assert {t for t in tops if not t.startswith("_")} <= allowed, sorted(tops - allowed)


def test_import_closure_rejects_boto3_in_probe(tmp_path: Path) -> None:
    fake = tmp_path / "p.py"
    fake.write_text("import boto3\n", encoding="utf-8")
    assert "boto3" in _imports(fake)


def test_child_error_survives_a_failing_revoke(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wired["child_raises"] = True
    monkeypatch.setattr(gat, "revoke", lambda api, token: (_ for _ in ()).throw(RuntimeError("revoke down")))
    with pytest.raises(RuntimeError, match="child failed"):
        _main()
    assert "token revoke also failed" in capsys.readouterr().err
