"""app_token.session: the row first, claims before the assume, an exact mint request, revoke on exit, nothing printed."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import app_token, oidc_assume, override
from src.data.handlers import github_app_token

REPO = "o/r"
APP_ID = 424242
SUB = "repo:o/r:environment:persona-verdict-app"
REF = f"{REPO}/.github/workflows/persona-verdict.yml@refs/heads/main"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nFAKEPEMBODY\n-----END RSA PRIVATE KEY-----"  # pragma: allowlist secret
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJ4In0.c2ln"  # pragma: allowlist secret
TOKEN = "ghs_" + "A" * 36
CREDS = {
    "AccessKeyId": "FAKEACCESSKEYTEST",
    "SecretAccessKey": "sk",  # pragma: allowlist secret
    "SessionToken": "IQoJb3JpZ2lu" + "x" * 20,  # pragma: allowlist secret
}
SECRET_SHAPES = re.compile(r"BEGIN (RSA )?PRIVATE KEY|eyJ[A-Za-z0-9_-]{10,}|ghs_|AKIA|ASIA|IQoJb3JpZ2lu|arn:aws|\b[0-9]{12}\b")


class FakeApi:
    instances: list[FakeApi] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.revoke_error: Exception | None = None
        self.minted_permissions: dict[str, str] = dict(app_token.PERMISSIONS)
        FakeApi.instances.append(self)

    def call(self, method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, token, body))
        if method == "GET":
            return 200, {"id": 9, "app_id": APP_ID}
        if method == "POST":
            return 201, {"token": TOKEN, "permissions": self.minted_permissions, "repository_selection": "selected"}
        if self.revoke_error:
            raise self.revoke_error
        return 204, None


class FakeBoto:
    class Session:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

        def client(self, name: str) -> Any:
            assert name == "ssm"
            return self

        def get_parameter(self, **kw: Any) -> dict[str, Any]:
            FakeBoto.params.append(kw)
            if FakeBoto.error:
                raise FakeBoto.error
            return {"Parameter": {"Value": PEM}}

    params: list[dict[str, Any]] = []
    error: Exception | None = None


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("An error occurred (" + code + ") with a secret detail")
        self.response = {"Error": {"Code": code}}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "sub": SUB,
        "ref": REF,
        "assume": oidc_assume.Outcome(200, "ok", dict(CREDS)),
        "log": [],
        "iss": [],
    }
    FakeApi.instances = []
    FakeBoto.params, FakeBoto.error = [], None
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("PERSONA_AWS_REGION", "xx-test-1")
    monkeypatch.setattr(override, "app_id", lambda: APP_ID)
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: state["log"].append("token") or JWT)
    monkeypatch.setattr(oidc_assume, "claims", lambda jwt: {"sub": state["sub"], "job_workflow_ref": state["ref"]})

    def assume(role: str, jwt: str, env: Any = None) -> oidc_assume.Outcome:
        state["log"].append(("assume", role))
        return state["assume"]

    monkeypatch.setattr(oidc_assume, "assume", assume)
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto)

    def app_jwt(client_id: str, pem: str, now: int) -> str:
        state["iss"].append(client_id)
        return JWT

    monkeypatch.setattr(github_app_token, "app_jwt", app_jwt)
    monkeypatch.setattr(github_app_token, "GitHubApi", FakeApi)
    return state


def _run(capfd: pytest.CaptureFixture[str]) -> tuple[str | None, str]:
    token = None
    try:
        with app_token.session(REPO) as token:
            pass
    except (app_token.AppTokenError, RuntimeError):
        pass
    out = capfd.readouterr()
    assert not SECRET_SHAPES.search(out.out + out.err), out.out + out.err
    return token, out.out


def test_a_good_session_mints_the_exact_request_and_revokes(wired: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    token, out = _run(capfd)
    assert token == TOKEN and wired["iss"] == [str(APP_ID)]
    api = FakeApi.instances[0]
    assert [c[:2] for c in api.calls] == [
        ("GET", f"/repos/{REPO}/installation"),
        ("POST", "/app/installations/9/access_tokens"),
        ("DELETE", "/installation/token"),
    ]
    assert api.calls[1][3] == {"repositories": ["r"], "permissions": {"checks": "write", "metadata": "read"}}
    assert api.calls[2][2] == TOKEN
    assert FakeBoto.params == [{"Name": app_token.KEY_PARAMETER, "WithDecryption": True}]
    assert wired["log"] == ["token", ("assume", oidc_assume.APP_ROLE)]
    lines = out.splitlines()
    assert lines[0] == f"OIDC-CLAIMS sub={SUB} job_workflow_ref={REF}"
    assert lines[1] == f"OIDC-ASSUME role={oidc_assume.APP_ROLE} expect=ok got=ok"
    assert lines[2:] == [f"APP-TOKEN minted app_id={APP_ID}", "APP-TOKEN revoked"]


@pytest.mark.parametrize("row", ["null", "missing", "unreadable"])
def test_a_bad_row_fails_before_any_network_call(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], row: str, tmp_path: Path
) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(github_app_token, "GitHubApi", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    cfg = tmp_path / "docs/contracts"
    cfg.mkdir(parents=True)
    if row == "null":
        (cfg / "project-configuration.yaml").write_text(
            "settings:\n  - id: gates.verdict_app.app_id\n    defaults: {local: null, cloud: null}\n"
        )
    elif row == "missing":
        (cfg / "project-configuration.yaml").write_text("settings: []\n")
    monkeypatch.setattr(override, "ROOT", tmp_path)
    token, out = _run(capfd)
    assert token is None and out.strip() == "APP-TOKEN FAIL row:Refused"


@pytest.mark.parametrize(
    "patch",
    [
        {"sub": "repo:o/r:environment:persona-verdict"},
        {"sub": "repo:o/r:ref:refs/heads/main"},
        {"ref": "x/y/.github/workflows/persona-verdict.yml@refs/heads/main"},
    ],
)
def test_foreign_claims_fail_before_the_assume(
    wired: dict[str, Any], capfd: pytest.CaptureFixture[str], patch: dict[str, str]
) -> None:
    wired.update(patch)
    token, out = _run(capfd)
    assert token is None and "APP-TOKEN FAIL claims:mismatch" in out and wired["log"] == ["token"]


def test_an_access_denied_assume_prints_one_fail_line(wired: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    wired["assume"] = oidc_assume.Outcome(403, "AccessDenied")
    token, out = _run(capfd)
    assert token is None and out.splitlines()[-1] == "APP-TOKEN FAIL assume:AccessDenied" and FakeBoto.params == []


def test_an_ssm_error_is_reported_by_code_only(wired: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    FakeBoto.error = ClientError("ParameterNotFound")
    token, out = _run(capfd)
    assert token is None and out.splitlines()[-1] == "APP-TOKEN FAIL ssm:ParameterNotFound" and FakeApi.instances == []


def test_a_non_client_error_is_reported_by_type_only(wired: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    FakeBoto.error = ValueError("secret detail " + PEM)
    _token, out = _run(capfd)
    assert out.splitlines()[-1] == "APP-TOKEN FAIL ssm:ValueError" and "secret detail" not in out


def test_a_permission_mismatch_prints_one_mint_line_and_revokes(
    wired: dict[str, Any], capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Wide(FakeApi):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__()
            self.minted_permissions = {"checks": "write", "metadata": "read", "contents": "write"}

    monkeypatch.setattr(github_app_token, "GitHubApi", Wide)
    token, out = _run(capfd)
    assert token is None and out.splitlines()[-1] == "APP-TOKEN FAIL mint:RuntimeError"
    assert [c[0] for c in FakeApi.instances[-1].calls] == ["GET", "POST", "DELETE"]


def test_a_failing_revoke_prints_one_line_and_raises(
    wired: dict[str, Any], capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(FakeApi):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__()
            self.revoke_error = github_app_token.GitHubApiError(500, "DELETE", "/installation/token", "boom secret")

    monkeypatch.setattr(github_app_token, "GitHubApi", Failing)
    with pytest.raises(app_token.AppTokenError):
        with app_token.session(REPO):
            pass
    out = capfd.readouterr().out
    assert out.count("APP-TOKEN FAIL revoke:GitHubApiError") == 1 and "APP-TOKEN revoked" not in out and "boom" not in out


def test_an_exception_in_the_body_still_revokes(wired: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(ZeroDivisionError):
        with app_token.session(REPO):
            raise ZeroDivisionError
    assert [c[0] for c in FakeApi.instances[0].calls][-1] == "DELETE" and "APP-TOKEN revoked" in capfd.readouterr().out


def test_the_handlers_are_restored(wired: dict[str, Any]) -> None:
    import signal

    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    with app_token.session(REPO):
        assert signal.getsignal(signal.SIGTERM) is not before[0]
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before


def test_importing_the_module_imports_neither_boto3_nor_jwt() -> None:
    import subprocess

    code = "import sys; import scripts.personas.app_token; print('boto3' in sys.modules, 'jwt' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r}); {code}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == "False False", done.stderr[-300:]
