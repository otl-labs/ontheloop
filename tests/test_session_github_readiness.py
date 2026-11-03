from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.session import github_readiness


def _result(code: int = 0, stdout: str = "work\n") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr="")


def test_capabilities_are_independent_and_pr_write_unknown() -> None:
    with (
        patch("scripts.session.github_readiness.subprocess.run", side_effect=[_result(), _result(), _result()]),
        patch("scripts.session.github_readiness._authenticated_read", return_value=("fail", "http 401")),
    ):
        report = github_readiness.probe()
    assert report["anonymous_fetch"]["status"] == "pass"
    assert report["authenticated_repository_read"]["status"] == "fail"
    assert report["feature_branch_push_dry_run"]["status"] == "pass"
    assert report["pr_write"]["status"] == "unknown"


def test_authenticated_read_requires_token(tmp_path) -> None:
    with patch("pathlib.Path.home", return_value=tmp_path):
        assert github_readiness._authenticated_read() == ("unknown", "token unavailable")


def test_authenticated_read_uses_api_without_exposing_token(tmp_path) -> None:
    token = tmp_path / ".config" / "gh-mcp" / "token"
    token.parent.mkdir(parents=True)
    token.write_text("credential-value", encoding="utf-8")
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("scripts.session.github_readiness.urllib.request.urlopen", return_value=response),
    ):
        assert github_readiness._authenticated_read() == ("pass", "")


def test_authenticated_read_handles_empty_token_http_failure_and_io_error(tmp_path) -> None:
    token = tmp_path / ".config" / "gh-mcp" / "token"
    token.parent.mkdir(parents=True)
    token.write_text("", encoding="utf-8")
    with patch("pathlib.Path.home", return_value=tmp_path):
        assert github_readiness._authenticated_read() == ("unknown", "token unavailable")
    token.write_text("value", encoding="utf-8")
    response = MagicMock(status=403)
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("scripts.session.github_readiness.urllib.request.urlopen", return_value=response),
    ):
        assert github_readiness._authenticated_read() == ("fail", "http 403")
    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("scripts.session.github_readiness.urllib.request.urlopen", side_effect=OSError("offline")),
    ):
        assert github_readiness._authenticated_read() == ("unknown", "OSError")


def test_timeout_is_unknown_and_does_not_expose_command_data() -> None:
    with patch(
        "scripts.session.github_readiness.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("git", 1)
    ):
        assert github_readiness._run(["git", "fetch"]) == ("unknown", "TimeoutExpired")


def test_main_prints_report(capsys) -> None:
    with patch("scripts.session.github_readiness.probe", return_value={"pr_write": {"status": "unknown"}}):
        assert github_readiness.main() == 0
    assert '"pr_write"' in capsys.readouterr().out


def test_authenticated_read_targets_the_ci_repository(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    token = tmp_path / ".config" / "gh-mcp" / "token"
    token.parent.mkdir(parents=True)
    token.write_text("credential-value", encoding="utf-8")
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("scripts.session.github_readiness.urllib.request.urlopen", return_value=response) as urlopen,
    ):
        assert github_readiness._authenticated_read() == ("pass", "")
    assert urlopen.call_args.args[0].full_url == "https://api.github.com/repos/example-org/example-repo"
