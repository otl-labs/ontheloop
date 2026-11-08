"""PR 4 (rec-4638): the code-owned comment gate for the trusted terraform/github plan job."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "ci" / "terraform_github_plan_comment.py"
PERSONAL = REPO_ROOT / "scripts" / "ci" / "terraform_personal_plan_report.py"
SHA = "a" * 40
URL = "https://github.com/o/r/actions/runs/123"
CLEAN_BODY = "### terraform/github speculative plan (trusted, Decision 238)\nState: CHANGES\n1 to add\n"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load(GATE, "tf_github_plan_comment")


def _run(*args: str, stdin: str = "", env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(GATE), *args], input=stdin, capture_output=True, text=True, env=env, check=False
    )


def _refusal(cat: str, tmp_path: Path, overlay: str = "", screen: str = "") -> str:
    o, s = tmp_path / "overlay.txt", tmp_path / "screen.txt"
    o.write_text(overlay, encoding="utf-8")
    s.write_text(screen, encoding="utf-8")
    return gate.render_refusal(cat, SHA, URL, str(o), str(s))


@pytest.mark.parametrize("cat", gate.CATEGORIES)
def test_each_category_renders(cat: str, tmp_path: Path) -> None:
    body = _refusal(cat, tmp_path)
    assert f"State: {cat}" in body
    assert body.startswith(gate.HEADER)


def test_there_are_eleven_categories() -> None:
    assert len(gate.CATEGORIES) == 11


def test_unknown_category_exits_two_and_prints_nothing(tmp_path: Path) -> None:
    res = _run("refusal", "--category", "OK", "--head-sha", SHA, "--run-url", URL)
    assert res.returncode == 2 and res.stdout == ""


@pytest.mark.parametrize("sha,url", [("abc", URL), (SHA, "https://example.com/x"), (SHA, URL + "?x=1")])
def test_bad_sha_or_url_is_refused(sha: str, url: str, tmp_path: Path) -> None:
    res = _run("refusal", "--category", "INIT_FAILED", "--head-sha", sha, "--run-url", url)
    assert res.returncode == 2 and res.stdout == ""


OVERLAY_LINES = [
    "OVERLAY_REFUSED main_only_file_changed terraform/github/main.tf",
    "OVERLAY_REFUSED unknown_root",
    "OVERLAY_REFUSED bad=value x.tf",
    "OVERLAY_REFUSED rule path trailing",
    'OVERLAY_REFUSED rule "quoted"',
    "noise line",
]
SCREEN_LINES = [
    "REFUSED resource_type repo.tf:12",
    "REFUSED rule main.tf:3 trailing",
    "REFUSED rule name=1:2",
    "REFUSED rule f.tf:x",
    "garbage",
]


def test_non_grammar_lines_are_dropped_and_grammar_lines_rerendered(tmp_path: Path) -> None:
    body = _refusal("OVERLAY_REFUSED", tmp_path, overlay="\n".join(OVERLAY_LINES))
    assert "REFUSED overlay:main_only_file_changed terraform/github/main.tf:0" in body
    assert "REFUSED overlay:unknown_root -:0" in body
    assert "=" not in body and '"' not in body and "trailing" not in body
    screen = _refusal("SCREEN_REFUSED", tmp_path, screen="\n".join(SCREEN_LINES))
    assert "REFUSED resource_type repo.tf:12" in screen
    assert "trailing" not in screen and "=" not in screen and "garbage" not in screen


def test_refusal_with_absent_inputs_renders_category_only() -> None:
    for cat in ("CONFIG_MISSING", "OVERLAY_REFUSED"):
        res = _run("refusal", "--category", cat, "--head-sha", SHA, "--run-url", URL)
        assert res.returncode == 0
        assert f"State: {cat}" in res.stdout
        assert "REFUSED " not in res.stdout.replace("State: OVERLAY_REFUSED", "")


def test_grammar_parity_with_the_personal_renderer(tmp_path: Path) -> None:
    personal = _load(PERSONAL, "tf_personal_plan_report")
    for cat, lines in (("OVERLAY_REFUSED", OVERLAY_LINES), ("SCREEN_REFUSED", SCREEN_LINES)):
        text = "\n".join(lines)
        (tmp_path / "f.txt").write_text(text, encoding="utf-8")
        f = str(tmp_path / "f.txt")
        mine = gate.render_refusal(cat, SHA, URL, f, f)
        theirs = personal.render_refusal(cat, SHA, URL, f, f)
        keep = lambda body: [ln for ln in body.splitlines() if ln.startswith("REFUSED ")]  # noqa: E731
        assert keep(mine) == keep(theirs), cat


def _clean(body: str, values: Path | None, **env: str):
    args = ["assert-clean", "--values-file", str(values or "/nonexistent/values")]
    full = {"PATH": "/usr/bin:/bin", **env}
    return _run(*args, stdin=body, env=full)


def test_clean_body_passes_with_and_without_values(tmp_path: Path) -> None:
    ids = tmp_path / "ids"
    ids.write_text(json.dumps([111222, 333444]), encoding="utf-8")
    assert _clean(CLEAN_BODY, ids).returncode == 0
    assert _clean(CLEAN_BODY, None).returncode == 0


@pytest.mark.parametrize(
    "leak",
    ["ghp_abcdefgh12345678", "ghs_abcdefgh12345678", "github_pat_abcdefgh12345678", "-----BEGIN " + "RSA PRIVATE KEY-----"],
)
def test_token_and_pem_shapes_fail(leak: str, tmp_path: Path) -> None:
    assert _clean(CLEAN_BODY + leak + "\n", None).returncode == 1


def test_exact_values_fail_as_whole_tokens(tmp_path: Path) -> None:
    ids = tmp_path / "ids"
    ids.write_text(json.dumps([111222]), encoding="utf-8")
    assert _clean(CLEAN_BODY + "user 111222 here\n", ids).returncode == 1
    assert _clean(CLEAN_BODY + "n 1112223 x\n", ids).returncode == 0
    assert _clean(CLEAN_BODY + "sha 4111222a" + "b" * 32 + "\n", ids).returncode == 0
    for env, value in (
        ("APP_CLIENT_ID", "Iv1.abc123"),
        ("AWS_ACCOUNT_ID", "424242424242"),
        ("AWS_ROLE_ID_ACCOUNT_PART", "AROAXYZ"),
    ):
        assert _clean(CLEAN_BODY + f"x {value} y\n", None, **{env: value}).returncode == 1
        assert _clean(CLEAN_BODY, None, **{env: ""}).returncode == 0


def test_malformed_or_unreadable_values_file_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "ids"
    for text in ("not json", '{"a": 1}', '["x"]', "[true]"):
        bad.write_text(text, encoding="utf-8")
        assert _clean(CLEAN_BODY, bad).returncode == 1
    bad.write_bytes(b"\xff\xfe")
    assert _clean(CLEAN_BODY, bad).returncode == 1


def test_in_process_main_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(CLEAN_BODY))
    assert gate.main(["assert-clean", "--values-file", str(tmp_path / "none")]) == 0
    assert gate.main(["refusal", "--category", "INIT_FAILED", "--head-sha", SHA, "--run-url", URL]) == 0
    assert "State: INIT_FAILED" in capsys.readouterr().out
