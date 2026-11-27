import hashlib
import json
import os

import pytest

from src.persona_backends.repo_read import ReadTree, tool_spec

BUDGET = {"max_calls": 5, "max_read_bytes": 200, "max_search_results": 3, "max_total_bytes": 4000}


def _tree(tmp_path):
    root = tmp_path / "head"
    if root.exists():
        return root
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "src" / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "src" / "bin.dat").write_bytes(b"\x00\x01\x02 needle")
    (root / "src" / "big.txt").write_text("\n".join(f"line {n} needle" for n in range(1, 60)), encoding="utf-8")
    os.symlink(root / "src" / "a.py", root / "src" / "link.py")
    (root / "src" / "zed.txt").write_text("z", encoding="utf-8")
    os.symlink(root / "src", root / "linkdir")
    return root


def _serve(reader, **args):
    return reader.serve(json.dumps(args))


def test_reads_are_bounded_recorded_and_traversal_refused(tmp_path):
    reader = ReadTree({"head": _tree(tmp_path)}, {**BUDGET, "max_calls": 50})
    for path in ("../x", "/etc/passwd", ".git/config", "src/../../x", "linkdir/a.py", "src/link.py"):
        text, record = _serve(reader, tree="head", op="read", path=path)
        assert record["result"] == "refused" and "one" not in text, path
    text, record = _serve(reader, tree="head", op="read", path="src/a.py", start=2, end=3)
    body = b"one\ntwo\nthree\n"
    assert record["result"] == "ok" and record["blob"] == hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()  # noqa: S324
    assert (record["start"], record["end"], record["tree"], record["op"]) == (2, 3, "head", "read")
    assert "2: two" in text and "3: three" in text and "1: one" not in text and record["blob"] in text
    assert "data" in text.splitlines()[0]
    text, record = _serve(reader, tree="head", op="read", path="src/big.txt")
    assert record["result"] == "ok" and "[truncated: next line" in text
    assert _serve(reader, tree="head", op="read", path="src/bin.dat")[1]["result"] == "binary"
    assert _serve(reader, tree="head", op="read", path="src/nope.py")[1]["result"] == "absent"
    assert _serve(reader, tree="main", op="read", path="src/a.py")[1]["result"] == "refused"


def test_search_and_list_are_capped(tmp_path):
    reader = ReadTree({"head": _tree(tmp_path)}, {**BUDGET, "max_calls": 50})
    text, record = _serve(reader, tree="head", op="search", query="needle")
    hits = [line for line in text.splitlines() if "needle" in line and not line.startswith("[")]
    assert record["result"] == "ok" and len(hits) == 3 and "more matches" in text
    assert "bin.dat" not in text and ".git" not in text
    text, _ = _serve(reader, tree="head", op="list", path="")
    assert ".git" not in text and "linkdir" not in text and "src/" in text
    text, _ = _serve(reader, tree="head", op="list", path="src")
    assert "link.py" not in text and "a.py" in text and "more entries" in text


def test_call_and_byte_caps_return_a_cap_record(tmp_path):
    reader = ReadTree({"head": _tree(tmp_path)}, {**BUDGET, "max_calls": 2})
    _serve(reader, tree="head", op="list")
    _serve(reader, tree="head", op="list")
    text, record = _serve(reader, tree="head", op="list")
    assert text == "" and record["result"] == "cap" and record["cap"] == "max_calls"
    small = ReadTree({"head": _tree(tmp_path)}, {**BUDGET, "max_total_bytes": 10})
    text, record = _serve(small, tree="head", op="read", path="src/a.py")
    assert text == "" and record["result"] == "cap" and record["cap"] == "max_total_bytes"


def test_deadline_stops_a_search(tmp_path):
    ticks = iter([0.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    reader = ReadTree({"head": _tree(tmp_path)}, BUDGET, deadline=1.0, clock=lambda: next(ticks))
    _, record = _serve(reader, tree="head", op="search", query="needle")
    assert record["result"] == "deadline"


def test_tool_spec_states_the_limits():
    spec = tool_spec(("head", "main"), BUDGET)
    assert "5 calls" in spec["function"]["description"]
    assert spec["function"]["parameters"]["properties"]["tree"]["enum"] == ["head", "main"]


@pytest.mark.parametrize("raw", ["{", "[]"])
def test_bad_arguments_are_refused(tmp_path, raw):
    _, record = ReadTree({"head": _tree(tmp_path)}, BUDGET).serve(raw)
    assert record["result"] == "refused"


def test_edge_paths_read_list_and_search(tmp_path):
    reader = ReadTree({"head": _tree(tmp_path)}, {**BUDGET, "max_calls": 100, "max_total_bytes": 10**6})
    cases = [
        ({"op": "read", "path": "src/nope.txt"}, "absent"),
        ({"op": "read", "path": "src/a.py/x"}, "absent"),
        ({"op": "read", "path": "src"}, "refused"),
        ({"op": "read", "path": ""}, "refused"),
        ({"op": "read", "path": "src/a.py", "start": 0}, "refused"),
        ({"op": "read", "path": "src/a.py", "start": True}, "refused"),
        ({"op": "read", "path": "src/bin.dat"}, "binary"),
        ({"op": "list", "path": "src/a.py"}, "refused"),
        ({"op": "list", "path": "src/nope"}, "absent"),
        ({"op": "search", "path": "nope", "query": "x"}, "absent"),
        ({"op": "search", "query": ""}, "refused"),
        ({"op": "search", "path": "src/a.py", "query": "two"}, "ok"),
        ({"op": "list"}, "ok"),
        ({"op": "read", "path": "src/big.txt"}, "ok"),
    ]
    for args, want in cases:
        text, record = _serve(reader, tree="head", **args)
        assert record["result"] == want, (args, text)
    text, record = _serve(reader, tree="head", op="search", query="needle")
    assert ".git" not in text and "link" not in text and "bin.dat" not in text
    text, record = _serve(reader, tree="head", op="list")
    assert ".git" not in text and "linkdir" not in text
    text, record = _serve(reader, tree="head", op="read", path="src/big.txt")
    assert "[truncated: next line" in text and record["result"] == "ok"


def test_os_errors_and_odd_inputs_are_refused_not_raised(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / "src" / "latin.txt").write_bytes(b"caf\xe9")
    reader = ReadTree({"head": root}, {**BUDGET, "max_calls": 100, "max_total_bytes": 10**6})
    assert _serve(reader, tree="head", op="read", path="src/latin.txt")[1]["result"] == "binary"
    assert _serve(reader, tree="head", op="nope", path="x")[1]["result"] == "refused"
    real_lstat, real_listdir, real_open = os.lstat, os.listdir, os.open

    def lstat(path, *a, **k):
        if str(path).endswith("zed.txt"):
            raise PermissionError("no")
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(os, "lstat", lstat)
    assert _serve(reader, tree="head", op="read", path="src/zed.txt")[1]["result"] == "refused"
    assert "zed.txt" not in _serve(reader, tree="head", op="list", path="src")[0]
    monkeypatch.setattr(os, "lstat", real_lstat)
    monkeypatch.setattr(os, "listdir", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert _serve(reader, tree="head", op="list", path="src")[1]["result"] == "refused"
    monkeypatch.setattr(os, "listdir", real_listdir)

    def opener(path, *a, **k):
        if str(path).endswith(("a.py", "zed.txt")):
            raise OSError("x")
        return real_open(path, *a, **k)

    monkeypatch.setattr(os, "open", opener)
    assert _serve(reader, tree="head", op="read", path="src/a.py")[1]["result"] == "refused"
    assert _serve(reader, tree="head", op="search", path="src", query="needle")[1]["result"] == "ok"
