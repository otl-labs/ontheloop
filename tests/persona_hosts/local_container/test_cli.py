"""The local_container CLI with run_image monkeypatched: argument mapping, the three files, and the refusals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.persona_hosts.local_container import cli
from src.persona_hosts.local_container.adapter import RunImageResult

IMAGE = "sha256:" + "a" * 64
SIDECAR = b'{"agent":"canary-persona"}'


def _argv(tmp_path: Path, out: Path | None = None, input_path: Path | None = None) -> list[str]:
    return [
        "--image", IMAGE, "--persona", "canary_persona", "--input", str(input_path or tmp_path / "in.json"),
        "--out", str(out or tmp_path / "out"), "--project-ref", "synthetic:cli", "--agent", "canary-persona",
    ]  # fmt: skip


def _input(tmp_path: Path) -> Path:
    path = tmp_path / "in.json"
    path.write_text('{"notes": "x"}', encoding="utf-8")
    return path


def test_cli_writes_result_events_and_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(image: str, persona: str, payload: dict[str, Any], **kwargs: Any) -> RunImageResult:
        seen.update(image=image, persona=persona, payload=payload, **kwargs)
        return RunImageResult(0, {"outcome": "success"}, {"header": {}}, "", SIDECAR)

    monkeypatch.setattr(cli, "run_image", fake)
    _input(tmp_path)
    assert cli.main([*_argv(tmp_path), "--network", "n1", "--timeout", "9"]) == 0
    assert (seen["image"], seen["persona"], seen["payload"]) == (IMAGE, "canary_persona", {"notes": "x"})
    assert (seen["project_ref"], seen["agent"], seen["network"], seen["timeout_s"]) == (
        "synthetic:cli",
        "canary-persona",
        "n1",
        9,
    )
    out = tmp_path / "out"
    assert json.loads((out / "result.json").read_text()) == {"outcome": "success"}
    assert json.loads((out / "events.json").read_text()) == {"header": {}}
    assert (out / "host_run.json").read_bytes() == SIDECAR

    monkeypatch.setattr(cli, "run_image", lambda *a, **k: RunImageResult(4, None, None, "boom", SIDECAR))
    assert cli.main(_argv(tmp_path, out=tmp_path / "out2")) == 4
    assert json.loads((tmp_path / "out2" / "events.json").read_text()) is None
    assert json.loads((tmp_path / "out2" / "result.json").read_text()) is None


def test_cli_refuses_a_non_empty_out_dir_or_unreadable_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def never(*a: Any, **k: Any) -> RunImageResult:
        calls.append(1)
        return RunImageResult(0, {}, None, "", SIDECAR)

    monkeypatch.setattr(cli, "run_image", never)
    _input(tmp_path)
    full = tmp_path / "full"
    full.mkdir()
    (full / "keep").write_text("x")
    assert cli.main(_argv(tmp_path, out=full)) == 2
    assert [p.name for p in full.iterdir()] == ["keep"]

    for bad in (tmp_path / "missing.json", tmp_path / "list.json"):
        if bad.name == "list.json":
            bad.write_text("[1]")
        out = tmp_path / f"out-{bad.name}"
        assert cli.main(_argv(tmp_path, out=out, input_path=bad)) == 2
        assert not out.exists()

    def refuse(*a: Any, **k: Any) -> RunImageResult:
        raise ValueError("bad sidecar")

    monkeypatch.setattr(cli, "run_image", refuse)
    assert cli.main(_argv(tmp_path, out=tmp_path / "v")) == 2
    assert not (tmp_path / "v").exists()

    def unreachable(*a: Any, **k: Any) -> RunImageResult:
        raise RuntimeError("docker unreachable")

    monkeypatch.setattr(cli, "run_image", unreachable)
    assert cli.main(_argv(tmp_path, out=tmp_path / "r")) == 3
    assert not (tmp_path / "r").exists()
    assert calls == []
