"""rec-5477: envelope rule R10 as a check. A stored session is never re-keyed, no root ref has two originators, and a
registered host_run_ref prefix never changes.

Stdlib + yaml, fast tier; reads files and runs git only. The golden corpora are the stored inputs: a session a golden
case produced at the merge-base with origin/main must still be produced under the same ref and session id.

Leg (b)'s claude_code root form restates the '<sessionId>:<agentId>' entity_ref grammar in
docs/contracts/telemetry_sessions.yaml (governance_notes_local); rec-4419 (T3.22:c3) owns moving that form to the
writer. A minter here is what envelope R10 calls a root ref's originator (a ref maker, never an id: Decision 199's
2026-10-24 update); an annotator (telemetry_sessions.yaml's annotate row, such as ci_annotate) originates none.

Selection literals: config/telemetry/parser_versions.yaml, docs/contracts/telemetry_agents.yaml,
tests/fixtures/turn_capture/golden/index.json, tests/fixtures/turn_capture/golden/litellm/index.json and
tests/fixtures/merge_annotate/golden/index.json.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

import pytest
import yaml

from src.telemetry.identity import KEY_PLANS

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REL = "config/telemetry/parser_versions.yaml"
AGENTS_REL = "docs/contracts/telemetry_agents.yaml"
PRODUCER_CORPUS = {
    "claude_code": "tests/fixtures/turn_capture/golden/index.json",
    "litellm": "tests/fixtures/turn_capture/golden/litellm/index.json",
    "ci_annotate": "tests/fixtures/merge_annotate/golden/index.json",
}
PRODUCER_ROLE: dict[str, tuple[str, str]] = {
    "claude_code": ("minter", "claude_code"),
    "litellm": ("minter", "litellm"),
    "ci_annotate": ("annotator", "claude_code"),
}
_UUID_ROOT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SESSION_TAG = "telemetry_sessions:session_id"

SessionPair = tuple[str, str | None]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


@functools.cache
def _base_sha() -> str | None:
    base = _git("merge-base", "HEAD", "origin/main")
    sha = base.stdout.strip()
    if base.returncode != 0 or not sha or _git("cat-file", "-e", sha).returncode != 0:
        return None
    return sha


def _require_base() -> str:
    sha = _base_sha()
    if sha is None:
        if os.environ.get("CI"):
            pytest.fail("origin/main is unreadable in CI: the R10 base comparison cannot run (Decision 55)")
        pytest.skip("env: origin/main unreadable locally")
    return sha


def _base_text(sha: str, rel: str) -> str | None:
    if _git("cat-file", "-e", f"{sha}:{rel}").returncode != 0:
        return None
    shown = _git("show", f"{sha}:{rel}")
    if shown.returncode != 0:
        pytest.fail(f"git show {sha}:{rel} failed: {shown.stderr.strip()}")
    return shown.stdout


def _head_text(rel: str) -> str | None:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _registry_producers() -> dict[str, Any]:
    return yaml.safe_load((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))["producers"]


def session_pairs(rows: Iterable[dict[str, Any]]) -> set[SessionPair]:
    pairs: set[SessionPair] = set()
    for row in rows:
        for column, plan in KEY_PLANS.get(row.get("table", ""), {}).items():
            if plan.domain_tag != SESSION_TAG:
                continue
            ref = row.get(plan.ref_field)
            if ref is not None:
                pairs.add((ref, row.get(column)))
    return pairs


def _case_pairs(text: str | None) -> set[SessionPair]:
    return session_pairs(json.loads(text)["expected_rows"]) if text is not None else set()


def _index_cases(text: str | None) -> list[str]:
    return list(json.loads(text)["cases"]) if text is not None else []


def lost_sessions(base: dict[str, set[SessionPair]], head: dict[str, set[SessionPair]]) -> list[str]:
    problems = []
    for case, pairs in base.items():
        if case not in head:
            problems.append(f"{case}: a base case is renamed or deleted")
            continue
        for pair in sorted(pairs - head[case], key=repr):
            problems.append(f"{case}: no stored session disappears or is re-keyed; lost {pair}")
    return problems


def split_alternatives(pattern: str) -> list[str]:
    inner = pattern.removeprefix("^(").removesuffix(")$")
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "|" and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
    parts.append(inner[start:])
    return parts


def host_run_ref_alternatives(agents_yaml: str) -> list[str]:
    return split_alternatives(yaml.safe_load(agents_yaml)["fields"]["host_run_ref"]["dq_intent"]["pattern"])


def prefix_problems(base: list[str], head: list[str]) -> list[str]:
    problems = [f"registered alternative changed or removed: {alt}" for alt in base if alt not in head]
    tags = [alt.split(":", 1)[0] for alt in head]
    for alt, tag in zip(head, tags):
        if not re.fullmatch(r"[a-z]+", tag):
            problems.append(f"prefix tag is not a lowercase literal: {alt}")
    problems += [f"prefix tag is not unique: {tag}" for tag in sorted({t for t in tags if tags.count(t) > 1})]
    return problems


def minter_forms(head_agents_yaml: str) -> dict[str, re.Pattern[str]]:
    litellm = "|".join(host_run_ref_alternatives(head_agents_yaml))
    return {
        "claude_code": re.compile(rf"{_UUID_ROOT}(:[A-Za-z0-9_.-]+)?"),
        "litellm": re.compile(rf"litellm:(?:{litellm})"),
    }


def root_ref_problems(
    refs: dict[str, set[str]], roles: dict[str, tuple[str, str]], forms: dict[str, re.Pattern[str]]
) -> list[str]:
    problems = []
    for producer in refs:
        if producer not in roles:
            problems.append(f"{producer}: no role declared (a new producer declares minter or annotator)")
    for producer, (role, target) in roles.items():
        if role == "minter" and producer not in forms:
            problems.append(f"{producer}: minter without a root form")
        if role == "annotator" and roles.get(target, ("", ""))[0] != "minter":
            problems.append(f"{producer}: annotator names no minter ({target})")
    for producer, producer_refs in refs.items():
        role, target = roles.get(producer, ("", ""))
        for ref in sorted(producer_refs):
            if role == "annotator":
                if not forms[target].fullmatch(ref):
                    problems.append(f"{producer}: annotator ref {ref} matches no {target} form")
                continue
            matched = [name for name, form in forms.items() if form.fullmatch(ref)]
            if matched != [producer] and role == "minter":
                problems.append(f"{producer}: ref {ref} matches forms {matched}, not only its own")
    return problems


def _head_corpus(index_rel: str) -> dict[str, str]:
    index = _head_text(index_rel)
    if index is None:
        return {}
    folder = Path(index_rel).parent
    return {name: (ROOT / folder / name).read_text(encoding="utf-8") for name in _index_cases(index)}


def test_golden_sessions_keep_their_refs() -> None:
    renamed = {"c.json": {("a", "i1")}}
    assert lost_sessions(renamed, {"c.json": {("b", "i1")}})
    assert lost_sessions(renamed, {}) and lost_sessions(renamed, {"c.json": {("a", "i2")}})
    assert not lost_sessions(renamed, {"c.json": {("a", "i1"), ("new", "i9")}, "new.json": {("n", "i")}})
    rows = [
        {
            "table": "telemetry_sessions",
            "entity_ref": "r",
            "session_id": "S",
            "parent_session_ref": "p",
            "parent_session_id": "P",
        },
        {"table": "telemetry_observations", "session_ref": "r", "session_id": "S"},
        {"table": "telemetry_agents", "session_ref": None, "session_id": "S"},
        {"table": "telemetry_observations", "session_ref": "q"},
    ]
    assert session_pairs(rows) == {("r", "S"), ("p", "P"), ("q", None)}

    sha = _require_base()
    for producer in _registry_producers():
        index_rel = PRODUCER_CORPUS.get(producer)
        assert index_rel, f"{producer}: no golden corpus declared in PRODUCER_CORPUS"
        folder = Path(index_rel).parent
        base_index = _base_text(sha, index_rel)
        head = {name: _case_pairs(text) for name, text in _head_corpus(index_rel).items()}
        base = {}
        for name in _index_cases(base_index):
            text = _base_text(sha, str(folder / name))
            if text is not None:
                base[name] = _case_pairs(text)
        assert not lost_sessions(base, head), lost_sessions(base, head)


def test_root_refs_are_disjoint_across_minters() -> None:
    forms = {
        "a": re.compile(r"a:[0-9]+"),
        "b": re.compile(r"b:[0-9]+"),
    }
    roles = {"a": ("minter", "a"), "b": ("minter", "b"), "n": ("annotator", "a")}
    assert not root_ref_problems({"a": {"a:1"}, "b": {"b:2"}, "n": {"a:3"}}, roles, forms)
    assert root_ref_problems({"a": {"b:1"}}, roles, forms)
    assert root_ref_problems({"a": {"a:1", "zzz"}}, roles, forms)
    assert root_ref_problems({"n": {"zzz"}}, roles, forms)
    assert root_ref_problems({"new": {"a:1"}}, roles, forms)
    overlapping = {**forms, "b": re.compile(r"[ab]:[0-9]+")}
    assert root_ref_problems({"a": {"a:1"}}, roles, overlapping)

    registered = _registry_producers()
    for producer, entry in registered.items():
        assert producer in PRODUCER_ROLE, f"{producer}: no role declared in PRODUCER_ROLE"
        assert PRODUCER_CORPUS.get(producer) == entry["golden_index"], f"{producer}: golden_index differs from the map"
        assert (ROOT / entry["golden_index"]).is_file(), f"{producer}: golden index absent at HEAD"
    refs: dict[str, set[str]] = {}
    for producer in PRODUCER_ROLE:
        if producer not in registered:
            continue
        pairs: set[SessionPair] = set()
        for text in _head_corpus(PRODUCER_CORPUS[producer]).values():
            pairs |= _case_pairs(text)
        refs[producer] = {ref for ref, _ in pairs}
    agents = _head_text(AGENTS_REL)
    assert agents is not None
    problems = root_ref_problems(refs, PRODUCER_ROLE, minter_forms(agents))
    assert not problems, problems


def test_host_run_ref_prefixes_are_append_only() -> None:
    base = ["gha:[0-9]+", "ecs:[a-z]+"]
    assert not prefix_problems(base, [*base, "lambda:[a-z]+"])
    assert prefix_problems(base, ["gha:[0-9]+"])
    assert prefix_problems(base, ["gha:[0-9]{1,5}", "ecs:[a-z]+"])
    assert prefix_problems(base, [*base, "gha:[0-9]+"]) and prefix_problems(base, [*base, "Local:[a-z]+"])
    assert split_alternatives("^(a:[x|y]{1,2}|b:(c|d)|e:f)$") == ["a:[x|y]{1,2}", "b:(c|d)", "e:f"]

    sha = _require_base()
    head_text = _head_text(AGENTS_REL)
    base_text = _base_text(sha, AGENTS_REL)
    assert head_text is not None
    base_alts = host_run_ref_alternatives(base_text) if base_text is not None else []
    problems = prefix_problems(base_alts, host_run_ref_alternatives(head_text))
    assert not problems, f"a whole registered alternative never changes, character class included: {problems}"
