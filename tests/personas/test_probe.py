"""The deterministic probe persona: each marker yields its outcome."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "persona_verdict" / "probe.py"


def _run(tmp_path: Path, marker: str) -> str:
    src = tmp_path / "input.json"
    src.write_text(json.dumps({"marker": marker + "\n"}), encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", str(PROBE), str(src)], capture_output=True, text=True, check=True)
    return done.stdout


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("proceed", {"outcome": "success", "verdict": "PROCEED"}),
        (
            "revise",
            {
                "outcome": "success",
                "verdict": "REVISE",
                "findings": [{"id": "probe:revise-1", "title": "probe revise finding", "blocking": True}],
            },
        ),
        (
            "block",
            {
                "outcome": "success",
                "verdict": "BLOCK",
                "findings": [
                    {"id": "probe:block-1", "title": "probe block finding one", "blocking": True},
                    {"id": "probe:block-2", "title": "probe block finding two", "blocking": True},
                ],
            },
        ),
        (
            "flags",
            {
                "outcome": "success",
                "verdict": "FLAGS_FOUND",
                "findings": [{"id": "probe:flag-1", "title": "probe flag finding", "blocking": False}],
            },
        ),
        ("fail", {"outcome": "failure", "failure": "input_unusable", "detail": "probe fail marker"}),
    ],
)
def test_marker_selects_output(tmp_path: Path, marker: str, expected: dict[str, object]) -> None:
    assert json.loads(_run(tmp_path, marker)) == expected


def test_malformed_marker_prints_unparseable_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        json.loads(_run(tmp_path, "malformed"))
