import json

from src.persona_backends.read_input import READ_CAP_BYTES, serve, tool_spec

BLOBS = {"notes": "a\nb\nc\nd", "data": {"z": 1, "a": 2}}


def call(**arguments):
    return serve(json.dumps(arguments), BLOBS)


def test_tool_spec_enumerates_blobs():
    spec = tool_spec(["notes"])
    assert spec["function"]["parameters"]["properties"]["blob"]["enum"] == ["notes"]


def test_lines_range_and_numbering():
    assert call(blob="notes", mode="lines", start=2, end=3) == "2: b\n3: c\n[notes: 4 lines]"


def test_default_range_is_whole_blob():
    assert call(blob="notes", mode="lines").splitlines()[:4] == ["1: a", "2: b", "3: c", "4: d"]


def test_byte_cap_truncates():
    big = {"notes": "\n".join("x" * 100 for _ in range(1000))}
    out = serve(json.dumps({"blob": "notes", "mode": "lines"}), big)
    assert "[truncated: next line" in out
    assert len(out.encode()) < READ_CAP_BYTES + 200


def test_search_is_literal():
    blobs = {"notes": "a.c\nabc\nx"}
    out = serve(json.dumps({"blob": "notes", "mode": "search", "query": "a.c"}), blobs)
    assert out == "1: a.c\n[notes: 3 lines]"


def test_search_match_cap():
    blobs = {"notes": "\n".join(["hit"] * 205)}
    out = serve(json.dumps({"blob": "notes", "mode": "search", "query": "hit"}), blobs)
    assert "[5 more matches]" in out


def test_non_string_blob_is_canonical_json():
    assert call(blob="data", mode="lines", start=2, end=2).startswith('2:   "a": 2')


def test_bad_arguments_return_error_text():
    assert serve("{", BLOBS).startswith("error:")
    assert call(blob="other", mode="lines").startswith("error:")
    assert call(blob="notes", mode="grep").startswith("error:")
    assert call(blob="notes", mode="lines", start=0).startswith("error:")
    assert call(blob="notes", mode="search").startswith("error:")
    assert serve("[]", BLOBS).startswith("error:")
