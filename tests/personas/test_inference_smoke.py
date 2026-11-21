"""inference_smoke: one role per process, claims before the assume, the assumed session reaches the smoke, no leak."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import inference_smoke, oidc_assume

ROOT = Path(__file__).resolve().parents[2]
REPO = "o/r"
GOOD_REF = f"{REPO}/.github/workflows/persona-verdict.yml@refs/heads/main"
GOOD_SUB = "repo:o/r:environment:persona-verdict"
CREDS = {"AccessKeyId": "FAKEACCESSKEYTEST", "SecretAccessKey": "sk", "SessionToken": "st"}  # pragma: allowlist secret
REGION = "xx-test-1"
PLANTED = "sk-planted-key-0123456789"


class FakeSession:
    instances: list[FakeSession] = []

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.regions: list[str] = []
        FakeSession.instances.append(self)

    def client(self, name: str, region_name: str) -> Any:
        self.regions.append(region_name)
        raise AssertionError("the smoke is stubbed in these cases")


def _env(tmp_path: Path, role: str = "primary") -> dict[str, str]:
    return {
        "PERSONA_SMOKE_ROLE": role,
        "PERSONA_AWS_REGION": REGION,
        "GITHUB_REPOSITORY": REPO,
        "RUNNER_TEMP": str(tmp_path),
    }


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"assumed": 0, "run": [], "sub": GOOD_SUB, "ref": GOOD_REF, "code": 0, "text": ""}
    FakeSession.instances = []
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: "jwt")
    monkeypatch.setattr(oidc_assume, "claims", lambda jwt: {"sub": state["sub"], "job_workflow_ref": state["ref"]})

    def assume(role: str, jwt: str, env: Any = None) -> oidc_assume.Outcome:
        state["assumed"] += 1
        return oidc_assume.Outcome(200, "ok", dict(CREDS))

    monkeypatch.setattr(oidc_assume, "assume", assume)
    monkeypatch.setattr(inference_smoke.boto3, "Session", FakeSession)

    def run(role: str, region: str | None = None, session: Any = None) -> int:
        state["run"].append((role, region, session))
        os.write(2, state["text"].encode())
        os.write(1, (PLANTED + "\n").encode())
        return int(state["code"])

    monkeypatch.setattr(inference_smoke.smoke, "run", run)
    return state


def test_a_pass_prints_only_marker_lines(stubbed: dict[str, Any], capfd: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, line, saved = inference_smoke.work(_env(tmp_path))
    print(line)
    out = capfd.readouterr()
    assert code == 0 and saved is None
    assert out.out.splitlines() == [
        f"OIDC-CLAIMS sub={GOOD_SUB} job_workflow_ref={GOOD_REF}",
        f"OIDC-ASSUME role={oidc_assume.ROLE} expect=ok got=ok",
        "INFERENCE-SMOKE role=primary outcome=PASS class=none",
    ]
    assert PLANTED not in out.out + out.err
    role, region, session = stubbed["run"][0]
    assert (role, region) == ("primary", REGION)
    assert session.kw == {
        "aws_access_key_id": CREDS["AccessKeyId"],
        "aws_secret_access_key": "sk",  # pragma: allowlist secret
        "aws_session_token": "st",
        "region_name": REGION,
    }


@pytest.mark.parametrize(
    ("text", "cls"),
    [
        ("FAIL [primary] contract load raised KeyError: 'x'\n", "contract:KeyError"),
        ("FAIL [primary] SSM error (AccessDeniedException)\n", "ssm:AccessDeniedException"),
        ("FAIL [primary] cold start raised AuthenticationError\n", "cold_start:AuthenticationError"),
        ("FAIL [primary] assertion=spent: detail with sk-secret\n", "assertion:spent"),
        ("FAIL [primary] Parameter '/x' has an empty value; set it\n", "empty_value"),
        ("something else with sk-secret\n", "unclassified"),
        ("FAIL [primary] cold start raised Bad-Name!\n", "cold_start:other"),
    ],
)
def test_a_failure_class_comes_from_a_closed_set(
    stubbed: dict[str, Any], capfd: pytest.CaptureFixture[str], tmp_path: Path, text: str, cls: str
) -> None:
    stubbed.update(code=1, text=text)
    code, line, _ = inference_smoke.work(_env(tmp_path))
    out = capfd.readouterr()
    assert code == 1 and line == f"INFERENCE-SMOKE role=primary outcome=FAIL class={cls}"
    assert "sk-secret" not in out.out + out.err + line


def test_each_tier_role_runs_alone(stubbed: dict[str, Any], tmp_path: Path) -> None:
    inference_smoke.work(_env(tmp_path, "warm_fetched_escape_hatch"))
    assert [r[0] for r in stubbed["run"]] == ["warm_fetched_escape_hatch"]


def test_an_unknown_role_fails_before_any_token(stubbed: dict[str, Any], tmp_path: Path) -> None:
    code, line, _ = inference_smoke.work(_env(tmp_path, "tier_3"))
    assert code == 1 and "class=bad_role" in line and stubbed["assumed"] == 0 and stubbed["run"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"sub": "repo:o/r:ref:refs/heads/main"},
        {"sub": "repo:o/r:environment:other"},
        {"ref": "o/r/.github/workflows/other.yml@refs/heads/main"},
        {"ref": "x/y/.github/workflows/persona-verdict.yml@refs/heads/main"},
    ],
)
def test_foreign_claims_fail_before_any_assume(stubbed: dict[str, Any], tmp_path: Path, patch: dict[str, str]) -> None:
    stubbed.update(patch)
    code, line, _ = inference_smoke.work(_env(tmp_path))
    assert code == 1 and "class=claims" in line and stubbed["assumed"] == 0 and stubbed["run"] == []


def test_a_refused_assume_fails_without_credentials(
    stubbed: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(oidc_assume, "assume", lambda *a, **k: oidc_assume.Outcome(403, "AccessDenied"))
    code, line, _ = inference_smoke.work(_env(tmp_path))
    assert code == 1 and "class=assume" in line and stubbed["run"] == []


def test_an_oidc_error_fails(stubbed: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: (_ for _ in ()).throw(oidc_assume.OidcError("x")))
    code, line, _ = inference_smoke.work(_env(tmp_path))
    assert code == 1 and "class=oidc" in line


CHILD = textwrap.dedent(
    """
    import atexit, os, sys, threading, time
    sys.path.insert(0, {root!r})
    from scripts.personas import inference_smoke, oidc_assume
    from scripts import smoke_test_inference_credentials as smoke

    oidc_assume.token = lambda env=None: "jwt"
    oidc_assume.claims = lambda jwt: {{"sub": {sub!r}, "job_workflow_ref": {ref!r}}}
    oidc_assume.assume = lambda *a, **k: oidc_assume.Outcome(200, "ok", {creds!r})

    class Client:
        def get_parameter(self, **kw):
            return {{"Parameter": {{"Value": {key!r}}}}}

    class Session:
        def __init__(self, **kw): pass
        def client(self, name, region_name): return Client()

    inference_smoke.boto3.Session = Session
    atexit.register(lambda: print("LATE-ATEXIT " + {key!r}))
    threading.Thread(target=lambda: (time.sleep(0.5), print("LATE-THREAD " + {key!r}, flush=True)), daemon=True).start()
    mode = sys.argv[1]
    if mode == "stub":
        def fake_run(role, region=None, session=None):
            print("RAW " + {key!r})
            print("RAW " + {key!r}, file=sys.stderr)
            return 0
        smoke.run = fake_run
    else:
        import http.server, json
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                auth = self.headers.get("Authorization", "")
                body = json.dumps({{"error": {{"message": "bad key " + auth, "type": "x"}}}}).encode()
                self.send_response(401); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        def cold(role):
            import litellm
            litellm.completion(model="openai/x", api_base="http://127.0.0.1:%d" % srv.server_port,
                               api_key=os.environ[{var!r}], messages=[{{"role": "user", "content": "hi"}}], num_retries=0)
        smoke.litellm_transport.cold_start_check = cold
    sys.exit(inference_smoke.main([]))
    """
)


def _child(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    var = "DEEPSEEK_API_KEY"
    from scripts import smoke_test_inference_credentials as smoke

    var = str(smoke._row("primary")["variable"])
    script = CHILD.format(root=str(ROOT), sub=GOOD_SUB, ref=GOOD_REF, creds=CREDS, key=PLANTED, var=var)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PERSONA_SMOKE_ROLE": "primary",
        "PERSONA_AWS_REGION": REGION,
        "GITHUB_REPOSITORY": REPO,
        "RUNNER_TEMP": str(tmp_path),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS": "True",
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", script, mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=180,
        check=False,
    )


def test_stdout_as_a_pipe_holds_only_the_three_marker_lines_in_order(tmp_path: Path) -> None:
    done = _child(tmp_path, "stub")
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.splitlines() == [
        f"OIDC-CLAIMS sub={GOOD_SUB} job_workflow_ref={GOOD_REF}",
        f"OIDC-ASSUME role={oidc_assume.ROLE} expect=ok got=ok",
        "INFERENCE-SMOKE role=primary outcome=PASS class=none",
    ]
    assert PLANTED not in done.stdout + done.stderr
    assert "LATE-" not in done.stdout + done.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_real_litellm_401_echoes_no_part_of_the_planted_key(tmp_path: Path) -> None:
    done = _child(tmp_path, "litellm")
    assert done.returncode == 1, done.stderr[-500:]
    lines = done.stdout.splitlines()
    assert lines[-1].startswith("INFERENCE-SMOKE role=primary outcome=FAIL class=cold_start:")
    assert len(lines) == 3
    both = done.stdout + done.stderr
    assert PLANTED not in both and PLANTED[:12] not in both and "LATE-" not in both
    assert list(tmp_path.iterdir()) == []
