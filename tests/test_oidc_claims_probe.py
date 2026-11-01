from __future__ import annotations

import base64
import io
import json
from unittest import mock

import pytest

from scripts.ci import oidc_claims_probe as probe

_SIGNATURE = "SIGNATURESEGMENTVALUE"


def _b64(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _token(claims: dict[str, object]) -> str:
    return f"{_b64({'alg': 'none'})}.{_b64(claims)}.{_SIGNATURE}"


_CLAIMS = {
    "sub": "repo:o/r:environment:e",
    "ref": "refs/heads/main",
    "job_workflow_ref": "o/r/.github/workflows/w.yml@refs/heads/main",
    "event_name": "pull_request_target",
    "aud": "sts.amazonaws.com",
    "jti": "JTIVALUE",
    "actor": "someone",
    "extra_claim": "EXTRAVALUE",
    "iat": 1,
}


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], token: str, *args: str) -> tuple[int, str, str]:
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.invalid/token?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "bearer")
    monkeypatch.setattr(probe, "fetch_token", lambda audience, environ: token)
    code = probe.main(["--audience", "sts.amazonaws.com", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_prints_exactly_the_allowlisted_claims_in_order(monkeypatch, capsys) -> None:
    code, out, _ = _run(monkeypatch, capsys, _token(_CLAIMS))
    assert code == 0
    assert out.splitlines() == [
        "sub=repo:o/r:environment:e",
        "ref=refs/heads/main",
        "event_name=pull_request_target",
        "job_workflow_ref=o/r/.github/workflows/w.yml@refs/heads/main",
    ]


def test_non_allowlisted_claims_and_token_never_appear(monkeypatch, capsys) -> None:
    token = _token(_CLAIMS)
    _, out, err = _run(monkeypatch, capsys, token)
    for forbidden in ("sts.amazonaws.com", "JTIVALUE", "someone", "EXTRAVALUE", _SIGNATURE, token):
        assert forbidden not in out + err


@pytest.mark.parametrize("claims", [{"sub": "s"}, {"sub": "s", "job_workflow_ref": ""}])
def test_require_fails_on_absent_or_empty_claim(monkeypatch, capsys, claims) -> None:
    code, _, err = _run(monkeypatch, capsys, _token(claims), "--require", "job_workflow_ref")
    assert code == 1
    assert "job_workflow_ref" in err


def test_require_passes_when_present(monkeypatch, capsys) -> None:
    code, _, _ = _run(monkeypatch, capsys, _token(_CLAIMS), "--require", "job_workflow_ref")
    assert code == 0


@pytest.mark.parametrize("bad", ["notatoken", "a.!!!.c", "a..c", f"a.{_b64([1])}.c"])
def test_malformed_token_exits_nonzero_without_echo(monkeypatch, capsys, bad) -> None:
    code, out, err = _run(monkeypatch, capsys, bad)
    assert code == 1
    assert out == ""
    assert bad not in err
    assert err.strip() == "oidc_claims_probe: malformed token"


def test_fetch_builds_the_audience_query(monkeypatch) -> None:
    seen: list[object] = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        return io.StringIO(json.dumps({"value": "tok"}))

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    environ = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/t?a=1", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "b"}
    assert probe.fetch_token("sts.amazonaws.com", environ) == "tok"
    request = seen[0]
    assert request.full_url == "https://example.invalid/t?a=1&audience=sts.amazonaws.com"
    assert request.get_header("Authorization") == "Bearer b"


def test_fetch_error_has_fixed_message_without_url(monkeypatch) -> None:
    with mock.patch.object(probe.urllib.request, "urlopen", side_effect=OSError("boom https://secret.invalid")):
        with pytest.raises(probe.ProbeError) as exc:
            probe.fetch_token(
                "a", {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://secret.invalid", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "b"}
            )
    assert str(exc.value) == "token fetch failed"


def test_missing_request_environment_is_an_error() -> None:
    with pytest.raises(probe.ProbeError):
        probe.fetch_token("a", {})


def test_control_characters_in_a_claim_value_cannot_inject_a_runner_command(monkeypatch, capsys) -> None:
    claims = {"head_ref": "branch\n::add-mask::x", "sub": "s"}
    _, out, _ = _run(monkeypatch, capsys, _token(claims))
    assert len(out.splitlines()) == 2
    assert "\n::" not in out
