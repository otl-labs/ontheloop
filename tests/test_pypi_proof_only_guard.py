"""Tests for scripts/ci/pypi_proof_only_guard.py (rec-4537)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.checks._common import ROOT
from scripts.ci import pypi_proof_only_guard as guard

WHEEL = "ontheloop-0.0.0-py3-none-any.whl"
SDIST = "ontheloop-0.0.0.tar.gz"


def _dist(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def _fetcher(*filenames: str, calls: list[tuple[str, dict[str, str]]] | None = None):
    def fetch(url: str, headers: dict[str, str]) -> Any:
        if calls is not None:
            calls.append((url, headers))
        return {"files": [{"filename": name} for name in filenames]}

    return fetch


def _no_network(url: str, headers: dict[str, str]) -> Any:
    raise AssertionError("fetch must not be called")


class TestProofOnlyGuard:
    def test_off_is_a_noop_that_never_fetches(self, tmp_path: Path) -> None:
        assert guard.check("ontheloop", tmp_path / "missing", "false", _no_network) == (0, "PROOF_ONLY_OFF")

    @pytest.mark.parametrize("flag", [None, "True", "", "1"])
    def test_invalid_flag_fails_closed(self, tmp_path: Path, flag: str | None) -> None:
        code, message = guard.check("ontheloop", _dist(tmp_path, WHEEL, SDIST), flag, _no_network)
        assert code == 2 and message.startswith("PROOF_ONLY_FLAG_INVALID")

    def test_empty_or_missing_dist_is_refused(self, tmp_path: Path) -> None:
        for dist in (tmp_path, tmp_path / "missing"):
            code, message = guard.check("ontheloop", dist, "true", _no_network)
            assert code == 1 and message.startswith("PROOF_ONLY_NO_DIST")

    def test_stray_file_is_refused_before_any_fetch(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path, WHEEL, SDIST, "ontheloop-0.0.0.zip")
        code, message = guard.check("ontheloop", dist, "true", _no_network)
        assert code == 1 and "unexpected file ontheloop-0.0.0.zip" in message

    def test_file_not_on_pypi_is_refused_and_named(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path, WHEEL, "ontheloop-0.0.1.tar.gz")
        code, message = guard.check("ontheloop", dist, "true", _fetcher(WHEEL, SDIST))
        assert code == 1 and "ontheloop-0.0.1.tar.gz" in message and WHEEL not in message

    def test_fetch_error_fails_closed_naming_only_the_type(self, tmp_path: Path) -> None:
        def boom(url: str, headers: dict[str, str]) -> Any:
            raise OSError("secret detail")

        code, message = guard.check("ontheloop", _dist(tmp_path, WHEEL), "true", boom)
        assert (code, message) == (1, "PROOF_ONLY_UNVERIFIED: OSError")

    def test_bad_response_shape_fails_closed(self, tmp_path: Path) -> None:
        code, message = guard.check("ontheloop", _dist(tmp_path, WHEEL), "true", lambda u, h: {"nope": []})
        assert (code, message) == (1, "PROOF_ONLY_UNVERIFIED: KeyError")

    def test_fetch_receives_simple_api_url_and_pep691_header(self, tmp_path: Path) -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        guard.check("ontheloop", _dist(tmp_path, WHEEL), "true", _fetcher(WHEEL, calls=calls))
        assert calls == [("https://pypi.org/simple/ontheloop/", {"Accept": "application/vnd.pypi.simple.v1+json"})]

    def test_all_files_published_passes(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path, WHEEL, SDIST)
        assert guard.check("ontheloop", dist, "true", _fetcher(WHEEL, SDIST)) == (0, "PROOF_ONLY_OK files=2")

    def test_main_wrong_argument_count_returns_2(self) -> None:
        assert guard.main([]) == 2
        assert guard.main(["a", "b", "c"]) == 2

    def test_main_prints_off_and_returns_0(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.setenv("PROOF_ONLY", "false")
        assert guard.main(["ontheloop", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == "PROOF_ONLY_OFF"

    def test_module_imports_are_stdlib_only(self) -> None:
        tree = ast.parse((ROOT / "scripts" / "ci" / "pypi_proof_only_guard.py").read_text(encoding="utf-8"))
        roots = {alias.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names} | {
            n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        }
        assert roots <= set(sys.stdlib_module_names)
