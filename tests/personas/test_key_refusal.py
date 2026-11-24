from __future__ import annotations

from pathlib import Path

import pytest

from scripts.personas import key_refusal, oidc_assume

ROOT = Path(__file__).resolve().parents[2]
REPO = "o/r"
GOOD_REF = f"{REPO}/.github/workflows/persona-verdict.yml@refs/heads/main"


def _arrange(
    monkeypatch: pytest.MonkeyPatch,
    sub: str = "repo:o/r:ref:refs/heads/main",
    ref: str = GOOD_REF,
    outcome: oidc_assume.Outcome | Exception | None = None,
) -> list[str]:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(oidc_assume, "token", lambda env=None: "jwt")
    monkeypatch.setattr(oidc_assume, "claims", lambda jwt: {"sub": sub, "job_workflow_ref": ref})
    calls: list[str] = []
    result = outcome if outcome is not None else oidc_assume.Outcome(403, "AccessDenied")

    def fake(role: str, jwt: str, env: object = None) -> oidc_assume.Outcome:
        calls.append(role)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(oidc_assume, "assume", fake)
    return calls


def test_passes_only_on_403_access_denied(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _arrange(monkeypatch)
    assert key_refusal.main([]) == 0
    out = capsys.readouterr().out
    assert "KEY-REFUSAL PASS" in out and "OIDC-CLAIMS sub=repo:o/r:ref:refs/heads/main" in out
    assert "expect=AccessDenied got=AccessDenied" in out and calls == [oidc_assume.ROLE]


@pytest.mark.parametrize(
    "outcome",
    [
        oidc_assume.Outcome(200, "ok", {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c"}),
        oidc_assume.Outcome(403, "InvalidIdentityToken"),
        oidc_assume.Outcome(0, "neterror"),
        oidc_assume.Outcome(0, "timeout"),
        oidc_assume.Outcome(400, "AccessDenied"),
    ],
)
def test_anything_else_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: oidc_assume.Outcome
) -> None:
    _arrange(monkeypatch, outcome=outcome)
    assert key_refusal.main([]) == 1
    assert "KEY-REFUSAL FAIL" in capsys.readouterr().out


def test_a_sub_with_an_environment_part_fails_before_any_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _arrange(monkeypatch, sub="repo:o/r:environment:persona-verdict")
    assert key_refusal.main([]) == 1 and calls == []


def test_a_foreign_job_workflow_ref_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _arrange(monkeypatch, ref="o/r/.github/workflows/other.yml@refs/heads/main")
    assert key_refusal.main([]) == 1 and calls == []


def test_oidc_errors_fail(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _arrange(monkeypatch, outcome=oidc_assume.OidcError("PERSONA_AWS_REGION is missing or malformed"))
    assert key_refusal.main([]) == 1
    assert "KEY-REFUSAL FAIL" in capsys.readouterr().out


def test_the_role_name_equals_the_terraform_role() -> None:
    tf = (ROOT / "terraform/personal/oidc_persona_verdict_role.tf").read_text(encoding="utf-8")
    assert f'"{oidc_assume.ROLE}"' in tf
    assert key_refusal.ATTEMPTS == ((oidc_assume.ROLE, "AccessDenied"),)
