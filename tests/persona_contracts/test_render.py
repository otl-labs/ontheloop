"""Deterministic strict renderer and its --check CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.persona_contracts import PersonaContract, load_index, render, sha256_hex
from scripts.persona_contracts.render import main
from tests.persona_contracts.fixture_tree import RENDERED_REL, build_tree, edit_contract, read_contract

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def _contract(tree: Path) -> PersonaContract:
    return PersonaContract.model_validate(read_contract(tree))


def test_same_contract_renders_same_bytes(tree: Path) -> None:
    index = load_index(tree)
    assert render(_contract(tree), index) == render(_contract(tree), index)
    assert sha256_hex(render(_contract(tree), index)) == read_contract(tree)["rendered_prompt_sha256"]


def test_clause_first_lf_and_one_trailing_newline(tree: Path) -> None:
    index = load_index(tree)
    data = render(_contract(tree), index)
    assert data.decode().startswith(index.rendered_prompt.quoted_content_clause.strip())
    assert b"\r" not in data and data.endswith(b"\n") and not data.endswith(b"\n\n")


def test_unknown_placeholder_raises(tree: Path) -> None:
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", "Hello $nobody"))
    with pytest.raises(KeyError):
        render(_contract(tree), load_index(tree))


def test_stray_dollar_raises(tree: Path) -> None:
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", "costs $ 5"))
    with pytest.raises(ValueError):
        render(_contract(tree), load_index(tree))


def test_escaped_dollar_raises(tree: Path) -> None:
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", "costs $$5"))
    with pytest.raises(ValueError):
        render(_contract(tree), load_index(tree))


def test_trailing_whitespace_line_raises(tree: Path) -> None:
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", "line with space \n$persona"))
    with pytest.raises(ValueError):
        render(_contract(tree), load_index(tree))


def test_check_passes_on_fresh_and_fails_on_stale(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"], root=tree) == 0
    assert "RENDER-CHECK OK 1" in capsys.readouterr().out
    (tree / RENDERED_REL).write_bytes(b"stale\n")
    assert main(["--check"], root=tree) == 1
    assert "stale" in capsys.readouterr().out


def test_check_fails_on_missing_prompt(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / RENDERED_REL).unlink()
    assert main(["--check"], root=tree) == 1
    assert "missing" in capsys.readouterr().out


def test_write_emits_the_hash(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / RENDERED_REL).unlink()
    assert main(["--write", "fixture_verdict"], root=tree) == 0
    assert capsys.readouterr().out.strip() == read_contract(tree)["rendered_prompt_sha256"]


def test_rendered_prompts_are_not_text_converted() -> None:
    out = subprocess.run(
        ["git", "check-attr", "text", "docs/contracts/personas/rendered/x.txt"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().endswith("text: unset")


def test_write_unknown_persona_is_refused(tree: Path) -> None:
    assert main(["--write", "nobody"], root=tree) == 2
