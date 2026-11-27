import json
import shutil

import pytest
import yaml

from src.persona_backends import RunRefused
from src.persona_backends.contract import load
from src.persona_backends.events import tools_sha256
from src.persona_backends.repo_read import tool_spec
from tests.persona_backends import fakes
from tests.persona_backends.fakes import FakeClock, drive, final, tool
from tests.persona_backends.fakes import ScriptedTransport as T

BUDGET = {"max_calls": 2, "max_read_bytes": 1000, "max_search_results": 5, "max_total_bytes": 100000}


def _contract(tmp_path, trees=("head",), budget=None):
    root = tmp_path / "rt"
    (root / "rendered").mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    data["capabilities"] += [f"read_tree:{t}" for t in trees]
    data["budget"]["read_tree"] = budget or dict(BUDGET)
    data["failure_outputs"].append({"id": "review_incomplete", "meaning": "A read limit was reached."})
    path = root / "fixture_persona.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.PROMPT, root / "rendered" / "fixture_persona.txt")
    return path


def _trees(tmp_path):
    head = tmp_path / "head"
    head.mkdir(exist_ok=True)
    (head / "a.txt").write_text("alpha\nbeta needle\n", encoding="utf-8")
    return {"head": head}


def _call(**args):
    return tool({"tree": "head", **args}, name="read_tree")


def test_reads_land_on_the_turn_record(tmp_path):
    t = T([_call(op="read", path="a.txt"), final()])
    result, events = drive(t, contract=_contract(tmp_path), trees=_trees(tmp_path))
    assert result["outcome"] == "success"
    turn = next(e for e in events if e.get("kind") == "tool")
    assert [r["path"] for r in turn["reads"]] == ["a.txt"] and turn["reads"][0]["result"] == "ok"
    assert "1: alpha" in t.calls[1]["messages"][-1]["content"]


def test_cap_ends_the_run_as_review_incomplete(tmp_path):
    t = T([_call(op="list"), _call(op="list"), _call(op="list"), final()])
    result, events = drive(t, contract=_contract(tmp_path), trees=_trees(tmp_path))
    assert result["outcome"] == "failure" and result["failure"] == "review_incomplete"
    assert "max_calls" in result["detail"] and "2 calls" in result["detail"]
    assert events[-1]["failure"] == "review_incomplete" and len(t.calls) == 3
    assert events[-2]["reads"][0]["result"] == "cap"


def test_missing_head_tree_is_run_refused(tmp_path):
    with pytest.raises(RunRefused, match="read_tree:head needs a mounted tree"):
        drive(T([final()]), contract=_contract(tmp_path))
    with pytest.raises(RunRefused, match="mounted tree"):
        drive(T([final()]), contract=_contract(tmp_path, trees=("main",)), trees=_trees(tmp_path))


def test_read_tree_spec_is_in_tools_sha256(tmp_path):
    t = T([final()])
    _, events = drive(t, contract=_contract(tmp_path), trees=_trees(tmp_path))
    names = [x["function"]["name"] for x in t.calls[0]["tools"]]
    assert names == ["read_input", "read_tree"]
    assert events[0]["tools_sha256"] == tools_sha256(t.calls[0]["tools"])
    assert t.calls[0]["tools"][1] == tool_spec(("head",), load(_contract(tmp_path)).read_tree_budget)


def test_search_past_deadline_is_review_incomplete(tmp_path):
    contract = _contract(tmp_path)
    clock = FakeClock(step=0.0)
    t = T([_call(op="search", query="needle"), final()])

    class Slow:
        def __call__(self):
            return clock.now

    original = t.__call__

    async def advancing(persona, messages, **kwargs):
        result = await original(persona, messages, **kwargs)
        clock.now += 10_000
        return result

    t.__class__ = type("T2", (T,), {"__call__": lambda self, p, m, **k: advancing(p, m, **k)})
    result, _ = drive(t, contract=contract, trees=_trees(tmp_path), clock=Slow())
    assert result["failure"] == "review_incomplete" and "run deadline" in result["detail"]


def test_unnamed_trees_are_ignored(tmp_path):
    t = T([final()])
    result, _ = drive(t, trees=_trees(tmp_path))
    assert result["outcome"] == "success"
    assert [x["function"]["name"] for x in t.calls[0]["tools"]] == ["read_input"]
    assert json.dumps(t.calls[0]["tools"]).count("read_tree") == 0
