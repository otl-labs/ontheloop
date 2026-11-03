"""Integration (local only, never CI): the check library over every real transcript tree on this machine.

Real trees exist only in a live session (TURN_CAPTURE_REAL_TRANSCRIPTS, else ~/.claude/projects/*/*.jsonl, found the way
tests/fixtures/turn_capture_real.py finds them). Prints counts, model names and finding classes only, never content (this
repository's CI logs are public); prints LEDGER_UNPROVEN when no tree holds a cost-state record; fails only on an alarm.
"""

from __future__ import annotations

from collections import Counter

import pytest

from scripts.telemetry_verify import ledger, shape
from src.turn_capture.transcript import FsTree
from tests.fixtures.turn_capture_real import discover

pytestmark = pytest.mark.integration


def _line(index: int, tree_models: int, classes: Counter[str], nonfinal: dict[tuple[str, str], int]) -> str:
    kinds = {f"{kind}/{position}": n for (kind, position), n in sorted(nonfinal.items())}
    return f"REAL_TREE {index}: ledger_models={tree_models} classes={dict(sorted(classes.items()))} nonfinal={kinds}"


def test_real_trees_report_counts_and_alarm_on_no_alarm_class_finding() -> None:
    paths = discover()
    if not paths:
        pytest.skip("no real transcript tree on this machine")
    alarms: list[str] = []
    cost_states = 0
    for index, path in enumerate(paths):
        tree = FsTree(path)
        findings = ledger.reconcile_ledger(tree)
        result = shape.check_shape(tree)
        cost_states += len({f.cut_index for f in findings})
        classes = Counter(f.cls for f in findings) + Counter(f.name for f in result.findings if f.name != "nonfinal_call")
        print(_line(index, len([f for f in findings if f.model]), classes, result.nonfinal))
        alarms += [f"tree {index}: {f.cls}" for f in findings if f.alarm]
        alarms += [f"tree {index}: {f.name}" for f in result.findings if f.alarm]
    if not cost_states:
        print("LEDGER_UNPROVEN: no tree holds a cost-state record")
    print(f"REAL_TREES trees={len(paths)} cost_state_records={cost_states}")
    assert alarms == []
