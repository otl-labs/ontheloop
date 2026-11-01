"""Line-cut helpers for record_turn tests: a tree cut to per-stream line counts, and the valid incremental cut paths."""

from __future__ import annotations

from src.turn_capture.streams import ROOT, parse_tree
from src.turn_capture.transcript import MemTree
from tests.fixtures.turn_capture_corpus import SID, fork_boundary


def line_counts(files):
    counts = {ROOT: files[f"{SID}.jsonl"].count("\n")}
    for rel, body in files.items():
        if "/subagents/agent-" in rel and rel.endswith(".jsonl"):
            counts[rel.split("agent-")[1][: -len(".jsonl")]] = body.count("\n")
    return counts


def rel(key):
    return f"{SID}.jsonl" if key == ROOT else f"{SID}/subagents/agent-{key}.jsonl"


def cut(files, cut):
    out = dict(files)
    for key, n in cut.items():
        out[rel(key)] = "".join(files[rel(key)].splitlines(keepends=True)[:n])
    return out


def valid(files, at, full):
    parsed = parse_tree(MemTree(cut(files, at)), None, False)
    return all(at[s.agent_id] == full[s.agent_id] for s in parsed.spawns if s.complete)


def paths(files):
    full = line_counts(files)
    keys = list(full)
    orders = [keys, keys[::-1], "rotate"]
    for order in orders:
        at = dict.fromkeys(keys, 0)
        at[ROOT] = fork_boundary(files)
        path = [dict(at)]
        turn = 0
        while at != full:
            prefer = [keys[(turn + i) % len(keys)] for i in range(len(keys))] if order == "rotate" else order
            turn += 1
            for key in prefer:
                if at[key] < full[key] and valid(files, {**at, key: at[key] + 1}, full):
                    at[key] += 1
                    path.append(dict(at))
                    break
            else:
                raise AssertionError("no valid step")
        yield path
