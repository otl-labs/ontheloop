"""Classifier for the rec writer session tripwire (PLAN-rec-writer-session-tripwire)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.session import rec_writer_match as match

U = "https://example.invalid/prod/ops/writer"
PORTAL_HEREDOC = (
    "bin/venv-python -m scripts.ops_data_portal --file-rec --context-v2-json - <<'EOF'\n"
    '{"problem": "_ducklake_write( retried 3x on ops/writer"}\nEOF'
)
SSM = "aws ssm get-parameter --name /agent-platform/ducklake/api_url/ops/writer --query Parameter.Value --output text"
B_CALL = "_ducklake_write('ops_recommendations', {}, action='file_ops')"

DENY = [
    ("A1", f"curl -X POST {U} -d @rec.json", "http_client"),
    ("A2", f"timeout 30 curl -sS {U}", "http_client"),
    ("A3", f"env A=1 /usr/bin/curl {U}", "http_client"),
    ("A4", f'bash -c "curl {U}"', "http_client"),
    ("A5", f'URL=$({SSM}); curl -X POST "$URL" -d @r.json', "http_client"),
    ("A6", 'wget --post-data=x "$DUCKLAKE_WRITER_URL"', "http_client"),
    ("B1", f'bin/venv-python -c "from scripts.ops_portal.writer_transport import _ducklake_write; {B_CALL}"', "inline_code"),
    (
        "B2",
        f"bin/venv-python - <<'EOF'\nfrom scripts.ops_portal import writer_transport\n{B_CALL}\nEOF",
        "inline_code",
    ),
    ("B3", "python3 -c \"import requests; requests.post(BASE + '/ops/writer', data='{}')\"", "inline_code"),
    ("A7", f"for u in a; do curl -sS {U}; done", "http_client"),
    ("A8", f"while true; do timeout 5 curl -sS {U}; sleep 30; done", "http_client"),
    (
        "C1",
        "aws lambda invoke --function-name agent-platform-ducklake-writer --payload file://p.json out.json",
        "lambda_invoke",
    ),
    ("C2", "aws lambda invoke --function-name agent-platform-ducklake-writer:live out.json", "lambda_invoke"),
]

ALLOW = [
    "grep -rn ops/writer scripts/",
    "cat scripts/ops_portal/writer_transport.py",
    "bin/venv-python -m pytest tests/ops_data_portal/test_writer_transport.py -q",
    f'bin/venv-python -m scripts.ops_data_portal --file-rec --title "curl {U} fails in _ducklake_write("',
    f"bin/venv-python -m scripts.ops_data_portal --file-rec --context \"repro: bash -c 'curl {U}'\"",
    PORTAL_HEREDOC,
    (
        'bin/venv-python -c "from scripts.ops_data_portal import file_rec; '
        "file_rec(title='_ducklake_write( times out on ops/writer')\""
    ),
    SSM,
    "aws lambda get-function-url-config --function-name agent-platform-ducklake-writer",
    "curl -sS https://example.invalid/prod/ops/reader",
    (
        "URL=$(aws ssm get-parameter --name /agent-platform/ducklake/telemetry_writer_url --query Parameter.Value "
        '--output text); curl -X POST "$URL" -d @e.json'
    ),
    "git log -S _ducklake_write --oneline",
]


@pytest.mark.parametrize(("name", "command", "shape"), DENY, ids=[d[0] for d in DENY])
def test_direct_writer_calls_are_refused(name: str, command: str, shape: str) -> None:
    hit = match.classify(command)
    assert hit is not None, name
    assert hit.shape == shape


@pytest.mark.parametrize("command", ALLOW)
def test_ordinary_commands_pass(command: str) -> None:
    assert match.classify(command) is None


def test_fast_path_skips_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("walk called")

    monkeypatch.setattr(match.shell, "walk", boom)
    assert match.classify("ls -la && git status") is None


def test_transport_names_still_exist() -> None:
    from scripts.ops_portal import writer_transport

    assert callable(writer_transport._ducklake_write)
    assert callable(writer_transport._resolve_writer_url)


@pytest.mark.parametrize("words", [["do"], ["timeout", "5", "ls"], ["ls", "curl"]])
def test_client_word_absent(words: list[str]) -> None:
    assert match._client_word(words) is None
