"""Tests for src/turn_capture/litellm_usage.py: golden vectors, the contract tie, the missing-usage rules.

"requirements-dev.txt" is spelled as a quoted token here so a LiteLLM bump selects this file (Decision 135).
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.deps.affected_tests import derive_affected_tests
from scripts.llm.litellm_wire import load_litellm
from src.turn_capture import litellm_usage as lu
from tests.fixtures import litellm_capture as cap

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "tests" / "fixtures" / "litellm_usage" / "vectors.json").read_text(encoding="utf-8"))
CONTRACT = yaml.safe_load((ROOT / "docs" / "contracts" / "inference-provider.yaml").read_text(encoding="utf-8"))
IDS = [v["name"] for v in VECTORS]
_SEQ = (str, int, list, dict)


def _columns(columns: lu.UsageColumns) -> dict[str, Any]:
    out = dataclasses.asdict(columns)
    out["usage_absent"] = list(out["usage_absent"])
    return out


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip(v) for v in value if v is not None]
    return value


def _dig(usage: dict[str, Any], path: str) -> Any:
    node: Any = usage
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _projection(dump: dict[str, Any], prefix: str) -> dict[str, Any]:
    row = lu.row_for(prefix)
    paths: set[str] = {"prompt_tokens", "completion_tokens", "total_tokens"}
    for name in ("tokens_input", "tokens_cache_read", "tokens_cache_creation", "tokens_output", "reasoning_tokens"):
        form = row[name]
        if isinstance(form, str) and form != "none":
            paths.update(form.split(" - "))
    split = row["cache_creation_split"]
    if split != "none":
        first, other = split.split(" and ")
        paths.update({first, first.rsplit(".", 1)[0] + "." + other})
    out = {p: _dig(dump, p) for p in sorted(paths)}
    out["iterations"] = dump.get("iterations")
    return out


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_usage_mapping_conformance(vector: dict[str, Any]) -> None:
    assert _columns(lu.map_usage(vector["prefix"], vector["dump"])) == vector["expected"]


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_vectors_match_litellm_completion(vector: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    cap.reset(monkeypatch)
    litellm = load_litellm()
    with cap.capture([(200, vector["wire"])]):
        response = litellm.completion(
            model=vector["model"],
            messages=[{"role": "user", "content": "hi"}],
            api_key="synthetic-value",  # pragma: allowlist secret
            num_retries=0,
        )
    live = _strip(response.usage.model_dump())
    assert _projection(live, vector["prefix"]) == _projection(vector["dump"], vector["prefix"])
    assert _columns(lu.map_usage(vector["prefix"], live)) == vector["expected"]
    if vector["prefix"] == "anthropic":
        assert response.id != vector["wire"]["id"]


def _wire_uncached(vector: dict[str, Any]) -> int:
    wire = vector["wire"]["usage"]
    if vector["prefix"] == "anthropic":
        iterations = wire.get("iterations")
        if iterations:
            return sum(item["input_tokens"] for item in iterations)
        return wire["input_tokens"]
    return wire["prompt_tokens"] - wire["prompt_tokens_details"]["cached_tokens"]


DIFFERENCES = [v for v in VECTORS if " - " in lu.row_for(v["prefix"])["tokens_input"] and "usage" in v["wire"]]


@pytest.mark.parametrize("vector", DIFFERENCES, ids=[v["name"] for v in DIFFERENCES])
def test_difference_equals_wire_uncached(vector: dict[str, Any]) -> None:
    assert vector["expected"]["tokens_input"] == _wire_uncached(vector)


def test_table_matches_contract() -> None:
    assert lu.USAGE_TABLE == CONTRACT["usage_mapping"]["rows"]


def test_lock_only_diff_selects_this_file() -> None:
    selected = derive_affected_tests([("M", "requirements-dev.txt")])["selected"]
    assert "tests/turn_capture/test_litellm_usage.py" in selected


@pytest.mark.parametrize(
    ("prefix", "names"),
    [
        ("anthropic", ["tokens_cache_creation", "tokens_cache_read", "tokens_input", "tokens_output"]),
        ("deepseek", ["tokens_cache_read", "tokens_input", "tokens_output"]),
        ("openai", ["tokens_cache_read", "tokens_input", "tokens_output"]),
    ],
)
@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}])
def test_missing_usage_marks_absent(prefix: str, names: list[str], usage: dict[str, Any] | None) -> None:
    columns = lu.map_usage(prefix, usage)
    assert list(columns.usage_absent) == names
    assert (columns.tokens_input, columns.tokens_output, columns.tokens_cache_read, columns.tokens_cache_creation) == (0,) * 4
    assert columns.reasoning_tokens is None and columns.cache_creation_1h_tokens is None


def test_missing_key_outside_guaranteed_is_not_named() -> None:
    columns = lu.map_usage("openai", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
    assert (columns.tokens_input, columns.tokens_cache_read, columns.usage_absent) == (10, 0, ())


def test_missing_guaranteed_key_is_named_in_each_class_it_feeds() -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cache_read_input_tokens": 1}
    columns = lu.map_usage("anthropic", usage)
    assert columns.usage_absent == ("tokens_cache_creation", "tokens_input")
    assert columns.tokens_input == 0 and columns.tokens_cache_read == 1


def test_negative_difference_is_zero_and_absent() -> None:
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "prompt_tokens_details": {"cached_tokens": 9}}
    columns = lu.map_usage("openai", usage)
    assert columns.tokens_input == 0 and columns.usage_absent == ("tokens_input",)


def test_split_above_cache_creation_is_none() -> None:
    usage = copy.deepcopy(next(v for v in VECTORS if v["name"] == "anthropic_cached_with_split")["dump"])
    details = usage["prompt_tokens_details"]["cache_creation_token_details"]
    details["ephemeral_1h_input_tokens"] = 999
    columns = lu.map_usage("anthropic", usage)
    assert columns.cache_creation_1h_tokens is None and columns.cache_creation_5m_tokens == 20
    del details["ephemeral_5m_input_tokens"]
    assert lu.map_usage("anthropic", usage).cache_creation_5m_tokens is None


@pytest.mark.parametrize("bad", [True, 1.5, "7"])
def test_non_int_value_is_missing(bad: Any) -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": bad, "total_tokens": 12, "prompt_tokens_details": {"cached_tokens": 0}}
    columns = lu.map_usage("openai", usage)
    assert columns.tokens_output == 0 and "tokens_output" in columns.usage_absent


def test_reasoning_tokens_provider_only() -> None:
    assert lu.map_usage("deepseek", VECTORS[0]["dump"]).reasoning_tokens == 5
    anthropic = next(v for v in VECTORS if v["prefix"] == "anthropic")
    assert anthropic["dump"]["completion_tokens_details"]["reasoning_tokens"] == 0
    assert lu.map_usage("anthropic", anthropic["dump"]).reasoning_tokens is None
    no_reasoning = copy.deepcopy(VECTORS[0]["dump"])
    del no_reasoning["completion_tokens_details"]
    assert lu.map_usage("deepseek", no_reasoning).reasoning_tokens is None


BAD_FORMS = [
    ("tokens_input", "prompt_tokens + 1"),
    ("tokens_cache_creation", 3),
    ("tokens_output", 1.5),
    ("reasoning_tokens", "a - b"),
    ("reasoning_tokens", 7),
    ("cache_creation_split", "a.b"),
    ("cache_creation_split", 7),
    ("response_id", "sometimes"),
]


@pytest.mark.parametrize(("field", "form"), BAD_FORMS)
@pytest.mark.parametrize(
    "usage",
    [None, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}],
    ids=["no_usage", "usage"],
)
def test_unknown_form_raises_on_first_use(
    field: str, form: Any, usage: dict[str, Any] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = copy.deepcopy(lu.USAGE_TABLE)
    table["default"][field] = form
    monkeypatch.setattr(lu, "USAGE_TABLE", table)
    if usage is None and field in ("reasoning_tokens", "cache_creation_split"):
        assert lu.map_usage("default", usage).usage_absent
        return
    with pytest.raises(ValueError, match="unknown form"):
        lu.map_usage("default", usage)


def test_table_parses_every_row() -> None:
    for prefix in lu.USAGE_TABLE:
        usage = {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}
        assert lu.map_usage(prefix, usage).tokens_output in (0, 1)


def _closure(entry: Path) -> set[Path]:
    seen: set[Path] = set()
    todo = [entry]
    while todo:
        path = todo.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for name in names:
                if name.split(".")[0] not in ("src", "scripts"):
                    continue
                parts = name.split(".")
                for i in range(1, len(parts) + 1):
                    base = ROOT.joinpath(*parts[:i])
                    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                        if candidate.exists():
                            todo.append(candidate)
    return seen


def test_run_persona_closure_adds_only_litellm_usage() -> None:
    closure = {p.relative_to(ROOT).as_posix() for p in _closure(ROOT / "scripts" / "run_persona.py")}
    turn_capture = {p for p in closure if p.startswith("src/turn_capture/")}
    assert turn_capture == {"src/turn_capture/__init__.py", "src/turn_capture/litellm_usage.py"}, (
        "scripts/run_persona.py's closure under src/turn_capture changed; companion edits: config/persona_image.yaml "
        "build.copy, its Dockerfile and .dockerignore lines, and validate_persona_github_agnosticism's pre_globs in "
        "scripts/checks/ci_guards/_manifest.py (when present); a deliberate widening edits this test"
    )
    assert not [p for p in closure if p.startswith(("src/sensitive_values/", "src/telemetry/"))]
