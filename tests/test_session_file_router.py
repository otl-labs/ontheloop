"""Tests for scripts/session/file_router.py (PLAN-contract-pointer-sections)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.session import file_router


def _router(tmp_path: Path, routes: object) -> Path:
    path = tmp_path / "router.yaml"
    path.write_text(yaml.safe_dump({"routes": routes}), encoding="utf-8")
    return path


_ROUTES = [
    {"topic": "alpha-topic", "targets": ["a/one.py"], "description": "Something  about\nwidgets."},
    {"topic": "beta", "targets": ["b/Gadget.yaml", "b/two.py"], "description": "Plain."},
    {"topic": "gamma", "targets": ["c/three.py"], "description": 5},
    {"topic": "delta", "targets": ["d/four.py"]},
]


def test_route_terms_match_all_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    router = _router(tmp_path, _ROUTES)
    assert file_router.main(["alpha", "--router", str(router)]) == 0
    assert capsys.readouterr().out == "alpha-topic: a/one.py -- Something about widgets.\n1 route(s)\n"
    assert file_router.main(["gadget", "--router", str(router)]) == 0
    assert capsys.readouterr().out == "beta: b/Gadget.yaml, b/two.py -- Plain.\n1 route(s)\n"
    assert file_router.main(["WIDGETS", "--router", str(router)]) == 0
    assert "alpha-topic" in capsys.readouterr().out
    assert file_router.main(["py", "plain", "--router", str(router)]) == 0
    assert capsys.readouterr().out.startswith("beta:")
    assert file_router.main(["py", "--router", str(router)]) == 0
    out = capsys.readouterr().out
    assert "gamma: c/three.py -- \n" in out and "delta: d/four.py -- \n" in out and out.endswith("4 route(s)\n")


def test_no_match_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    router = _router(tmp_path, _ROUTES)
    assert file_router.main(["alpha", "plain", "--router", str(router)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "file_router: no route matches all of: alpha plain\n"


def test_missing_router_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert file_router.main(["x", "--router", str(tmp_path / "nope.yaml")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("file_router: ") and "does not exist" in err and "Traceback" not in err


def test_malformed_router_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    router = _router(tmp_path, "not-a-list")
    assert file_router.main(["x", "--router", str(router)]) == 1
    err = capsys.readouterr().err
    assert "routes" in err and "Traceback" not in err


def test_real_router_finds_platform_roadmap(capsys: pytest.CaptureFixture[str]) -> None:
    assert file_router.main(["roadmap"]) == 0
    out = capsys.readouterr().out
    assert "platform-roadmap: docs/ROADMAP-PLATFORM.yaml" in out and "route(s)" in out
