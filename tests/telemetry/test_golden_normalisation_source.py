from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from tests.fixtures import turn_capture_corpus as corpus

INDEX = Path(corpus.GOLDEN_DIR) / "index.json"


def _row(extra: dict[str, object]) -> dict[str, object]:
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {"external_ref": "r", "event_timestamp": stamp, "session_started_at": stamp, **extra}


def test_normalisation_drops_exactly_the_index_list() -> None:
    listed = json.loads(INDEX.read_text(encoding="utf-8"))["normalised_out"]
    assert listed
    row = _row({**dict.fromkeys(listed, "x"), "keepme": "y"})
    (norm,) = corpus.normalise([("telemetry_agents", [row])])
    assert not set(listed) & set(norm)
    assert norm["keepme"] == "y"


def test_no_literal_drop_list_remains() -> None:
    listed = set(json.loads(INDEX.read_text(encoding="utf-8"))["normalised_out"])
    tree = ast.parse(Path(corpus.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            names = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            assert not names & listed, f"literal drop list at line {node.lineno}"
