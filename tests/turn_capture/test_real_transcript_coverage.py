"""DG-2 silent-failure checks on REAL transcripts, computed from the raw records independently of the producer.

pytest.mark.integration, local only. Counts and booleans only are printed or asserted: nothing from a transcript is
echoed. A class the tree set does not hold (sub-agent, resumed tree, background notice) is reported UNPROVEN, never as a
pass. The checkout columns are proven after merge by VP 13: no pre-merge real tree carries the new pin line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import FsTree
from tests.fixtures.turn_capture_real import discover, raw_inherited, snapshot

pytestmark = pytest.mark.integration

PROJECT_REF = "real/coverage"
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
EFFORT = re.compile(r"[a-z][a-z0-9_-]{0,31}")
STATUS = re.compile(r"<status>(.*?)</status>", re.DOTALL)
INPUTS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def raw_records(path: Path) -> list[dict]:
    """The decoded JSON objects of a stream file in file order, a root fork's copied lines excluded (independent of src)."""
    inherited = raw_inherited(path)[0]
    out: list[dict] = []
    for index, raw in enumerate(path.read_bytes().split(b"\n")[:-1]):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and index not in inherited:
            out.append(data)
    return out


def _streams(root: Path) -> dict[str, Path]:
    files = {root.stem: root}
    for path in sorted((root.parent / root.stem / "subagents").glob("agent-*.jsonl")):
        files[f"{root.stem}:{path.stem[len('agent-') :]}"] = path
    return files


def _owners(records: list[dict]) -> dict[str, dict]:
    """message id -> its LAST assistant record (the response's owner), API errors and synthetic records excluded."""
    out: dict[str, dict] = {}
    for d in records:
        message = d.get("message") if d.get("type") == "assistant" else None
        if isinstance(message, dict) and isinstance(message.get("id"), str) and d.get("isApiErrorMessage") is not True:
            if message.get("model") != "<synthetic>":
                out[message["id"]] = d
    return out


def _sync_usages(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in _streams(root).values():
        for d in raw_records(path):
            info = d.get("toolUseResult") if d.get("type") == "user" else None
            if isinstance(info, dict) and isinstance(info.get("agentId"), str) and not info.get("isAsync"):
                if isinstance(info.get("usage"), dict):
                    out[info["agentId"]] = info["usage"]
    return out


def _notices(records: list[dict]) -> list[tuple[str, str | None]]:
    """(agent id, raw status token or None) of every <task-notification> record, in file order."""
    found = []
    for d in records:
        att = d.get("attachment") if d.get("type") == "attachment" else None
        text = att.get("prompt") if isinstance(att, dict) and att.get("type") == "queued_command" else None
        if text is None and d.get("type") == "user" and isinstance((d.get("message") or {}).get("content"), str):
            text = d["message"]["content"]
        agent = re.search(r"<task-id>(.*?)</task-id>", text) if isinstance(text, str) else None
        if isinstance(text, str) and text.startswith("<task-notification>") and agent:
            status = STATUS.search(text)
            found.append((agent.group(1), status.group(1) if status else None))
    return found


def _expected_outcome(status: str | None) -> str:
    return {"completed": "success", "timeout": "timeout"}.get(status or "", "failed")


def _corrected(owner: dict, spawn: dict | None) -> bool:
    message = owner["message"]
    if spawn is None or isinstance(message.get("stop_reason"), str) or owner.get("isAbortedMidStream") is True:
        return False
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return all(type(spawn.get(k)) is int and spawn.get(k) == usage.get(k) for k in INPUTS)


def _first_turn_last_owner(records: list[dict]) -> str | None:
    """The message id of the first turn's last response in a stream (turns open at each new promptId)."""
    seen: set[str] = set()
    current: str | None = None
    last: str | None = None
    for d in records:
        pid = d.get("promptId")
        if (
            d.get("type") == "user"
            and isinstance(pid, str)
            and pid
            and not d.get("sourceToolUseID")
            and not d.get("turnCompanion")
        ):
            if current is not None and pid != current:
                break
            current = pid
            seen.add(pid)
        elif d.get("type") == "assistant" and current is not None and isinstance((d.get("message") or {}).get("id"), str):
            if d.get("isApiErrorMessage") is not True and d["message"].get("model") != "<synthetic>":
                last = d["message"]["id"]
    return last


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    sources = discover()
    if not sources:
        pytest.fail(
            "no real Claude Code transcripts found: set TURN_CAPTURE_REAL_TRANSCRIPTS or run on a machine that has them"
        )
    out = []
    for root in snapshot(sources, tmp_path_factory.mktemp("coverage")):
        result = record_turn(FsTree(root), None, project_ref=PROJECT_REF, session_final=True)
        out.append({"root": root, "result": result})
    return [r for r in out if r["result"].next_cursor is not None]


def _check_opens(records: list[dict], result: Any, stream: str, checked: dict[str, int]) -> None:
    changes = [
        (i, d)
        for i, d in enumerate(records)
        if d.get("type") == "attachment" and (d.get("attachment") or {}).get("type") == "remote_session_change"
    ]
    urls = [str((d["attachment"].get("url") or "")).rsplit("/", 1)[-1] for _, d in changes]
    first_assistant = next((i for i, d in enumerate(records) if d.get("type") == "assistant"), None)
    for row in (r for r in result.sessions if r["event_kind"] == "open" and r["entity_ref"] == stream):
        if row.get("harness_session_key") is not None:
            assert row["harness_session_key"] in urls, "an open row's harness_session_key is in no raw attachment"
            checked["open_keys"] += 1
        if changes and first_assistant is not None and changes[0][0] < first_assistant and KEY.fullmatch(urls[0]):
            assert row.get("harness_session_key") == urls[0], "a raw remote_session_change was missed (silent failure)"


def _check_split(row: dict, usage: dict) -> None:
    raw_split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    for col, key in (
        ("cache_creation_1h_tokens", "ephemeral_1h_input_tokens"),
        ("cache_creation_5m_tokens", "ephemeral_5m_input_tokens"),
    ):
        value = raw_split.get(key)
        ok = type(value) is int and value <= row["tokens_cache_creation"]
        assert row.get(col) == (value if ok else None), f"{col} disagrees with the raw usage.cache_creation"


def _check_calls(records: list[dict], result: Any, stream: str, spawn: dict | None, checked: dict[str, int]) -> None:
    owners = _owners(records)
    first_last = _first_turn_last_owner(records)
    for row in (r for r in result.observations if r["observation_type"] == "model_call" and r["session_ref"] == stream):
        owner = owners[row["entity_ref"]]
        message = owner["message"]
        requested, effort = owner.get("requestedModel"), owner.get("effort")
        assert row.get("requested_model") == (requested if isinstance(requested, str) and requested else None)
        assert row.get("reasoning_effort") == (effort if isinstance(effort, str) and EFFORT.fullmatch(effort) else None)
        fixed = _corrected(owner, spawn if row["entity_ref"] == first_last else None)
        _check_split(row, (spawn if fixed else message.get("usage")) or {})
        assert (row["usage_source"] == "spawn_result") is fixed
        final = fixed or isinstance(message.get("stop_reason"), str)
        assert row["usage_final"] is final
        checked["model_calls"] += 1
        checked["corrected"] += fixed
        checked["not_final"] += not final


def _check_notices(root: Path, records: list[dict], result: Any, checked: dict[str, int]) -> int:
    seen = 0
    for agent, status in _notices(records):
        if f"{root.stem}:{agent}" not in _streams(root):
            continue
        seen += 1
        closes = [r for r in result.agents if r["event_kind"] == "close" and r["session_ref"].endswith(f":{agent}")]
        assert closes, "a background notice produced no close row"
        assert _expected_outcome(status) in {r["outcome"] for r in closes}
        checked["agent_closes"] += 1
    return seen


def _resumed(records: list[dict]) -> bool:
    return any(
        d.get("type") == "attachment"
        and str((d.get("attachment") or {}).get("hookName") or "").startswith("SessionStart:resume")
        for d in records
    )


def test_new_columns_agree_with_the_raw_records(runs: list[dict[str, Any]]) -> None:
    classes = {"sub-agent": 0, "resumed tree": 0, "background notice": 0}
    checked = {"open_keys": 0, "model_calls": 0, "corrected": 0, "not_final": 0, "agent_closes": 0}
    for run in runs:
        root, result = run["root"], run["result"]
        spawns = _sync_usages(root)
        for stream, path in _streams(root).items():
            records = raw_records(path)
            agent = stream.split(":", 1)[1] if ":" in stream else None
            classes["sub-agent"] += bool(agent)
            classes["resumed tree"] += _resumed(records) and not agent
            _check_opens(records, result, stream, checked)
            _check_calls(records, result, stream, spawns.get(agent) if agent else None, checked)
            if not agent:
                classes["background notice"] += _check_notices(root, records, result, checked)
    print(f"\ncoverage: checked {checked}")
    for name, count in classes.items():
        print(f"coverage: class {name}: {'present (' + str(count) + ')' if count else 'UNPROVEN -- absent from the tree set'}")
    assert checked["model_calls"], "no model_call was checked"
